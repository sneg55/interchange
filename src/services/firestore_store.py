"""Store backed by Firestore. Sections 8, 19.5 and 19.6.

The production half of the pair whose local half is `local_store.py`. That
module's docstring says it is "deliberately constrained to what Firestore can
actually do given the indexes in section 19.5" so that code written locally
cannot fail in production with a missing-index error at runtime. This module
holds the other end of that promise, and it holds it by reading
`infra/firestore.indexes.json` rather than by trusting a comment: a `recent()`
call whose sort has no deployed index is refused here, on a laptop, instead of
succeeding in tests and failing at 3am against the live fleet.

`google-cloud-firestore` is imported lazily and only when a client is actually
built. The offline path in `scripts/run_cycle.py` needs jsonschema and nothing
else, and section 19.3 records why that separation is load-bearing: the cloud
access question must block M1 and M3 rather than the whole project.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.constants.error_ids import AppError, ErrorIds

INDEX_FILE = Path(__file__).resolve().parents[2] / "infra" / "firestore.indexes.json"

# Firestore's own document-ID rules. Enforced rather than worked around: the
# tempting fix is to sanitise silently, and a store that rewrites `a/b` and
# `a_b` to the same ID merges two publishers' histories without saying so.
MAX_DOC_ID_BYTES = 1500
RESERVED_DOC_IDS = (".", "..")

# Firestore's own limit on operations in one committed batch.
BATCH_LIMIT = 500
# Documents per query when reading a collection whole. Measured against the live
# canonical_source_map: one unbounded query times out, 2000-document pages read
# all 303,962 in 81s.
READ_PAGE_LIMIT = 2000


def _descending_sorts(path: Path = INDEX_FILE) -> set[tuple[str, str]]:
    """(collection, field) pairs a newest-first query is actually indexed for.

    Read from the deployed index definition, so this cannot drift from what
    Firestore will accept. A missing file is an error rather than a permissive
    default: "we could not check which queries are indexed" must not be recorded
    as "every query is indexed", which is the same absence-as-pass this system
    refuses everywhere else.
    """
    try:
        definition = json.loads(path.read_text())
    except OSError as exc:
        raise AppError(
            ErrorIds.STORE_UNAVAILABLE,
            f"cannot read the Firestore index definition at {path}",
            {"error": f"{type(exc).__name__}: {exc}"},
        ) from exc
    sorts: set[tuple[str, str]] = set()
    for index in definition.get("indexes", []):
        collection = index.get("collectionGroup", "")
        for field in index.get("fields", []):
            if field.get("order") == "DESCENDING":
                sorts.add((collection, field.get("fieldPath", "")))
    return sorts


def encode_for_firestore(value: Any) -> Any:
    """Encode any array-of-arrays as a JSON string, recursively.

    **Firestore cannot store an array whose elements are arrays**, and GeoJSON
    `coordinates` is exactly that: a LineString is `[[lon, lat], [lon, lat]]`.
    `CanonicalZone` is declared a Firestore document in spec section 7 with
    `geometry` as a field, so the record as specified cannot be written. The live
    fleet's first cycle against a real database died on it:
    `400 Property geometry contains an invalid nested entity`.

    `console/src/scripts/seed-emulator.ts` hit this first and works around it the
    same way, calling the workaround "not the fix" and saying the fix is a
    decision that belongs in the spec. It now is one, in section 7: this encoding
    is the storage contract, both writers implement it, and the console types
    `coordinates` as `unknown` and does not read it, so nothing decodes today.

    Lossless and reversible by `json.loads`, which is what makes it a contract
    rather than a truncation. It never drops a coordinate, so it is not one of
    the absences section 6.4 is about.
    """
    if isinstance(value, list):
        if any(isinstance(entry, list) for entry in value):
            return json.dumps(value)
        return [encode_for_firestore(entry) for entry in value]
    if isinstance(value, dict):
        return {key: encode_for_firestore(entry) for key, entry in value.items()}
    return value


def _check_doc_id(doc_id: str) -> str:
    if not doc_id:
        raise AppError(ErrorIds.STORE_BAD_DOC_ID, "empty document id")
    if (
        "/" in doc_id
        or doc_id in RESERVED_DOC_IDS
        or (doc_id.startswith("__") and doc_id.endswith("__"))
    ):
        raise AppError(
            ErrorIds.STORE_BAD_DOC_ID,
            f"Firestore will not accept {doc_id!r} as a document id",
            {"doc_id": doc_id},
        )
    if len(doc_id.encode()) > MAX_DOC_ID_BYTES:
        raise AppError(
            ErrorIds.STORE_BAD_DOC_ID,
            f"document id is {len(doc_id.encode())} bytes, over the {MAX_DOC_ID_BYTES} limit",
            {"doc_id": doc_id[:80]},
        )
    return doc_id


class FirestoreStore:
    """Store over a Firestore database.

    Honours `FIRESTORE_EMULATOR_HOST` through the client library, so the same
    class runs against the emulator the console is developed on and against the
    real database, which is the only way the write path gets exercised before it
    matters.
    """

    def __init__(
        self,
        project: str,
        database: str = "(default)",
        client: Any = None,
        indexed_sorts: set[tuple[str, str]] | None = None,
    ) -> None:
        self._client = client or self._build(project, database)
        self._sorts = indexed_sorts if indexed_sorts is not None else _descending_sorts()

    @staticmethod
    def _build(project: str, database: str) -> Any:
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise AppError(
                ErrorIds.STORE_UNAVAILABLE,
                "google-cloud-firestore is not installed; it is in requirements.txt "
                "under the cloud-only section",
            ) from exc
        return firestore.Client(project=project, database=database)

    # ------------------------------------------------------------------- Store

    def put(self, collection: str, doc_id: str, doc: dict[str, Any]) -> None:
        self._client.collection(collection).document(_check_doc_id(doc_id)).set(
            encode_for_firestore(dict(doc))
        )

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        snapshot = self._client.collection(collection).document(_check_doc_id(doc_id)).get()
        return snapshot.to_dict() if snapshot.exists else None

    def append(self, collection: str, doc: dict[str, Any]) -> str:
        # The ID is minted here rather than by Firestore, matching `LocalStore`,
        # so a document carries the same ID in both stores and a seed written
        # from one can be diffed against the other.
        doc_id = doc.get("id") or str(uuid.uuid4())
        self.put(collection, doc_id, {**doc, "id": doc_id})
        return doc_id

    def recent(
        self, collection: str, publisher_key: str, limit: int, order_by: str = "polled_at"
    ) -> list[dict[str, Any]]:
        if (collection, order_by) not in self._sorts:
            raise AppError(
                ErrorIds.STORE_UNAVAILABLE,
                f"no deployed index sorts {collection} by {order_by} descending; "
                f"add one to infra/firestore.indexes.json and deploy it before querying",
                {"collection": collection, "order_by": order_by},
            )
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (
            self._client.collection(collection)
            .where(filter=FieldFilter("publisher_key", "==", publisher_key))
            .order_by(order_by, direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [snapshot.to_dict() for snapshot in query.stream()]

    def transact(
        self,
        collection: str,
        doc_id: str,
        mutate: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        """Read-modify-write under contention. Section 19.6.

        This is what makes the CanonicalSourceMap guarantee real: two cycles
        overlapping must never mint competing canonical IDs for one source zone,
        and a plain get-then-set would let exactly that happen and leave no trace.
        """
        from google.cloud import firestore

        reference = self._client.collection(collection).document(_check_doc_id(doc_id))

        @firestore.transactional
        def _run(transaction: Any) -> dict[str, Any]:
            snapshot = reference.get(transaction=transaction)
            current = snapshot.to_dict() if snapshot.exists else None
            updated = mutate(current)
            transaction.set(reference, updated)
            return updated

        return _run(self._client.transaction())

    # -------------------------------------------------------- beyond the Store

    def put_many(
        self,
        collection: str,
        docs: list[tuple[str, dict[str, Any]]],
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Write many documents in committed chunks. Not part of the Store protocol.

        One cycle writes tens of thousands of canonical zones, and a document at
        a time over the network is not a slow version of this, it is a cycle that
        does not finish inside its own poll interval.

        `WriteBatch` rather than `BulkWriter`, which is the obvious choice and the
        wrong one. Measured against the emulator: 2,000 documents took 3.0s
        through a BulkWriter and 0.1s through chunked batches, and at the real
        cycle size of roughly 50,000 the BulkWriter wrote nothing at all and
        blocked the process indefinitely at 0 percent CPU. A write path that can
        hang is worse than a slow one, because a fleet meant to run for weeks
        stops accruing history and reports nothing at all while it does.

        Each chunk is committed before the next is built, so an interruption
        leaves a prefix written rather than everything or nothing, and `progress`
        makes a long write visible rather than silent.
        """
        if not docs:
            return 0
        target = self._client.collection(collection)
        written = 0
        for start in range(0, len(docs), BATCH_LIMIT):
            chunk = docs[start : start + BATCH_LIMIT]
            batch = self._client.batch()
            for doc_id, doc in chunk:
                batch.set(target.document(_check_doc_id(doc_id)), encode_for_firestore(dict(doc)))
            batch.commit()
            written += len(chunk)
            if progress is not None:
                progress(written, len(docs))
        return written

    def all(self, collection: str) -> list[dict[str, Any]]:
        """Every document in a collection, read a page at a time. Mirrors
        `LocalStore.all`.

        For the two collections the runner reads whole at startup. Streaming
        `canonical_zones` through here would pull tens of thousands of documents
        into memory to answer a question nobody asked.

        Paged because one query cannot scan an unbounded collection: at 303,962
        documents Firestore answered `canonical_source_map` with `503 Query
        timed out`, and the map is append-only, so it only grows.
        """
        docs: list[dict[str, Any]] = []
        cursor = None
        while True:
            query = self._client.collection(collection).order_by("__name__").limit(READ_PAGE_LIMIT)
            if cursor is not None:
                query = query.start_after(cursor)
            page = list(query.stream())
            if not page:
                break
            docs.extend(snapshot.to_dict() for snapshot in page)
            if len(page) < READ_PAGE_LIMIT:
                break
            cursor = page[-1]
        return docs

    def count(self, collection: str) -> int:
        """Mirrors `LocalStore.count`, for cycle summaries and tests."""
        result = self._client.collection(collection).count().get()
        # The aggregation comes back as a list of lists of AggregationResult.
        first = result[0][0] if result and isinstance(result[0], list) else result[0]
        return int(first.value)
