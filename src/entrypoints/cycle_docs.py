"""One cycle's output, as the documents the console reads.

`scripts/dump_console_data.py` has built these documents since M6, but it builds
them for a seed: it runs N cycles in memory and writes the terminal state once,
so its publishers are the last cycle's and its observations are every cycle's.
A fleet that runs continuously cannot work that way. It writes what one cycle
produced, then forgets it, because the next cycle may be hours later in a
different process.

The two accumulation shapes are genuinely different and are kept apart. What is
NOT kept apart is the document IDs, which live here and are imported by both:
the seed and the live fleet writing the same cycle under different IDs would
give the console two of everything, and the first place it would show is a fleet
board with eighty publishers on it.

Not written here: screening incidents, which no console screen subscribes to,
and the canonical source map, which is fleet state rather than read model. The
runner persists that separately, because it is what makes canonical IDs survive
a restart.
"""

from __future__ import annotations

import hashlib
from typing import Any

from src.entrypoints.cycle_report import CycleReport
from src.features.registry_warden.records import PublisherRecord
from src.features.trust_scorer.rollup import roll_up_all

# collection -> [(document id, document)]
Documents = dict[str, list[tuple[str, dict[str, Any]]]]


# Firestore's rules, restated here because the ID has to be legal before the
# store sees it. `firestore_store._check_doc_id` enforces the same set and stays
# as the backstop for any ID that reaches it without passing through this file.
MAX_DOC_ID_BYTES = 1500
RESERVED_DOC_IDS = (".", "..")

# What an escaped ID keeps of the original before the hash takes over. Long
# enough that a publisher key and a timestamp both survive intact.
READABLE_BYTES = 200

# Escaped first, so the escape character cannot be forged. `~` is escaped for the
# same reason it is the hash separator below: it must appear in exactly one form.
_ESCAPES = (("%", "%25"), ("/", "%2F"), ("~", "%7E"))


def _escape(value: str) -> str:
    for character, replacement in _ESCAPES:
        value = value.replace(character, replacement)
    return value


def _clip(value: str, budget: int) -> str:
    """Truncate to a byte budget without splitting a character."""
    encoded = value.encode()
    if len(encoded) <= budget:
        return value
    return encoded[:budget].decode(errors="ignore")


def doc_id(value: str) -> str:
    """A Firestore-legal document ID that is still one ID per value.

    Firestore refuses `/`, refuses `.` and `..`, refuses `__like_this__` and caps
    an ID at 1500 bytes. The obvious fix is to replace the offending characters,
    and it is wrong: `a/b` and `a_b` would land on one document and two records
    would merge with nothing anywhere saying so. `firestore_store` says as much
    in its own docstring and then this function did it anyway.

    It mattered because not every ID is ours. Publisher keys and cycle IDs come
    from the registry and the clock, but `CanonicalSourceMap` is keyed by
    `road_event_id`, which is whatever the publisher put in the `id` field. New
    York DOT publishes base64, so the fleet's first cycle against a real database
    tried to write `New York DOT|nysdot|+grr56wpX1KG6U86st06pEWl/gI=` and was
    refused. A publisher can as easily send `..`, an empty string, or two
    kilobytes.

    So: percent-escape, which is reversible and therefore cannot merge two
    values. If the result is legal, it is used as-is, which keeps every ID this
    project already writes byte-identical (no live publisher key contains any
    escaped character; `scripts/run_live_cycle.py` prints the registry that
    settles it). If it is not legal, the ID becomes a readable prefix, a `~`, and
    a digest of the whole value. `~` cannot occur in an escaped ID, so the two
    forms cannot collide either.
    """
    escaped = _escape(value)
    if (
        escaped
        and escaped not in RESERVED_DOC_IDS
        and not (escaped.startswith("__") and escaped.endswith("__"))
        and len(escaped.encode()) <= MAX_DOC_ID_BYTES
    ):
        return escaped
    return f"{_clip(escaped, READABLE_BYTES)}~{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def observation_id(publisher_key: str, polled_at: str) -> str:
    """`(publisher_key, polled_at)`, per section 19.6.

    Deterministic on purpose: a retried write must not duplicate a poll, or the
    consecutive-poll counting that R1 and hysteresis depend on counts one
    outage twice and quarantines a publisher for a network blip at this end.

    `Observation.doc_id` composes the same pair unescaped, and that is what an
    evidence packet stores in `observation_ids`. The two agree for every live
    publisher key and would diverge for one containing `/`, `%` or `~`. That is
    tolerable only because the packet holds a logical reference the console
    prints rather than a document path it resolves: if anything ever looks an
    observation up by that string, it has to come through here.
    """
    return doc_id(f"{publisher_key}@{polled_at}")


