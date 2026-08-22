"""Section 6.6's negative controls, against the real snapshot.

Run with: python3 -m unittest discover -s tests -v

Split from `test_reconciler.py` on size. These are the tests that answer "does
the coverage rule do work", as opposed to "does the grouping code behave", and
they are the ones the console's negative-control panel renders. Keeping them
together makes it obvious when one is removed.
"""


from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.matching import candidate_pairs
from src.services.fixtures import FixtureSet
from tests.support import features_for


class TestNegativeControls(unittest.TestCase):
    """Section 6.6's controls, against the real snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()

    def features(self, org):
        return features_for(self.fixtures, org)

    def test_missouri_against_st_charles_rejects_every_pair(self):
        """The more valuable control, because it tests the matching rule rather
        than testing geography. Three of the four pairs geometrically intersect
        at zero metres for zones that are plainly different work zones."""
        pairs, excluded, _rejected = candidate_pairs(
            {
                "Missouri DOT|modot": self.features("Missouri DOT"),
                "St. Charles County|stcharlesco_v4": self.features("St. Charles County"),
            }
        )
        self.assertEqual(pairs, [], "no pair may survive the coverage rule")
        self.assertGreater(excluded["rejected_by_coverage"], 0)

    def test_the_rejected_pairs_are_retained_not_merely_counted(self):
        """The console renders this control. It was rendering an empty list under
        a heading that named the control, which reads as a control that ran and
        found nothing: the opposite of what the measurement says."""
        _, excluded, rejected = candidate_pairs(
            {
                "Missouri DOT|modot": self.features("Missouri DOT"),
                "St. Charles County|stcharlesco_v4": self.features("St. Charles County"),
            }
        )
        self.assertEqual(len(rejected), excluded["rejected_by_coverage"])
        for entry in rejected:
            self.assertNotEqual(entry.left_publisher, entry.right_publisher)
            self.assertIsNotNone(entry.distance_m)
            # Inside the threshold and refused anyway IS the control. A pair
            # beyond the threshold was never a candidate and proves nothing.
            self.assertLessEqual(entry.distance_m, 150.0)

    def test_the_rejected_sample_is_capped_and_the_total_still_reported(self):
        """A sample that silently became the total would be the same silent
        truncation this system exists to catch."""
        _, excluded, rejected = candidate_pairs(
            {
                "Missouri DOT|modot": self.features("Missouri DOT"),
                "St. Charles County|stcharlesco_v4": self.features("St. Charles County"),
            },
            keep_rejected=1,
        )
        self.assertEqual(len(rejected), 1)
        self.assertGreater(
            excluded["rejected_by_coverage"],
            len(rejected),
            "the authoritative total survives the cap",
        )

    def test_civiclink_against_missouri_produces_no_candidates_at_all(self):
        """Overlapping bounding boxes, zero candidate pairs."""
        pairs, _, _rejected = candidate_pairs(
            {
                "CivicLink|CivicLink_CrewCast": self.features("CivicLink"),
                "Missouri DOT|modot": self.features("Missouri DOT"),
            }
        )
        self.assertEqual(pairs, [])

    def test_the_flagship_pair_matches_in_bulk_and_mostly_at_tier_1(self):
        """New York DOT and NJIT both republish TRANSCOM.

        Asserted as proportions and directions, never as counts: New York DOT
        carried 6,848 features when section 5 was written and 6,299 in this
        snapshot, so any exact figure here would fail for a reason that has
        nothing to do with the code.
        """
        pairs, _, _rejected = candidate_pairs(
            {
                "New York DOT|nysdot": self.features("New York DOT"),
                "NJIT|njdot": self.features("New Jersey Institute of Technology"),
            }
        )
        self.assertGreater(len(pairs), 500, "the flagship pair is a bulk duplication")
        tier_1 = [p for p in pairs if p.tier == "TIER_1_DETERMINISTIC"]
        self.assertGreater(
            len(tier_1) / len(pairs),
            0.9,
            "shared data_source_id should carry the overwhelming majority",
        )

    def test_road_name_and_direction_would_reject_the_flagship_pair(self):
        """The measurement that forced them to be corroborators rather than
        requirements. A gate demanding either would reject essentially every true
        duplicate in the pair the whole demo rests on."""
        pairs, _, _rejected = candidate_pairs(
            {
                "New York DOT|nysdot": self.features("New York DOT"),
                "NJIT|njdot": self.features("New Jersey Institute of Technology"),
            }
        )
        agreeing_names = sum(1 for p in pairs if p.road_names_agree)
        self.assertLess(agreeing_names / len(pairs), 0.05)
        self.assertEqual(
            sum(1 for p in pairs if p.direction_agrees),
            0,
            "New York DOT publishes direction: unknown for every feature",
        )
        overlapping = sum(1 for p in pairs if p.dates_overlap)
        self.assertGreater(overlapping / len(pairs), 0.5, "date overlap IS a usable corroborator")

    def test_null_geometry_is_counted_rather_than_dropped(self):
        """Quebec City serves four. A reconciler that silently discarded them
        would report a coverage figure that quietly excluded them from its own
        denominator."""
        _, excluded, _rejected = candidate_pairs(
            {
                "Quebec City|quebec": self.features("Quebec City"),
                "Utah DOT|udot": self.features("Utah DOT"),
            }
        )
        self.assertGreater(excluded["null_geometry"], 0)
