"""Ruleset v1: the rules that read one poll. Section 6.4.

Run with: python3 -m unittest discover -s tests -v

The recurring assertion is that a rule which could not run returns
NOT_APPLICABLE rather than ADMIT. That is the invariant the whole system rests
on, and it is the one an implementation drifts away from silently: an ADMIT from
an unevaluable rule looks exactly like an ADMIT from a passing one.

R5 and R6 live in `test_trust_rules_windowed.py`; this file outgrew the size
limit and R5 reads a window rather than a poll, which is the natural seam.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.trust_scorer import rules
from src.features.trust_scorer.verdicts import (
    ADMIT,
    NOT_APPLICABLE,
    QUARANTINE,
    WATCH,
    most_severe,
)
from tests.rule_support import (
    DAY,
    failed,
    obs,
    r1,
    r2,
    r3,
    r4,
    unreached,
)


class TestR1Unreachable(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(r1(obs(), []), ADMIT)
        self.assertEqual(r1(failed(), [failed(5)]), ADMIT)
        self.assertEqual(r1(failed(), [failed(5), failed(10)]), WATCH)
        self.assertEqual(r1(failed(), [failed(i) for i in range(1, 12)]), QUARANTINE)

    def test_our_own_failure_is_not_the_publishers(self):
        """A poll that never left Interchange says nothing about the publisher.

        R1 counted any failed poll, so an outage at this end, or a missing
        offline capture, produced a QUARANTINE and drafted a notice to the
        registry owner asserting the feed had been unreachable. The console
        showed thirteen such polls against Minnesota DOT reading
        `NoFixture: nothing captured for <url>`.
        """
        outcome = rules.r1_unreachable(unreached(), [unreached(5), unreached(10)])
        self.assertEqual(outcome.verdict, NOT_APPLICABLE)
        # Not ADMIT either. We did not learn the feed was up, and R1 is the one
        # rule that could otherwise retire a latch on our own gap.
        self.assertEqual(outcome.reason, "MISSING_INPUT")

    def test_our_own_failure_does_not_extend_a_real_streak(self):
        """A gap at this end breaks the run rather than counting toward it."""
        history = [unreached(5), failed(10), failed(15)]
        self.assertEqual(r1(failed(), history), ADMIT)

    def test_a_304_ends_the_failure_streak(self):
        """A 304 is a successful poll. Collapsing it into failure would make
        every well-behaved conditional-GET publisher look unreachable."""
        not_modified = obs(http_status=304, not_modified=True, carried_forward=True)
        history = [not_modified, *[failed(i) for i in range(2, 20)]]
        self.assertEqual(r1(failed(), history), ADMIT)


class TestR2Stale(unittest.TestCase):
    def test_absolute_bounds(self):
        self.assertEqual(r2(obs(update_age_seconds=3 * DAY), 300), ADMIT)
        self.assertEqual(r2(obs(update_age_seconds=10 * DAY), 300), WATCH)
        self.assertEqual(r2(obs(update_age_seconds=40 * DAY), 300), QUARANTINE)

    def test_a_slow_publisher_is_not_libelled_by_the_absolute_bound(self):
        """Hawaii DOT declares a 168h cadence. A flat seven day rule would
        quarantine a publisher behaving exactly as it said it would."""
        cadence = 168 * 3600
        self.assertEqual(r2(obs(update_age_seconds=10 * DAY), cadence), ADMIT)

    def test_hawaii_still_quarantines_at_896_days(self):
        cadence = 168 * 3600
        self.assertEqual(r2(obs(update_age_seconds=896 * DAY), cadence), QUARANTINE)

    def test_unknown_age_is_not_applicable_not_admit(self):
        self.assertEqual(r2(obs(update_age_seconds=None), 300), NOT_APPLICABLE)
        self.assertEqual(r2(failed(), 300), NOT_APPLICABLE)

    def test_the_bounds_are_strict(self):
        """Exactly at the bound is not over it. Tested at the boundary because
        values comfortably away from it pass under either comparison, which is
        how an inclusive/exclusive slip survives a test suite."""
        watch_at = max(rules.R2_WATCH_ABSOLUTE_SECONDS, 3 * 300)
        self.assertEqual(r2(obs(update_age_seconds=watch_at), 300), ADMIT)
        self.assertEqual(r2(obs(update_age_seconds=watch_at + 1), 300), WATCH)
        quarantine_at = max(rules.R2_QUARANTINE_ABSOLUTE_SECONDS, 10 * 300)
        self.assertEqual(r2(obs(update_age_seconds=quarantine_at), 300), WATCH)
        self.assertEqual(r2(obs(update_age_seconds=quarantine_at + 1), 300), QUARANTINE)

    def test_a_forward_dated_header_does_not_read_as_fresh(self):
        """R6's condition. Scored as ADMIT here, a publisher could evade R2
        indefinitely by dating its header into next week."""
        self.assertEqual(r2(obs(update_age_seconds=-5000.0), 300), NOT_APPLICABLE)


class TestR3Schema(unittest.TestCase):
    def test_errors_watch_never_quarantine(self):
        self.assertEqual(r3(obs(schema_error_count=0)), ADMIT)
        self.assertEqual(r3(obs(schema_error_count=81)), WATCH)

    def test_unknown_version_suppresses_rather_than_fails(self):
        """No publisher may be penalised for publishing a specification
        Interchange has not implemented. CWZ 1.0 is four live entries."""
        unknown = obs(schema_version_used="SCHEMA_UNKNOWN", schema_error_count=None)
        self.assertEqual(r3(unknown), NOT_APPLICABLE)

    def test_a_304_cannot_validate_a_document_nobody_fetched(self):
        carried = obs(http_status=304, not_modified=True, schema_error_count=None)
        self.assertEqual(r3(carried), NOT_APPLICABLE)


class TestR4Contradiction(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=3)), ADMIT)
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=20)), WATCH)
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=90)), QUARANTINE)

    def test_the_fractions_are_strict(self):
        """Exactly 5 percent is not over 5 percent, and exactly 50 is not over 50."""
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=5)), ADMIT)
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=6)), WATCH)
        self.assertEqual(r4(obs(active_count=100, active_with_past_end_date=50)), WATCH)
        self.assertEqual(
            r4(obs(active_count=100, active_with_past_end_date=51)),
            QUARANTINE,
        )

    def test_a_missing_undated_count_is_not_a_measured_zero(self):
        """The dated denominator could be 100 or 5, and the rule cannot tell 5
        percent from 100 percent. Not checked, so not a pass."""
        unknown = obs(active_count=100, active_with_past_end_date=5, active_undated=None)
        self.assertEqual(r4(unknown), NOT_APPLICABLE)

    def test_utah_quarantines(self):
        """744 of 744. The finding that survives even if Utah starts refreshing
        its timestamp, which is what makes R4 independent of R2."""
        utah = obs(active_count=744, active_with_past_end_date=744)
        self.assertEqual(r4(utah), QUARANTINE)

    def test_zero_active_is_not_applicable_not_admit(self):
        """Hawaii DOT publishes no event_status at all on any of its features,
        so a percentage over zero active zones is undefined."""
        hawaii = obs(active_count=0, active_with_past_end_date=0)
        self.assertEqual(r4(hawaii), NOT_APPLICABLE)

    def test_undated_zones_leave_the_denominator(self):
        """An unparseable end date is not evidence of a contradiction. Counted
        in the denominator it would dilute a real finding; counted in the
        numerator it would manufacture one."""
        every_zone_undated = obs(active_count=50, active_with_past_end_date=0, active_undated=50)
        self.assertEqual(r4(every_zone_undated), NOT_APPLICABLE)
        mixed = obs(active_count=100, active_with_past_end_date=6, active_undated=90)
        self.assertEqual(r4(mixed), QUARANTINE)  # 6 of 10 dated

    def test_a_304_cannot_clear_a_contradiction_only_leave_it_unmeasured(self):
        carried = obs(
            http_status=304,
            not_modified=True,
            carried_forward=True,
            active_count=744,
            active_with_past_end_date=744,
        )
        self.assertEqual(r4(carried), NOT_APPLICABLE)


class TestReasons(unittest.TestCase):
    """NOT_APPLICABLE is not one thing. Round three found that conflating a
    measured inapplicability with an unevaluable one let a run of polls from an
    older agent build clear a quarantine nobody re-measured."""

    def test_a_publisher_that_complied_is_measured_inapplicable(self):
        complied = obs(active_count=0, active_with_past_end_date=0, active_undated=0)
        outcome = rules.r4_contradiction(complied)
        self.assertEqual(outcome.verdict, NOT_APPLICABLE)
        self.assertEqual(outcome.reason, "MEASURED_INAPPLICABLE")
        self.assertTrue(outcome.evaluated, "compliance must count toward recovery")

    def test_a_missing_field_is_unevaluated(self):
        stale_build = obs(active_count=100, active_with_past_end_date=5, active_undated=None)
        outcome = rules.r4_contradiction(stale_build)
        self.assertEqual(outcome.verdict, NOT_APPLICABLE)
        self.assertEqual(outcome.reason, "MISSING_INPUT")
        self.assertFalse(outcome.evaluated, "nothing was measured, so nothing is earned")

    def test_a_304_is_unevaluated_for_every_body_dependent_rule(self):
        carried = obs(
            http_status=304, not_modified=True, carried_forward=True, schema_error_count=None
        )
        for rule in (rules.r3_schema, rules.r4_contradiction):
            self.assertFalse(rule(carried).evaluated, rule.__name__)
        self.assertFalse(rules.r5_frozen(carried, []).evaluated)

    def test_an_unknown_schema_version_is_unevaluated(self):
        unknown = obs(schema_version_used="SCHEMA_UNKNOWN", schema_error_count=None)
        self.assertEqual(rules.r3_schema(unknown).reason, "SCHEMA_UNKNOWN")
        self.assertFalse(rules.r3_schema(unknown).evaluated)


class TestSeverity(unittest.TestCase):
    def test_not_applicable_never_raises_the_maximum(self):
        self.assertEqual(most_severe([NOT_APPLICABLE, ADMIT, NOT_APPLICABLE]), ADMIT)
        self.assertEqual(most_severe([ADMIT, WATCH, NOT_APPLICABLE]), WATCH)
        self.assertEqual(most_severe([WATCH, QUARANTINE]), QUARANTINE)

    def test_nothing_evaluable_is_not_an_admission(self):
        """A publisher on which nothing could be evaluated has not passed
        anything. ADMIT here is the exact failure the product exists to catch."""
        self.assertEqual(most_severe([NOT_APPLICABLE] * 6), WATCH)


if __name__ == "__main__":
    unittest.main()
