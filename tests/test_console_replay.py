"""PublisherDaily and historical replay. Section 6.9.

Run with: python3 -m unittest discover -s tests -v

Replay is deliberately scoped to what is reconstructible from append-only
records. A replay of last month showing today's fleet with last month's trust
states attached is a more convincing lie than showing nothing, which is why
membership comes from RegistryEvent rather than from the mutable
PublisherRecord.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.console_api.replay import horizon, membership_at, snapshot_at, states_at
from src.features.registry_warden.records import RegistryEvent
from src.features.trust_scorer.rollup import percentile, roll_up, roll_up_all
from src.features.trust_scorer.verdicts import ADMIT, QUARANTINE, WATCH
from tests.support import CONSOLE_AT as AT
from tests.support import observation as obs
from tests.support import trust_transition as transition


class TestRollup(unittest.TestCase):
    def test_percentiles_never_invent_a_value(self):
        """Nearest-rank on the sample. An interpolated p95 is a latency no poll
        actually produced."""
        sample = [10.0, 20.0, 30.0, 40.0]
        self.assertIn(percentile(sample, 0.5), sample)
        self.assertIn(percentile(sample, 0.95), sample)

    def test_an_empty_sample_is_none_not_zero(self):
        """Zero would render the publisher as the fastest in the fleet on
        exactly the day it was unreachable."""
        self.assertIsNone(percentile([], 0.5))
        daily = roll_up("A|a", "2026-08-01", [obs(0, http_status=0, error="down")])
        self.assertIsNone(daily.latency_p50_ms)
        self.assertEqual(daily.failure_count, 1)

    def test_a_frozen_publisher_reports_no_content_change(self):
        daily = roll_up("A|a", "2026-08-01", [obs(i) for i in range(20)])
        self.assertEqual(daily.content_hash_changes, 0)

    def test_a_day_boundary_is_not_a_content_change(self):
        """Without the carry-in, a frozen publisher would report one change per
        day forever, which is exactly the signal R5 exists to say is absent."""
        dailies = roll_up_all([obs(i, day=1) for i in range(5)] + [obs(i, day=2) for i in range(5)])
        self.assertEqual([d.content_hash_changes for d in dailies], [0, 0])

    def test_a_real_change_is_counted(self):
        polls = [obs(0), obs(1, content_hash="different"), obs(2, content_hash="different")]
        self.assertEqual(roll_up("A|a", "2026-08-01", polls).content_hash_changes, 1)

    def test_a_304_never_contributes_a_schema_error_count(self):
        """Summing over carried-forward polls would report a document nobody
        fetched as validating."""
        carried = obs(1, http_status=304, not_modified=True, schema_error_count=None)
        daily = roll_up("A|a", "2026-08-01", [carried])
        self.assertIsNone(daily.schema_error_count)
        self.assertEqual(daily.not_modified_count, 1)

    def test_a_clean_validation_is_zero_not_none(self):
        daily = roll_up("A|a", "2026-08-01", [obs(0), obs(1)])
        self.assertEqual(daily.schema_error_count, 0)


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.events = [
            RegistryEvent("A|a", "2026-01-01T00:00:00Z", "PROVISIONED"),
            RegistryEvent("B|b", "2026-03-01T00:00:00Z", "PROVISIONED"),
            RegistryEvent("A|a", "2026-06-01T00:00:00Z", "DECOMMISSIONED"),
        ]
        self.transitions = [
            transition("A|a", "2026-02-01T00:00:00Z", QUARANTINE),
            transition("B|b", "2026-04-01T00:00:00Z", ADMIT),
            transition("B|b", "2026-07-01T00:00:00Z", QUARANTINE),
        ]

    def test_membership_is_replayed_not_read_off_the_mutable_record(self):
        """A replay of last month showing today's fleet with last month's trust
        states attached is a more convincing lie than showing nothing."""
        self.assertEqual(membership_at(self.events, "2026-02-01T00:00:00Z"), {"A|a"})
        self.assertEqual(membership_at(self.events, "2026-04-01T00:00:00Z"), {"A|a", "B|b"})
        self.assertEqual(membership_at(self.events, "2026-08-01T00:00:00Z"), {"B|b"})

    def test_state_is_the_last_transition_at_or_before_the_instant(self):
        self.assertEqual(states_at(self.transitions, "2026-05-01T00:00:00Z")["B|b"], ADMIT)
        self.assertEqual(states_at(self.transitions, "2026-08-01T00:00:00Z")["B|b"], QUARANTINE)

    def test_a_publisher_with_no_transition_yet_is_not_admitted(self):
        """Defaulting to ADMIT would record 'not checked' as 'passed' at replay
        time, which is the same error the scorer refuses at evaluation time."""
        self.assertNotIn("B|b", states_at(self.transitions, "2026-03-15T00:00:00Z"))
        board = snapshot_at(self.transitions, self.events, "2026-03-15T00:00:00Z")
        self.assertEqual(board.states["B|b"], WATCH)

    def test_a_snapshot_only_contains_publishers_that_existed_then(self):
        board = snapshot_at(self.transitions, self.events, "2026-02-15T00:00:00Z")
        self.assertEqual(board.members, {"A|a"})
        self.assertEqual(board.states["A|a"], QUARANTINE)

    def test_no_access_is_excluded_from_the_coverage_denominator(self):
        events = [RegistryEvent("C|c", "2026-01-01T00:00:00Z", "PROVISIONED")]
        board = snapshot_at([transition("C|c", "2026-01-02T00:00:00Z", "NO_ACCESS")], events, AT)
        self.assertEqual(board.coverage_denominator, 0)
        self.assertEqual(len(board.members), 1)

    def test_the_horizon_states_where_history_begins(self):
        """The scrubber says so at its left edge rather than showing an empty
        chart."""
        empty = horizon([], [], [])
        self.assertIsNone(empty.earliest)
        self.assertIn("No retained history", empty.note)
        real = horizon([], self.transitions, self.events)
        self.assertEqual(real.earliest, "2026-01-01T00:00:00Z")
        self.assertIn("90 days", real.note)


class TestRoundFiveRegressions(unittest.TestCase):
    def test_percentile_is_nearest_rank_at_the_boundaries(self):
        """round() is banker's rounding, which put p50 of a two-sample set on
        the SECOND value and p95 of a hundred on the 96th. Asserted at the
        boundaries, because a value from the middle of the sample passes under
        either definition."""
        self.assertEqual(percentile([10.0, 20.0], 0.5), 10.0)
        hundred = [float(i) for i in range(1, 101)]
        self.assertEqual(percentile(hundred, 0.95), 95.0)
        self.assertEqual(percentile(hundred, 0.5), 50.0)
        self.assertEqual(percentile([5.0], 0.95), 5.0)

    def test_a_partially_validated_day_is_not_reported_as_clean(self):
        """Three validated clean polls beside two hundred never checked must not
        sum to a confident zero. That is "not checked" recorded as "checked and
        passed" at the rollup layer."""
        polls = [obs(0), obs(1, schema_error_count=None, schema_version_used="SCHEMA_UNKNOWN")]
        self.assertIsNone(roll_up("A|a", "2026-08-01", polls).schema_error_count)

    def test_a_fully_validated_day_still_reports_zero(self):
        self.assertEqual(roll_up("A|a", "2026-08-01", [obs(0), obs(1)]).schema_error_count, 0)


