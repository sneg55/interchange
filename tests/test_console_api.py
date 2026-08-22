"""Fleet board, notice queue, output health and the map cap. Section 6.9.

Run with: python3 -m unittest discover -s tests -v

The recurring assertion is that a subset always states what it is a subset of.
A band count without its fleet total, or a map without "showing N of M", is the
same failure as silent truncation: a partial answer presented as complete.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.console_api.views import fleet_board, output_health, viewport
from src.features.evidence.renderings import RULE_SUMMARIES
from src.features.reconciler.records import CanonicalZone
from src.features.republisher.publisher import OutputArtifact
from src.features.trust_scorer import rules
from tests.support import CONSOLE_AT as AT
from tests.support import publisher as record


class TestFleetBoard(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("A|a", "ADMIT"),
            record("B|b", "WATCH", us_state="NY"),
            record("C|c", "QUARANTINE"),
            record("D|d", "NO_ACCESS"),
            record("E|e", "ADMIT", declared_version="4.1"),
        ]

    def test_no_access_is_its_own_band(self):
        """It is not a trust verdict, so folding it into one would misreport
        both the coverage numerator and the denominator."""
        board = fleet_board(self.records)
        bands = {b.band: b.total for b in board.bands}
        self.assertEqual(bands["NO_ACCESS"], 1)
        self.assertEqual(bands["ADMIT"], 2)

    def test_a_filtered_view_always_carries_the_fleet_total(self):
        """A filtered count that looked like the whole fleet would be the same
        failure as silent truncation."""
        board = fleet_board(self.records, state="ADMIT")
        self.assertTrue(board.is_filtered)
        self.assertEqual(board.shown_total, 2)
        self.assertEqual(board.fleet_total, 5)
        admit = next(b for b in board.bands if b.band == "ADMIT")
        self.assertEqual((admit.shown, admit.total), (2, 2))
        watch = next(b for b in board.bands if b.band == "WATCH")
        self.assertEqual((watch.shown, watch.total), (0, 1), "zero shown, one exists")

    def test_an_unfiltered_view_says_so(self):
        board = fleet_board(self.records)
        self.assertFalse(board.is_filtered)
        self.assertEqual(board.filters_applied, {})

    def test_decommissioned_publishers_leave_the_board_but_not_the_store(self):
        records = [*self.records, record("Z|z", "QUARANTINE", decommissioned_at=AT)]
        board = fleet_board(records)
        self.assertEqual(board.fleet_total, 5)
        self.assertNotIn("Z|z", [r["publisher_key"] for r in board.rows])

    def test_churn_status_rides_alongside_the_state(self):
        """An absent churn signal has to be visible rather than implied."""
        board = fleet_board([record("A|a", "ADMIT")])
        self.assertEqual(board.rows[0]["churn_status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(board.rows[0]["fleet_state"], "ADMIT")

    def test_backoff_is_reported_against_the_declared_cadence(self):
        backed_off = record("A|a", declared_cadence_seconds=60, poll_interval_seconds=3600)
        normal = record("B|b", declared_cadence_seconds=60, poll_interval_seconds=300)
        rows = {r["publisher_key"]: r for r in fleet_board([backed_off, normal]).rows}
        self.assertTrue(rows["A|a"]["backoff_active"])
        self.assertFalse(rows["B|b"]["backoff_active"])

    def test_search_and_filters_compose(self):
        board = fleet_board(self.records, schema_version="4.1")
        self.assertEqual(board.shown_total, 1)
        self.assertEqual(fleet_board(self.records, us_state="NY").shown_total, 1)
        self.assertEqual(fleet_board(self.records, search="b").shown_total, 1)


class TestOutputHealth(unittest.TestCase):
    def artifact(self, published=True, **kw):
        base = {
            "cycle_id": "c1",
            "at": AT,
            "feed_uri": None,
            "byte_size": 0,
            "input_zone_count": 15,
            "canonical_zone_count": 10,
            "source_zone_count": 12,
            "validation_result": {"error_count": 0, "unresolvable": False},
            "published": published,
            "excluded_counts": {"null_geometry": 1},
        }
        base.update(kw)
        return OutputArtifact(**base)

    def test_a_failure_is_the_headline(self):
        """If Interchange's own output fails validation, this screen says so
        first."""
        health = output_health(
            self.artifact(False, validation_result={"error_count": 7, "unresolvable": False})
        )
        self.assertFalse(health.published)
        self.assertTrue(health.headline.startswith("NOT PUBLISHED"))
        self.assertIn("7 errors", health.headline)

    def test_an_unresolvable_schema_reads_as_not_validated(self):
        health = output_health(
            self.artifact(False, validation_result={"error_count": None, "unresolvable": True})
        )
        self.assertIn("nothing was validated", health.headline)

    def test_a_success_reports_what_was_published_against_what_was_produced(self):
        """The three counts on this screen must reconcile without arithmetic.

        "10 canonical zones from 12 source zones" read as a funnel from the
        second number to the first, and the exclusion counts below it subtract
        from a third number that appeared nowhere.
        """
        health = output_health(self.artifact())
        self.assertIn("10 of the 15 canonical zones", health.headline)
        self.assertIn("12 publisher records", health.headline)


class TestTheTwoSidesSayTheSameThing(unittest.TestCase):
    """The console's glossary and the notice renderer must not drift.

    Both hold the sentence a rule asserts. One goes on screen to an operator and
    one goes to the registry owner in an outbound notice, and they are about the
    same rule: two copies drifted apart once already, so the queue said
    "the feed was unreachable across consecutive polls" while the notice said
    something else about the same finding.
    """

    GLOSSARY = Path(__file__).resolve().parent.parent / "console" / "src" / "lib" / "glossary.ts"

    def test_every_rule_asserts_the_same_sentence_on_both_sides(self):
        source = self.GLOSSARY.read_text(encoding="utf-8")
        found = dict(re.findall(r"'(R\d)',\s*\{\s*asserts:\s*\n?\s*'([^']+)'", source))
        if not found:
            found = dict(re.findall(r"'(R\d)',\s*\{\s*asserts:\s*'([^']+)'", source))
        self.assertEqual(
            set(found), set(RULE_SUMMARIES), "the two sides disagree about which rules exist"
        )
        for rule_id, sentence in found.items():
            self.assertEqual(sentence, RULE_SUMMARIES[rule_id], f"{rule_id} drifted")

    # Spelled rather than digits, because that is how the definitions are
    # written. Only the values a threshold can plausibly take.
    WORDS: ClassVar[dict[int, str]] = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
    }

    def test_the_console_knows_which_ruleset_is_in_force(self):
        """The console compares a packet's ruleset against this to decide whether
        the finding is still one the system makes. A stale copy would silently
        stop flagging superseded drafts, which is the failure it exists to catch.
        """
        source = self.GLOSSARY.read_text(encoding="utf-8")
        declared = re.search(r"export const RULESET_VERSION = '([^']+)'", source)
        self.assertIsNotNone(declared, "the console must declare a ruleset version")
        self.assertEqual(declared.group(1), rules.RULESET_VERSION)

    def test_a_definition_that_names_a_threshold_names_the_real_one(self):
        """R1's definition said Watch at two polls and Quarantine at three. The
        ruleset uses three and twelve, so both numbers were wrong, on the screen
        whose entire job is defining the vocabulary this console decides in.

        It survived the sibling test above because that one compares the
        `asserts` summary, which agreed, and nothing looked at the prose
        underneath. Targeted at R1 because R1 is the only definition that names a
        threshold at all: the other five describe their inputs and leave the
        constants out, which is why only this one could drift.
        """
        source = self.GLOSSARY.read_text(encoding="utf-8")
        measures = dict(re.findall(r"'(R\d)',\s*\{.*?measures:\s*\n?\s*'([^']+)'", source, re.S))
        r1 = measures["R1"].lower()
        for threshold in (rules.R1_WATCH_FAILURES, rules.R1_QUARANTINE_FAILURES):
            self.assertIn(
                self.WORDS[threshold],
                r1,
                f"R1's definition does not name its own threshold of {threshold}",
            )


class TestViewport(unittest.TestCase):
    def zone(self, i):
        return CanonicalZone(
            canonical_id=f"z-{i:04d}",
            geometry={"type": "Point", "coordinates": [-111.0, 40.0]},
            core_details={},
            start_date=None,
            end_date=None,
            bbox=[-111.0, 40.0, -111.0, 40.0],
        )

    def test_the_cap_is_reported_never_silent(self):
        """Silent truncation would be the same failure this product exists to
        catch."""
        result = viewport([self.zone(i) for i in range(100)], [-112, 39, -110, 41], cap=10)
        self.assertTrue(result.capped)
        self.assertEqual(result.note, "Showing 10 of 100 in view.")

    def test_an_uncapped_view_says_it_is_complete(self):
        result = viewport([self.zone(i) for i in range(5)], [-112, 39, -110, 41], cap=10)
        self.assertFalse(result.capped)
        self.assertIn("all 5", result.note)

    def test_zones_outside_the_viewport_are_excluded(self):
        result = viewport([self.zone(0)], [0, 0, 1, 1])
        self.assertEqual(result.matched, 0)

    def test_a_zone_with_no_bbox_is_not_placed_at_the_origin(self):
        """A zero box would put every geometry-less zone in the Gulf of Guinea,
        where a viewport query would happily return it."""
        nowhere = self.zone(0)
        nowhere.bbox = None
        self.assertEqual(viewport([nowhere], [-1, -1, 1, 1]).matched, 0)


if __name__ == "__main__":
    unittest.main()
