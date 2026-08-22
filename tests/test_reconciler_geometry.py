"""Spatial predicate, corroborators and merge tiers. Section 6.6.

Run with: python3 -m unittest discover -s tests -v

The comments here name defects that actually shipped in the research probes.
Every one of them passed review before a measurement caught it, which is why the
tests are written against the measurement rather than against the intent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.geometry import (
    MIN_SYMMETRIC_COVERAGE,
    length_m,
    min_distance_m,
    spatially_matches,
    symmetric_coverage,
    vertices,
)
from src.features.reconciler.matching import (
    MATCH_THRESHOLD_M,
    classify,
    normalize_road,
    ranges_overlap,
)


def line(*coords):
    return {"geometry": {"type": "LineString", "coordinates": [list(c) for c in coords]}}


def point(lon, lat, **props):
    feature = {"geometry": {"type": "MultiPoint", "coordinates": [[lon, lat]]}}
    if props:
        feature["properties"] = {"core_details": props}
    return feature


class TestGeometry(unittest.TestCase):
    def test_crossing_lines_are_zero_metres_apart(self):
        """A vertex-only minimum gets this wrong by kilometres: an earlier
        version reported 2,554 m for two LineStrings that intersect."""
        a = vertices(line((-111.0, 40.0), (-111.0, 40.1)))
        b = vertices(line((-111.05, 40.05), (-110.95, 40.05)))
        self.assertAlmostEqual(min_distance_m(a, b), 0.0, places=6)

    def test_null_geometry_is_empty_not_an_error(self):
        """Quebec City serves four such features. They must be counted, not
        crash the cycle."""
        self.assertEqual(vertices({"geometry": None}), [])
        self.assertEqual(vertices({}), [])
        self.assertIsNone(min_distance_m([], [(0.0, 0.0)]))

    def test_multipoint_carries_both_single_points_and_segments(self):
        """New York DOT and NJIT use MultiPoint for single points; Iowa DOT and
        Mississippi DOT use it for two-point segments. Neither can be assumed."""
        single = vertices({"geometry": {"type": "MultiPoint", "coordinates": [[-74.0, 40.7]]}})
        pair = vertices(
            {"geometry": {"type": "MultiPoint", "coordinates": [[-74.0, 40.7], [-74.01, 40.7]]}}
        )
        self.assertEqual(len(single), 1)
        self.assertEqual(len(pair), 2)

    def test_coverage_is_insensitive_to_vertex_density(self):
        """Sampling per segment rather than by cumulative arc length made
        identical geometry score 0.73 and 0.41 depending only on how finely the
        publisher encoded it. St. Charles spends 65 vertices on 4.8 km where
        Missouri DOT spends far fewer on 33 km."""
        sparse = [(-111.0, 40.0), (-111.0, 40.01)]
        dense = [(-111.0, 40.0 + 0.01 * i / 100) for i in range(101)]
        self.assertAlmostEqual(length_m(sparse), length_m(dense), delta=1.0)
        self.assertAlmostEqual(
            symmetric_coverage(sparse, dense, 150.0),
            symmetric_coverage(dense, sparse, 150.0),
            delta=0.05,
        )

    def test_point_like_features_match_on_distance_alone(self):
        a, b = vertices(point(-111.0, 40.0)), vertices(point(-111.0, 40.0009))
        matched, distance, cov = spatially_matches(a, b, MATCH_THRESHOLD_M)
        self.assertTrue(matched)
        self.assertLess(distance, MATCH_THRESHOLD_M)
        self.assertIsNone(cov, "coverage is not computed for point-like pairs")

    def test_a_long_line_touching_a_short_one_fails_coverage(self):
        """Minimum distance alone is actively wrong for polylines. This is the
        St. Charles ramp inside the Missouri DOT corridor, in miniature."""
        corridor = vertices(line((-90.0, 38.8), (-90.0, 39.2)))
        ramp = vertices(line((-90.0, 38.9), (-90.0, 38.902)))
        matched, distance, cov = spatially_matches(corridor, ramp, MATCH_THRESHOLD_M)
        self.assertEqual(distance, 0.0, "they intersect")
        self.assertLess(cov, MIN_SYMMETRIC_COVERAGE)
        self.assertFalse(matched)


class TestCorroborators(unittest.TestCase):
    def test_road_name_normalisation_keeps_the_route_token(self):
        """Order matters: the compass rules would eat 'State Route N'."""
        self.assertEqual(normalize_road("State Route 9"), "sr 9")
        self.assertEqual(normalize_road("Interstate 70"), "i 70")
        self.assertEqual(normalize_road("I-70"), "i 70")

    def test_unknown_dates_count_as_overlapping(self):
        """A missing date is not evidence that two zones are distinct, and
        scoring it as a mismatch would suppress real duplicates."""
        self.assertTrue(ranges_overlap((None, None), ("2026-01-01T00:00:00Z", None)))
        self.assertTrue(ranges_overlap(("nonsense", "nonsense"), (None, None)))

    def test_disjoint_ranges_do_not_overlap(self):
        self.assertFalse(
            ranges_overlap(
                ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
                ("2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            )
        )


class TestTiers(unittest.TestCase):
    def test_tier_1_needs_both_a_shared_source_and_sub_metre_distance(self):
        """When two publishers both declare they are republishing TRANSCOM and
        their zones are on the same spot, the duplication is declared upstream
        rather than inferred."""
        self.assertEqual(classify(0.4, "TRANSCOM"), "TIER_1_DETERMINISTIC")
        self.assertEqual(classify(0.4, None), "TIER_2_ADJUDICATED")
        self.assertEqual(classify(90.0, "TRANSCOM"), "TIER_2_ADJUDICATED")
        self.assertEqual(classify(None, "TRANSCOM"), "TIER_2_ADJUDICATED")


if __name__ == "__main__":
    unittest.main()
