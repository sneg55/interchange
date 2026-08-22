"""The two refusals in `FirestoreStore` that do not need a database.

Run with: python3 -m unittest discover -s tests

Everything else in that class is a thin call onto the client library and was
verified by hand against the emulator; there is nothing to assert about it here
that would not be an assertion about a mock. These two are different. Both are
guards this project added on purpose, both are pure, and both fail in ways that
are invisible until they matter:

- a document ID Firestore refuses, sanitised silently, merges two publishers'
  histories under one document
- a `recent()` sort with no deployed composite index works in every local test
  and raises against the live fleet

The index guard reads `infra/firestore.indexes.json`, so it also pins the file:
delete the observations index and this fails here rather than at 3am.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.error_ids import AppError, ErrorIds
from src.services.firestore_store import (
    INDEX_FILE,
    FirestoreStore,
    _check_doc_id,
    _descending_sorts,
    encode_for_firestore,
)


class NoClient:
    """Enough of a client to construct the store. Never called by these tests."""


def store() -> FirestoreStore:
    return FirestoreStore("test-project", client=NoClient())


class TestDocumentIds(unittest.TestCase):
    def test_ids_firestore_refuses_are_refused_here(self):
        for bad in ("a/b", ".", "..", "__internal__", "", "x" * 1501):
            with self.subTest(doc_id=bad), self.assertRaises(AppError) as caught:
                _check_doc_id(bad)
            self.assertEqual(caught.exception.id, ErrorIds.STORE_BAD_DOC_ID)

    def test_real_publisher_and_cycle_ids_pass(self):
        for good in (
            "Utah DOT|udot",
            "Utah DOT|udot@2026-08-14T17:02:18.105213+00:00",
            "cycle-2026-08-14T17:02:18.105213+00:00",
            "St. Charles County|stcharlesco_v4",
        ):
            self.assertEqual(_check_doc_id(good), good)


class TestGeometryEncoding(unittest.TestCase):
    """Spec 7. Firestore refuses an array of arrays, and GeoJSON coordinates are
    one, so a canonical zone as specified cannot be written. The live fleet's
    first cycle against a real database died on exactly this."""

    def test_a_linestring_survives_the_round_trip(self):
        zone = {
            "canonical_id": "abc",
            "geometry": {"type": "LineString", "coordinates": [[-111.9, 40.7], [-111.8, 40.6]]},
        }
        encoded = encode_for_firestore(zone)
        self.assertIsInstance(encoded["geometry"]["coordinates"], str)
        self.assertEqual(
            json.loads(encoded["geometry"]["coordinates"]),
            zone["geometry"]["coordinates"],
            "the encoding must be reversible, or it is a truncation",
        )

    def test_flat_arrays_are_left_alone(self):
        """A bbox and a source list are ordinary arrays and stay queryable."""
        doc = {"bbox": [-111.9, 40.6, -111.8, 40.7], "publisher_keys": ["Utah DOT|udot"]}
        self.assertEqual(encode_for_firestore(doc), doc)

    def test_nesting_is_found_at_any_depth(self):
        deep = {"a": {"b": [{"c": [[1, 2], [3, 4]]}]}}
        self.assertEqual(json.loads(encode_for_firestore(deep)["a"]["b"][0]["c"]), [[1, 2], [3, 4]])

    def test_a_multipolygon_is_encoded_whole(self):
        """Three levels deep. Encoding only the innermost pair would leave an
        array of arrays behind and Firestore would still refuse it."""
        coords = [[[[0.0, 0.0], [1.0, 1.0], [0.0, 0.0]]]]
        encoded = encode_for_firestore({"coordinates": coords})["coordinates"]
        self.assertIsInstance(encoded, str)
        self.assertEqual(json.loads(encoded), coords)


class TestIndexGuard(unittest.TestCase):
    def test_the_sorts_the_rules_need_are_deployed(self):
        """`load_state` reads observations newest-first on every restart, and
        the console reads four more. A missing index here is a fleet that
        cannot reload its own history, or a console screen that dies on a query
        every other screen makes successfully.

        `publisher_daily` is in this list because it was NOT, and the first
        person to open a publisher detail page against the real database got
        "The query requires an index" where the rollup should have been. The
        index was declared ASCENDING and the only query that reads it asks for
        the most recent N days."""
        sorts = _descending_sorts()
        for collection, field in (
            ("observations", "polled_at"),
            ("rule_evaluations", "evaluated_at"),
            ("trust_transitions", "at"),
            ("registry_events", "at"),
            ("publisher_daily", "day"),
        ):
            self.assertIn((collection, field), sorts, f"{collection} by {field}")

    def test_an_unindexed_sort_is_refused_rather_than_attempted(self):
        """A field nothing sorts on, deliberately. This test used
        `publisher_daily` by `day`, which pinned a real gap in place as though
        it were intentional: the console sorts on exactly that, descending, and
        the index was declared ascending. A guard test asserting that a needed
        index is absent is worse than no test, because it makes the fix look
        like a regression."""
        with self.assertRaises(AppError) as caught:
            store().recent("observations", "Utah DOT|udot", 5, order_by="latency_ms")
        self.assertEqual(caught.exception.id, ErrorIds.STORE_UNAVAILABLE)

    def test_a_missing_index_file_is_an_error_not_a_permissive_default(self):
        """ "We could not check which queries are indexed" must not be recorded
        as "every query is indexed"."""
        with self.assertRaises(AppError) as caught:
            _descending_sorts(INDEX_FILE.parent / "does-not-exist.json")
        self.assertEqual(caught.exception.id, ErrorIds.STORE_UNAVAILABLE)


class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict):
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeQuery:
    """A collection that refuses an unbounded scan, the way the live one did.

    Firestore answered `503 Query timed out. Please try either limiting the
    entities scanned` once `canonical_source_map` passed 300k documents. A fake
    that streams everything happily would have passed against the code that
    took the fleet down for two days.
    """

    def __init__(self, docs, scan_ceiling, page=None, after=None):
        self._docs = docs
        self._scan_ceiling = scan_ceiling
        self._page = page
        self._after = after

    def order_by(self, field):
        if field != "__name__":
            raise AssertionError(f"paging must order by document name, got {field}")
        return self

    def limit(self, count):
        return FakeQuery(self._docs, self._scan_ceiling, page=count, after=self._after)

    def start_after(self, snapshot):
        return FakeQuery(self._docs, self._scan_ceiling, page=self._page, after=snapshot.id)

    def stream(self):
        rows = self._docs
        if self._after is not None:
            rows = [d for d in rows if d.id > self._after]
        if self._page is None or self._page > self._scan_ceiling:
            raise RuntimeError("503 Query timed out. Please try limiting the entities scanned")
        return iter(rows[: self._page])


class FakeClient:
    def __init__(self, docs, scan_ceiling):
        self._docs = docs
        self._scan_ceiling = scan_ceiling

    def collection(self, name):
        return FakeQuery(self._docs, self._scan_ceiling)


class TestReadingACollectionWhole(unittest.TestCase):
    """The outage of 2026-08-20. `all()` streamed `canonical_source_map` in one
    query; at 303,962 documents Firestore timed out, and the client library's
    retry path raised `AttributeError` instead of retrying, so the runner died
    in `LiveFleet.__init__` and crash-looped for 49 hours."""

    def _store(self, count: int, scan_ceiling: int) -> FirestoreStore:
        docs = [FakeSnapshot(f"{i:06d}", {"n": i}) for i in range(count)]
        return FirestoreStore("test-project", client=FakeClient(docs, scan_ceiling))

    def test_a_collection_too_large_to_scan_at_once_is_still_read_whole(self):
        got = self._store(4_500, scan_ceiling=2_000).all("canonical_source_map")
        self.assertEqual([d["n"] for d in got], list(range(4_500)))

    def test_no_document_is_dropped_or_repeated_at_a_page_boundary(self):
        # Exactly divisible by the page size is where an off-by-one shows up.
        got = self._store(4_000, scan_ceiling=2_000).all("canonical_source_map")
        self.assertEqual([d["n"] for d in got], list(range(4_000)))

    def test_a_collection_smaller_than_one_page_needs_one_query(self):
        got = self._store(7, scan_ceiling=2_000).all("publishers")
        self.assertEqual([d["n"] for d in got], list(range(7)))

    def test_an_empty_collection_reads_as_empty(self):
        self.assertEqual(self._store(0, scan_ceiling=2_000).all("publishers"), [])


if __name__ == "__main__":
    unittest.main()