class TestAccessReplay(unittest.TestCase):
    """NO_ACCESS never produces a TrustTransition, so replay has to read it from
    the registry events or it reports a trust verdict about a publisher that was
    never polled."""

    def test_a_key_gated_publisher_replays_as_no_access_not_watch(self):
        events = [
            RegistryEvent("G|g", "2026-01-01T00:00:00Z", "PROVISIONED"),
            RegistryEvent("G|g", "2026-01-01T00:00:00Z", "ACCESS_LOST", None, "NO_ACCESS"),
        ]
        board = snapshot_at([], events, AT)
        self.assertEqual(board.states["G|g"], "NO_ACCESS")
        self.assertEqual(board.coverage_denominator, 0)

    def test_regaining_access_returns_it_to_the_trust_axis(self):
        events = [
            RegistryEvent("G|g", "2026-01-01T00:00:00Z", "PROVISIONED"),
            RegistryEvent("G|g", "2026-01-01T00:00:00Z", "ACCESS_LOST", None, "NO_ACCESS"),
            RegistryEvent("G|g", "2026-03-01T00:00:00Z", "ACCESS_GAINED", "NO_ACCESS", "WATCH"),
        ]
        before = snapshot_at([], events, "2026-02-01T00:00:00Z")
        after = snapshot_at([], events, "2026-04-01T00:00:00Z")
        self.assertEqual(before.states["G|g"], "NO_ACCESS")
        self.assertEqual(after.states["G|g"], "WATCH")
        self.assertEqual(after.coverage_denominator, 1)

    def test_the_warden_emits_the_access_events_replay_needs(self):
        """The production path, not a fabricated transition. A test that made up
        a NO_ACCESS transition would pass against a warden that emits nothing."""
        from src.features.registry_warden.warden import RegistryWarden
        from src.services.fixtures import FixtureRegistrySource

        warden = RegistryWarden(FixtureRegistrySource())
        entry = {
            "issuingorganization": "G",
            "feedname": "g",
            "url": {"url": "https://example.test/f.json"},
            "version": "4.2",
            "datafeed_frequency_update": "5m",
            "active": True,
            "needapikey": True,
        }
        result = warden.reconcile([entry], {}, AT)
        self.assertIn("ACCESS_LOST", [e.event for e in result.events])
        opened = dict(entry)
        del opened["needapikey"]
        later = warden.reconcile([opened], result.records, "2026-09-01T00:00:00Z")
        self.assertIn("ACCESS_GAINED", [e.event for e in later.events])


if __name__ == "__main__":
    unittest.main()
