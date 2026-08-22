"""The result of one feed fetch.

Concrete because every FeedSource returns it and the trust rules read it
directly. A 304 is modelled as a distinct state rather than an absent body,
because the scorer treats the two very differently: a 304 is a successful poll
that carries values forward, while a failure feeds R1. Collapsing them into
"body is None" loses that and the loss is silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    body: dict[str, Any] | None = None
    # None when nothing was timed, not 0.0. A poll that never completed has no
    # round-trip time, and zero milliseconds is the BEST possible one: the
    # console rendered `0ms` on every failed poll, beside four columns showing an
    # absence marker for that same poll, while the daily rollup on the same page
    # reported the latency as never measured. Recording "not measured" as a
    # measured best case is this system's cardinal error committed in its own
    # records.
    latency_ms: float | None = None
    error: str | None = None
    # Whose failure this was. "INTERCHANGE" means the request never reached the
    # publisher, so nothing was learned about the publisher's availability: our
    # own outage, our own missing capture, our own misconfiguration. R1 must not
    # count those, or a notice goes to the registry owner asserting a feed was
    # unreachable on the strength of a gap at this end. None on a success.
    error_origin: str | None = None
    wire_bytes: int = 0

    @property
    def ok(self) -> bool:
        """A successful poll. Includes 304, excludes every transport failure.

        This is the definition section 6.4 depends on: a failed poll is any
        transport-level failure, and a 304 is not one.
        """
        return self.error is None and (self.not_modified or self.body is not None)

    @property
    def has_body(self) -> bool:
        """Whether body-dependent rules (R3, R4, R5) can be evaluated at all.

        When false they return NOT_APPLICABLE, never ADMIT, and a publisher
        latched by one of them cannot recover on this poll. Section 6.4.
        """
        return self.body is not None

    @classmethod
    def failure(
        cls, error: str, latency_ms: float | None = None, origin: str = "PUBLISHER"
    ) -> FetchResult:
        """A failed poll. `origin` defaults to PUBLISHER, which is the safe default.

        Defaulting the other way would silently exempt every unlabelled failure
        from R1, which is the same "absence recorded as a pass" this system
        refuses everywhere else. A caller that knows the failure was ours says so.
        """
        return cls(status=0, error=error, latency_ms=latency_ms, error_origin=origin)

    @classmethod
    def unchanged(
        cls, etag: str | None, latency_ms: float | None = None, last_modified: str | None = None
    ) -> FetchResult:
        return cls(
            status=304,
            not_modified=True,
            etag=etag,
            last_modified=last_modified,
            latency_ms=latency_ms,
        )
