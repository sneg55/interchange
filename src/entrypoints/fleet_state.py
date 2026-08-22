"""Rebuilding the fleet's state from the store at process start. Build plan M3.

Split from `live_fleet` on the file size limit. Everything here runs once, before
the first cycle, and each function answers the same question for a different kind
of state: what does this process need to know that it cannot recompute?

The three failure modes are in `live_fleet`'s docstring. This module is where
they are actually prevented.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import AppError, ErrorIds
from src.features.publisher_agent.observation import Observation
from src.features.reconciler.identity import CanonicalIdentity, CanonicalSourceMapEntry
from src.features.registry_warden.records import PublisherRecord
from src.features.screener.records import ScreeningResult

PUBLISHERS = "publishers"
OBSERVATIONS = "observations"
CANONICAL_SOURCE_MAP = "canonical_source_map"
SCREENING_RESULTS = "screening_results"


def load_screening(store: Any) -> tuple[list[ScreeningResult], int]:
    """Persisted screening verdicts, and how many documents could not be read.

    Returns the count rather than swallowing it. A malformed document is skipped
    rather than fatal, which is the opposite of how a bad publisher record is
    treated below and deliberately so: a dropped publisher silently leaves the
    fleet, while a dropped verdict only means that string is screened again.
    Failing a cycle over a cache entry would trade a cheap re-screen for an
    outage.

    But skipped-and-silent is how a cache quietly stops working, and the symptom
    would be a bill rather than an error. The caller prints the count.
    """
    results: list[ScreeningResult] = []
    skipped = 0
    for doc in store.all(SCREENING_RESULTS):
        try:
            results.append(ScreeningResult.from_doc(doc))
        except TypeError:
            skipped += 1
    return results, skipped


def trim(history: dict[str, list[Observation]], retain: int) -> dict[str, list[Observation]]:
    """Newest `retain` polls per publisher. History is newest-first throughout."""
    return {key: series[:retain] for key, series in history.items()}


def load_state(
    store: Any, retain: int
) -> tuple[dict[str, PublisherRecord], dict[str, list[Observation]], CanonicalIdentity]:
    """Rebuild the fleet's state from the store, as of now.

    Read whole rather than by key for publishers and the source map, and by the
    indexed newest-first query for observations. A fresh store yields empty
    everything, which is the correct reading of a fleet that has never run: the
    warden provisions all forty publishers and every rule that needs history
    says it has none.
    """
    records: dict[str, PublisherRecord] = {}
    for doc in store.all(PUBLISHERS):
        try:
            record = PublisherRecord.from_doc(doc)
        except TypeError as exc:
            # Named, and fatal. Skipping the document would drop a publisher out
            # of the fleet entirely: it stops being polled, its trust state
            # disappears from the board, and nothing anywhere says so. That is
            # this system's cardinal error committed against its own records.
            raise AppError(
                ErrorIds.STORE_BAD_RECORD,
                f"a stored publisher record cannot be rebuilt: {exc}",
                {"doc_id": doc.get("publisher_key") or sorted(doc)[:6]},
            ) from exc
        records[record.publisher_key] = record

    history: dict[str, list[Observation]] = {}
    for key in records:
        docs = store.recent(OBSERVATIONS, key, retain)
        history[key] = [Observation.from_doc(doc) for doc in docs]

    fields = set(CanonicalSourceMapEntry.__slots__)
    entries = [
        CanonicalSourceMapEntry(**{k: v for k, v in doc.items() if k in fields})
        for doc in store.all(CANONICAL_SOURCE_MAP)
    ]
    return records, history, CanonicalIdentity(entries)
