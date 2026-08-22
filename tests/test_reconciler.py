"""Grouping, canonical identity and the cycle. Section 6.6.

Run with: python3 -m unittest discover -s tests -v

The negative controls that used to live here are in
`test_reconciler_controls.py`; this file is the grouping and identity machinery
they exercise. Both run in the same suite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.grouping import build_groups
from src.features.reconciler.identity import CanonicalIdentity, CanonicalSourceMapEntry
from src.features.reconciler.matching import CandidatePair

AT = "2026-08-07T12:00:00+00:00"
CYCLE = "cycle-1"


def pair(left, right, left_pub, right_pub, distance, tier="TIER_1_DETERMINISTIC"):
    return CandidatePair(
        left_index=left,
        right_index=right,
        left_publisher=left_pub,
        right_publisher=right_pub,
        distance_m=distance,
        coverage=None,
        tier=tier,
    )


class TestGrouping(unittest.TestCase):
    def test_a_component_holds_one_zone_per_publisher(self):
        """A1 and A2 both match B. The publisher is asserting they are distinct,
        so B joins the closer one and is never duplicated."""
        groups = build_groups(
            [pair(0, 2, "A", "B", 5.0), pair(1, 2, "A", "B", 50.0)],
            all_indices=[0, 1, 2],
        )
        sizes = sorted(g.size for g in groups)
        self.assertEqual(sizes, [1, 2])
        merged = next(g for g in groups if g.size == 2)
        self.assertEqual(merged.members, [0, 2], "the closer edge wins")

    def test_the_dropped_edge_is_recorded_not_discarded(self):
        groups = build_groups(
            [pair(0, 2, "A", "B", 5.0), pair(1, 2, "A", "B", 50.0)],
            all_indices=[0, 1, 2],
        )
        dropped = [e for g in groups for e in g.dropped]
        self.assertTrue(dropped, "the refused pairing must be nameable")
        self.assertEqual(dropped[0][2], 50.0, "the dropped edge keeps its distance")
        self.assertEqual(dropped[0][:2], (1, 2), "and names both source zones")

    def test_the_outcome_does_not_depend_on_edge_order(self):
        """A component breaching the cap must drop the same edge regardless of
        the order adjudications happened to complete in."""
        edges = [pair(1, 2, "A", "B", 50.0), pair(0, 2, "A", "B", 5.0)]
        forward = build_groups(edges, all_indices=[0, 1, 2])
        reverse = build_groups(list(reversed(edges)), all_indices=[0, 1, 2])
        self.assertEqual([g.members for g in forward], [g.members for g in reverse])

    def test_a_tier_2_edge_joins_only_when_adjudication_accepts(self):
        edges = [pair(0, 1, "A", "B", 10.0, tier="TIER_2_ADJUDICATED")]
        rejected = build_groups(edges, accepted=set(), all_indices=[0, 1])
        accepted = build_groups(edges, accepted={(0, 1)}, all_indices=[0, 1])
        self.assertEqual(sorted(g.size for g in rejected), [1, 1])
        self.assertEqual(sorted(g.size for g in accepted), [2])

    def test_an_unmatched_zone_still_becomes_a_group(self):
        """A zone that matched nothing is one publisher's work zone, not an
        absence to be tidied away."""
        groups = build_groups([], all_indices=[0, 1, 2])
        self.assertEqual([g.members for g in groups], [[0], [1], [2]])

    def test_three_publishers_merge_into_one_component(self):
        groups = build_groups(
            [pair(0, 1, "A", "B", 1.0), pair(1, 2, "B", "C", 1.0)], all_indices=[0, 1, 2]
        )
        self.assertEqual([g.members for g in groups], [[0, 1, 2]])


class TestCanonicalIdentity(unittest.TestCase):
    def setUp(self):
        self.counter = iter(f"id-{i}" for i in range(100))
        self.identity = CanonicalIdentity(mint=lambda: next(self.counter))

    def test_an_id_is_stable_across_cycles(self):
        """If IDs were recomputed from group membership, one publisher adding one
        zone would rewrite the IDs of everything near it."""
        first = self.identity.assign([("A", "1"), ("B", "2")], AT, "cycle-1")
        second = self.identity.assign([("A", "1"), ("B", "2")], AT, "cycle-2")
        self.assertEqual(first.canonical_id, second.canonical_id)
        self.assertTrue(first.minted)
        self.assertFalse(second.minted)

    def test_the_age_of_an_id_is_stable_across_cycles(self):
        """`CanonicalZone.first_merged_at` is republished into the merged feed.
        It was set to the cycle's own clock, so a field named "first" changed
        every cycle and told every consumer the zone was new again."""
        first = self.identity.assign([("A", "1")], "2026-01-01T00:00:00Z", "cycle-1")
        later = self.identity.assign([("A", "1")], "2026-08-01T00:00:00Z", "cycle-2")
        self.assertEqual(first.first_mapped_at, "2026-01-01T00:00:00Z")
        self.assertEqual(later.first_mapped_at, first.first_mapped_at)

    def test_a_group_inherits_the_age_of_the_id_it_inherits(self):
        """The oldest member's ID wins, so the oldest member's age has to come
        with it. Taking the newcomer's date would date the zone to the merge."""
        self.identity.assign([("A", "1")], "2026-01-01T00:00:00Z", "c1")
        self.identity.assign([("C", "9")], "2026-06-01T00:00:00Z", "c1")
        grown = self.identity.assign([("A", "1"), ("C", "9")], "2026-08-01T00:00:00Z", "c2")
        self.assertEqual(grown.first_mapped_at, "2026-01-01T00:00:00Z")

    def test_a_fragment_that_mints_is_genuinely_new(self):
        """The other direction. A remint is a new identity and must be dated to
        the cycle that minted it, or a fragment created today out-ranks a truly
        older zone the next time the two are considered for a merge."""
        self.identity.assign([("A", "1"), ("B", "2"), ("C", "3")], "2026-01-01T00:00:00Z", "c1")
        split = self.identity.assign_all(
            [[("A", "1"), ("B", "2")], [("C", "3")]], "2026-08-01T00:00:00Z", "c2"
        )
        self.assertEqual(split[0].first_mapped_at, "2026-01-01T00:00:00Z")
        self.assertEqual(split[1].first_mapped_at, "2026-08-01T00:00:00Z")

    def test_a_growing_group_keeps_the_oldest_id(self):
        """Adopting the newcomer's identity would republish everything under a
        new ID for the sake of one added zone."""
        original = self.identity.assign([("A", "1")], "2026-01-01T00:00:00Z", "c1")
        self.identity.assign([("C", "9")], "2026-06-01T00:00:00Z", "c1")
        grown = self.identity.assign([("A", "1"), ("C", "9")], "2026-08-01T00:00:00Z", "c2")
        self.assertEqual(grown.canonical_id, original.canonical_id)
        self.assertTrue(grown.supersedes, "the newcomer's old id is superseded, not lost")

    def test_the_mapping_stays_one_to_one_when_a_group_splits(self):
        """The largest surviving fragment keeps the ID; the others mint."""
        self.identity.assign([("A", "1"), ("B", "2"), ("C", "3")], AT, "c1")
        assignments = self.identity.assign_all([[("A", "1"), ("B", "2")], [("C", "3")]], AT, "c2")
        ids = [a.canonical_id for a in assignments]
        self.assertEqual(len(set(ids)), 2, "no two groups may share a canonical id")
        self.assertFalse(assignments[0].minted, "the larger fragment keeps it")
        self.assertTrue(assignments[1].minted)

    def test_the_larger_fragment_keeps_the_id_regardless_of_input_order(self):
        self.identity.assign([("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")], AT, "c1")
        assignments = self.identity.assign_all(
            [[("D", "4")], [("A", "1"), ("B", "2"), ("C", "3")]], AT, "c2"
        )
        self.assertTrue(assignments[0].minted, "the single-zone fragment mints")
        self.assertFalse(assignments[1].minted, "the three-zone fragment inherits")

    def test_a_persisted_mapping_is_honoured(self):
        identity = CanonicalIdentity(
            [
                CanonicalSourceMapEntry("A", "1", "persisted-id", "2020-01-01T00:00:00Z"),
            ],
            mint=lambda: "should-not-be-used",
        )
        assignment = identity.assign([("A", "1"), ("B", "2")], AT, CYCLE)
        self.assertEqual(assignment.canonical_id, "persisted-id")
        self.assertFalse(assignment.minted)


