"""What a 304 carries forward, and what it must not. Section 6.2.

Run with: python3 -m unittest discover -s tests -v

Split out of `test_publisher_agent.py` when that file passed the 300 line limit.
These are the load-bearing tests of the two defects the carry-forward has
actually shipped:

- Round three found a deadlock where a quarantined publisher answering 304
  forever could never accumulate a clean poll.
- The body was not carried at all, only the observation's counts, so a
  publisher's zones left the merge on its first 304 and the second cycle
  onward published a fraction of the fleet.

Both are the same mistake in different clothes: reading "nothing changed" as
"nothing is there".
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.publisher_agent.poller import Poller
from src.services.body_snapshots import InMemoryBodySnapshots
from src.services.fixtures import FixtureFeedSource, FixtureSet
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)


def url_for(fixtures, org):
    for entry in fixtures.registry():
        if entry["issuingorganization"] == org:
            return entry["url"]["url"]
    raise AssertionError(f"{org} not in the snapshot")


class TestCarryForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = FixtureSet()
        cls.url = url_for(cls.fixtures, "Utah DOT")
        cls.poller = Poller(
            FixtureFeedSource(cls.fixtures), SchemaRegistry(FixtureSchemaLoader(cls.fixtures))
        )

    def first_then_304(self, later=None):
        first = self.poller.poll("Utah DOT|udot", self.url, "4", now=NOW)
        second = self.poller.poll("Utah DOT|udot", self.url, "4", history=[first], now=later or NOW)
        return first, second

    def test_a_304_carries_body_derived_fields_forward(self):
        first, second = self.first_then_304()
        self.assertTrue(second.not_modified)
        self.assertTrue(second.carried_forward)
        self.assertEqual(second.content_hash, first.content_hash)
        self.assertEqual(second.active_count, first.active_count)
        self.assertEqual(second.update_date, first.update_date)

    def test_a_304_is_a_successful_poll_not_a_failure(self):
        _, second = self.first_then_304()
        self.assertFalse(second.failed)

    def test_a_304_is_not_a_body_poll(self):
        """The values are carried, not measured. A rule reading them as a fresh
        measurement is the deadlock section 6.4 forbids."""
        _, second = self.first_then_304()
        self.assertFalse(second.has_body)

    def test_schema_error_count_is_never_carried_forward(self):
        """It would assert that a document nobody fetched still validates."""
        _, second = self.first_then_304()
        self.assertIsNone(second.schema_error_count)

    def test_a_304_returns_the_body_it_refers_to(self):
        """The defect this whole store exists for. A 304 says the copy you hold
        is current, so the zones behind it are still the publisher's current
        zones. Answering with no body dropped them from the merge, and by the
        second cycle a fleet of publishers behaving correctly merged nothing."""
        bodies = InMemoryBodySnapshots()
        poller = Poller(
            FixtureFeedSource(self.fixtures),
            SchemaRegistry(FixtureSchemaLoader(self.fixtures)),
            bodies,
        )
        first, first_body = poller.poll_with_body("Utah DOT|udot", self.url, "4", now=NOW)
        second, second_body = poller.poll_with_body(
            "Utah DOT|udot", self.url, "4", history=[first], now=NOW
        )
        self.assertTrue(second.not_modified)
        self.assertIsNotNone(second_body)
        self.assertEqual(second_body, first_body)

    def test_a_304_with_no_retained_body_still_returns_none(self):
        """Fail closed. A poller with nowhere to have kept the body behaves
        exactly as it did before the store existed."""
        _, body = self.first_then_304_with_body(bodies=None)
        self.assertIsNone(body)

    def test_a_body_the_observation_does_not_describe_is_not_served(self):
        """The hash check, and the reason it is not paranoia. The carried
        content_hash describes what the last measured poll saw; a retained body
        that hashes to something else is content no rule was evaluated against,
        and putting it in the merge would be `not checked` wearing the costume
        of `checked`."""

        class Stale:
            """A store holding a body under a hash nothing will ever carry."""

            def latest(self, publisher_key):
                del publisher_key
                return ({"features": [{"id": "ghost"}]}, "hash-of-something-else")

            def record(self, publisher_key, body, content_hash):
                del publisher_key, body, content_hash

        _, body = self.first_then_304_with_body(bodies=Stale())
        self.assertIsNone(body)

    def first_then_304_with_body(self, bodies):
        poller = Poller(
            FixtureFeedSource(self.fixtures),
            SchemaRegistry(FixtureSchemaLoader(self.fixtures)),
            bodies,
        )
        first, _ = poller.poll_with_body("Utah DOT|udot", self.url, "4", now=NOW)
        return poller.poll_with_body("Utah DOT|udot", self.url, "4", history=[first], now=NOW)

    def test_update_age_is_recomputed_not_carried(self):
        """This is what lets a publisher answering 304 forever still go stale
        under R2."""
        later = NOW + datetime.timedelta(days=30)
        first, second = self.first_then_304(later=later)
        self.assertGreater(second.update_age_seconds, first.update_age_seconds)

    def test_suppressing_the_conditional_request_forces_a_body(self):
        first = self.poller.poll("Utah DOT|udot", self.url, "4", now=NOW)
        second = self.poller.poll(
            "Utah DOT|udot", self.url, "4", history=[first], send_conditional=False, now=NOW
        )
        self.assertFalse(second.not_modified)
        self.assertTrue(second.has_body)

    def test_no_conditional_request_is_sent_without_a_body_bearing_ancestor(self):
        """Otherwise the publisher is trapped with no route back.

        The exchange would be: send a validator, get 304, have nothing to carry
        forward, R6 fires on the absent update_date, and R6 does not suspend
        conditional GET, so the identical exchange repeats forever and no poll is
        ever clean. Not sending the validator is what breaks the cycle.
        """
        source = FixtureFeedSource(self.fixtures)
        orphan = Observation(
            publisher_key="Utah DOT|udot",
            polled_at="2026-08-07T11:00:00+00:00",
            http_status=304,
            not_modified=True,
            etag=source._etag_for(self.url),
        )
        result = Poller(source).poll("Utah DOT|udot", self.url, "4", history=[orphan], now=NOW)
        self.assertFalse(result.not_modified, "a body must be fetched instead")
        self.assertTrue(result.has_body)
        self.assertIsNotNone(result.content_hash)

    def test_a_304_with_no_ancestor_still_reports_nothing_rather_than_zeros(self):
        """Belt and braces on the carry-forward itself.

        Reporting zeros would tell R4 that a publisher has no contradictory zones
        when in fact none were ever counted, which is exactly the shape of "not
        checked" stored as "checked and passed".
        """
        observation = Observation(
            publisher_key="p|f",
            polled_at="2026-08-07T12:00:00+00:00",
            http_status=304,
            not_modified=True,
        )
        Poller._apply_carry_forward(observation, [], NOW)
        self.assertFalse(observation.carried_forward)
        self.assertIsNone(observation.content_hash)
        self.assertIsNone(observation.active_count)
        self.assertIsNone(observation.active_undated)

    def test_carry_forward_survives_an_intervening_failed_poll(self):
        """body -> failure -> 304 must still carry the body's values.

        A failed poll keeps the previous validator, so the poll after it can
        legitimately return 304. Looking only one step back finds the failure,
        which has nothing on it, and writes a 304 with no counts at all.
        """
        source = FixtureFeedSource(self.fixtures)
        poller = Poller(source)
        first = poller.poll("Utah DOT|udot", self.url, "4", now=NOW)
        source.fail_urls.add(self.url)
        failed = poller.poll("Utah DOT|udot", self.url, "4", history=[first], now=NOW)
        source.fail_urls.clear()
        self.assertTrue(failed.failed)
        third = poller.poll("Utah DOT|udot", self.url, "4", history=[failed, first], now=NOW)
        self.assertTrue(third.not_modified)
        self.assertTrue(third.carried_forward)
        self.assertEqual(third.content_hash, first.content_hash)
        self.assertEqual(third.active_count, first.active_count)


if __name__ == "__main__":
    unittest.main()
