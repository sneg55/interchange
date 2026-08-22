"""The fleet running against live feeds and a durable store. Build plan M3.

Every cycle this project has run so far read the checksummed snapshot and kept
its state in one process. That is the right shape for a seed and the wrong shape
for the thing section 13 calls the milestone that cannot be recovered later: a
reliability history only counts if the polls in it happened, against publishers
who did not know they were being watched, on days that have actually passed.

Three pieces of state have to survive both the cycle and the process, and each
one has a specific failure if it does not:

- **Publisher records.** They carry `latching_rule_ids` and the clean-poll
  streak. Lose them and every quarantined publisher silently returns to WATCH on
  restart, which is a gate that forgets what it decided.
- **Observations.** The rules read a window, not a poll. R1 counts consecutive
  failures and R5 asks what changed across 24 hours; both answer
  INSUFFICIENT_HISTORY or NOT_APPLICABLE against an empty history, correctly,
  and a runner that threw the window away would make that read on the board as
  forty publishers nobody can measure.
- **The canonical source map.** Lose it and every canonical ID is reminted, so
  every downstream consumer sees the entire merged feed churn: exactly what
  section 6.6 exists to prevent.

Retention is derived from R5's own window rather than chosen, so a cadence
change cannot quietly starve a rule.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from collections.abc import Callable
from typing import Any

from src.entrypoints.cycle_docs import Documents, cycle_documents, doc_id
from src.entrypoints.cycle_report import CycleReport
from src.entrypoints.fleet_state import (
    CANONICAL_SOURCE_MAP,
    load_screening,
    load_state,
    trim,
)
from src.features.trust_scorer.churn import R5_WINDOW_SECONDS

CANONICAL_ZONES = "canonical_zones"
# Reported beside the write counts rather than inferred from them. A collection
# that wrote 4,000 of 50,000 documents has to say which it is: a throttle working
# or a store failing halfway.
ZONES_UNCHANGED = "canonical_zones_unchanged"

# Retained beyond what R5 strictly needs. The window is measured in time and the
# retention in polls, so a single slow cycle would otherwise drop the oldest
# poll out of a window that still reaches back to it.
RETENTION_MARGIN_POLLS = 4


def retained_polls(poll_interval_seconds: int) -> int:
    """How many observations per publisher the rules actually need.

    Derived from R5's window rather than typed in. Retain fewer polls than the
    window holds and R5 drops to INSUFFICIENT_HISTORY on a fleet with plenty of
    history, which reads on the board as a publisher nobody could measure rather
    than as a runner that discarded the evidence.
    """
    return math.ceil(R5_WINDOW_SECONDS / max(1, poll_interval_seconds)) + RETENTION_MARGIN_POLLS


# What changes on a zone document every cycle no matter what any publisher
# published. Excluded from the content hash, and therefore permitted to go stale
# on a document the throttle does not rewrite.
ZONE_BOOKKEEPING = ("last_seen_cycle",)
SOURCE_BOOKKEEPING = ("ingested_at",)


def zone_content_hash(doc: dict[str, Any]) -> str:
    """What a canonical zone says, with the per-cycle bookkeeping taken out.

    Hashing the document whole would make every zone differ every cycle, because
    `last_seen_cycle` and each source's `ingested_at` carry this cycle's clock.
    The throttle would then save nothing and would look like it worked.
    """
    body = {key: value for key, value in doc.items() if key not in ZONE_BOOKKEEPING}
    if "sources" in body:
        body["sources"] = [
            {key: value for key, value in source.items() if key not in SOURCE_BOOKKEEPING}
            for source in body["sources"]
        ]
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def write_cycle(
    store: Any,
    documents: Documents,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, int]:
    """Persist one cycle. Returns what was written, per collection.

    Counts are returned rather than logged here so the caller can print them
    beside the cycle report. A runner that reported "cycle complete" while a
    collection silently wrote nothing is the shape of failure this system is
    least able to notice from the outside, and it is not hypothetical: the first
    Firestore write path enqueued 50,000 zones into a BulkWriter and blocked
    forever, having written every small collection first, so the store looked
    healthy from every angle except the one that mattered.

    `progress` is threaded down to the store for the same reason: the collection
    that takes minutes should say so while it takes them.
    """
    written: dict[str, int] = {}
    put_many = getattr(store, "put_many", None)
    for collection, rows in documents.items():
        if not rows:
            written[collection] = 0
            continue
        if put_many is None:
            for doc_id, doc in rows:
                store.put(collection, doc_id, doc)
            written[collection] = len(rows)
        elif progress is None:
            written[collection] = put_many(collection, rows)
        else:
            written[collection] = put_many(
                collection,
                rows,
                progress=lambda done, total, name=collection: progress(name, done, total),
            )
    return written


class LiveFleet:
    """One long-lived runner over a `FleetCycle`.

    State is loaded from the store once, at construction, and carried in memory
    afterwards. That is the same code path whether this is the fleet's first
    cycle ever or the first after a restart, which is the only way the restart
    path gets exercised often enough to be trusted.
    """

    def __init__(self, store: Any, cycle: Any, poll_interval_seconds: int) -> None:
        self._store = store
        self._cycle = cycle
        self._interval = poll_interval_seconds
        self.retain = retained_polls(poll_interval_seconds)
        self.records, self.history, self.identity = load_state(store, self.retain)
        # The identity map is injected into the cycle rather than owned by it,
        # precisely so it can be loaded from the store here. A cycle holding its
        # own would mint fresh UUIDs on every process start.
        self._cycle.identity = self.identity
        self._persisted_entries = len(self.identity.new_entries)
        # The screening verdict cache, warmed from the store. `ScreeningGate.load`
        # has always accepted one and nothing ever passed it, so the cache lived
        # and died with the process: every restart re-screened everything, and so
        # did every cycle, because a verdict reached in one was never available to
        # the next.
        #
        # `load` filters to the CURRENT policy and model version, so a Model Armor
        # template edit invalidates the lot without deleting anything: the old
        # verdicts stay for audit and simply stop matching. That is also why this
        # reads the whole collection rather than querying, and why the collection
        # needs a TTL policy before it has been running for months.
        warmed, unreadable = load_screening(store)
        self._cycle.gate.load(warmed)
        self.screening_cached = len(warmed)
        self.screening_unreadable = unreadable
        # Empty on every process start, so the first cycle after a restart
        # rewrites every zone. That is the safe direction: a zone whose stored
        # content this process has not itself written is written again, never
        # assumed current. Held in memory rather than in the store, because
        # persisting 50,000 hashes per cycle is the write this exists to avoid.
        self._zone_hashes: dict[str, str] = {}

    def run_once(
        self,
        now: datetime.datetime | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> tuple[CycleReport, dict[str, int]]:
        # The fleet's own cadence goes down with the call. Adaptive backoff has
        # to know how often the next chance to poll comes round, or it defers a
        # publisher onto a cycle that lands well past its interval.
        report, records, history = self._cycle.run(
            self.records, self.history, now=now, cycle_interval_seconds=self._interval
        )
        history = trim(history, self.retain)
        documents = cycle_documents(self._cycle, report, records, history)
        documents[CANONICAL_ZONES], pending, unchanged = self._plan_zone_writes(
            documents[CANONICAL_ZONES]
        )
        written = write_cycle(self._store, documents, progress)
        # Recorded only once the write has returned. Marking a zone written
        # before the batch commits would let a failed cycle skip those zones on
        # every cycle afterwards, and the store would hold stale documents that
        # nothing would ever correct.
        self._zone_hashes.update(pending)
        written[ZONES_UNCHANGED] = unchanged
        written[CANONICAL_SOURCE_MAP] = self._persist_new_entries(progress)
        self.records, self.history = records, history
        return report, written

    def _plan_zone_writes(
        self, rows: list[tuple[str, dict[str, Any]]]
    ) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str], int]:
        """Drop zones whose content this process already wrote.

        Canonical zones are the fleet's dominant write by two orders of
        magnitude: roughly 50,000 documents a cycle against 27 observations, and
        section 8 never sized them. Most of them do not change between cycles,
        because most publishers do not change their feed between cycles, which is
        the same fact R5 measures.

        The cost is that a skipped document keeps the `last_seen_cycle` and
        `ingested_at` it was last written with. Those fields therefore mean "as
        of the cycle that last wrote this zone", which section 7 now says.
        `reconciliation_snapshots` remains the authority for what a given cycle
        actually saw, and it is written every cycle.
        """
        write: list[tuple[str, dict[str, Any]]] = []
        pending: dict[str, str] = {}
        unchanged = 0
        for zone_id, doc in rows:
            digest = zone_content_hash(doc)
            if self._zone_hashes.get(zone_id) == digest:
                unchanged += 1
                continue
            pending[zone_id] = digest
            write.append((zone_id, doc))
        return write, pending, unchanged

    def _persist_new_entries(self, progress: Callable[[str, int, int], None] | None = None) -> int:
        """Only the IDs minted since the last cycle.

        `CanonicalIdentity.new_entries` accumulates for the life of the object,
        so rewriting all of it every cycle would mean writing tens of thousands
        of unchanged documents at the fleet's poll cadence. The entries are
        immutable once minted, so a watermark is enough.
        """
        fresh = self.identity.new_entries[self._persisted_entries :]
        if not fresh:
            return 0
        # Through `doc_id` like every other collection. The entry's own property
        # is `(publisher_key, road_event_id)` joined, and the second half of that
        # is publisher-controlled text: this is the one collection whose IDs a
        # publisher writes, and the one that refused to be written.
        rows = [(doc_id(entry.doc_id), entry.to_doc()) for entry in fresh]
        # Through `write_cycle` rather than its own copy of the batching and
        # fallback, so there is one place that knows how a collection is written.
        written = write_cycle(self._store, {CANONICAL_SOURCE_MAP: rows}, progress)
        self._persisted_entries = len(self.identity.new_entries)
        return written[CANONICAL_SOURCE_MAP]
