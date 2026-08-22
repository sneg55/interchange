"""The boundaries between Interchange's logic and the world it runs in.

Everything the system needs from outside is one of four things: a registry, a
feed, a screener, or a store. Each gets a Protocol here and at least two
implementations, one live and one local.

This exists to decouple the build from cloud access. Section 19.3 records that no
GCP project is provisioned and that the reference deployment needs
organization-level IAM, which is an access question that can stall for days. The
trust scorer, reconciler, republisher and evidence packet are pure logic and need
none of it. Written against these ports they can be built and fully tested against
the checksummed snapshot in tests/fixtures/, so the access blocker holds up only
M1 and M3 rather than the whole project.

A second reason, which matters at demo time: section 10's fallback requires every
beat to survive a network failure. A system whose only feed source is the live
internet cannot offer that. Swapping FeedSource is the whole mechanism.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

# Matches the ScreeningResult verdicts in spec section 7.
ScreenVerdict = Literal["PASS", "BLOCK"]


class RegistrySource(Protocol):
    """The WZDx Feed Registry. Section 6.1."""

    def active_entries(self) -> list[dict[str, Any]]:
        """Every entry with `active` true, raw as the registry serves it.

        Callers must not assume `issuingorganization` is unique; it is not. The
        publisher key is (issuingorganization, feedname).
        """
        ...


class FetchResult(Protocol):
    """One feed fetch. Models a 304 explicitly, since the scorer depends on it."""

    status: int
    not_modified: bool
    etag: str | None
    last_modified: str | None
    body: dict[str, Any] | None  # None on 304 and on failure
    latency_ms: float | None
    error: str | None
    error_origin: str | None


class FeedSource(Protocol):
    """A publisher's feed. Section 6.2."""

    def fetch(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
        timeout: float = 30.0,
    ) -> FetchResult:
        """Fetch, sending a conditional request when a validator is supplied.

        Both validators, because section 6.3 asks for both. A publisher exposing
        only `Last-Modified` would otherwise resend its whole body on every poll,
        and those publishers are disproportionately the uncompressed ones that
        account for 89 percent of sweep bytes.

        Must never raise for a transport failure: an unreachable publisher is a
        trust signal (R1) and has to be recorded as an observation, not thrown
        away as an exception. Raising here would turn a publisher going dark into
        a gap in the history, which is exactly what section 3 says not to do.
        """
        ...


class Screener(Protocol):
    """Model Armor, or a stand-in. Section 6.5.

    Implementations MUST fail closed: any inability to screen returns BLOCK. A
    screener that returns PASS when it could not reach the service breaks the
    invariant the security beat rests on, and it fails silently, which is worse.
    """

    def screen(self, text: str) -> tuple[ScreenVerdict, str | None]:
        """Return (verdict, category). Category is None on PASS."""
        ...

    @property
    def policy_version(self) -> str:
        """Part of the ScreeningResult cache key. Changing it invalidates
        cached verdicts, which is the point: text screened under an old policy
        has not been screened under the new one."""
        ...

    @property
    def model_version(self) -> str: ...


class BodySnapshots(Protocol):
    """The last body each publisher actually served. Sections 6.2 and 8.

    A `304` says "what you already have is current", which is only useful to a
    system that still has it. Without this the poller answered a 304 with no
    body, the publisher's zones left the merge, and by the second cycle a fleet
    of correctly-behaving publishers merged nothing at all.

    Section 8 already provisions the storage: body snapshots written on
    content-hash change, gzipped, to GCS, retained 30 days. This is the read
    side of that, narrowed to the one question the poller asks.
    """

    def latest(self, publisher_key: str) -> tuple[dict[str, Any], str] | None:
        """The retained body and the content hash it was stored under.

        The hash comes back with the body because the caller must not trust the
        two to correspond: a body served in answer to a 304 has to be the one the
        carried-forward observation describes, or the merge would contain
        content no observation measured.
        """
        ...

    def record(self, publisher_key: str, body: dict[str, Any], content_hash: str) -> None:
        """Retain a body that was actually fetched, keyed by its content hash."""
        ...


class Store(Protocol):
    """Persistence. Firestore in production, files locally.

    Deliberately narrow. The spec's 16 records fall into three access patterns
    and nothing here exposes a query language, so the local implementation stays
    honest rather than quietly supporting queries Firestore would refuse without
    a composite index (section 19.5).
    """

    def put(self, collection: str, doc_id: str, doc: dict[str, Any]) -> None:
        """Write one document, overwriting. Idempotent by doc_id.

        Observations use (publisher_key, polled_at) as their ID precisely so a
        retried write cannot duplicate a poll and skew the consecutive-poll
        counting that R1 and hysteresis depend on. Section 19.6.
        """
        ...

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...

    def append(self, collection: str, doc: dict[str, Any]) -> str:
        """Add to an append-only collection, returning the generated ID."""
        ...

    def recent(
        self, collection: str, publisher_key: str, limit: int, order_by: str = "polled_at"
    ) -> list[dict[str, Any]]:
        """Most recent documents for one publisher, newest first.

        This is the only read shape the rules need, and it maps to the first
        composite index in section 19.5.
        """
        ...

    def transact(self, collection: str, doc_id: str, mutate: Any) -> dict[str, Any]:
        """Read-modify-write under contention.

        Used for CanonicalSourceMap, where two reconciliation cycles must never
        mint competing IDs for one source zone (section 19.6). `mutate` takes the
        current document (or None) and returns the new one.
        """
        ...
