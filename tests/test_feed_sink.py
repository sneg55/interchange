"""The merged feed actually goes somewhere. Section 6.8.

Run with: python3 -m unittest discover -s tests -v

The republisher built a feed, validated it against the official schema, recorded
that it had passed, and dropped it: `feed_uri` was null on every artifact and
`byte_size` a hardcoded zero, so the product's whole output existed as counts on
a screen.

The assertions that matter are the two the sink is not allowed to change. A feed
that failed its own gate is never written, and a storage failure never costs a
cycle its reliability history.
"""

from __future__ import annotations

import gzip
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.error_ids import ErrorIds
from src.entrypoints.cycle_publish import publish_feed
from src.services.feed_sink import GcsFeedSink, serialise

FEED = {"type": "FeatureCollection", "features": [{"id": "a"}], "road_event_feed_info": {}}


class Output:
    """The shape `Republisher.build` returns, reduced to what publish reads."""

    def __init__(self, published: bool, feed: dict | None = FEED) -> None:
        self.published = published
        self.feed = feed
        self.artifact = type(
            "A", (), {"feed_uri": None, "byte_size": None, "validation_result": {}}
        )()


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def put(self, feed: dict, cycle_id: str) -> tuple[str, int]:
        self.calls.append(cycle_id)
        if self.fail:
            raise OSError("bucket unreachable")
        return f"gs://b/feeds/{cycle_id}.json", len(serialise(feed))


class TestOnlyAPassingFeedIsWritten(unittest.TestCase):
    def test_a_published_feed_is_written_and_measured(self):
        sink, out = RecordingSink(), Output(published=True)
        publish_feed(sink, out, "cycle-1")
        self.assertEqual(sink.calls, ["cycle-1"])
        self.assertEqual(out.artifact.feed_uri, "gs://b/feeds/cycle-1.json")
        self.assertEqual(out.artifact.byte_size, len(serialise(FEED)))

    def test_a_feed_that_failed_its_own_gate_is_never_written(self):
        """The invariant this project cannot ship without. A merged feed that
        would quarantine its own publisher does not get published, and a sink
        that wrote regardless would route around the republisher's verdict."""
        sink, out = RecordingSink(), Output(published=False)
        publish_feed(sink, out, "cycle-1")
        self.assertEqual(sink.calls, [])
        self.assertIsNone(out.artifact.feed_uri)

    def test_no_sink_configured_leaves_both_fields_null(self):
        """Null is the honest reading. Zero would be a measured size standing in
        for a measurement never taken, which is the `latency_ms = 0.0` mistake."""
        out = Output(published=True)
        publish_feed(None, out, "cycle-1")
        self.assertIsNone(out.artifact.feed_uri)
        self.assertIsNone(out.artifact.byte_size)


class TestAStorageFailureDoesNotCostTheCycle(unittest.TestCase):
    def test_a_failed_upload_is_recorded_rather_than_raised(self):
        """The observations and trust decisions are already correct. Discarding
        them over a storage error would lose reliability history to fix nothing.
        """
        sink, out = RecordingSink(fail=True), Output(published=True)
        publish_feed(sink, out, "cycle-1")  # must not raise
        self.assertIsNone(out.artifact.feed_uri)
        self.assertIn(
            str(ErrorIds.PUB_SINK_FAILED), out.artifact.validation_result["publish_error"]
        )

    def test_the_error_distinguishes_not_written_from_not_configured(self):
        """Both leave feed_uri null, and an operator has to be able to tell a
        deployment with no bucket from a bucket that is refusing writes."""
        configured, unconfigured = Output(published=True), Output(published=True)
        publish_feed(RecordingSink(fail=True), configured, "c")
        publish_feed(None, unconfigured, "c")
        self.assertIn("publish_error", configured.artifact.validation_result)
        self.assertNotIn("publish_error", unconfigured.artifact.validation_result)


class TestWhatGoesInTheBucket(unittest.TestCase):
    def bucket(self):
        written: dict[str, bytes] = {}
        self.encodings: dict[str, str | None] = {}
        encodings = self.encodings

        class Blob:
            def __init__(self, name):
                self.name = name
                self.cache_control = None
                self.content_encoding = None

            def upload_from_string(self, data, content_type=None):
                written[self.name] = data
                encodings[self.name] = self.content_encoding

        class Bucket:
            def blob(self, name):
                return Blob(name)

        class Client:
            def bucket(self, name):
                return Bucket()

        return written, Client()

    def test_it_writes_the_cycle_and_a_stable_latest_pointer(self):
        """A consumer that had to discover the newest cycle id first would be
        reimplementing the reconciler's job in order to read its output."""
        written, client = self.bucket()
        sink = GcsFeedSink("b", client=client)
        uri, size = sink.put(FEED, "cycle-7")
        self.assertEqual(set(written), {"feeds/cycle-7.json", "feeds/latest.json"})
        self.assertEqual(written["feeds/cycle-7.json"], written["feeds/latest.json"])
        self.assertEqual(size, len(serialise(FEED)))
        self.assertTrue(uri.endswith("feeds/cycle-7.json"), uri)

    def test_the_object_decompresses_to_exactly_the_reported_size(self):
        """`byte_size` is the FEED's size, not the compressed body's.

        It answers "how big is the thing Interchange published", and a consumer
        decompressing the object gets exactly that many bytes. Asserting it
        against the stored length instead would make it describe the transport.
        """
        written, client = self.bucket()
        _, size = GcsFeedSink("b", client=client).put(FEED, "c")
        raw = gzip.decompress(written["feeds/c.json"])
        self.assertEqual(size, len(raw))
        self.assertEqual(json.loads(raw), FEED)

    def test_it_is_stored_gzipped_and_declared_as_such(self):
        """Without `content_encoding` a client would be handed gzip bytes with a
        JSON content type and no way to know. The merged feed is 85 MB raw and a
        new object is written every cycle."""
        written, client = self.bucket()
        GcsFeedSink("b", client=client).put(FEED, "c")
        for name in ("feeds/c.json", "feeds/latest.json"):
            body, encoding = written[name], self.encodings[name]
            self.assertEqual(encoding, "gzip", name)
            self.assertEqual(json.loads(gzip.decompress(body)), FEED, name)


if __name__ == "__main__":
    unittest.main()
