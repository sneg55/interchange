"""R5 and R6. Section 6.4.

Run with: python3 -m unittest discover -s tests -v

R5 is the only rule that reads a window rather than a single poll, and R6 is
the one that decides whether a timestamp can be believed at all. Both are
about what CANNOT be concluded, which is why they carry the most
NOT_APPLICABLE cases in the ruleset.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.trust_scorer import rules
from src.features.trust_scorer.churn import churn_status
from src.features.trust_scorer.records import RuleResult
from src.features.trust_scorer.verdicts import (
    ADMIT,
    NOT_APPLICABLE,
    QUARANTINE,
    WATCH,
)
from tests.rule_support import (
    NOW,
    failed,
    obs,
    r2,
    r5,
    r6,
)


class TestR5Frozen(unittest.TestCase):
    """R5's window is 24 hours AND 12 polls, and it needs both.

    An earlier version of these tests built windows of twenty polls five minutes
    apart, spanning 100 minutes, and asserted WATCH. That codified a defect:
    the rule counted polls inside a nominal 24 hour window without ever checking
    that the history reached back through it, so a publisher could be called
    frozen on under two hours of evidence.
    """

    # 90 minutes apart: 20 polls span 28.5 h, and 16 of them fall inside the
    # trailing 24 h, comfortably over the 12 poll minimum.
    SPACING = 90

    def window(self, n, digest="same", stamps=None, spacing=None):
        spacing = spacing or self.SPACING
        return [
            obs(
                minutes_ago=spacing * (i + 1),
                structural_hash=digest,
                update_date=(stamps[i] if stamps else "2020-01-01T00:00:00Z"),
            )
            for i in range(n)
        ]

    def test_history_shorter_than_the_window_is_not_applicable(self):
        """Without this every publisher looks frozen at fleet launch."""
        verdict, detail = r5(obs(), self.window(20, spacing=5))
        self.assertEqual(verdict, NOT_APPLICABLE)
        self.assertLess(detail["span_seconds"], rules.R5_WINDOW_SECONDS)

    def test_too_few_polls_in_the_window_is_not_applicable(self):
        """Static across the window but too sparsely observed to say so."""
        verdict, detail = r5(obs(), self.window(6, spacing=600))
        self.assertEqual(verdict, NOT_APPLICABLE)
        self.assertLess(detail["polls_in_window"], rules.R5_MIN_POLLS)

    def test_frozen_content_with_a_frozen_timestamp_is_only_watch(self):
        """R2 already has this publisher. R5 exists for the other adversary."""
        verdict, detail = r5(obs(), self.window(20))
        self.assertEqual(verdict, WATCH)
        self.assertGreaterEqual(detail["polls_in_window"], rules.R5_MIN_POLLS)

    def test_frozen_content_with_an_advancing_timestamp_quarantines(self):
        """The harder adversary: asserting freshness it does not have. No
        timestamp check sees this and no schema check can."""
        stamps = [(NOW - datetime.timedelta(minutes=i)).isoformat() for i in range(20)]
        verdict, detail = r5(obs(), self.window(20, stamps=stamps))
        self.assertEqual(verdict, QUARANTINE)
        self.assertGreaterEqual(detail["advances"], rules.R5_QUARANTINE_ADVANCES)

    def test_a_timestamp_moving_backwards_is_recorded_but_never_advances(self):
        """Usually inconsistent replicas. Counted as an advance it would
        manufacture the quarantine condition out of a publisher's flapping."""
        stamps = [(NOW - datetime.timedelta(minutes=20 - i)).isoformat() for i in range(20)]
        current = obs(update_date=(NOW - datetime.timedelta(minutes=21)).isoformat())
        verdict, detail = r5(current, self.window(20, stamps=stamps))
        self.assertGreater(detail["regressions"], 0)
        self.assertEqual(detail["advances"], 0)
        self.assertEqual(verdict, WATCH)

    def test_churn_status_is_ok_only_when_r5_actually_spoke(self):
        """The console's churn column, which for the life of the fleet rendered
        a dataclass default: nothing wrote `churn_status` at all.

        Read off R5's reason rather than its verdict, because NOT_APPLICABLE
        covers both "measured and does not apply" and "could not measure", and
        only the second is a cold start."""
        spoke = rules.r5_frozen(obs(), self.window(20))
        self.assertEqual(spoke.reason, "EVALUATED")
        self.assertEqual(churn_status([RuleResult("R5", spoke.verdict, spoke.reason, {})]), "OK")

        silent = rules.r5_frozen(obs(), self.window(6, spacing=600))
        self.assertEqual(silent.reason, "INSUFFICIENT_HISTORY")
        self.assertEqual(
            churn_status([RuleResult("R5", silent.verdict, silent.reason, {})]),
            "INSUFFICIENT_HISTORY",
        )

    def test_churn_status_without_an_r5_result_is_not_a_pass(self):
        """Absence is not a pass, here as everywhere."""
        self.assertEqual(churn_status([]), "INSUFFICIENT_HISTORY")
        self.assertEqual(
            churn_status([RuleResult("R2", ADMIT, "EVALUATED", {})]), "INSUFFICIENT_HISTORY"
        )

    def test_a_304_leaves_churn_unmeasured_however_long_the_history(self):
        """A publisher polled only through conditional requests has not had its
        structure re-measured. R5 reports NO_BODY and that is a cold start, not
        a clean bill."""
        carried = rules.r5_frozen(obs(structural_hash=None), self.window(20))
        self.assertEqual(carried.reason, "NO_BODY")
        self.assertEqual(
            churn_status([RuleResult("R5", carried.verdict, carried.reason, {})]),
            "INSUFFICIENT_HISTORY",
        )

    def test_content_changing_inside_the_window_is_a_measured_admit(self):
        """Distinct from NOT_APPLICABLE. History reaches back a full window and
        the hash demonstrably moved, so this is a checked negative."""
        window = [
            obs(minutes_ago=self.SPACING * (i + 1), structural_hash=f"h{i}") for i in range(20)
        ]
        verdict, _ = r5(obs(structural_hash="h-new"), window)
        self.assertEqual(verdict, ADMIT)

    def test_duplicate_observations_do_not_satisfy_the_poll_minimum(self):
        """A retried write must not inflate the count toward the minimum without
        any new observation having been made. Section 19.6."""
        sparse = self.window(6, spacing=600)
        once = r5(obs(), sparse)[1]["polls_in_window"]
        four_times = r5(obs(), sparse * 4)[1]["polls_in_window"]
        self.assertEqual(four_times, once)
        self.assertLess(four_times, rules.R5_MIN_POLLS)
        self.assertEqual(r5(obs(), sparse * 4)[0], NOT_APPLICABLE)

    def test_an_out_of_order_record_does_not_truncate_the_window(self):
        """Sorted rather than trusted: an old record arriving first would
        otherwise cut the window short at the first stale timestamp."""
        ordered = self.window(20)
        shuffled = [ordered[-1], *ordered[:-1]]
        self.assertEqual(
            r5(obs(), shuffled)[0],
            r5(obs(), ordered)[0],
        )

    def test_an_advance_across_the_window_cutoff_is_counted(self):
        """The oldest in-window poll must be compared against its predecessor
        just OUTSIDE the window.

        Without that baseline one advance is silently lost, and at exactly the
        three-advance threshold that is the difference between WATCH and
        QUARANTINE. Constructed to sit on the threshold on purpose: a window with
        five advances passes either way and proves nothing.
        """
        # 90-minute spacing puts indices 0..15 inside the 24 h window and 16..19
        # outside it. Exactly three advances, the earliest straddling the cutoff
        # between index 16 (outside) and index 15 (inside).
        marks = [NOW - datetime.timedelta(days=d) for d in (400, 300, 200, 100)]
        stamps = []
        for i in range(20):
            mark = marks[3] if i == 0 else marks[2] if i <= 8 else marks[1] if i <= 15 else marks[0]
            stamps.append(mark.isoformat())
        current = obs(update_date=stamps[0])
        outcome = rules.r5_frozen(current, self.window(20, stamps=stamps))
        self.assertEqual(outcome.detail["advances"], 3)
        self.assertEqual(outcome.verdict, QUARANTINE)

    def test_a_missing_hash_is_not_evidence_of_a_change(self):
        """An orphan 304 carries no structural hash. Read as a change it would
        clear the frozen-content signal on its own."""
        window = self.window(20)
        orphan = obs(minutes_ago=45, structural_hash=None, http_status=304, not_modified=True)
        with_orphan = rules.r5_frozen(obs(), [orphan, *window])
        without = rules.r5_frozen(obs(), window)
        self.assertEqual(with_orphan.verdict, without.verdict)
        self.assertNotEqual(with_orphan.verdict, ADMIT)

    def test_rotating_free_text_cannot_clear_the_frozen_signal(self):
        """Section 6.5: injected text can never raise a trust score. Text
        reaching a rule through a hash is still text reaching a rule."""
        window = self.window(20)
        # The structural hash is unchanged; only descriptions rotated, so
        # content_hash would differ on every poll.
        for i, observation in enumerate(window):
            observation.content_hash = f"rotated-{i}"
        outcome = rules.r5_frozen(obs(content_hash="rotated-new"), window)
        self.assertIn(outcome.verdict, (WATCH, QUARANTINE))

    def test_a_304_leaves_churn_unmeasured(self):
        carried = obs(http_status=304, not_modified=True, carried_forward=True)
        verdict, _ = r5(carried, self.window(20))
        self.assertEqual(verdict, NOT_APPLICABLE)


