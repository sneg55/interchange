"""Latching, escalation and hysteresis. Section 6.4.

Run with: python3 -m unittest discover -s tests -v

The deadlock test is the one that matters most. Round three of spec review found
that a publisher quarantined by a body-dependent rule, correctly answering 304
forever because its content genuinely had not changed, could never accumulate a
clean poll no matter how long it behaved. Two mechanisms resolve it and both are
asserted here: a bodyless poll is never clean while a body-dependent rule
latches, and conditional GET is suspended in that state so a body arrives.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.publisher_agent.scheduler import send_conditional
from src.features.trust_scorer.scorer import TrustScorer
from src.features.trust_scorer.verdicts import (
    ADMIT,
    QUARANTINE,
    QUARANTINE_TO_WATCH_CLEAN_POLLS,
    WATCH,
    WATCH_TO_ADMIT_CLEAN_POLLS,
)
from tests.scorer_support import (
    CADENCE,
    START,
    clean_obs,
    contradictory,
    not_modified,
    run_polls,
)


class TestEscalation(unittest.TestCase):
    def setUp(self):
        self.scorer = TrustScorer()

    def test_escalation_is_immediate(self):
        """A feed that has just gone dark should not keep serving traffic for
        six hours while a counter fills."""
        result = self.scorer.score(contradictory(), [], WATCH, CADENCE, START)
        self.assertEqual(result.state, QUARANTINE)
        self.assertIsNotNone(result.transition)
        self.assertEqual(result.transition.direction, "ESCALATION")
        self.assertIn("R4", result.latching_rule_ids)

    def test_the_transition_records_every_firing_rule_most_severe_first(self):
        o = contradictory()
        o.schema_error_count = 12  # R3 fires WATCH alongside R4's QUARANTINE
        result = self.scorer.score(o, [], WATCH, CADENCE, START)
        self.assertEqual(result.transition.primary_rule_id, "R4")
        self.assertIn("R3", result.transition.rule_ids)
        self.assertEqual(result.transition.rule_ids[0], "R4")

    def test_no_transition_when_the_state_does_not_move(self):
        result = self.scorer.score(clean_obs(), [], WATCH, CADENCE, START)
        self.assertEqual(result.state, WATCH)
        self.assertIsNone(result.transition)


class TestHysteresis(unittest.TestCase):
    def setUp(self):
        self.scorer = TrustScorer()

    def test_watch_to_admit_needs_six_clean_polls(self):
        _, state = run_polls(WATCH_TO_ADMIT_CLEAN_POLLS - 1, WATCH, [])
        self.assertEqual(state, WATCH)
        _, state = run_polls(WATCH_TO_ADMIT_CLEAN_POLLS, WATCH, [])
        self.assertEqual(state, ADMIT)

    def test_quarantine_to_watch_needs_twelve_clean_polls_and_six_hours(self):
        # Twelve polls five minutes apart span under an hour. An hour of good
        # behaviour must not retire a quarantine.
        _, state = run_polls(QUARANTINE_TO_WATCH_CLEAN_POLLS, QUARANTINE, ["R1"], minutes=5)
        self.assertEqual(state, QUARANTINE)
        _, state = run_polls(QUARANTINE_TO_WATCH_CLEAN_POLLS, QUARANTINE, ["R1"], minutes=60)
        self.assertEqual(state, WATCH)

    def test_a_non_clean_poll_resets_the_counter(self):
        """Indiana DOT and Minnesota DOT were both observed flapping inside one
        afternoon. Without a reset they would oscillate."""
        streak, started, state, latching = 0, None, WATCH, []
        history = []
        for i in range(20):
            observation = clean_obs(i) if i != 10 else contradictory(i)
            result = self.scorer.score(
                observation,
                list(history),
                state,
                CADENCE,
                START,
                latching_rule_ids=latching,
                clean_streak=streak,
                clean_streak_started_at=started,
            )
            state, latching = result.state, result.latching_rule_ids
            streak, started = result.clean_streak, result.streak_started_at
            history.insert(0, observation)
            if i == 10:
                self.assertEqual(result.clean_streak, 0)
                self.assertEqual(state, QUARANTINE)

    def test_recovery_clears_the_latch_so_the_next_step_is_earned_from_zero(self):
        result, state = run_polls(QUARANTINE_TO_WATCH_CLEAN_POLLS, QUARANTINE, ["R1"], minutes=60)
        self.assertEqual(state, WATCH)
        self.assertEqual(result.latching_rule_ids, [])
        self.assertEqual(result.clean_streak, 0)


class TestTheDeadlock(unittest.TestCase):
    """A quarantined publisher answering 304 forever must not recover for free,
    and must not be unable to recover at all."""

    def setUp(self):
        self.scorer = TrustScorer()

    def test_bodyless_polls_never_retire_a_body_dependent_quarantine(self):
        state, latching, streak, started = QUARANTINE, ["R4"], 0, None
        history = []
        for i in range(40):
            observation = not_modified(i, minutes=60)
            result = self.scorer.score(
                observation,
                list(history),
                state,
                CADENCE,
                START,
                latching_rule_ids=latching,
                clean_streak=streak,
                clean_streak_started_at=started,
            )
            state, latching = result.state, result.latching_rule_ids
            streak, started = result.clean_streak, result.streak_started_at
            history.insert(0, observation)
        self.assertEqual(state, QUARANTINE, "40 bodyless polls must not clear R4")
        self.assertEqual(streak, 0)

    def test_conditional_get_is_suspended_so_a_body_can_arrive(self):
        """The other half of the resolution. Without it the publisher is stuck
        for a reason unrelated to its behaviour."""
        self.assertFalse(send_conditional(["R4"]))
        self.assertTrue(send_conditional(["R1"]))

    def test_and_then_real_bodies_do_retire_it(self):
        _, state = run_polls(QUARANTINE_TO_WATCH_CLEAN_POLLS, QUARANTINE, ["R4"], minutes=60)
        self.assertEqual(state, WATCH)

    def test_a_failed_poll_is_never_clean(self):
        dark = Observation(
            publisher_key="p|f", polled_at=START.isoformat(), http_status=0, error="Injected"
        )
        result = self.scorer.score(dark, [], WATCH, CADENCE, START, clean_streak=5)
        self.assertEqual(result.clean_streak, 0)
        self.assertFalse(result.evaluation.clean)


class TestLatching(unittest.TestCase):
    """Round two found that only an escalation updated the latch."""

    def setUp(self):
        self.scorer = TrustScorer()

    def test_a_rule_firing_at_the_current_severity_latches(self):
        """A publisher already at WATCH whose R3 starts firing is held there BY
        R3. With an empty latch, bodyless polls make R3 not-applicable, count as
        clean, and walk the publisher to ADMIT with nobody re-validating it."""
        o = clean_obs()
        o.schema_error_count = 7
        result = self.scorer.score(o, [], WATCH, CADENCE, START)
        self.assertEqual(result.state, WATCH)
        self.assertIsNone(result.transition, "no state change, so no transition")
        self.assertIn("R3", result.latching_rule_ids)

    def test_bodyless_polls_cannot_walk_a_watch_latched_publisher_to_admit(self):
        state, latching, streak, started = WATCH, [], 0, None
        history = []
        dirty = clean_obs(0)
        dirty.schema_error_count = 7
        for i in range(30):
            observation = dirty if i == 0 else not_modified(i, minutes=60)
            result = self.scorer.score(
                observation,
                list(history),
                state,
                CADENCE,
                START,
                latching_rule_ids=latching,
                clean_streak=streak,
                clean_streak_started_at=started,
            )
            state, latching = result.state, result.latching_rule_ids
            streak, started = result.clean_streak, result.streak_started_at
            history.insert(0, observation)
        self.assertEqual(state, WATCH, "R3 latched it; only a body may clear it")
        self.assertIn("R3", latching)

    def test_a_latched_rule_that_becomes_unevaluable_stays_latched(self):
        """Otherwise a run of bodyless polls quietly drops it and the state
        decays on evidence nobody gathered."""
        carried = not_modified(1, minutes=60)
        result = self.scorer.score(
            carried, [], QUARANTINE, CADENCE, START, latching_rule_ids=["R4"]
        )
        self.assertIn("R4", result.latching_rule_ids)


class TestRoundThreeRegressions(unittest.TestCase):
    def test_an_admit_publisher_holds_no_latches(self):
        """Nothing holds a publisher at ADMIT. Latching every passing rule there
        would put a body-dependent rule in the set for the whole healthy fleet
        and suspend conditional GET across it."""
        result, state = run_polls(WATCH_TO_ADMIT_CLEAN_POLLS + 5, WATCH, [])
        self.assertEqual(state, ADMIT)
        self.assertEqual(result.latching_rule_ids, [])
        self.assertTrue(send_conditional(result.latching_rule_ids))


if __name__ == "__main__":
    unittest.main()
