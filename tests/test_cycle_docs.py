"""Document IDs. Section 19.6.

Run with: python3 -m unittest discover -s tests

One collection is keyed by text a publisher controls: `CanonicalSourceMap` is
`(publisher_key, road_event_id)` and `road_event_id` is whatever was in the
feed's `id` field. Everything asserted here is about that: an ID Firestore
accepts, and never two source zones on one document, which would break the
one-to-one mapping section 6.6 rests on.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.cycle_docs import MAX_DOC_ID_BYTES, doc_id
from src.services.firestore_store import _check_doc_id

# What real publishers put in `id`, plus what the rules say is illegal.
HOSTILE = [
    "+grr56wpX1KG6U86st06pEWl/gI=",  # New York DOT, base64. The one that failed.
    "New York DOT|nysdot|+grr56wpX1KG6U86st06pEWl/gI=",
    "a/b",
    "a_b",
    "a%2Fb",
    "..",
    ".",
    "",
    "__internal__",
    "x" * 4000,
    "US-1/US-9 northbound",
    "zone~1",
    "échangé/1",  # multibyte, so a byte-budget clip could split a character
]


class TestDocumentIds(unittest.TestCase):
    def test_every_hostile_id_is_one_firestore_accepts(self):
        for value in HOSTILE:
            with self.subTest(value=value[:40]):
                encoded = doc_id(value)
                # The store's own guard is the oracle: if it refuses, the fleet
                # dies mid-cycle, which is how this defect was found.
                self.assertEqual(_check_doc_id(encoded), encoded)
                self.assertLessEqual(len(encoded.encode()), MAX_DOC_ID_BYTES)

    def test_distinct_values_never_share_a_document(self):
        """The failure this guards is silent. `a/b` and `a_b` mapping to one ID
        merges two publishers' zones under one canonical identity and nothing
        anywhere reports it."""
        encoded = {value: doc_id(value) for value in HOSTILE}
        self.assertEqual(
            len(set(encoded.values())),
            len(HOSTILE),
            f"two values collided: {sorted(encoded.items())}",
        )

    def test_ids_already_written_are_unchanged(self):
        """No live publisher key contains an escaped character, so this encoding
        must be a no-op on everything the console already holds. If it is not,
        the fleet board shows two of every publisher after one deploy."""
        for value in (
            "Utah DOT|udot",
            "Utah DOT|udot@2026-08-14T17:02:18.105213+00:00",
            "cycle-2026-08-14T17:02:18.105213+00:00",
            "St. Charles County|stcharlesco_v4",
            "3f2c1e8a-0b44-4f9a-9a1e-2c3d4e5f6a7b",
            "Iowa DOT|iowadot@2026-08-14",
        ):
            self.assertEqual(doc_id(value), value)

    def test_a_clipped_id_still_names_its_publisher(self):
        """A hashed ID is only debuggable if the readable half survives."""
        encoded = doc_id("New York DOT|nysdot|" + "x" * 4000)
        self.assertTrue(encoded.startswith("New York DOT|nysdot|"))
        self.assertIn("~", encoded)

    def test_the_hash_form_cannot_be_forged_by_a_publisher(self):
        """`~` is escaped on the way in, so a `road_event_id` that looks like a
        hashed ID cannot land on the document of the value it imitates."""
        target = "a/b"
        collision_attempt = doc_id(target)
        self.assertNotEqual(doc_id(collision_attempt), collision_attempt)


if __name__ == "__main__":
    unittest.main()