def cycle_documents(
    cycle: Any,
    report: CycleReport,
    records: dict[str, PublisherRecord],
    history: dict[str, list[Any]],
) -> Documents:
    """Everything one cycle produced, keyed by collection.

    `history` is the retained window, newest first per publisher. This cycle's
    observation for a polled publisher is its first element; the rest are here
    only for the daily rollup, which needs the window rather than the poll.
    """
    documents: Documents = {
        "publishers": [(doc_id(key), record.to_doc()) for key, record in sorted(records.items())],
        "observations": [],
        "rule_evaluations": [],
        "publisher_daily": [],
        "trust_transitions": [],
        "registry_events": [],
        "canonical_zones": [],
        "reconciliation_snapshots": [],
        "output_artifacts": [],
        "evidence_packets": [],
        "screening_results": [],
        "blocked_text": [],
    }

    # The verdict cache, persisted. Until this existed the gate held it in memory
    # only, so every restart re-screened everything and, because nothing ever
    # loaded it, so did every cycle: roughly 15,900 distinct strings, 96 times a
    # day, against a service billed per token. It was 71 percent of what the
    # system cost to run.
    #
    # `CacheKey.doc_id` carries the policy version, which for Model Armor is a
    # template resource path containing slashes, so it goes through `doc_id`
    # rather than being used raw. Firestore rejects a slash in a document id.
    #
    # Incidents are deliberately NOT persisted here. One is recorded per blocked
    # OCCURRENCE rather than per distinct string, so the volume is unbounded by
    # anything this code controls; it is a publisher's repetition that sets it.
    # They are dropped today as well, so nothing is lost by holding off, but the
    # decision is a real one and belongs somewhere visible.
    screening = cycle.gate.drain()
    for result in screening["results"]:
        documents["screening_results"].append((doc_id(result.key.doc_id), result.to_doc()))
    for blocked in screening["blocked_text"]:
        # Write-once and keyed by hash alone: the same hostile string served by a
        # second publisher is the same string, and re-keying it per policy would
        # lose when it was first seen.
        documents["blocked_text"].append((doc_id(blocked.text_sha256), blocked.to_doc()))

    for evaluation in cycle.evaluations:
        doc = evaluation.to_doc()
        key = doc["publisher_key"]
        documents["rule_evaluations"].append((doc_id(f"{key}@{doc['evaluated_at']}"), doc))
        # Exactly one evaluation exists per publisher polled this cycle, and the
        # poll that produced it is that publisher's newest retained observation.
        # Reading the observation off the evaluation rather than re-deriving the
        # polled set keeps the two from ever disagreeing about who was polled.
        series = history.get(key) or []
        if series:
            observation = series[0].to_doc()
            documents["observations"].append(
                (observation_id(key, observation["polled_at"]), observation)
            )

    for transition in report.transitions:
        documents["trust_transitions"].append(
            (doc_id(f"{transition['publisher_key']}@{transition['at']}"), transition)
        )
    for packet in cycle.packets:
        documents["evidence_packets"].append((doc_id(packet.packet_id), packet.to_doc()))
    for event in cycle.registry_events:
        documents["registry_events"].append(
            (doc_id(f"{event['publisher_key']}@{event['at']}@{event['event']}"), event)
        )

    if cycle.snapshot is not None:
        snapshot = cycle.snapshot.to_doc()
        documents["reconciliation_snapshots"].append((doc_id(snapshot["cycle_id"]), snapshot))
        # Gated on RECONCILING, not on publishing. The reconciliation screen
        # reports what the reconciler decided; withholding is the republisher's
        # separate verdict and belongs on the output screen. Gating here emptied
        # the store the first time ten North Carolina features spelled
        # `direction` with a space.
        documents["canonical_zones"] = [
            (doc_id(zone.to_doc()["canonical_id"]), zone.to_doc()) for zone in cycle.zones
        ]
    if cycle.artifact is not None:
        artifact = cycle.artifact.to_doc()
        documents["output_artifacts"].append((doc_id(artifact["cycle_id"]), artifact))

    rollups = roll_up_all(
        [observation for series in history.values() for observation in series], cycle.evaluations
    )
    for rollup in rollups:
        doc = rollup.to_doc()
        documents["publisher_daily"].append((doc_id(f"{doc['publisher_key']}@{doc['day']}"), doc))

    return documents
