"""Publisher agent: signals, the 304 carry-forward, and scheduling. Section 6.2, 6.3.

Run with: python3 -m unittest discover -s tests -v

The carry-forward tests are the load-bearing ones. Round three of review found a
deadlock in which a quarantined publisher answering 304 forever could never
accumulate a clean poll; two of the tests below exist so that cannot come back.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.publisher_agent.poller import Poller
from src.features.publisher_agent.scheduler import (
    BACKOFF_AFTER_UNCHANGED_POLLS,
    due,
    poll_interval_seconds,
    send_conditional,
    unchanged_streak,
)
from src.features.registry_warden.cadence import MAX_POLL_SECONDS, MIN_POLL_SECONDS
from src.services.fetch_result import FetchResult
from src.services.fixtures import FixtureFeedSource, FixtureSet
from src.services.schema_registry import SCHEMA_UNKNOWN, FixtureSchemaLoader, SchemaRegistry

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)


class TestPoller(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.schemas = SchemaRegistry(FixtureSchemaLoader(cls.fixtures))
        cls.url = cls._url_for(cls.fixtures, "Utah DOT")

    @staticmethod
    def _url_for(fixtures, org):
        for entry in fixtures.registry():
            if entry["issuingorganization"] == org:
                return entry["url"]["url"]
        raise AssertionError(f"{org} not in the snapshot")

    def poller(self, **kw):
        return Poller(FixtureFeedSource(self.fixtures, **kw), self.schemas)

    def test_a_body_poll_populates_every_measured_field(self):
        obs = self.poller().poll("Utah DOT|udot", self.url, "4", now=NOW)
        self.assertTrue(obs.has_body)
        self.assertFalse(obs.carried_forward)
        self.assertIsNotNone(obs.content_hash)
        self.assertIsNotNone(obs.feature_count)
        self.assertEqual(obs.schema_version_used, "4.0")
        self.assertEqual(obs.schema_error_count, 0)

    def test_utah_is_internally_contradictory(self):
        """The headline finding: active zones whose end_date has already passed.
        Asserted as a majority rather than as 744, because the feed moves."""
        obs = self.poller().poll("Utah DOT|udot", self.url, "4", now=NOW)
        self.assertGreater(obs.active_count, 0)
        self.assertGreater(obs.active_with_past_end_date, obs.active_count * 0.5)

    def test_a_transport_failure_is_recorded_not_raised(self):
        """A publisher going dark is an R1 signal. Raising would turn it into a
        gap in the history instead."""
        obs = self.poller(fail_urls={self.url}).poll("Utah DOT|udot", self.url, "4", now=NOW)
        self.assertTrue(obs.failed)
        self.assertFalse(obs.has_body)
        self.assertIsNone(obs.content_hash)

    def test_unknown_schema_version_suppresses_rather_than_fails(self):
        obs = self.poller().poll("Utah DOT|udot", self.url, "CWZ 1.0", now=NOW)
        self.assertEqual(obs.schema_version_used, SCHEMA_UNKNOWN)
        self.assertIsNone(obs.schema_error_count, "None is 'not checked', not zero errors")

    def test_no_schema_registry_records_not_checked(self):
        obs = Poller(FixtureFeedSource(self.fixtures)).poll("Utah DOT|udot", self.url, "4", now=NOW)
        self.assertEqual(obs.schema_version_used, SCHEMA_UNKNOWN)
        self.assertIsNone(obs.schema_error_count)


class TestFailureClassification(unittest.TestCase):
    """A poll must not be scored as successful unless it really was."""

    class _Source:
        def __init__(self, result):
            self._result = result

        def fetch(self, url, etag=None, last_modified=None, timeout=30.0):
            del url, etag, last_modified, timeout
            return self._result

    def poll_with(self, result):
        return Poller(self._Source(result)).poll("p|f", "https://example.test/f", now=NOW)

    def test_a_non_2xx_status_carrying_a_body_is_a_failed_poll(self):
        """An error page that happens to be JSON must never count toward the
        clean-poll streak that retires a quarantine."""
        obs = self.poll_with(FetchResult(status=503, body={"features": []}))
        self.assertTrue(obs.failed)
        self.assertFalse(obs.has_body)

    def test_a_source_returning_nothing_at_all_is_a_failed_poll(self):
        obs = self.poll_with(FetchResult(status=200, body=None))
        self.assertTrue(obs.failed)

    def test_not_modified_set_on_a_non_304_is_a_failed_poll(self):
        """The flag is not the authority; the status is. A 503 carrying the flag
        would otherwise be a successful poll with no error, able to count toward
        the streak that retires a quarantine."""
        obs = self.poll_with(FetchResult(status=503, not_modified=True))
        self.assertTrue(obs.failed)
        self.assertFalse(obs.has_body)

    def test_a_real_304_is_still_a_successful_poll(self):
        obs = self.poll_with(FetchResult(status=304, not_modified=True))
        self.assertFalse(obs.failed)

    def test_a_2xx_body_is_a_successful_poll(self):
        obs = self.poll_with(FetchResult(status=200, body={"features": []}))
        self.assertFalse(obs.failed)
        self.assertTrue(obs.has_body)

    def test_a_poll_that_never_completed_has_no_latency(self):
        """Null, not 0.0. Zero milliseconds is the BEST possible latency, and it
        was what every failed poll recorded: the console printed `0ms` beside
        four columns showing an absence marker for the same poll, while the
        daily rollup on the same page reported the latency as never measured."""
        obs = self.poll_with(FetchResult.failure("down"))
        self.assertIsNone(obs.latency_ms)

    def test_a_failure_records_whose_it_was(self):
        """R1 counts polls the PUBLISHER did not answer. A request that never
        left this process says nothing about the publisher, and counting it
        drafted a notice to the registry owner on the strength of our own gap."""
        theirs = self.poll_with(FetchResult.failure("connection refused"))
        self.assertTrue(theirs.failed)
        self.assertFalse(theirs.unreached)
        ours = self.poll_with(FetchResult.failure("no capture", origin="INTERCHANGE"))
        self.assertTrue(ours.failed, "still no body, so body rules stay not-applicable")
        self.assertTrue(ours.unreached)

    def test_a_document_that_is_not_a_feature_collection_is_recorded_not_raised(self):
        """R3's job is to score it. A crash here loses R1 and R2 as well."""
        obs = self.poll_with(FetchResult(status=200, body={"features": ["nope", 7]}))
        self.assertFalse(obs.failed)
        self.assertEqual(obs.feature_count, 2)
        self.assertEqual(obs.active_count, 0)


