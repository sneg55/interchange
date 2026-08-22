"""The gate Interchange applies to itself, and what it withholds. Section 6.8.

Run with: python3 -m unittest discover -s tests -v

The load-bearing test is that the output validates against the official WZDx 4.2
schema, and that a failure refuses to publish rather than publishing anyway. A
merged feed that would quarantine its own publisher is the one failure this
project cannot ship.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.republisher.mapping import OUTPUT_VERSION, missing_required
from src.features.republisher.publisher import Republisher
from tests.support import AT, build, republisher, source, zone


class TestSelfValidation(unittest.TestCase):
    """Interchange must pass the gate it applies to others."""

    @classmethod
    def setUpClass(cls):
        cls.republisher = republisher()

    def build(self, zones, trust=None, **kw):
        return build(self.republisher, zones, trust, **kw)

    def test_the_output_validates_against_the_official_schema(self):
        result = self.build([zone()])
        self.assertTrue(result.published, result.artifact.validation_result)
        self.assertEqual(result.artifact.validation_result["error_count"], 0)

    def test_an_invalid_zone_is_dropped_rather_than_vetoing_the_feed(self):
        """One publisher's malformed feature must not decide what the whole
        fleet publishes. North Carolina DOT emits ten `direction` values with a
        space where 4.2 requires the hyphen, and those ten withheld 32,313
        zones."""
        broken = zone()
        broken.core_details = {"event_type": 42}  # required strings, wrong types
        good = zone(canonical_id="22222222-2222-2222-2222-222222222222")
        result = self.build([broken, good])
        self.assertTrue(result.published, result.artifact.validation_result)
        self.assertEqual(result.artifact.validation_result["error_count"], 0)
        self.assertEqual(len(result.feed["features"]), 1)
        self.assertEqual(result.artifact.excluded_counts["failed_schema_validation"], 1)
        self.assertEqual(
            result.artifact.excluded_zone_ids["failed_schema_validation"],
            ["11111111-1111-1111-1111-111111111111"],
        )
        # The drop is an exclusion, not a self-validation failure. Conflating the
        # two would report a published cycle as a refusal.
        self.assertEqual(result.artifact.excluded_counts["failed_self_validation"], 0)

    def test_an_error_that_names_no_feature_still_refuses_to_publish(self):
        """Dropping zones answers an error about a zone. An error about the feed
        itself is not any zone's fault, so there is nothing to drop and the
        cycle fails closed. A refusal is evidence rather than an absence."""

        class HeaderError:
            absolute_path = ("feed_info", "publisher")

        class BrokenHeader:
            def resolve(self, version):
                return version

            def errors(self, doc, version):
                del doc, version
                return [HeaderError()]

        result = Republisher(BrokenHeader()).build(
            [zone()], {"Utah DOT|udot": "ADMIT"}, cycle_id="c1", at=AT
        )
        self.assertFalse(result.published)
        self.assertIn("E_PUB_001", result.artifact.validation_result["error_id"])
        self.assertEqual(result.artifact.excluded_counts["failed_schema_validation"], 0)
        self.assertEqual(result.artifact.excluded_counts["failed_self_validation"], 1)
        # The bucket that used to hold a count with no zones behind it.
        self.assertEqual(
            result.artifact.excluded_zone_ids["failed_self_validation"],
            ["11111111-1111-1111-1111-111111111111"],
        )

    def test_an_unresolvable_schema_is_not_a_pass(self):
        """Interchange cannot claim to have validated output against a schema it
        could not load, and publishing on that basis is the precise failure the
        whole product exists to catch."""

        class NoSchemas:
            def resolve(self, version):
                del version
                return "SCHEMA_UNKNOWN"

            def errors(self, doc, version):
                del doc, version
                return "SCHEMA_UNKNOWN"

        result = Republisher(NoSchemas()).build(
            [zone()], {"Utah DOT|udot": "ADMIT"}, cycle_id="c1", at=AT
        )
        self.assertFalse(result.published)
        self.assertTrue(result.artifact.validation_result["unresolvable"])

    def test_the_declared_output_version_is_42_regardless_of_input(self):
        result = self.build([zone()])
        self.assertEqual(result.feed["feed_info"]["version"], OUTPUT_VERSION)


class TestExclusions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.republisher = republisher()

    def build(self, zones, trust=None, **kw):
        return build(self.republisher, zones, trust, **kw)

    def test_a_quarantined_publishers_zones_are_withheld(self):
        """A trust state that does not change what gets published is a label,
        not a gate."""
        result = self.build([zone()], {"Utah DOT|udot": "QUARANTINE"})
        self.assertEqual(result.artifact.excluded_counts["quarantined_sources_only"], 1)
        self.assertEqual(result.feed["features"], [])

    def test_a_watched_publishers_zones_still_flow(self):
        """WATCH describes defects, not falsehoods. Withholding a whole state's
        road data over a formatting bug would do more harm than the bug."""
        result = self.build([zone()], {"Utah DOT|udot": "WATCH"})
        self.assertEqual(len(result.feed["features"]), 1)

    def test_a_zone_surviving_on_one_admitted_source_is_emitted(self):
        merged = zone(sources=[source("A|a"), source("B|b", "z-2")])
        result = self.build([merged], {"A|a": "QUARANTINE", "B|b": "ADMIT"})
        self.assertEqual(len(result.feed["features"]), 1)

    def test_decommissioned_publishers_stop_contributing(self):
        """A publisher no longer in the federal registry must not keep
        contributing to Interchange's output indefinitely."""
        result = self.build([zone()], {"Utah DOT|udot": "ADMIT"}, decommissioned={"Utah DOT|udot"})
        self.assertEqual(result.artifact.excluded_counts["decommissioned_sources_only"], 1)

    def test_a_missing_required_field_excludes_rather_than_invents(self):
        """Publishing a guessed vehicle_impact would be inventing a fact about a
        lane closure, which is worse than omitting the zone and saying so."""
        incomplete = zone(sources=[source(vehicle_impact=None)])
        self.assertIn("vehicle_impact", missing_required(incomplete))
        result = self.build([incomplete])
        self.assertEqual(result.artifact.excluded_counts["missing_required_field"], 1)

    def test_a_missing_verification_pair_excludes(self):
        """Both members absent is a failure; either one present is enough."""
        both_absent = zone(sources=[source(is_start_date_verified=None, start_date_accuracy=None)])
        self.assertTrue(any("is_start_date_verified" in m for m in missing_required(both_absent)))
        either = zone(
            sources=[source(is_start_date_verified=None, start_date_accuracy="estimated")]
        )
        self.assertFalse(any("is_start_date_verified" in m for m in missing_required(either)))

    def test_null_geometry_is_excluded_and_counted(self):
        result = self.build([zone(geometry=None)])
        self.assertEqual(result.artifact.excluded_counts["null_geometry"], 1)

    def test_every_exclusion_names_the_zone_it_dropped(self):
        """Excluding a zone is always reported; it is never a silent drop."""
        result = self.build([zone(geometry=None)])
        self.assertEqual(
            result.artifact.excluded_zone_ids["null_geometry"],
            ["11111111-1111-1111-1111-111111111111"],
        )

    def test_the_counts_on_the_output_screen_reconcile(self):
        """Published + excluded must add to something the artifact records.

        The screen printed zones published, zones missing a required field and
        zones failing validation, and the number they add to appeared nowhere:
        a reader could check none of it.
        """
        incomplete = zone(sources=[source(vehicle_impact=None)])
        result = self.build([zone(), incomplete, zone(geometry=None)])
        artifact = result.artifact
        self.assertEqual(artifact.input_zone_count, 3)
        self.assertEqual(
            artifact.canonical_zone_count + sum(artifact.excluded_counts.values()),
            artifact.input_zone_count,
        )

    def test_a_missing_field_exclusion_names_the_field(self):
        """ "missing required field: 16151 zones" named no field, and expanding
        it gave 25 bare UUIDs. Neither tells an operator what to ask for."""
        incomplete = zone(sources=[source(vehicle_impact=None)])
        result = self.build([incomplete])
        self.assertEqual(result.artifact.missing_field_counts.get("vehicle_impact"), 1)

    def test_withholding_records_why(self):
        """The withheld table gave a publisher and a count and no reason, so the
        most consequential fact on the screen had to be reconstructed."""
        result = self.build(
            [zone()],
            {"Utah DOT|udot": "ADMIT"},
            withheld_source_zones={"Hawaii DOT|hidot": 80},
            withheld_reasons={"Hawaii DOT|hidot": "QUARANTINE"},
        )
        self.assertEqual(result.artifact.withheld_reasons["Hawaii DOT|hidot"], "QUARANTINE")

    def test_zones_withheld_upstream_are_carried_onto_the_artifact(self):
        """`quarantined_sources_only` can only count among zones the republisher
        RECEIVED, and quarantined publishers are excluded before the merge. So it
        reads zero while hundreds of zones were withheld, and the console showed
        that zero as the whole account of what quarantine excluded."""
        result = self.build([zone()], withheld_source_zones={"Utah DOT|udot": 744})
        artifact = result.artifact
        self.assertEqual(artifact.excluded_counts["quarantined_sources_only"], 0)
        self.assertEqual(artifact.withheld_source_zones, {"Utah DOT|udot": 744})
        self.assertEqual(artifact.withheld_source_zone_count, 744)
        self.assertEqual(artifact.to_doc()["withheld_source_zone_count"], 744)

    def test_withholding_nothing_is_zero_rather_than_absent(self):
        """An absent key would render as a blank rather than as "nothing was
        withheld", and those read differently to an operator."""
        artifact = self.build([zone()]).artifact
        self.assertEqual(artifact.withheld_source_zones, {})
        self.assertEqual(artifact.withheld_source_zone_count, 0)


if __name__ == "__main__":
    unittest.main()
