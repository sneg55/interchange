"""The fleet's state surviving a restart.

Run with: python3 -m unittest discover -s tests

The live runner's whole job beyond polling is to make three things outlive the
process, and each has a specific failure that is invisible from inside one run:
a gate that forgets what it quarantined, a rule window that resets to empty, and
canonical IDs that are reminted so every consumer sees the merged feed churn.

The test for that is a restart, so this builds a fleet, runs it, throws the
object away, and builds a second one over the same store. Feeds come from the
checksummed snapshot; what is under test is the state, not the network.
"""

from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.error_ids import AppError, ErrorIds
from src.entrypoints.fleet_cycle import FleetCycle
from src.entrypoints.live_fleet import (
    CANONICAL_SOURCE_MAP,
    ZONES_UNCHANGED,
    LiveFleet,
    retained_polls,
    trim,
    zone_content_hash,
)
from src.features.reconciler.identity import CanonicalIdentity
from src.features.registry_warden.cadence import MAX_POLL_SECONDS
from src.features.trust_scorer.churn import R5_MIN_POLLS, R5_WINDOW_SECONDS
from src.services.body_snapshots import FileBodySnapshots
from src.services.fixtures import FixtureFeedSource, FixtureRegistrySource, FixtureSet
from src.services.local_store import LocalStore
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry
from src.services.screeners import KeywordScreener

# A few publishers rather than all forty. The subject here is state across a
# restart, and reconciling the whole fleet twice to prove it would make this one
# of the slowest tests in the suite for no extra coverage.
SUBSET = ("Utah DOT", "Hawaii DOT", "St. Charles County")

INTERVAL = 900

# Explicit, and a full poll ceiling apart. Two cycles at wall-clock speed are
# milliseconds apart, so adaptive backoff correctly declines to poll anyone on
# the second, and a test about two polls surviving a restart would be asserting
# against one. The ceiling is due for any clamped interval by construction.
FIRST_CYCLE = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
SECOND_CYCLE = FIRST_CYCLE + datetime.timedelta(seconds=MAX_POLL_SECONDS)


class FewPublishers:
    """RegistrySource narrowed to a handful of captured publishers."""

    def __init__(self, fixtures: FixtureSet) -> None:
        self._inner = FixtureRegistrySource(fixtures)

    def active_entries(self) -> list[dict[str, Any]]:
        return [e for e in self._inner.active_entries() if e["issuingorganization"] in SUBSET]


def build_cycle(root: Path, identity: CanonicalIdentity | None = None) -> FleetCycle:
    fixtures = FixtureSet()
    return FleetCycle(
        registry=FewPublishers(fixtures),
        feeds=FixtureFeedSource(fixtures),
        schemas=SchemaRegistry(FixtureSchemaLoader(fixtures)),
        screener=KeywordScreener(),
        identity=identity,
        # On disk, so the restart below has bodies to answer a 304 with.
        bodies=FileBodySnapshots(root / "bodies"),
    )


class TestRetention(unittest.TestCase):
    def test_retention_covers_r5s_whole_window(self):
        """Retain fewer polls than the window holds and R5 reads
        INSUFFICIENT_HISTORY on a fleet that has plenty of history."""
        for interval in (300, 900, 3600):
            with self.subTest(interval=interval):
                retained = retained_polls(interval)
                self.assertGreaterEqual(retained * interval, R5_WINDOW_SECONDS)
                self.assertGreaterEqual(retained, R5_MIN_POLLS)

    def test_trim_keeps_the_newest(self):
        history = {"a": ["newest", "middle", "oldest"]}
        self.assertEqual(trim(history, 2), {"a": ["newest", "middle"]})


