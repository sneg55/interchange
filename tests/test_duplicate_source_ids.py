"""A publisher repeating a road event id must not churn canonical identity.

Run with: python3 -m unittest discover -s tests -v

Section 6.6's mapping is one to one on `(publisher_key, road_event_id)`. Two
features sharing an `id` collapse onto one map key: the second group to reach it
finds the id already claimed, `assign_all` mints a fresh one, and it does so
again on the next cycle and the next. Canonical ids that change every cycle are
the exact failure the CanonicalSourceMap exists to prevent, and downstream
consumers see it as churn they cannot explain.

Two real publishers do this. Kentucky repeats 8 ids across 293 features and
Washington State repeats 17 across 588, so this runs against the snapshot rather
than a constructed fixture: the defect was found in live data and the regression
should be too.
"""

from __future__ import annotations

import collections
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.fleet_cycle import FleetCycle
from src.services.fixtures import FixtureFeedSource, FixtureSet
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry
from src.services.screeners import KeywordScreener
from tests.test_second_cycle import SubsetRegistry

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
REPEATERS = ("Kentucky Transportation Cabinet", "Washington State DOT")


class TestDuplicateSourceIds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.cycle = FleetCycle(
            registry=SubsetRegistry(cls.fixtures, REPEATERS),
            feeds=FixtureFeedSource(cls.fixtures),
            schemas=SchemaRegistry(FixtureSchemaLoader(cls.fixtures)),
            screener=KeywordScreener(),
        )
        _, records, history = cls.cycle.run(known=None, now=NOW)
        cls.first = {e.canonical_id for e in cls.cycle.identity.entries()}
        cls.dropped = cls.cycle.snapshot.excluded_counts["duplicate_source_id"]
        # Three cycles, not two. The churn repeats every cycle, so a test that
        # stopped at two would still pass against a fix that only settled once.
        cls.cycle.run(known=records, history=history, now=NOW + datetime.timedelta(seconds=300))
        cls.second = {e.canonical_id for e in cls.cycle.identity.entries()}
        cls.cycle.run(known=records, history=history, now=NOW + datetime.timedelta(seconds=600))
        cls.third = {e.canonical_id for e in cls.cycle.identity.entries()}

    def test_the_snapshot_really_does_contain_repeated_ids(self):
        """The control. If a re-capture ever cleans these feeds up, this test
        stops proving anything and should say so rather than passing quietly."""
        repeats = 0
        for org in REPEATERS:
            entry = next(
                v for k, v in self.fixtures.manifest["feeds"].items() if k.startswith(f"{org}|")
            )
            ids = [f.get("id") for f in self.fixtures.body_for_url(entry["url"])["features"]]
            repeats += sum(n - 1 for n in collections.Counter(ids).values() if n > 1)
        self.assertGreater(repeats, 0, "no publisher in the snapshot repeats an id any more")
        self.assertEqual(self.dropped, repeats)

    def test_the_duplicates_are_counted_not_silently_dropped(self):
        self.assertGreater(self.dropped, 0)

    def test_canonical_ids_do_not_churn_across_cycles(self):
        self.assertEqual(self.first, self.second)
        self.assertEqual(self.second, self.third)


if __name__ == "__main__":
    unittest.main()
