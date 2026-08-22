"""The Observation record. Section 7.

Append-only and written by the publisher agent alone. The trust scorer reads it
and writes a separate `RuleEvaluation`; it never mutates this. That split is what
lets a ruleset change be re-evaluated against retained observations without
rewriting history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Which build of the publisher agent produced an observation, stamped on every
# publisher record as `agent_identity`. Bump it whenever the set of fields an
# observation carries changes.
#
# It exists because "an older agent build omitted a field" is a real failure this
# system has already hit: a build that did not record `active_undated` made R4
# return MISSING_INPUT over a perfectly good body, and nothing in the record said
# which build had taken the poll. Without this an operator reading a run of
# unevaluable observations cannot tell a publisher that changed from an agent
# that did.
AGENT_BUILD = "2"
AGENT_IDENTITY = f"publisher-agent/{AGENT_BUILD}"

# Body-derived fields copied forward from the last observation that had a body
# when a poll returns 304. Section 6.2.
#
# Copying rather than nulling is what keeps R2 running against a publisher that
# is correctly answering 304 because nothing changed. Leaving them null would
# make a well-behaved conditional-GET publisher look undeterminable on every
# poll, which is the same "not checked recorded as failed" error in reverse.
#
# `schema_error_count` is deliberately absent: it is null on a 304 and R3 is
# not-applicable. A carried-forward error count would assert that a document
# nobody fetched still validates.
CARRY_FORWARD_FIELDS = (
    "update_date",
    "feature_count",
    "active_count",
    "active_with_past_end_date",
    "active_undated",
    "content_hash",
    "structural_hash",
)


@dataclass(slots=True)
class Observation:
    publisher_key: str
    polled_at: str
    http_status: int
    # None when the poll never completed, never 0.0. Zero is the best possible
    # latency and it was what a failed poll rendered, on a screen that shows an
    # absence marker for every other unmeasured field in the same row.
    latency_ms: float | None = None
    not_modified: bool = False
    carried_forward: bool = False
    etag: str | None = None
    last_modified: str | None = None
    update_date: str | None = None
    update_age_seconds: float | None = None
    feature_count: int | None = None
    active_count: int | None = None
    active_with_past_end_date: int | None = None
    active_undated: int | None = None
    schema_version_used: str | None = None
    schema_error_count: int | None = None
    content_hash: str | None = None
    structural_hash: str | None = None
    body_uri: str | None = None
    trace_id: str | None = None
    error: str | None = None
    # Whose failure this was: "PUBLISHER", "INTERCHANGE", or None on a success.
    #
    # R1 counts consecutive polls the publisher did not answer. A poll that never
    # left Interchange says nothing about the publisher, and counting it produced
    # a QUARANTINE and a drafted notice to the registry owner asserting a feed
    # was unreachable when the only thing that had failed was at this end.
    error_origin: str | None = None

    @property
    def unreached(self) -> bool:
        """Whether the failure was Interchange's rather than the publisher's.

        Distinct from `failed`, which stays true: there is no body either way, so
        every body-dependent rule is still NOT_APPLICABLE and the poll still does
        not count toward a clean streak. What changes is only that R1 declines to
        read it as evidence about the publisher.
        """
        return self.error is not None and self.error_origin == "INTERCHANGE"

    @property
    def failed(self) -> bool:
        """A failed poll per section 6.4: any transport-level failure.

        A 304 is a successful poll, not a failure. R1 counts consecutive failed
        polls and collapsing the two would make every well-behaved
        conditional-GET publisher look unreachable.

        Status is checked as well as `error`, so a non-2xx response carrying a
        JSON body cannot be scored as a successful poll and later count toward
        the clean-poll streak that retires a quarantine.
        """
        if self.error is not None:
            return True
        if self.not_modified:
            # The flag alone is not enough. A source setting `not_modified` on a
            # 503 would otherwise produce a successful poll with no error that
            # could count toward the streak retiring a quarantine. Only 304 is a
            # not-modified response.
            return self.http_status != 304
        return not 200 <= self.http_status < 300

    @property
    def has_body(self) -> bool:
        """Whether the body-dependent rules could be evaluated on this poll.

        False on a 304 even though the body-derived fields are populated: they
        were carried forward, not measured. A rule reading `active_count` off a
        carried-forward observation and calling it a fresh measurement is exactly
        the deadlock section 6.4 forbids.
        """
        return not self.failed and not self.not_modified

    @property
    def doc_id(self) -> str:
        """(publisher_key, polled_at). Section 19.6 relies on this for
        idempotency: a retried write must not duplicate a poll, or the
        consecutive-poll counting behind R1 and hysteresis skews."""
        return f"{self.publisher_key}@{self.polled_at}"

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> Observation:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in doc.items() if k in known})

    def carry_forward_into(self, target: Observation) -> None:
        """Copy this observation's body-derived fields onto a 304 observation.

        `update_age_seconds` is NOT copied. The carried `update_date` is the
        publisher's own assertion and stays true; its age is measured against the
        current poll, which is what lets a publisher answering 304 forever still
        go stale under R2.
        """
        for field_name in CARRY_FORWARD_FIELDS:
            setattr(target, field_name, getattr(self, field_name))
        target.carried_forward = True
