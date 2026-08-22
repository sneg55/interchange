"""The publisher agent, as deployed to Vertex AI Agent Engine.

Deliberately contains no LLM call. Section 2's governing decision keeps the model
out of the gate path, and the publisher agent IS the gate path: it polls a feed
and computes deterministic signals. Deploying it as a reasoning engine buys the
per-agent identity, durable sessions and managed runtime that sections 6.2 and
19.2 depend on, and none of that requires a model.

That is worth stating because "agent" and "reasoning engine" both imply an LLM,
and someone will eventually try to add one here. The signals in `signals.py` are
arithmetic over a JSON document. A model would add cost, latency, and a
non-determinism that the trust scorer explicitly forbids.

Self-contained by necessity: Agent Engine serialises this class by value
(`cloudpickle.register_pickle_by_value`), so this module and `signals.py` may
import nothing from `src`. Both are registered at deploy time.
"""

from __future__ import annotations

import datetime
import json
import urllib.parse
from typing import Any

from . import signals

# Body-derived fields copied forward on a 304. Section 6.2.
CARRY_FORWARD_FIELDS = (
    "update_date",
    "feature_count",
    "active_count",
    "active_with_past_end_date",
    "active_undated",
    "content_hash",
    "structural_hash",
)


class PublisherAgent:
    """One publisher's poller. One deployed instance per publisher key.

    Agent Engine calls set_up() once per instance and query() per request.
    """

    ALLOWED_SCHEMES = ("https", "http")

    def __init__(
        self,
        publisher_key: str,
        url: str,
        declared_version: str = "",
        declared_cadence: str = "",
    ) -> None:
        # The URL comes from the federal registry, which is third-party data and
        # is therefore hostile input like any other. Without a scheme check a
        # registry entry of file:///etc/passwd or a custom scheme would be
        # fetched by an agent holding this publisher's identity. Validate at
        # construction so a bad entry fails at provisioning, not mid-poll.
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in self.ALLOWED_SCHEMES:
            raise ValueError(
                f"refusing to poll {publisher_key}: scheme {scheme!r} not in {self.ALLOWED_SCHEMES}"
            )
        # Only plain data below: Agent Engine pickles the object to deploy it,
        # so clients and connections must be built in set_up() instead.
        self.publisher_key = publisher_key
        self.url = url
        self.declared_version = declared_version
        self.declared_cadence = declared_cadence

    def set_up(self) -> None:
        """Runs once in the deployed container, not at pickle time."""
        import ssl

        self._ctx = ssl.create_default_context()

    # Kept as classmethods so callers that already hold the class keep working;
    # signals.py is the single definition.
    content_hash = staticmethod(signals.content_hash)
    consistency = staticmethod(signals.consistency)

    # ------------------------------------------------------------------ query

    def query(
        self,
        previous: dict[str, Any] | None = None,
        last_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Poll once and return an Observation-shaped dict.

        `previous` is the last observation, supplying the conditional-request
        validators. `last_body` is the last observation that carried measured
        body-derived values, supplying what a 304 copies forward.

        They are separate because they are often different records. A failed poll
        keeps the validators, so `body -> failure -> 304` is a real sequence, and
        a 304 carrying forward from the failure would record no counts at all.
        `last_body` defaults to `previous` for the ordinary case where they are
        the same observation.

        Never raises. An unreachable publisher is a trust signal (R1) and must be
        recorded as an observation; raising would turn a publisher going dark
        into a gap in the history instead.
        """
        import time
        import urllib.error
        import urllib.request

        prior = previous or {}
        carry = last_body or prior
        started = time.time()
        now = datetime.datetime.now(datetime.UTC)
        base = {
            "publisher_key": self.publisher_key,
            "polled_at": now.isoformat(),
            "not_modified": False,
            "carried_forward": False,
        }
        try:
            if urllib.parse.urlparse(self.url).scheme.lower() not in self.ALLOWED_SCHEMES:
                # Re-checked as well as in __init__: the object is pickled for
                # deployment and could be unpickled with a mutated url.
                raise ValueError(f"disallowed scheme for {self.publisher_key}")
            req = urllib.request.Request(self.url, headers=self._headers(prior))  # noqa: S310
            with urllib.request.urlopen(req, timeout=25, context=self._ctx) as resp:  # noqa: S310
                raw, status = resp.read(), resp.status
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                encoding = resp.headers.get("Content-Encoding")
            if encoding == "gzip":
                import gzip

                raw = gzip.decompress(raw)
            # Inside the try on purpose: an unparseable body is a failed poll to
            # be recorded, not an exception that loses the whole observation.
            doc = json.loads(raw)
        except urllib.error.HTTPError as exc:
            latency = (time.time() - started) * 1000
            if exc.code == 304:
                # urlopen RAISES for 304; it is not delivered through the success
                # path. Handled here, or the intended branch is unreachable and
                # every well-behaved conditional-GET publisher looks unreachable
                # to R1.
                return self._carry_forward(base, prior, carry, latency, now, exc.headers)
            return {
                **base,
                **self._validators(prior),
                "http_status": exc.code,
                "latency_ms": latency,
                "error": f"HTTPError: {exc.code} {exc.reason}",
            }
        except Exception as exc:  # noqa: BLE001 - any failure is an R1 signal
            return {
                **base,
                **self._validators(prior),
                "http_status": 0,
                "latency_ms": (time.time() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
            }

        latency = (time.time() - started) * 1000
        if not 200 <= status < 300:
            # A body alongside a non-2xx status is a failed poll, not a small
            # one. Scoring it normally would let an error page count as clean.
            return {
                **base,
                **self._validators(prior),
                "http_status": status,
                "latency_ms": latency,
                "error": f"HTTPStatus: {status}",
            }
        return {
            **base,
            **self._measure(doc, now),
            "http_status": status,
            "latency_ms": latency,
            "etag": etag,
            "last_modified": last_modified,
            "wire_bytes": len(raw),
        }

    # ----------------------------------------------------------------- detail

    @staticmethod
    def _validators(prior: dict[str, Any]) -> dict[str, Any]:
        """Carry the conditional-request validators onto a failed observation.

        A transport failure says nothing about whether the content changed.
        Dropping the etag over one timeout would force a full re-fetch of the
        largest feeds in the fleet on the next poll.
        """
        return {"etag": prior.get("etag"), "last_modified": prior.get("last_modified")}

    @staticmethod
    def _headers(prior: dict[str, Any]) -> dict[str, str]:
        headers = {"User-Agent": "interchange/0.1", "Accept-Encoding": "gzip"}
        if prior.get("etag"):
            headers["If-None-Match"] = prior["etag"]
        if prior.get("last_modified"):
            # Section 6.3 asks for both validators. Publishers serving only
            # Last-Modified would otherwise resend the full body every poll, and
            # they are disproportionately the uncompressed ones.
            headers["If-Modified-Since"] = prior["last_modified"]
        return headers

    @staticmethod
    def _measure(doc: Any, now: datetime.datetime) -> dict[str, Any]:
        features = signals.feature_list(doc)
        update_date = signals.feed_header(doc).get("update_date")
        parsed = signals.parse_stamp(update_date)
        active, past, undated = signals.consistency(features, now)
        return {
            "update_date": update_date,
            "update_age_seconds": None if parsed is None else (now - parsed).total_seconds(),
            "feature_count": len(features),
            "active_count": active,
            "active_with_past_end_date": past,
            "active_undated": undated,
            "content_hash": signals.content_hash(features),
            "structural_hash": signals.structural_hash(features),
            # Conformance is scored fleet-side, where the pinned schema set
            # lives. Recorded as not-checked rather than omitted, so R3 is
            # NOT_APPLICABLE here and never reads as a clean validation.
            "schema_version_used": "SCHEMA_UNKNOWN",
            "schema_error_count": None,
        }

    @staticmethod
    def _carry_forward(
        base: dict[str, Any],
        prior: dict[str, Any],
        carry: dict[str, Any],
        latency: float,
        now: datetime.datetime,
        headers: Any = None,
    ) -> dict[str, Any]:
        """Build the 304 observation. Section 6.2.

        Copies the body-derived fields from the last observation that had them
        and recomputes the age against THIS poll, which is what lets a publisher
        answering 304 forever still go stale under R2. With nothing to copy the
        fields stay absent rather than becoming zeros: a zero would tell R4 that
        a publisher has no contradictory zones when none were ever counted.
        """
        observation = {
            **base,
            "http_status": 304,
            "not_modified": True,
            "latency_ms": latency,
            # A 304 may carry a refreshed validator. Reusing only the prior one
            # would keep sending a validator the publisher has already replaced.
            "etag": (headers.get("ETag") if headers else None) or prior.get("etag"),
            "last_modified": (headers.get("Last-Modified") if headers else None)
            or prior.get("last_modified")
            or carry.get("last_modified"),
            "schema_error_count": None,
        }
        if not carry.get("content_hash"):
            return observation
        for field in CARRY_FORWARD_FIELDS:
            observation[field] = carry.get(field)
        observation["carried_forward"] = True
        parsed = signals.parse_stamp(observation.get("update_date"))
        observation["update_age_seconds"] = (
            None if parsed is None else (now - parsed).total_seconds()
        )
        return observation
