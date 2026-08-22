"""ScreeningResult, ScreeningIncident and BlockedText. Section 7.

Three records rather than one because they have three different lifetimes. A
verdict is per distinct string and is cached; an incident is per occurrence and is
append-only; the blocked string itself is stored once and referenced, because one
injected description typically appears across many zones.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Literal

# UNAVAILABLE is a third state, and it is NOT a softer BLOCK. It means the
# screener could not be reached, so the text is redacted exactly as a block would
# be (see `ScreeningResult.blocked`, which is deliberately "anything but PASS")
# while nothing is claimed about the text itself.
#
# It exists because conflating the two corrupted the one collection that is
# supposed to be forensically clean: an outage filed the publisher's ordinary
# road names into `blocked_text` alongside real attack payloads. On the live
# fleet 1,900 of 1,916 stored strings were benign text captured during transient
# failures, things like "Ethyl Street" and "No left turn", and every one of them
# passed screening on a later attempt. That is this system's own invariant
# inverted: storing "we could not check" as "this was hostile".
Verdict = Literal["PASS", "BLOCK", "UNAVAILABLE"]
ScreenedField = Literal["description", "road_names"]

# WZDx sets no maximum description length and Firestore caps a document at 1 MiB,
# so an otherwise schema-valid hostile string could break the very path that
# records hostile strings. The hash is always of the full text; only the stored
# copy is truncated.
MAX_STORED_TEXT_BYTES = 64 * 1024


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheKey:
    """`(text_sha256, policy_version, model_version)`.

    Keying on text alone would pin an obsolete verdict forever. When Model
    Armor's policy is updated, text screened under the old policy has NOT been
    screened under the new one, and a hash-only cache could not tell the
    difference: it would keep serving a verdict nobody currently stands behind.
    """

    text_sha256: str
    policy_version: str
    model_version: str

    @property
    def doc_id(self) -> str:
        return f"{self.text_sha256}|{self.policy_version}|{self.model_version}"


@dataclass(slots=True)
class ScreeningResult:
    text_sha256: str
    policy_version: str
    model_version: str
    verdict: Verdict
    category: str | None
    screened_at: str
    first_seen_publisher_keys: list[str]

    @property
    def key(self) -> CacheKey:
        return CacheKey(self.text_sha256, self.policy_version, self.model_version)

    @property
    def blocked(self) -> bool:
        """Anything that is not exactly PASS is blocked.

        Not `verdict == "BLOCK"`. That reading fails OPEN on any value the code
        does not recognise: a corrupted record, a typo in a persisted document, a
        future verdict this build predates. The safe default has to be the one
        that redacts, or an unrecognised string silently forwards raw publisher
        text to a model.
        """
        return self.verdict != "PASS"

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> ScreeningResult:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in doc.items() if k in known})


@dataclass(slots=True)
class ScreeningIncident:
    publisher_key: str
    road_event_id: str | None
    field: ScreenedField
    text_sha256: str
    category: str | None
    policy_version: str
    at: str

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BlockedText:
    """Write-once, keyed by hash. Truncated for storage, never for hashing."""

    text_sha256: str
    text: str
    truncated: bool
    original_length: int
    first_seen_at: str

    @classmethod
    def create(cls, text: str, at: str) -> BlockedText:
        encoded = text.encode("utf-8")
        truncated = len(encoded) > MAX_STORED_TEXT_BYTES
        stored = (
            encoded[:MAX_STORED_TEXT_BYTES].decode("utf-8", errors="ignore") if truncated else text
        )
        return cls(
            # Hash of the FULL text. A hash of the truncated copy would not match
            # the cache key, so the same string would be screened forever.
            text_sha256=text_sha256(text),
            text=stored,
            truncated=truncated,
            original_length=len(text),
            first_seen_at=at,
        )

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Screened:
    """One field's outcome. `text` is always safe to forward."""

    text: str
    verdict: str
    category: str | None
    text_sha256: str
    cached: bool
    incident: ScreeningIncident | None = None
    blocked_text: BlockedText | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"
