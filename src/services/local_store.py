"""File-backed Store for local development and tests.

Deliberately constrained to what Firestore can actually do given the indexes in
section 19.5. A local store that answered richer queries would let code be
written that works locally and fails in production with a missing-index error at
runtime, which is the worst time to find out.

Not concurrent-safe across processes. It is a development aid; production uses
Firestore, whose transactions back the CanonicalSourceMap guarantee in 19.6.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LocalStore:
    """Store backed by one JSON file per collection.

    Held in memory and flushed on write. Volumes here are small by construction:
    a local run polls a handful of fixture publishers, not 40 live feeds for
    three weeks.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}

    def _path(self, collection: str) -> Path:
        return self.root / f"{collection}.json"

    def _load(self, collection: str) -> dict[str, dict[str, Any]]:
        if collection in self._cache:
            return self._cache[collection]
        path = self._path(collection)
        if path.exists():
            with path.open() as fh:
                self._cache[collection] = json.load(fh)
        else:
            self._cache[collection] = {}
        return self._cache[collection]

    def _flush(self, collection: str) -> None:
        # Write to a temporary file and replace, so an interrupted write cannot
        # leave a half-written collection that later loads as valid JSON.
        path = self._path(collection)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            json.dump(self._cache[collection], fh, indent=2, sort_keys=True, default=str)
        tmp.replace(path)

    def put(self, collection: str, doc_id: str, doc: dict[str, Any]) -> None:
        with self._lock:
            docs = self._load(collection)
            docs[doc_id] = dict(doc)
            self._flush(collection)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load(collection).get(doc_id)

    def append(self, collection: str, doc: dict[str, Any]) -> str:
        doc_id = doc.get("id") or str(uuid.uuid4())
        self.put(collection, doc_id, {**doc, "id": doc_id})
        return doc_id

    def recent(
        self, collection: str, publisher_key: str, limit: int, order_by: str = "polled_at"
    ) -> list[dict[str, Any]]:
        with self._lock:
            docs = self._load(collection).values()
        matching = [d for d in docs if d.get("publisher_key") == publisher_key]
        # Missing sort keys sort last rather than raising, matching Firestore,
        # where a document lacking an indexed field is simply absent from that
        # index rather than an error.
        matching.sort(key=lambda d: (d.get(order_by) is None, d.get(order_by)), reverse=True)
        return matching[:limit]

    def transact(
        self,
        collection: str,
        doc_id: str,
        mutate: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            docs = self._load(collection)
            updated = mutate(docs.get(doc_id))
            docs[doc_id] = updated
            self._flush(collection)
            return updated

    def put_many(self, collection: str, docs: list[tuple[str, dict[str, Any]]]) -> int:
        """Batch write, one flush. Not part of the Store protocol.

        `put` rewrites the whole collection file on every call, so writing a
        cycle's canonical zones one document at a time is quadratic: tens of
        thousands of full rewrites of a file tens of megabytes long, for one
        cycle. The fleet runner uses this where it has a batch.
        """
        with self._lock:
            store = self._load(collection)
            for doc_id, doc in docs:
                store[doc_id] = dict(doc)
            self._flush(collection)
        return len(docs)

    def count(self, collection: str) -> int:
        """Test and console-summary helper. Not part of the Store protocol."""
        with self._lock:
            return len(self._load(collection))

    def all(self, collection: str) -> list[dict[str, Any]]:
        """Every document in a collection. Not part of the Store protocol.

        The fleet runner reads two collections whole at startup rather than by
        key: the publisher records, and the canonical source map that has to
        outlive the process or every canonical ID is reminted on restart.
        Deliberately not offered for the rest, which grow without bound.
        """
        with self._lock:
            return list(self._load(collection).values())