class TestScheduling(unittest.TestCase):
    @staticmethod
    def history(n, digest="same", failed=False):
        return [
            Observation(
                publisher_key="p",
                polled_at=f"t{i}",
                http_status=0 if failed else 200,
                content_hash=None if failed else digest,
                error="Injected" if failed else None,
            )
            for i in range(n)
        ]

    def test_backoff_after_a_long_unchanged_streak(self):
        history = self.history(BACKOFF_AFTER_UNCHANGED_POLLS)
        self.assertEqual(poll_interval_seconds(60, history), MAX_POLL_SECONDS)

    def test_no_backoff_before_the_threshold(self):
        history = self.history(BACKOFF_AFTER_UNCHANGED_POLLS - 1)
        self.assertEqual(poll_interval_seconds(60, history), MIN_POLL_SECONDS)

    def test_a_failed_poll_ends_the_streak(self):
        """An unreachable publisher is not one whose content is stable. Backing
        off there would slow observation exactly when it matters most."""
        history = self.history(1, failed=True) + self.history(20)
        self.assertEqual(unchanged_streak(history), 0)
        self.assertEqual(poll_interval_seconds(60, history), MIN_POLL_SECONDS)

    def test_demo_pinning_escapes_backoff_but_not_the_clamp(self):
        history = self.history(BACKOFF_AFTER_UNCHANGED_POLLS)
        self.assertEqual(poll_interval_seconds(60, history, demo_pinned=True), MIN_POLL_SECONDS)

    def test_a_304_extends_the_streak(self):
        carried = [
            Observation(
                publisher_key="p",
                polled_at=f"t{i}",
                http_status=304,
                not_modified=True,
                carried_forward=True,
                content_hash="same",
            )
            for i in range(BACKOFF_AFTER_UNCHANGED_POLLS)
        ]
        self.assertEqual(unchanged_streak(carried), BACKOFF_AFTER_UNCHANGED_POLLS)

    def test_an_interval_that_has_not_elapsed_is_not_due(self):
        """The half of backoff that was missing. `poll_interval_seconds` was
        computed, stored on the record and rendered in the console, and nothing
        read it back, so every cycle polled every publisher and the reduction
        section 6.3 calls the largest of the three never happened."""
        last = "2026-08-07T12:00:00+00:00"
        at = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
        self.assertFalse(due(last, MAX_POLL_SECONDS, at + datetime.timedelta(seconds=900)))
        self.assertTrue(due(last, MAX_POLL_SECONDS, at + datetime.timedelta(seconds=3600)))

    def test_the_nearest_cycle_wins_rather_than_the_next_one(self):
        """A fleet cycling every 900s sees 2700 and 3600 against a 3600 interval.
        The tolerance is half the fleet's cycle, so the poll lands on whichever
        cycle is nearer the deadline instead of drifting systematically late."""
        last = "2026-08-07T12:00:00+00:00"
        at = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
        fleet = 900

        def elapsed(seconds):
            return due(last, MAX_POLL_SECONDS, at + datetime.timedelta(seconds=seconds), fleet)

        self.assertFalse(elapsed(2700))
        self.assertTrue(elapsed(3600))

    def test_the_tolerance_cannot_break_the_clamp_floor(self):
        """The failure of a flat tolerance: subtracting a fixed amount makes a
        publisher at the 300s floor due one second after its last poll, and the
        floor is the ingress bound the whole cadence model rests on."""
        last = "2026-08-07T12:00:00+00:00"
        at = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
        self.assertFalse(
            due(last, MIN_POLL_SECONDS, at + datetime.timedelta(seconds=1), 60),
            "a publisher at the floor was polled a second after its last poll",
        )
        self.assertTrue(due(last, MIN_POLL_SECONDS, at + datetime.timedelta(seconds=300), 60))

    def test_a_caller_that_steps_time_itself_gets_an_exact_comparison(self):
        """Zero means the caller has not said how often it runs. The seed and the
        tests step time deliberately, and a tolerance would poll them early."""
        last = "2026-08-07T12:00:00+00:00"
        at = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
        self.assertFalse(due(last, MAX_POLL_SECONDS, at + datetime.timedelta(seconds=3599)))
        self.assertTrue(due(last, MAX_POLL_SECONDS, at + datetime.timedelta(seconds=3600)))

    def test_what_cannot_be_measured_is_polled(self):
        """Never polled, no interval decided, or an unparseable stamp. A missing
        measurement is not evidence of a recent poll, and erring the other way
        is a publisher that silently stops being observed."""
        at = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
        self.assertTrue(due("", MAX_POLL_SECONDS, at))
        self.assertTrue(due(None, MAX_POLL_SECONDS, at))
        self.assertTrue(due("not a timestamp", MAX_POLL_SECONDS, at))
        self.assertTrue(due("2026-08-07T12:00:00+00:00", 0, at))

    def test_conditional_get_is_suspended_by_body_dependent_rules(self):
        self.assertTrue(send_conditional(None))
        self.assertTrue(send_conditional(["R1", "R2", "R6"]))
        for rule in ("R3", "R4", "R5"):
            self.assertFalse(send_conditional([rule]), rule)


if __name__ == "__main__":
    unittest.main()
