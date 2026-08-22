"""The registry and the feeds, over the live internet. Sections 6.1, 6.2, 6.3.

`ports.py` promises every boundary "at least two implementations, one live and
one local", and until now `RegistrySource` and `FeedSource` had only the local
one. Every cycle this fleet has ever run read the checksummed snapshot. This is
the other half, and M3 needs it, because a reliability history is worth having
only if the polls in it actually happened.

The fetch logic is not new. It is `PublisherAgent._fetch` behind the port, and
the duplication is structural rather than an oversight: that module is pickled
by value into each deployed reasoning engine and may import nothing from `src`,
so the two cannot share code and have to be kept in step by hand. Every trap it
documents is repeated here because each one was paid for once already.

- **`urlopen` RAISES on 304.** It is not delivered through the success path. A
  conditional GET handled only in the success branch makes every well-behaved
  publisher look unreachable to R1, which is the opposite of the truth: sending
  a validator and getting a 304 is a publisher doing exactly the right thing.
- **gzip is decompressed here.** Eleven feeds serve no compression at all and
  are 89 percent of sweep bytes (section 17), so asking for it matters; the ones
  that honour it must not then be handed to `json.loads` as compressed bytes.
- **A body that will not parse is a failed poll, not an exception.** It is
  recorded as an observation, because an unparseable feed is a trust signal and
  raising would turn a publisher going dark into a gap in the history.

Nothing here decides what a status MEANS. The source reports what happened and
the `Poller` classifies it, so the live path and the fixture path cannot drift
into two different readings of the same response.
"""

from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.constants.error_ids import AppError, ErrorIds

from .fetch_result import FetchResult

# The registry, as captured in `tests/fixtures/manifest.json`. The two are
# asserted equal in `tests/test_live_sources.py`: a snapshot taken from one URL
# and a fleet polling another would make the offline reproduction path a
# reproduction of something else.
REGISTRY_URL = "https://datahub.transportation.gov/resource/69qe-yiui.json?$limit=500"

# Matches `PublisherAgent.ALLOWED_SCHEMES`. The registry is third-party data and
# a `url` field is hostile input like any other: without this check an entry of
# `file:///etc/passwd` would be read by a process holding cloud credentials.
ALLOWED_SCHEMES = ("https", "http")

USER_AGENT = "interchange/0.1"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _elapsed_ms(started: float) -> float:
    """Monotonic, so a clock adjustment mid-poll cannot produce a negative."""
    return (time.monotonic() - started) * 1000


def _decompress(raw: bytes, headers: dict[str, str]) -> bytes:
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        return gzip.decompress(raw)
    return raw


