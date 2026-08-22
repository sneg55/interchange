"""The live registry and feed sources, without a network.

Run with: python3 -m unittest discover -s tests

Every branch here is one the offline fleet never takes and the live fleet takes
constantly, which is the argument for testing it rather than trusting a smoke
run: a smoke run against 27 publishers on a good afternoon exercises the happy
path and nothing else. The 304, the gzip, the malformed body and the refused
scheme are the branches that decide whether a publisher's trust history is true.
"""

from __future__ import annotations

import gzip
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.error_ids import AppError
from src.services.fixtures import FixtureSet
from src.services.live_sources import (
    REGISTRY_URL,
    LiveFeedSource,
    LiveRegistrySource,
)

FEED_URL = "https://example.test/feed.geojson"
BODY = {"type": "FeatureCollection", "features": [], "road_event_feed_info": {}}


class FakeHttp:
    """Stands in for `Http`, recording what was asked and answering as told."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def open(self, url: str, headers: dict[str, str], timeout: float) -> Any:
        self.calls.append((url, dict(headers), timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def http_error(code: int, reason: str, headers: dict[str, str] | None = None) -> Any:
    return urllib.error.HTTPError(FEED_URL, code, reason, headers or {}, None)  # type: ignore[arg-type]


class TestRegistryUrlMatchesTheSnapshot(unittest.TestCase):
    def test_the_live_url_is_the_one_the_snapshot_was_captured_from(self):
        """A snapshot taken from one URL while the fleet polls another would make
        the offline reproduction path a reproduction of something else."""
        self.assertEqual(REGISTRY_URL, FixtureSet().manifest["registry"]["url"])


class TestLiveFeedSource(unittest.TestCase):
    def test_a_body_comes_back_parsed_with_its_validators(self):
        http = FakeHttp((json.dumps(BODY).encode(), 200, {"ETag": 'W/"abc"'}))
        result = LiveFeedSource(http=http).fetch(FEED_URL)
        self.assertEqual(result.body, BODY)
        self.assertEqual(result.etag, 'W/"abc"')
        self.assertTrue(result.ok)
        self.assertIsNone(result.error_origin)
        self.assertIsNotNone(result.latency_ms)

    def test_gzip_is_decompressed(self):
        """Eleven feeds serve no compression and are 89 percent of sweep bytes,
        so asking for it matters; the ones that honour it must not then be
        handed to json.loads as compressed bytes."""
        raw = gzip.compress(json.dumps(BODY).encode())
        http = FakeHttp((raw, 200, {"Content-Encoding": "gzip"}))
        self.assertEqual(LiveFeedSource(http=http).fetch(FEED_URL).body, BODY)

    def test_both_validators_are_sent_when_supplied(self):
        http = FakeHttp((json.dumps(BODY).encode(), 200, {}))
        LiveFeedSource(http=http).fetch(FEED_URL, etag='W/"a"', last_modified="Mon, 1 Jan 2029")
        headers = http.calls[0][1]
        self.assertEqual(headers["If-None-Match"], 'W/"a"')
        self.assertEqual(headers["If-Modified-Since"], "Mon, 1 Jan 2029")

    def test_no_validators_are_sent_when_none_are_held(self):
        http = FakeHttp((json.dumps(BODY).encode(), 200, {}))
        LiveFeedSource(http=http).fetch(FEED_URL)
        self.assertNotIn("If-None-Match", http.calls[0][1])
        self.assertNotIn("If-Modified-Since", http.calls[0][1])

    def test_a_304_is_a_successful_poll_not_an_error(self):
        """urlopen RAISES on 304. Handled only in the success branch, every
        well-behaved publisher looks unreachable to R1."""
        result = LiveFeedSource(http=FakeHttp(http_error(304, "Not Modified"))).fetch(
            FEED_URL, etag='W/"held"'
        )
        self.assertTrue(result.not_modified)
        self.assertEqual(result.status, 304)
        self.assertIsNone(result.error)
        self.assertTrue(result.ok)
        # The validator we sent is carried back, because a 304 often carries none.
        self.assertEqual(result.etag, 'W/"held"')

    def test_a_non_2xx_is_the_publishers_failure(self):
        result = LiveFeedSource(http=FakeHttp(http_error(503, "Service Unavailable"))).fetch(
            FEED_URL
        )
        self.assertEqual(result.status, 503)
        self.assertEqual(result.error_origin, "PUBLISHER")
        self.assertFalse(result.ok)

    def test_a_transport_failure_is_recorded_not_raised(self):
        result = LiveFeedSource(http=FakeHttp(TimeoutError("timed out"))).fetch(FEED_URL)
        self.assertEqual(result.error_origin, "PUBLISHER")
        self.assertIn("TimeoutError", result.error or "")
        self.assertIsNotNone(result.latency_ms)

    def test_a_refused_scheme_is_interchanges_own_failure(self):
        """R1 must not count a request that never left this process, or a notice
        goes to the registry owner over a gap at our end."""
        result = LiveFeedSource(http=FakeHttp(None)).fetch("file:///etc/passwd")
        self.assertEqual(result.error_origin, "INTERCHANGE")
        self.assertIsNone(result.latency_ms)
        self.assertFalse(result.ok)

    def test_an_unparseable_body_is_a_failed_poll(self):
        result = LiveFeedSource(http=FakeHttp((b"<html>404</html>", 200, {}))).fetch(FEED_URL)
        self.assertIsNone(result.body)
        self.assertEqual(result.error_origin, "PUBLISHER")
        self.assertFalse(result.ok)

    def test_a_json_list_is_not_a_feed(self):
        """A list parses cleanly and then has no features and no header, which
        downstream reads as a feed with zero zones rather than a malformed one."""
        result = LiveFeedSource(http=FakeHttp((b"[]", 200, {}))).fetch(FEED_URL)
        self.assertIsNone(result.body)
        self.assertIn("BadShape", result.error or "")


class TestLiveRegistrySource(unittest.TestCase):
    def test_only_active_entries_come_back(self):
        payload = [
            {"issuingorganization": "A", "active": True},
            {"issuingorganization": "B", "active": False},
            {"issuingorganization": "C"},
        ]
        http = FakeHttp((json.dumps(payload).encode(), 200, {}))
        entries = LiveRegistrySource(http=http).active_entries()
        self.assertEqual([e["issuingorganization"] for e in entries], ["A"])

    def test_an_unreachable_registry_raises_rather_than_returning_nothing(self):
        """An empty list would say forty organizations stopped publishing. The
        warden's short-read guard does not catch it on the first cycle, where
        nothing is known and zero entries clears a threshold of zero."""
        with self.assertRaises(AppError) as caught:
            LiveRegistrySource(http=FakeHttp(TimeoutError("timed out"))).active_entries()
        self.assertEqual(caught.exception.id, "E_REG_001")

    def test_a_registry_that_is_not_a_list_raises(self):
        with self.assertRaises(AppError) as caught:
            LiveRegistrySource(http=FakeHttp((b'{"error": "nope"}', 200, {}))).active_entries()
        self.assertEqual(caught.exception.id, "E_REG_002")


if __name__ == "__main__":
    unittest.main()
