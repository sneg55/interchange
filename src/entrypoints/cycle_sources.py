"""Which source zones enter the merge, and what they look like when they do.

Split out of `fleet_cycle.py`, which had grown past the point where the ordering
argument at the top of it was readable. These three functions are that argument:

1. `admitted_feeds` decides whose zones are eligible, and reports what it held
   back rather than letting an upstream exclusion vanish from the accounting.
2. `screen_sources` redacts hostile free text and returns COPIES, because the
   Tier 2 adjudicator is handed source records and computing a verdict without
   applying it would leave the model reading what the output redacts.
3. `blocked_for_zones` carries a source-level block onto every canonical zone
   that inherited from it, so a merge cannot launder one.

They are free functions over their inputs. The gate is passed in rather than
reached for, which is what lets each be tested without a cycle around it.
"""

from __future__ import annotations

import copy
from typing import Any

from src.features.reconciler.matching import core, road_event_id
from src.features.registry_warden.records import PublisherRecord
from src.features.screener.gate import ScreeningGate


def carried_body(
    snapshots: Any,
    publisher_key: str,
    history: list[Any],
) -> tuple[list[dict[str, Any]], str | None] | None:
    """What a publisher that was NOT polled this cycle contributes, or None.

    Adaptive backoff decides not to poll; the publisher's zones still belong in
    the merged feed, from the body it last served. Dropping them would let an
    ingress optimisation withdraw a healthy publisher from the output, which is
    far worse than the bandwidth it saves.

    The hash comparison is the same one `Poller._retained_body` makes on a 304
    and exists for the same reason: the retained body is only the one the last
    observation describes if the hashes agree. Serving a body they disagree on
    would put content into the merged feed that no rule was ever evaluated
    against. Where they disagree there is nothing, and `note_missing_bodies`
    reports it rather than letting the publisher vanish.
    """
    snapshot = snapshots.latest(publisher_key)
    measured = history[0].content_hash if history else None
    if snapshot is None or measured is None or snapshot[1] != measured:
        return None
    return snapshot[0].get("features") or [], history[0].update_date


def note_missing_bodies(
    records: dict[str, PublisherRecord],
    raw_feeds: dict[str, list[dict[str, Any]]],
    withheld: dict[str, int],
    reasons: dict[str, str],
    history: dict[str, list[Any]],
) -> None:
    """Account for a publisher that contributed no body at all. In place.

    A publisher can reach the merge pollable, not quarantined, and with nothing
    to contribute: its poll failed, or it answered `304` with nothing retained to
    answer it with, or it was not due and what is retained does not match what
    its last observation measured. `admitted_feeds` cannot see any of those,
    because it only sees the bodies it was handed, so such a publisher left the
    merged feed counted as neither published nor withheld. A silent drop by
    omission, which is the failure this system spends most of its effort
    refusing everywhere else, and adaptive backoff made it reachable on a
    schedule rather than only on an outage.

    The count is the feature count of the most recent poll that measured one. It
    is what the publisher would have contributed as last measured, not a count of
    zones withheld this cycle, and `NO_RETAINED_BODY` is the reason precisely so
    the screen reading it does not present the two as the same thing.
    """
    for key, record in sorted(records.items()):
        if key in raw_feeds or key in withheld or not record.is_pollable:
            continue
        series = history.get(key) or []
        withheld[key] = next((o.feature_count for o in series if o.feature_count is not None), 0)
        reasons[key] = "NO_RETAINED_BODY"


def admitted_feeds(
    records: dict[str, PublisherRecord],
    states: dict[str, str],
    bodies: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], dict[str, str]]:
    """Bodies for publishers whose zones may enter the merge, and what was held back.

    A quarantined publisher is excluded HERE rather than filtered out of the
    output later. Letting its zones into the merge and removing them afterwards
    would leave canonical zones whose only source was withdrawn, and section 6.4
    says those are removed rather than frozen.

    The withheld counts are returned because excluding upstream makes the
    republisher's own `quarantined_sources_only` counter permanently zero: it can
    only count among zones it receives. Reporting nothing withheld while
    withholding thousands of zones would be a silent drop by arithmetic rather
    than by omission. They now travel onto the OutputArtifact for that reason.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    withheld: dict[str, int] = {}
    # WHY each one was held back. The output screen listed a publisher and a
    # count and no reason, so the most consequential fact on it had to be
    # reconstructed by opening each publisher in turn. There are exactly two
    # reasons and they are not interchangeable: a quarantine is a trust verdict
    # and a key-gated feed is not one.
    reasons: dict[str, str] = {}
    for key, record in sorted(records.items()):
        quarantined = states.get(key) == "QUARANTINE"
        if not record.is_pollable or quarantined:
            if key in bodies:
                withheld[key] = len(bodies[key])
                reasons[key] = "QUARANTINE" if quarantined else "NOT_POLLABLE"
            continue
        if key in bodies:
            out[key] = bodies[key]
    return out, withheld, reasons


def screen_sources(
    gate: ScreeningGate, feeds: dict[str, list[dict[str, Any]]], at: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str], set[str]]]:
    """Screen every source zone's free text and RETURN THE REDACTED COPIES.

    Returning verdicts alone was not enough. Section 6.5's three egresses are
    Tier 2 adjudication, notice drafting and the republished feed, and the first
    happens inside reconciliation: the adjudicator is handed both source records.
    Computing a verdict and passing the original on would redact the output while
    the model had already read the payload, which is the invariant failing in the
    one place nobody looks at.

    Copies, never mutation. The observation and its hashes were computed from the
    body as served, and rewriting it underneath them would make the content hash
    describe a document the publisher never published.
    """
    verdicts: dict[tuple[str, str], set[str]] = {}
    safe: dict[str, list[dict[str, Any]]] = {}
    for publisher_key, features in sorted(feeds.items()):
        cleaned = []
        for feature in features:
            if not isinstance(feature, dict):
                cleaned.append(feature)
                continue
            event_id = road_event_id(feature)
            copied = copy.deepcopy(feature)
            details = core(copied)
            fields = set()

            description = gate.screen(
                details.get("description"), publisher_key, "description", at, event_id
            )
            if not description.passed:
                details["description"] = description.text
                fields.add("description")

            names, outcomes = gate.screen_names(
                details.get("road_names"), publisher_key, at, event_id
            )
            if any(not o.passed for o in outcomes):
                details["road_names"] = names
                fields.add("road_names")

            if fields:
                verdicts[(publisher_key, event_id)] = fields
            cleaned.append(copied)
        safe[publisher_key] = cleaned
    return safe, verdicts


def blocked_for_zones(
    zones: list[Any], screened: dict[tuple[str, str], set[str]]
) -> dict[str, set[str]]:
    """Map source-level screening verdicts onto the canonical zones.

    A canonical zone inherits a block from ANY of its sources. Merging a clean
    source with a blocked one and emitting the clean text would let the merge
    launder the block.
    """
    blocked: dict[str, set[str]] = {}
    for zone in zones:
        fields: set[str] = set()
        for source in zone.sources:
            fields |= screened.get((source.publisher_key, source.road_event_id), set())
        if fields:
            blocked[zone.canonical_id] = fields
    return blocked
