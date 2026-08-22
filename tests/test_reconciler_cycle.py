"""One reconciliation cycle, and what its snapshot reports. Section 6.6.

Run with: python3 -m unittest discover -s tests -v

Split from `test_reconciler.py` on size. The snapshot tests are here because the
snapshot is what the console reads: `merged_zone_count` and the rejected-pair
sample exist so a screen can state a true total rather than counting whatever
slice of `canonical_zones` it happened to load.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.cycle import ReconciliationCycle
from src.features.reconciler.identity import CanonicalIdentity
from src.services.fixtures import FixtureSet
from tests.support import features_for

AT = "2026-08-07T12:00:00+00:00"


class TestCycle(unittest.TestCase):
    """The whole reconciliation path over the flagship pair."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.feeds = {
            "New York DOT|nysdot": features_for(cls.fixtures, "New York DOT"),
            "NJIT|njdot": features_for(cls.fixtures, "New Jersey Institute of Technology"),
        }
        cls.trust = {"New York DOT|nysdot": "ADMIT", "NJIT|njdot": "ADMIT"}

    def cycle(self, adjudicator=None):
        return ReconciliationCycle(CanonicalIdentity(), adjudicator)

    def test_a_cycle_reduces_the_zone_count(self):
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        sources = sum(len(f) for f in self.feeds.values())
        self.assertLess(len(result.zones), sources)
        self.assertGreater(sum(1 for z in result.zones if z.merged), 100)

    def test_the_snapshot_counts_merged_zones_separately_from_groups(self):
        """`group_count` includes singletons, so it cannot answer "how many did
        we merge". Without a recorded figure the console computed one from
        whatever slice of canonical_zones it had loaded, which is a
        complete-looking count of an arbitrary subset."""
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        merged = sum(1 for z in result.zones if len(z.sources) > 1)
        self.assertEqual(result.snapshot.merged_zone_count, merged)
        self.assertLess(
            result.snapshot.merged_zone_count,
            result.snapshot.group_count,
            "singletons are groups too, which is why the two differ",
        )

    def test_the_snapshot_carries_the_rejected_sample_and_its_true_total(self):
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        snapshot = result.snapshot
        self.assertEqual(
            snapshot.rejected_pair_total, snapshot.excluded_counts["rejected_by_coverage"]
        )
        self.assertLessEqual(len(snapshot.rejected_pairs), snapshot.rejected_pair_total)
        for entry in snapshot.rejected_pairs:
            self.assertIn("distance_m", entry)
            self.assertIn("coverage", entry)

    def test_canonical_ids_are_stable_across_cycles(self):
        """The property the whole identity mapping exists for. Recomputing IDs
        from group membership would show every downstream consumer total churn
        whenever one publisher adds one zone."""
        cycle = self.cycle()
        first = cycle.run(self.feeds, self.trust, "c1", AT)
        second = cycle.run(self.feeds, self.trust, "c2", "2026-08-07T13:00:00Z")
        self.assertEqual(
            {z.canonical_id for z in first.zones}, {z.canonical_id for z in second.zones}
        )

    def test_no_source_zone_appears_in_two_canonical_zones(self):
        """CanonicalSourceMap is one to one. Duplicating a source would make it
        unsatisfiable, which is why the grouping caps exist at all."""
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        seen = set()
        for zone in result.zones:
            for ref in zone.sources:
                self.assertNotIn(ref.source_id, seen, f"{ref.source_id} appears twice")
                seen.add(ref.source_id)

    def test_no_canonical_zone_holds_two_zones_from_one_publisher(self):
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        for zone in result.zones:
            keys = zone.publisher_keys
            self.assertEqual(len(keys), len(set(keys)), zone.canonical_id)

    def test_without_an_adjudicator_tier_2_is_not_run_rather_than_merged(self):
        """No adjudicator configured is 'not decided', never 'duplicate'.
        Defaulting to a merge would hide a real closure on the strength of a call
        nobody made."""
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        counts = result.snapshot.adjudication_counts
        self.assertEqual(counts["DUPLICATE"], 0)
        self.assertEqual(counts["NOT_RUN"], result.snapshot.tier_counts["TIER_2_ADJUDICATED"])

    def test_unsure_does_not_merge_but_is_counted_separately(self):
        """A model that cannot tell must not be pushed into guessing: a wrong
        merge hides a real closure, a wrong split merely double counts."""

        class Unsure:
            def adjudicate(self, left, right, pair):
                del left, right, pair
                return "UNSURE"

        result = self.cycle(Unsure()).run(self.feeds, self.trust, "c1", AT)
        self.assertGreater(result.snapshot.adjudication_counts["UNSURE"], 0)
        self.assertEqual(result.snapshot.adjudication_counts["DUPLICATE"], 0)

    def test_a_dropped_edge_names_real_road_event_ids(self):
        """The console has to be able to look the refused pairing up, so the
        record cannot carry the positional indices grouping works over."""
        result = self.cycle().run(self.feeds, self.trust, "c1", AT)
        edges = [
            c.dropped_edge for z in result.zones for c in z.conflicts if c.dropped_edge is not None
        ]
        self.assertTrue(edges, "the flagship pair is many-to-one, so edges are dropped")
        known = {(key, str(f.get("id"))) for key, features in self.feeds.items() for f in features}
        for edge in edges[:50]:
            self.assertIn((edge.publisher_key, edge.road_event_id), known)

if __name__ == "__main__":
    unittest.main()