class TestExplicitRejections(unittest.TestCase):
    """Section 6.6: a contradiction is not resolved by transitivity."""

    def test_a_rejected_pair_blocks_the_transitive_join(self):
        """A matches B and A matches C, but B and C were adjudicated DISTINCT.
        Filtering rejections out and relying on absence erases the difference
        between "never a candidate" and "explicitly refused", and all three end
        up in one component with no conflict recorded anywhere."""
        edges = [
            pair(0, 1, "A", "B", 5.0, tier="TIER_2_ADJUDICATED"),
            pair(0, 2, "A", "C", 6.0, tier="TIER_2_ADJUDICATED"),
            pair(1, 2, "B", "C", 7.0, tier="TIER_2_ADJUDICATED"),
        ]
        groups = build_groups(
            edges, accepted={(0, 1), (0, 2)}, rejected={(1, 2)}, all_indices=[0, 1, 2]
        )
        sizes = sorted(g.size for g in groups)
        self.assertEqual(sizes, [1, 2], "B and C must not be joined through A")
        self.assertTrue([e for g in groups for e in g.dropped], "and the refusal is recorded")

    def test_without_the_rejection_transitivity_joins_them(self):
        """The control: the same edges with nothing refused DO merge, so the
        test above is measuring the rejection and not the cap."""
        edges = [
            pair(0, 1, "A", "B", 5.0, tier="TIER_2_ADJUDICATED"),
            pair(0, 2, "A", "C", 6.0, tier="TIER_2_ADJUDICATED"),
        ]
        groups = build_groups(edges, accepted={(0, 1), (0, 2)}, all_indices=[0, 1, 2])
        self.assertEqual([g.members for g in groups], [[0, 1, 2]])

    def test_a_tier_1_edge_is_never_dropped_for_a_closer_tier_2_edge(self):
        """Tier 1 is a declared upstream duplication, the strongest evidence in
        the system. An adjudicated Tier 2 edge that merely happens to be closer
        must not displace it."""
        # Both edges are A-to-C, so only one can be kept: whichever is admitted
        # first takes C and the other breaches the one-zone-per-publisher cap.
        # Under a distance-only ordering the 0.1 m Tier 2 edge wins and the
        # declared-upstream Tier 1 edge is discarded.
        edges = [
            pair(0, 2, "A", "C", 0.1, tier="TIER_2_ADJUDICATED"),
            pair(1, 2, "A", "C", 0.8, tier="TIER_1_DETERMINISTIC"),
        ]
        groups = build_groups(edges, accepted={(0, 2)}, all_indices=[0, 1, 2])
        merged = next(g for g in groups if g.size > 1)
        self.assertEqual(merged.members, [1, 2], "the Tier 1 edge wins")

    def test_equal_distance_edges_are_ordered_by_source_index(self):
        """Python's stable sort leaves ties in caller order, so without the
        indices in the key, reversing the input changes which zone joins which."""
        edges = [
            pair(0, 2, "A", "B", 5.0),
            pair(1, 2, "A", "B", 5.0),
        ]
        forward = build_groups(edges, all_indices=[0, 1, 2])
        reverse = build_groups(list(reversed(edges)), all_indices=[0, 1, 2])
        self.assertEqual([g.members for g in forward], [g.members for g in reverse])


if __name__ == "__main__":
    unittest.main()