class TestFleetStateSurvivesARestart(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = LocalStore(self.root / "store")
        self.addCleanup(self._tmp.cleanup)

    def test_a_second_process_resumes_the_first_ones_fleet(self):
        first = LiveFleet(self.store, build_cycle(self.root), INTERVAL)
        self.assertEqual(first.records, {}, "a fresh store knows no publishers")
        report_one, written = first.run_once(now=FIRST_CYCLE)

        self.assertEqual(len(report_one.states), len(SUBSET))
        self.assertEqual(written["publishers"], len(SUBSET))
        self.assertEqual(written["observations"], report_one.publishers_polled)
        self.assertGreater(written[CANONICAL_SOURCE_MAP], 0)
        zones_one = {doc["canonical_id"] for doc in self.store.all("canonical_zones")}
        self.assertGreater(len(zones_one), 0)

        # The restart. Nothing is carried over in memory: a new store handle, a
        # new cycle, a new runner, exactly as a redeployed job would see it.
        reopened = LocalStore(self.root / "store")
        second = LiveFleet(reopened, build_cycle(self.root), INTERVAL)

        self.assertEqual(
            {key: record.fleet_state for key, record in second.records.items()},
            report_one.states,
            "the gate must not forget what it decided",
        )
        # Keyed against the records, not iterated. Walking `history.items()`
        # alone passes vacuously when the window comes back empty, which is
        # precisely the failure this asserts against.
        self.assertEqual(set(second.history), set(second.records))
        for key, series in second.history.items():
            self.assertEqual(len(series), 1, f"{key} lost its poll history")
        self.assertGreater(len(second.identity.entries()), 0)

        report_two, _ = second.run_once(now=SECOND_CYCLE)
        zones_two = {doc["canonical_id"] for doc in reopened.all("canonical_zones")}
        self.assertEqual(
            zones_one,
            zones_two,
            "canonical ids were reminted across a restart, so every consumer sees churn",
        )
        # Two polls per publisher retained, not one overwritten and not one lost.
        for key in report_two.states:
            self.assertEqual(len(reopened.recent("observations", key, 10)), 2)

    def test_a_publisher_record_that_cannot_be_rebuilt_stops_the_run(self):
        """Found by running it: a malformed document took the runner down with a
        bare TypeError naming nothing. Skipping it would be worse, because a
        publisher would drop out of the fleet, stop being polled, and vanish from
        the board with nothing anywhere saying so."""
        self.store.put("publishers", "Utah DOT|udot", {"publisher_key": "Utah DOT|udot"})
        with self.assertRaises(AppError) as caught:
            LiveFleet(self.store, build_cycle(self.root), INTERVAL)
        self.assertEqual(caught.exception.id, ErrorIds.STORE_BAD_RECORD)
        self.assertEqual(caught.exception.context["doc_id"], "Utah DOT|udot")

    def test_latching_and_streaks_come_back(self):
        """The fields that make the gate hysteretic. A record that round-trips
        its state but loses `latching_rule_ids` reads as ADMIT-eligible on the
        next poll, which is a quarantine that quietly expires on redeploy."""
        first = LiveFleet(self.store, build_cycle(self.root), INTERVAL)
        first.run_once()
        before = {key: record.to_doc() for key, record in first.records.items()}

        second = LiveFleet(LocalStore(self.root / "store"), build_cycle(self.root), INTERVAL)
        for key, record in second.records.items():
            after = record.to_doc()
            for field in (
                "fleet_state",
                "latching_rule_ids",
                "clean_poll_streak",
                "clean_streak_started_at",
                "churn_status",
                "last_polled_at",
            ):
                self.assertEqual(after[field], before[key][field], f"{key}.{field}")


class TestZoneWriteThrottle(unittest.TestCase):
    """Canonical zones are the fleet's dominant write and most of them do not
    change between cycles. Skipping the unchanged ones is only safe if it is
    exact, so the two failures asserted here are a zone that changed and was not
    rewritten, and a zone that was skipped without anyone being told."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = LocalStore(self.root / "store")
        self.addCleanup(self._tmp.cleanup)

    def test_bookkeeping_alone_does_not_count_as_a_change(self):
        """`last_seen_cycle` and `ingested_at` carry this cycle's clock, so
        hashing the document whole would make every zone differ every cycle and
        the throttle would save nothing while looking like it worked."""
        zone = {
            "canonical_id": "abc",
            "core_details": {"road_names": ["I-15"]},
            "last_seen_cycle": "cycle-1",
            "sources": [{"publisher_key": "Utah DOT|udot", "ingested_at": "2026-08-14T00:00:00Z"}],
        }
        later = {
            **zone,
            "last_seen_cycle": "cycle-2",
            "sources": [{"publisher_key": "Utah DOT|udot", "ingested_at": "2026-08-14T00:15:00Z"}],
        }
        self.assertEqual(zone_content_hash(zone), zone_content_hash(later))

    def test_a_real_change_is_still_a_change(self):
        zone = {"canonical_id": "abc", "core_details": {"road_names": ["I-15"]}}
        moved = {"canonical_id": "abc", "core_details": {"road_names": ["I-80"]}}
        self.assertNotEqual(zone_content_hash(zone), zone_content_hash(moved))

    def test_the_second_cycle_rewrites_only_what_changed_and_says_so(self):
        fleet = LiveFleet(self.store, build_cycle(self.root), INTERVAL)
        _, first = fleet.run_once(now=FIRST_CYCLE)
        self.assertGreater(first["canonical_zones"], 0)
        self.assertEqual(first[ZONES_UNCHANGED], 0, "nothing is unchanged on an empty store")

        # Same fixtures, so the same zones. Anything rewritten here is either a
        # genuine change or the throttle failing. A cycle a full ceiling later,
        # so every publisher is genuinely repolled: a not-due cycle would skip
        # the polls and the throttle would look perfect for the wrong reason.
        _, second = fleet.run_once(now=SECOND_CYCLE)
        self.assertGreater(second[ZONES_UNCHANGED], 0)
        self.assertEqual(
            second["canonical_zones"] + second[ZONES_UNCHANGED],
            first["canonical_zones"],
            "every zone must be either rewritten or accounted for as unchanged",
        )

    def test_a_restart_rewrites_every_zone(self):
        """The hashes are in memory. A process that has not written a zone must
        not assume the stored one is current, so the safe direction is to write."""
        LiveFleet(self.store, build_cycle(self.root), INTERVAL).run_once(now=FIRST_CYCLE)
        reopened = LiveFleet(LocalStore(self.root / "store"), build_cycle(self.root), INTERVAL)
        _, written = reopened.run_once(now=SECOND_CYCLE)
        self.assertEqual(written[ZONES_UNCHANGED], 0)
        self.assertGreater(written["canonical_zones"], 0)


if __name__ == "__main__":
    unittest.main()
