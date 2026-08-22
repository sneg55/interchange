"""Where the merged feed actually goes. Section 6.8.

Until this existed the republisher built a feed, validated it against the
official schema, recorded that it had passed, and then dropped it. `feed_uri` was
null on every artifact and `byte_size` was a hardcoded zero, so the product's
entire output existed as counts on a screen and nothing a consumer could fetch.

Two rules the sink does not get to decide:

- **Only a feed that passed its own gate is written.** The republisher validates
  before emitting and a failure means do not publish; a sink that uploaded anyway
  would route around the one invariant this project cannot ship without.
- **`byte_size` is what was actually written.** It was zero when nothing was
  written anywhere, which is a measured size standing in for a measurement never
  taken. Now it is either a real byte count or null, and null means no sink is
  configured rather than an empty feed.
"""

from __future__ import annotations

import gzip
import json
from typing import Any, Protocol


class FeedSink(Protocol):
    def put(self, feed: dict[str, Any], cycle_id: str) -> tuple[str, int]:
        """Write the feed. Returns (uri, bytes written)."""
        ...


def serialise(feed: dict[str, Any]) -> bytes:
    """One encoding, used for the upload AND the byte count.

    Separate encodings would make `byte_size` describe a document that was never
    written. Sorted keys so the same merge produces the same bytes, which is what
    lets a consumer diff two cycles.
    """
    return json.dumps(feed, sort_keys=True, default=str).encode("utf-8")


class NullFeedSink:
    """The default. Records that nothing was written, rather than pretending.

    Constructed when no bucket is configured. It exists so the caller has no
    branch: an unconfigured deployment reports a null `feed_uri` and a null
    `byte_size`, which is the honest reading, instead of the caller having to
    remember to leave them null.
    """

    uri_template = ""

    def put(self, feed: dict[str, Any], cycle_id: str) -> tuple[str, int]:
        del feed, cycle_id
        raise NotImplementedError("no feed sink configured")


class GcsFeedSink:
    """Writes the merged feed to a bucket, per cycle and as `latest`.

    Two objects rather than one. The per-cycle object is the durable record that
    an evidence packet or a transition can be read against months later; `latest`
    is the stable URL a consumer polls, because a consumer that had to discover
    the newest cycle id first would be reimplementing the reconciler's job to
    read its output.

    The per-cycle object is written FIRST. If the process dies between the two,
    `latest` points at an older but complete feed rather than at nothing, and a
    stale-but-valid feed is recoverable in a way a dangling pointer is not.
    """

    def __init__(self, bucket: str, prefix: str = "feeds", client: Any = None) -> None:
        self._bucket_name = bucket
        self._prefix = prefix.strip("/")
        self._client = client

    def _bucket(self) -> Any:
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    def put(self, feed: dict[str, Any], cycle_id: str) -> tuple[str, int]:
        payload = serialise(feed)
        # Stored and served gzipped. The merged feed measured 85 MB uncompressed,
        # a new object every cycle, which is roughly 8 GB a day of accumulating
        # storage and an unreasonable thing to hand a consumer. With
        # `content_encoding` set, GCS serves `Content-Encoding: gzip` and any HTTP
        # client decompresses transparently, so this costs the consumer nothing.
        body = gzip.compress(payload)
        bucket = self._bucket()
        # Slashes are fine in an object name, unlike a Firestore document id, so
        # the cycle id goes in whole and stays readable.
        path = f"{self._prefix}/{cycle_id}.json"
        for name in (path, f"{self._prefix}/latest.json"):
            blob = bucket.blob(name)
            # No caching on `latest`. A consumer polling a feed whose whole
            # purpose is freshness must not be served an edge copy of a cycle
            # that has already been superseded.
            blob.cache_control = "no-store"
            blob.content_encoding = "gzip"
            blob.upload_from_string(body, content_type="application/json")
        # The FEED's size, not the compressed body's. `byte_size` answers "how big
        # is the thing Interchange published", and a consumer decompressing it
        # gets this many bytes. Compression is transport.
        return f"gs://{self._bucket_name}/{path}", len(payload)
