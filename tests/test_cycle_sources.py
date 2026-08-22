"""What is held out of the merge, and whether anyone is told. Section 6.6.

Run with: python3 -m unittest discover -s tests

`admitted_feeds` reports the two exclusions it can see: a quarantined publisher
and a key-gated one. It cannot see the third, because it is handed bodies rather
than the reason a body is absent. A publisher that is pollable, not quarantined,
and simply has nothing to contribute used to leave the merged feed counted as
neither published nor withheld.

That case was rare while every cycle polled everyone and only an outage produced
it. Adaptive backoff makes it reachable on a schedule: a publisher that is not
due contributes the body it last served, and if nothing retained matches what its
last observation measured, it contributes nothing at all.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.cycle_sources import admitted_feeds, note_missing_bodies
from src.features.publisher_agent.observation import Observation
from src.features.registry_warden.records import PublisherRecord


def record(key: str, state: str = "WATCH", decommissioned: bool = False) -> PublisherRecord:
    org, feedname = key.split("|")
    return PublisherRecord(
        publisher_key=key,
        org=org,
        feedname=feedname,
        fleet_state=state,  # type: ignore[arg-type]
        decommissioned_at="2026-01-01T00:00:00Z" if decommissioned else None,
    )


def polls(key: str, *counts: int | None) -> list[Observation]:
    """Newest first, matching the history the cycle keeps."""
    return [
        Observation(publisher_key=key, polled_at=f"t{i}", http_status=200, feature_count=count)
        for i, count in enumerate(counts)
    ]


class TestMissingBodiesAreAccountedFor(unittest.TestCase):
    def setUp(self):
        self.records = {
            "Healthy|a": record("Healthy|a"),
            "Silent|b": record("Silent|b"),
            "Quarantined|c": record("Quarantined|c", "QUARANTINE"),
            "Gated|d": record("Gated|d", "NO_ACCESS"),
        }
        self.bodies = {
            "Healthy|a": [{"id": "1"}],
            "Quarantined|c": [{"id": "2"}, {"id": "3"}],
        }
        self.states = {key: rec.fleet_state for key, rec in self.records.items()}
        self.history = {
            "Silent|b": polls("Silent|b", None, 412, 400),
            "Healthy|a": polls("Healthy|a", 1),
        }

    def run_it(self):
        feeds, withheld, reasons = admitted_feeds(self.records, self.states, self.bodies)
        note_missing_bodies(self.records, feeds, withheld, reasons, self.history)
        return feeds, withheld, reasons

    def test_a_publisher_that_contributed_nothing_is_named_and_counted(self):
        _, withheld, reasons = self.run_it()
        self.assertEqual(reasons["Silent|b"], "NO_RETAINED_BODY")
        self.assertEqual(
            withheld["Silent|b"],
            412,
            "the count is the most recent poll that MEASURED one, skipping the "
            "304s and failures above it that measured nothing",
        )

    def test_a_quarantine_keeps_its_own_reason(self):
        """A trust verdict outranks a missing body, and the two are not
        interchangeable on a screen an operator reads."""
        _, withheld, reasons = self.run_it()
        self.assertEqual(reasons["Quarantined|c"], "QUARANTINE")
        self.assertEqual(withheld["Quarantined|c"], 2)

    def test_a_key_gated_publisher_is_not_reported_as_missing_a_body(self):
        """NO_ACCESS is not a trust verdict and not a failure to fetch. It is
        excluded before anything tries to poll it."""
        _, withheld, reasons = self.run_it()
        self.assertNotIn("Gated|d", reasons)
        self.assertNotIn("Gated|d", withheld)

    def test_a_publisher_that_contributed_is_left_alone(self):
        feeds, withheld, _ = self.run_it()
        self.assertIn("Healthy|a", feeds)
        self.assertNotIn("Healthy|a", withheld)

    def test_an_empty_feed_is_not_a_missing_body(self):
        """A publisher with zero zones published zero zones. Reporting that as
        withheld would invent an exclusion that never happened."""
        self.bodies["Silent|b"] = []
        feeds, _withheld, reasons = self.run_it()
        self.assertIn("Silent|b", feeds)
        self.assertNotIn("Silent|b", reasons)

    def test_a_publisher_with_no_history_at_all_reports_zero(self):
        """First cycle, poll failed. Nothing has ever been measured, so the
        honest count is zero and the reason carries the meaning."""
        self.history.pop("Silent|b")
        _, withheld, reasons = self.run_it()
        self.assertEqual(withheld["Silent|b"], 0)
        self.assertEqual(reasons["Silent|b"], "NO_RETAINED_BODY")


if __name__ == "__main__":
    unittest.main()
