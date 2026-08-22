"""CanonicalZone to a WZDx 4.2 feature. Section 6.8.

Run with: python3 -m unittest discover -s tests -v

Section 6.6 preserves disagreement; a schema field takes one value. These tests
are about that seam: which value is emitted, and where the losing values go so
they are preserved rather than discarded.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.records import ConflictRecord
from src.features.republisher.mapping import (
    EXTENSION_KEY,
    primary_source,
    resolve_field,
    to_feature,
)
from src.services.screeners import REDACTION_PLACEHOLDER
from tests.support import AT, build, republisher, source, zone


class TestMapping(unittest.TestCase):
    def test_the_id_is_the_canonical_id_not_a_source_id(self):
        """A UUID rather than a source ID, which is what makes it stable when
        group membership changes."""
        feature = to_feature(zone())
        self.assertEqual(feature["id"], "11111111-1111-1111-1111-111111111111")

    def test_the_singular_data_source_id_names_the_primary_source(self):
        """Defined for the unconflicted case rather than left to fall through it."""
        merged = zone(
            sources=[
                source("A|a", updated="2026-08-01T00:00:00Z"),
                source("B|b", "z-2", updated="2026-08-06T00:00:00Z"),
            ]
        )
        self.assertEqual(primary_source(merged).publisher_key, "B|b")
        self.assertEqual(to_feature(merged)["properties"]["core_details"]["data_source_id"], "B|b")

    def test_a_conflict_resolves_on_recency_and_keeps_the_losing_value(self):
        """Section 6.6 preserves disagreement; a schema field takes one value.
        The losers are not discarded, they ride in the extension object."""
        merged = zone(
            sources=[
                source("A|a", updated="2026-08-01T00:00:00Z", vehicle_impact="all-lanes-closed"),
                source(
                    "B|b", "z-2", updated="2026-08-06T00:00:00Z", vehicle_impact="some-lanes-closed"
                ),
            ]
        )
        value, losers = resolve_field(merged, "vehicle_impact")
        self.assertEqual(value, "some-lanes-closed")
        self.assertEqual(losers[0]["value"], "all-lanes-closed")
        extension = to_feature(merged)["properties"][EXTENSION_KEY]
        self.assertTrue(any(c["field"] == "vehicle_impact" for c in extension["conflicts"]))

    def test_ties_break_on_the_lowest_publisher_key(self):
        """Section 6.8 says lowest. Reversing a sort to get "most recent first"
        also reverses the tie-break and hands the win to the highest key, which
        is a stable, deterministic, wrong answer."""
        merged = zone(
            sources=[
                source("B|b", updated=AT, vehicle_impact="some-lanes-closed"),
                source("A|a", "z-2", updated=AT, vehicle_impact="all-lanes-closed"),
            ]
        )
        self.assertEqual(primary_source(merged).publisher_key, "A|a")
        self.assertEqual(resolve_field(merged, "vehicle_impact")[0], "all-lanes-closed")

    def test_recency_still_beats_the_tie_break(self):
        merged = zone(
            sources=[
                source("Z|z", updated="2026-08-06T00:00:00Z", vehicle_impact="some-lanes-closed"),
                source("A|a", "z-2", updated="2026-01-01T00:00:00Z", vehicle_impact="all-lanes-closed"),
            ]
        )
        self.assertEqual(primary_source(merged).publisher_key, "Z|z")

    def test_a_blocked_road_name_is_redacted_not_dropped(self):
        """road_names is REQUIRED, so dropping it would fail validation and
        passing it through would break the screening invariant. That settles the
        question rather than leaving it to taste."""
        feature = to_feature(zone(), blocked_fields={"road_names"})
        self.assertEqual(
            feature["properties"]["core_details"]["road_names"], [REDACTION_PLACEHOLDER]
        )

    def test_a_blocked_description_is_redacted(self):
        feature = to_feature(zone(), blocked_fields={"description"})
        self.assertEqual(
            feature["properties"]["core_details"]["description"], REDACTION_PLACEHOLDER
        )

    def test_direction_falls_back_to_unknown(self):
        """Which the enum permits, and which New York DOT already publishes on
        100 percent of its features."""
        z = zone()
        z.core_details.pop("direction")
        self.assertEqual(to_feature(z)["properties"]["core_details"]["direction"], "unknown")

    def test_provenance_rides_in_one_namespaced_extension(self):
        feature = to_feature(zone(sources=[source("A|a"), source("B|b", "z-2")]))
        extension = feature["properties"][EXTENSION_KEY]
        self.assertEqual(len(extension["sources"]), 2)
        self.assertEqual({s["publisher_key"] for s in extension["sources"]}, {"A|a", "B|b"})

    def test_an_ambiguous_grouping_conflict_survives_into_the_output(self):
        z = zone()
        z.conflicts.append(ConflictRecord(type="AMBIGUOUS_GROUPING", detected_at=AT))
        extension = to_feature(z)["properties"][EXTENSION_KEY]
        self.assertTrue(any(c["type"] == "AMBIGUOUS_GROUPING" for c in extension["conflicts"]))


class TestFeedInfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.republisher = republisher()

    def test_one_data_sources_entry_per_contributing_publisher(self):
        """Interchange's own output is auditable by exactly the method section
        6.6 applies to others."""
        merged = zone(sources=[source("A|a"), source("B|b", "z-2")])
        result = build(self.republisher, [merged], {"A|a": "ADMIT", "B|b": "ADMIT"})
        ids = [d["data_source_id"] for d in result.feed["feed_info"]["data_sources"]]
        self.assertEqual(ids, ["A|a", "B|b"])

    def test_publisher_contact_fields_are_dropped(self):
        """At least one live feed carries a named individual's details there.
        Passing them through would republish a person's contact information
        under Interchange's name."""
        result = build(self.republisher, [zone()])
        for banned in ("contact_name", "contact_email", "contact_phone"):
            self.assertNotIn(banned, result.feed["feed_info"])


if __name__ == "__main__":
    unittest.main()
