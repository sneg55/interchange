"""What the evaluation record carries, and the two ways out of a latch. Section 6.4.

Run with: python3 -m unittest discover -s tests -v

Split from `test_trust_scorer.py`, which keeps latching, escalation and
hysteresis. The seam is what is being asserted: everything here is about the
record a decision leaves behind, and about compliance being a route out rather
than a trap.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.trust_scorer import rules
from src.features.trust_scorer.churn import churn_detail
from src.features.trust_scorer.scorer import TrustScorer
from src.features.trust_scorer.verdicts import (
    QUARANTINE,
    QUARANTINE_TO_WATCH_CLEAN_POLLS,
    WATCH,
)
from tests.scorer_support import (
    CADENCE,
    START,
    clean_obs,
    complied,
    contradictory,
    not_modified,
    run_polls,
)


class TestEvaluationRecord(unittest.TestCase):
    def test_records_every_rule_including_the_ones_that_did_not_run(self):
        """'We did not check' has to be visible in the record, or the packet
        cannot distinguish it from 'we checked and it passed'."""
        result = TrustScorer().score(contradictory(), [], WATCH, CADENCE, START)
        evaluation = result.evaluation
        self.assertEqual(
            [r.rule_id for r in evaluation.results],
            ["R1", "R2", "R3", "R4", "R5", "R6"],
            "fixed order is what makes an evidence packet reproducible",
        )
        self.assertNotIn("R5", evaluation.evaluated_rule_ids)  # no history yet
        # Against the constant, not a literal. Pinning "v1" here made the test
        # fail the moment the ruleset was correctly bumped, which reads as the
        # bump being the mistake rather than the fix.
        self.assertEqual(evaluation.ruleset_version, rules.RULESET_VERSION)
        self.assertIn("fired_rules", evaluation.to_doc())

    def test_the_ruleset_version_is_stamped_on_every_transition(self):
        """A transition carrying no ruleset version cannot be read later against
        the rules that produced it, which is the whole point of recording one."""
        result, _ = run_polls(6, WATCH, ["R4"], minutes=60, factory=contradictory)
        self.assertEqual(result.evaluation.ruleset_version, rules.RULESET_VERSION)


class TestWhatTheRecordCanSay(unittest.TestCase):
    """Two figures the scorer had on every poll and nobody wrote down.

    Both showed up on the console as a word where a measurement belonged: a
    column headed Churn whose only value was "measured", and an evidence packet
    that cited one poll for an assertion about a run of them.
    """

    def test_churn_detail_carries_what_r5_actually_measured(self):
        def frozen(i):
            o = clean_obs(i, minutes=120)
            o.structural_hash = "frozen"
            return o

        # Two-hourly over 40 hours, so the retained run reaches back past R5's
        # 24h window AND puts at least R5_MIN_POLLS inside it. Falling short of
        # either is INSUFFICIENT_HISTORY, which is a different answer and a
        # correct one.
        history = [frozen(i) for i in range(19, 0, -1)]
        result = TrustScorer().score(frozen(20), history, WATCH, CADENCE, START)
        detail = churn_detail(result.evaluation.results)
        self.assertIsNotNone(detail)
        self.assertGreater(detail["polls_in_window"], 1)
        self.assertIn("advances", detail)

    def test_churn_detail_is_none_when_r5_could_not_measure(self):
        """None, not a dict of zeros. A publisher R5 could not evaluate has not
        been measured as having zero churn, and four zeros read as the second."""
        result = TrustScorer().score(clean_obs(), [], WATCH, CADENCE, START)
        self.assertIsNone(churn_detail(result.evaluation.results))

    def test_evidence_depth_covers_the_polls_a_fired_rule_rested_on(self):
        """Every packet embedded the single poll that tripped the transition.

        So every notice asserted behaviour "across consecutive polls" over a
        window whose start equalled its end, while the publisher's own page
        listed the run of failures that would have supported it.
        """
        failures = [
            Observation(
                publisher_key="p|f",
                polled_at=(START + datetime.timedelta(minutes=30 * i)).isoformat(),
                http_status=0,
                error="Injected",
                error_origin="PUBLISHER",
            )
            for i in range(6)
        ]
        result = TrustScorer().score(failures[-1], failures[:-1][::-1], WATCH, CADENCE, START)
        self.assertGreaterEqual(result.evaluation.evidence_depth, 3)

    def test_evidence_depth_is_one_when_one_poll_is_the_whole_case(self):
        """R4 reads a single body. Widening the window there would cite polls
        the rule never looked at, which is the same overstatement in reverse."""
        result = TrustScorer().score(contradictory(), [], WATCH, CADENCE, START)
        self.assertEqual(result.evaluation.evidence_depth, 1)


class TestRecoveryByCompliance(unittest.TestCase):
    """Complying with a finding must be a way out, not a trap.

    Round two found that a publisher quarantined by R4 which then moved every
    offending zone out of `active` produced zero active zones, hence
    NOT_APPLICABLE, hence no clean poll, hence no route back. It had done exactly
    what the finding asked and was punished for it. Section 6.4's clean poll
    requires the latching rules to have been evaluated WITH A BODY, not to have
    returned a verdict.
    """

    def test_a_publisher_that_fixes_an_r4_quarantine_can_recover(self):
        result, state = run_polls(
            QUARANTINE_TO_WATCH_CLEAN_POLLS, QUARANTINE, ["R4"], minutes=60, factory=complied
        )
        self.assertEqual(state, WATCH)
        self.assertEqual(result.latching_rule_ids, [])

    def test_but_bodyless_polls_still_cannot(self):
        _, state = run_polls(40, QUARANTINE, ["R4"], minutes=60, factory=not_modified)
        self.assertEqual(state, QUARANTINE)

    def test_polls_missing_a_rules_input_cannot_retire_its_quarantine(self):
        """An older agent build omitting active_undated makes R4 MISSING_INPUT
        over a real body. A body-only cleanliness test accepted those and walked
        the publisher out of quarantine without R4 ever being re-measured."""

        def stale_build(index=0, minutes=60):
            o = clean_obs(index, minutes)
            o.active_undated = None
            o.active_count = 100
            o.active_with_past_end_date = 5
            return o

        _, state = run_polls(40, QUARANTINE, ["R4"], minutes=60, factory=stale_build)
        self.assertEqual(state, QUARANTINE)


if __name__ == "__main__":
    unittest.main()