class TestR6Undeterminable(unittest.TestCase):
    def test_missing_and_unparseable_timestamps_watch(self):
        self.assertEqual(r6(obs(update_date=None)), WATCH)
        self.assertEqual(r6(obs(update_date="2020-01-01")), WATCH)
        self.assertEqual(r6(obs(update_date="whenever")), WATCH)

    def test_a_forward_dated_header_fires(self):
        self.assertEqual(r6(obs(update_age_seconds=-9000.0)), WATCH)

    def test_the_round_trip_is_not_a_finding(self):
        """A publisher stamps `update_date` when it starts building the response
        and we stamp the poll when it arrives, so a legitimate feed is slightly
        ahead of us. Measured live: seven of nineteen publishers came in 0.19 to
        1.7 seconds ahead against latencies of 0.8 to 0.9, and with no allowance
        every one was a WATCH and a notice naming a state DOT."""
        self.assertEqual(r6(obs(update_age_seconds=-1.7, latency_ms=900.0)), ADMIT)
        self.assertEqual(r6(obs(update_age_seconds=-4.9, latency_ms=0.0)), ADMIT)

    def test_the_allowance_cannot_hide_staleness(self):
        """The branch exists to stop a publisher evading R2 by dating its header
        forward, and evading R2 means hiding minutes at least."""
        self.assertEqual(r6(obs(update_age_seconds=-60.0, latency_ms=900.0)), WATCH)

    def test_the_finding_says_what_it_measured_against(self):
        """A notice asserting "forward-dated" without the number is an assertion
        the recipient cannot check."""
        detail = rules.r6_undeterminable(obs(update_age_seconds=-60.0, latency_ms=900.0)).detail
        self.assertEqual(detail["cause"], "forward_dated")
        self.assertEqual(detail["seconds_ahead"], 60.0)
        self.assertAlmostEqual(detail["allowance_seconds"], rules.R6_CLOCK_SKEW_SECONDS + 0.9)

    def test_suppressed_on_a_failed_poll(self):
        """A transport failure has no document. Firing here would raise WATCH on
        the first failed poll and make R1's three-poll threshold meaningless."""
        self.assertEqual(r6(failed()), NOT_APPLICABLE)

    def test_a_good_timestamp_admits(self):
        self.assertEqual(r6(obs()), ADMIT)

    def test_r2_is_not_applicable_while_r6_fires(self):
        """Section 6.4's stated case: the unusable timestamp is itself the
        finding, and treating it as merely unevaluable everywhere would make it
        disappear."""
        bad = obs(update_date="2020-01-01", update_age_seconds=None)
        self.assertEqual(r2(bad, 300), NOT_APPLICABLE)
        self.assertEqual(r6(bad), WATCH)




if __name__ == "__main__":
    unittest.main()
