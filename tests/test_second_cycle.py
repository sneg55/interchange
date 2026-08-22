"""A fleet answering 304 must publish the same feed it published before.

Run with: python3 -m unittest discover -s tests -v

The defect this exists for: a publisher answering `304 Not Modified` carried its
observation forward but not its body, so its zones left the merge. One cycle
looked correct. From the second onward, a fleet of publishers doing exactly the
right thing merged almost nothing, and the console's reconciliation screen was
reporting that as an absence of duplicates rather than as an absence of input.

Nothing caught it because every composition test ran ONE cycle, or ran a second
cycle and asserted only that canonical ids survived it. Ids do survive an empty
merge: there is nothing to give a new id to.

Two publishers rather than twenty-five, and deliberately: New York DOT and NJIT
overlap, so this proves the property on a merge that actually happens, and it
runs in seconds instead of eight minutes.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.fleet_cycle import FleetCycle
from src.features.registry_warden.cadence import MAX_POLL_SECONDS
from src.services.fixtures import FixtureFeedSource, FixtureSet
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry
from src.services.screeners import KeywordScreener
from src.utils.timestamps import iso

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
OVERLAPPING = ("New York DOT", "New Jersey Institute of Technology")


class SubsetRegistry:
    """The registry narrowed to a few organizations.

    A RegistrySource rather than a filter applied afterwards, because the fleet
    is derived from the registry: narrowing anywhere else would leave the runner
    polling publishers this test does not care about.
    """

    def __init__(self, fixtures: FixtureSet, orgs: tuple[str, ...]) -> None:
        self._fixtures = fixtures
        self._orgs = set(orgs)

    def active_entries(self):
        entries = [
            e
            for e in self._fixtures.registry()
            if e.get("active") and e["issuingorganization"] in self._orgs
        ]
        if len(entries) != len(self._orgs):
            raise AssertionError(f"snapshot is missing one of {sorted(self._orgs)}")
        return entries


class TestASecondCycleStillMerges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = FixtureSet()
        cls.cycle = FleetCycle(
            registry=SubsetRegistry(fixtures, OVERLAPPING),
            feeds=FixtureFeedSource(fixtures),
            schemas=SchemaRegistry(FixtureSchemaLoader(fixtures)),
            screener=KeywordScreener(),
        )
        cls.first, records, history = cls.cycle.run(known=None, now=NOW)
        cls.first_merged = cls.cycle.snapshot.merged_zone_count
        # Set to the value the cycle must overwrite. `INSUFFICIENT_HISTORY` is
        # also the dataclass default, so asserting it on a record the cycle
        # never touched passes whether the field is written or not: that is
        # precisely how a field nobody wrote survived to production and rendered
        # in the console as a measurement.
        for record in records.values():
            record.churn_status = "OK"
        # A full ceiling later, not five minutes. Adaptive backoff decides
        # whether a publisher is polled at all, so a step shorter than the
        # publisher's own interval produces a cycle that skipped it: no second
        # poll, no 304, and this file's whole subject unexercised. The ceiling is
        # due for any clamped interval by construction.
        cls.second, cls.records, cls.history = cls.cycle.run(
            known=records, history=history, now=NOW + datetime.timedelta(seconds=MAX_POLL_SECONDS)
        )
        cls.second_merged = cls.cycle.snapshot.merged_zone_count

    def test_the_second_cycle_really_did_get_304s(self):
        """Without this the rest of the file could pass by never exercising the
        path at all: two body polls also produce two identical merges."""
        latest = [obs[0] for obs in self.history.values()]
        self.assertTrue(latest, "no observations retained")
        self.assertTrue(all(o.not_modified for o in latest), [o.http_status for o in latest])
        self.assertTrue(all(o.carried_forward for o in latest))

    def test_the_first_cycle_merges_something(self):
        """The control. A property about preserving merges proves nothing if the
        first cycle merged nothing either."""
        self.assertGreater(self.first_merged, 0)

    def test_a_304_does_not_withdraw_a_publishers_zones(self):
        self.assertEqual(self.second.source_zones, self.first.source_zones)
        self.assertEqual(self.second.canonical_zones, self.first.canonical_zones)

    def test_the_merge_survives_the_second_cycle(self):
        """The count that went to zero: zones with more than one source."""
        self.assertEqual(self.second_merged, self.first_merged)

    def test_the_second_cycle_still_publishes(self):
        self.assertTrue(self.second.published, self.second.validation)

    def test_churn_status_is_written_by_the_cycle(self):
        """Not merely equal to the default. Both publishers are two polls into
        a 24 hour window, so R5 cannot speak and the honest answer is
        INSUFFICIENT_HISTORY; the point of the test is that the cycle PUT it
        there, having been handed records that claimed OK."""
        self.assertTrue(self.records)
        for key, record in self.records.items():
            self.assertEqual(record.churn_status, "INSUFFICIENT_HISTORY", key)


class TestEachPollIsStampedWhenItHappens(unittest.TestCase):
    """A cycle is not an instant, and pretending it is accuses publishers.

    The live fleet walks 25 publishers over about nine minutes and every poll was
    stamped with the cycle's START time. `update_age_seconds` is measured against
    that stamp, so a publisher reached late in the polling order had its feed
    compared to a moment already past, and a feed regenerated in between came out
    with a NEGATIVE age. R6 reads that as `forward_dated`.

    In production this pushed 15 of 25 real organizations to WATCH and opened an
    evidence packet, for a clock error that was ours. The skew ran from 7 seconds
    to 195 in polling order, and 310 observations carried 13 distinct timestamps,
    one per cycle, which is what proved it.
    """

    @classmethod
    def setUpClass(cls):
        fixtures = FixtureSet()
        cls.cycle = FleetCycle(
            registry=SubsetRegistry(fixtures, OVERLAPPING),
            feeds=FixtureFeedSource(fixtures),
            schemas=SchemaRegistry(FixtureSchemaLoader(fixtures)),
            screener=KeywordScreener(),
        )
        # No `now`. This is the live shape, and the only one with the defect.
        cls.report, cls.records, cls.history = cls.cycle.run(known=None)
        # Captured HERE. `FleetCycle.evaluations` is cleared and rebuilt by every
        # run, so reading it from a test that runs after a sibling re-ran the
        # same object gets that sibling's cycle. Which is how this file first
        # failed: the pinned run below sets `now` earlier than the fixtures' own
        # update_date, where a negative age is correct rather than a defect.
        cls.evaluations = [e.to_doc() for e in cls.cycle.evaluations]

    def test_two_publishers_polled_in_one_cycle_carry_different_stamps(self):
        stamps = [series[0].polled_at for series in self.history.values()]
        self.assertEqual(len(stamps), len(OVERLAPPING), "both publishers polled")
        self.assertEqual(
            len(set(stamps)),
            len(stamps),
            f"every poll in the cycle shares one timestamp: {stamps}",
        )

    def test_no_feed_is_reported_as_dated_in_the_future(self):
        """The symptom, asserted at the rule rather than the timestamp.

        A guard rather than a reproduction, and the difference matters: the
        fixtures are a week-old snapshot, so a cycle taking a few seconds cannot
        drive their age negative and this passes under the old behaviour too.
        The test above is the one that fails without the fix. This one exists so
        that any future change which lets a negative age reach a rule is caught
        by an assertion about the rule, not only about the clock."""
        self.assertTrue(self.evaluations, "no evaluations to check")
        for evaluation in self.evaluations:
            for result in evaluation["results"]:
                detail = result.get("detail") or {}
                age = detail.get("update_age_seconds")
                if age is not None:
                    self.assertGreaterEqual(
                        age, 0, f"{evaluation['publisher_key']} {result['rule_id']} {detail}"
                    )

    def test_a_pinned_caller_still_gets_one_instant(self):
        """The seed, the tests and replay pass `now` deliberately. Every poll in
        such a cycle shares it, which is what makes a seed reproducible."""
        _, _, history = self.cycle.run(known=None, now=NOW)
        stamps = {series[0].polled_at for series in history.values()}
        self.assertEqual(stamps, {iso(NOW)})


class TestBackoffDoesNotWithdrawZones(unittest.TestCase):
    """The same failure as the 304 collapse, reached by a different door.

    Adaptive backoff decides not to poll a publisher. If its zones left the merge
    on the cycles it was not polled, an ingress optimisation would be silently
    withdrawing healthy publishers from the republished feed, and the feed would
    oscillate between two sizes on a cadence nobody could see from outside.
    """

    @classmethod
    def setUpClass(cls):
        fixtures = FixtureSet()
        cls.cycle = FleetCycle(
            registry=SubsetRegistry(fixtures, OVERLAPPING),
            feeds=FixtureFeedSource(fixtures),
            schemas=SchemaRegistry(FixtureSchemaLoader(fixtures)),
            screener=KeywordScreener(),
        )
        cls.first, records, history = cls.cycle.run(known=None, now=NOW)
        # One second later. Nothing can be due, whatever its declared cadence,
        # because the clamp floor is five minutes.
        cls.second, cls.records, _ = cls.cycle.run(
            known=records, history=history, now=NOW + datetime.timedelta(seconds=1)
        )

    def test_nobody_was_polled(self):
        """The control. Without it every assertion below passes on a cycle that
        polled normally."""
        self.assertEqual(self.second.publishers_polled, 0)
        self.assertEqual(self.second.publishers_not_due, len(OVERLAPPING))

    def test_the_feed_is_the_same_size_as_when_everyone_was_polled(self):
        self.assertEqual(self.second.source_zones, self.first.source_zones)
        self.assertEqual(self.second.canonical_zones, self.first.canonical_zones)
        self.assertTrue(self.second.published, self.second.validation)

    def test_a_skipped_poll_is_not_recorded_as_a_poll(self):
        """No observation, because none was taken. A fabricated one would feed
        R1's consecutive counting and R5's window with a measurement nobody
        made, which is this system's cardinal error committed against itself."""
        for key, record in self.records.items():
            self.assertEqual(record.last_polled_at, self.first.at, key)


if __name__ == "__main__":
    unittest.main()