class Http:
    """One TLS context, shared across polls, with the request shape in one place."""

    def __init__(self, user_agent: str = USER_AGENT, context: ssl.SSLContext | None = None) -> None:
        self._user_agent = user_agent
        # Built here rather than at import, so a caller can inject a context and
        # tests never touch the system trust store.
        self._ctx = context or ssl.create_default_context()

    def open(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> tuple[bytes, int, dict[str, str]]:
        request = urllib.request.Request(  # noqa: S310 - scheme checked by the caller
            url, headers={"User-Agent": self._user_agent, **headers}
        )
        with urllib.request.urlopen(  # noqa: S310 - scheme checked by the caller
            request, timeout=timeout, context=self._ctx
        ) as response:
            return response.read(), response.status, dict(response.headers)


class LiveFeedSource:
    """A publisher's feed over HTTPS. Section 6.2.

    Never raises, as the port requires. Every failure becomes a `FetchResult`
    the scorer can read, and the one distinction that matters is carried on it:
    a request this process refused to send is `INTERCHANGE`, and everything that
    actually left is `PUBLISHER`. R1 counts only the latter, or a notice goes to
    the registry owner asserting a feed was unreachable on the strength of a gap
    at this end.
    """

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        context: ssl.SSLContext | None = None,
        http: Http | None = None,
    ) -> None:
        # `http` is injectable so the tests below can exercise every branch of
        # this class without a network, which matters more here than usual: the
        # branches ARE the behaviour, and the ones that go wrong (304, gzip, a
        # body that will not parse) are the ones a live smoke test rarely hits.
        self._http = http or Http(user_agent, context)

    def fetch(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> FetchResult:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            # Ours, and stated as ours. Nothing was learned about the publisher:
            # we declined to ask. Latency is None because nothing was timed.
            return FetchResult.failure(
                f"Interchange refused to poll {url}: scheme {scheme!r} is not one of "
                f"{ALLOWED_SCHEMES}, so this poll was never attempted against the publisher",
                origin="INTERCHANGE",
            )

        headers: dict[str, str] = {"Accept-Encoding": "gzip"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            # Both validators, per section 6.3. A publisher exposing only
            # Last-Modified would otherwise resend its whole body every poll,
            # and those are disproportionately the uncompressed ones.
            headers["If-Modified-Since"] = last_modified

        started = time.monotonic()
        try:
            raw, status, response_headers = self._http.open(url, headers, timeout)
        except urllib.error.HTTPError as exc:
            return self._from_http_error(exc, etag, last_modified, _elapsed_ms(started))
        except Exception as exc:  # noqa: BLE001 - any transport failure is an R1 signal
            return FetchResult.failure(
                f"{type(exc).__name__}: {exc}",
                latency_ms=_elapsed_ms(started),
                origin="PUBLISHER",
            )

        latency = _elapsed_ms(started)
        served_etag = response_headers.get("ETag") or etag
        served_stamp = response_headers.get("Last-Modified") or last_modified
        try:
            payload = json.loads(_decompress(raw, response_headers))
        except Exception as exc:  # noqa: BLE001 - an unparseable feed is a trust signal
            return FetchResult(
                status=status,
                etag=served_etag,
                last_modified=served_stamp,
                latency_ms=latency,
                error=f"{ErrorIds.FEED_BAD_JSON} {type(exc).__name__}: {exc}",
                error_origin="PUBLISHER",
                wire_bytes=len(raw),
            )
        if not isinstance(payload, dict):
            # A GeoJSON FeatureCollection is an object. A list parses cleanly and
            # then has no `features` and no header, which downstream reads as a
            # feed with zero zones rather than as the malformed document it is.
            return FetchResult(
                status=status,
                etag=served_etag,
                last_modified=served_stamp,
                latency_ms=latency,
                error=(
                    f"{ErrorIds.FEED_BAD_JSON} BadShape: feed is a "
                    f"{type(payload).__name__}, not a JSON object"
                ),
                error_origin="PUBLISHER",
                wire_bytes=len(raw),
            )
        return FetchResult(
            status=status,
            etag=served_etag,
            last_modified=served_stamp,
            body=payload,
            latency_ms=latency,
            wire_bytes=len(raw),
        )

    @staticmethod
    def _from_http_error(
        exc: urllib.error.HTTPError,
        etag: str | None,
        last_modified: str | None,
        latency: float,
    ) -> FetchResult:
        served_etag = exc.headers.get("ETag") or etag
        served_stamp = exc.headers.get("Last-Modified") or last_modified
        if exc.code == 304:
            # The successful poll that looks like an error. A 304 carries no
            # body and no new validators of its own on some publishers, so the
            # ones we sent are carried back rather than dropped.
            return FetchResult.unchanged(
                etag=served_etag, last_modified=served_stamp, latency_ms=latency
            )
        return FetchResult(
            status=exc.code,
            etag=served_etag,
            last_modified=served_stamp,
            latency_ms=latency,
            error=f"HTTPStatus: {exc.code} {exc.reason}",
            error_origin="PUBLISHER",
        )


class LiveRegistrySource:
    """The WZDx Feed Registry over HTTPS. Section 6.1.

    Raises where `LiveFeedSource` does not, and the asymmetry is deliberate. An
    unreachable publisher is a fact about that publisher and belongs in its
    history. An unreachable registry is a fact about nothing: it is not evidence
    that forty organizations stopped publishing, and returning an empty list
    would say exactly that.

    The warden's short-read guard is not enough on its own. It compares the pull
    against the count of live known records (`SHORT_READ_FRACTION`), and on the
    very first cycle nothing is known, so `0 entries` clears a threshold of zero
    and the fleet provisions itself as empty. Failing here means the cycle stops
    with the reason named, and the next one retries against a registry that is
    back.
    """

    def __init__(
        self,
        url: str = REGISTRY_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        context: ssl.SSLContext | None = None,
        http: Http | None = None,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._http = http or Http(context=context)

    def active_entries(self) -> list[dict[str, Any]]:
        try:
            raw, status, headers = self._http.open(
                self._url, {"Accept-Encoding": "gzip"}, self._timeout
            )
        except Exception as exc:
            raise AppError(
                ErrorIds.REG_FETCH_FAIL,
                f"registry unreachable: {type(exc).__name__}: {exc}",
                {"url": self._url},
            ) from exc

        try:
            payload = json.loads(_decompress(raw, headers))
        except Exception as exc:
            raise AppError(
                ErrorIds.REG_BAD_SHAPE,
                f"registry response did not parse: {type(exc).__name__}: {exc}",
                {"url": self._url, "status": status, "bytes": len(raw)},
            ) from exc

        if not isinstance(payload, list):
            raise AppError(
                ErrorIds.REG_BAD_SHAPE,
                f"registry returned a {type(payload).__name__}, expected a list of entries",
                {"url": self._url, "status": status},
            )
        # `active` is a real boolean in the registry, not a string. Read through
        # `.get` anyway: an entry missing the field is not an active entry, and
        # treating absence as presence is this system's cardinal error.
        return [entry for entry in payload if isinstance(entry, dict) and entry.get("active")]
