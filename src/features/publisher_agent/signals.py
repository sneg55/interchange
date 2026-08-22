"""The deterministic signals computed from a feed body. Section 6.2.

Split from `agent.py` so the deployed class holds transport and this holds
arithmetic. Both modules are registered for by-value pickling when the agent is
deployed, so this file, like `agent.py`, may import nothing from `src`.

`poller.py` imports these rather than reimplementing them. One definition of the
content hash, not a deployed one and a local one free to drift.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from typing import Any

# RFC 3339, strict about shape and lenient about precision. Utah DOT serves seven
# fractional digits, which `fromisoformat` rejects before Python 3.11; a bare
# date is NOT a valid WZDx timestamp and must not be silently read as midnight
# UTC. Mirrors src/utils/timestamps.py, which cannot be imported here.
ISO_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:?\d{2})$"
)


def parse_stamp(stamp: Any) -> datetime.datetime | None:
    """Parse, or return None. None means unusable, never "assume midnight UTC"."""
    if not isinstance(stamp, str):
        return None
    match = ISO_DATETIME.match(stamp.strip())
    if not match:
        return None
    date, clock, fraction, offset = match.groups()
    fraction = (fraction or ".0")[1:][:6].ljust(6, "0")
    offset = "+00:00" if offset in ("Z", "z") else offset
    if len(offset) == 5:  # +HHMM
        offset = offset[:3] + ":" + offset[3:]
    try:
        parsed = datetime.datetime.fromisoformat(f"{date}T{clock}.{fraction}{offset}")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def properties(feature: Any) -> dict[str, Any]:
    """`properties`, or an empty dict when it is not an object.

    Guarding the type rather than just the truthiness matters: a feature of
    `{"properties": "malformed"}` is schema-invalid but parses as JSON, and
    calling `.get()` on the string raises AttributeError. That would lose the
    whole observation, taking the R1 and R2 signals with it, over a defect R3
    exists to score.
    """
    if not isinstance(feature, dict):
        return {}
    props = feature.get("properties")
    return props if isinstance(props, dict) else {}


def core(feature: Any) -> dict[str, Any]:
    props = properties(feature)
    nested = props.get("core_details")
    return nested if isinstance(nested, dict) else props


def event_status(feature: Any) -> Any:
    """`event_status` from wherever this version puts it.

    WZDx moved several fields into `core_details` from v4.1, but every live 4.x
    feed in the snapshot carries `event_status` as a SIBLING of `core_details`
    under `properties`. Reading only `core_details` returns None on all of them,
    which silently drops the field from the content hash and makes an
    active-to-completed edit produce an identical digest.
    """
    status = core(feature).get("event_status")
    return properties(feature).get("event_status") if status is None else status


def _raw_properties(feature: Any) -> Any:
    """The `properties` value exactly as served, dict or not.

    Hashed alongside the parsed view so a malformed value still contributes. With
    only the guarded `{}` view, `{"properties": "A"}` and `{"properties": "B"}`
    hash identically, and a publisher whose content is genuinely changing would
    look frozen to R5.
    """
    return feature.get("properties") if isinstance(feature, dict) else None


def _digest(payload: list[Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _hash_features(features: list[Any], include_free_text: bool) -> str:
    """SHA-256 over SORTED per-feature digests.

    Sorting is what makes this a content hash rather than an ordering hash.
    Publishers do not hold feature order stable, and hashing the document would
    report churn on every reorder, which would make R5 useless.
    """
    digests = []
    for feature in features:
        if not isinstance(feature, dict):
            # A malformed feature still has to hash to something stable, or a
            # schema-invalid feed would crash the poll instead of being recorded
            # and scored by R3.
            digests.append(hashlib.sha256(repr(feature).encode()).hexdigest())
            continue
        props = properties(feature)
        payload: list[Any] = [
            feature.get("id"),
            feature.get("geometry"),
            props.get("start_date"),
            props.get("end_date"),
            event_status(feature),
        ]
        if not isinstance(_raw_properties(feature), dict):
            payload.append(repr(_raw_properties(feature)))
        if include_free_text:
            # Both from core_details. v4.x nests them there, and reading
            # road_names off `properties` returns None on every live feature, so
            # a road-name-only edit would not move the content hash at all.
            payload.append(core(feature).get("description"))
            payload.append(core(feature).get("road_names"))
        digests.append(_digest(payload))
    return hashlib.sha256("".join(sorted(digests)).encode()).hexdigest()


def content_hash(features: list[Any]) -> str:
    """Everything, free text included. Drives body snapshots and backoff."""
    return _hash_features(features, include_free_text=True)


def structural_hash(features: list[Any]) -> str:
    """Everything EXCEPT publisher-controlled free text. This is what R5 reads.

    A security property, not an optimisation. `description` and `road_names` are
    third-party free text that Model Armor may block, and a publisher could hold
    its road zones frozen while rotating injected descriptions: every rotation
    would move the content hash, clear the frozen-content signal, and raise its
    standing. Section 6.5's invariant is that injected text can never raise a
    trust score, and text reaching a rule through a hash is still text reaching a
    rule.
    """
    return _hash_features(features, include_free_text=False)


def consistency(features: list[Any], now: datetime.datetime) -> tuple[int, int, int]:
    """R4 inputs: (active, active_with_past_end_date, active_undated).

    Returning `active` alongside the counts matters: R4 over zero active zones is
    NOT_APPLICABLE, not a pass, and a caller given only the numerator cannot tell
    those apart.
    """
    active = past = undated = 0
    for feature in features:
        if not isinstance(feature, dict) or event_status(feature) != "active":
            continue
        active += 1
        parsed = parse_stamp(properties(feature).get("end_date"))
        if parsed is None:
            # Missing, malformed, or date-only. Undated, never past: an
            # unparseable end date is not evidence of a contradiction, and
            # counting it as past would manufacture R4's finding.
            undated += 1
        elif parsed < now:
            past += 1
    return active, past, undated


def feed_header(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    header = doc.get("feed_info") or doc.get("road_event_feed_info") or {}
    return header if isinstance(header, dict) else {}


def feature_list(doc: Any) -> list[Any]:
    """Features, or an empty list. Never raises on a malformed document.

    A document that parses as JSON but is not a feature collection is a
    conformance defect for R3 to score, not a crash. Raising here would lose the
    observation entirely and take the R1 and R2 signals with it.
    """
    if not isinstance(doc, dict):
        return []
    features = doc.get("features")
    return features if isinstance(features, list) else []
