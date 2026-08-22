"""Deterministic signals over a feed body. Section 6.2.

Run with: python3 -m unittest discover -s tests -v

These are the functions the deployed agent and the local poller share. A defect
here is invisible in both: the content hash silently dropped `event_status` on
every real 4.x feed because the only test fixture nested it where the live feeds
do not.
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent import signals
from src.features.publisher_agent.agent import PublisherAgent

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)


def feature(fid="a", status="active", end="2020-01-01T00:00:00Z", geom=None, nested=False):
    """A road event feature.

    `nested` chooses where `event_status` sits. Default is FALSE because that is
    where every live 4.x feed in the snapshot puts it: a sibling of
    `core_details` under `properties`. A helper that only ever nested it hid a
    real defect, in which the content hash read None for the field on all real
    data and an active-to-completed edit produced an identical digest.
    """
    core = {"description": "lane closure"}
    props = {"start_date": "2019-01-01T00:00:00Z", "end_date": end, "core_details": core}
    (core if nested else props)["event_status"] = status
    return {
        "id": fid,
        "geometry": geom or {"type": "LineString", "coordinates": [[-111.0, 40.0], [-111.1, 40.1]]},
        "properties": props,
    }


class TestSignals(unittest.TestCase):
    def test_content_hash_ignores_feature_order(self):
        """Publishers do not hold feature order stable. Hashing the document
        would report churn on every reorder and make R5 useless."""
        a, b = feature("a"), feature("b")
        self.assertEqual(PublisherAgent.content_hash([a, b]), PublisherAgent.content_hash([b, a]))

    def test_content_hash_is_sensitive_to_a_real_edit(self):
        changed = feature("a")
        changed["properties"]["end_date"] = "2021-01-01T00:00:00Z"
        self.assertNotEqual(
            PublisherAgent.content_hash([feature("a")]),
            PublisherAgent.content_hash([changed]),
        )

    def test_consistency_counts_past_end_dates_among_active_only(self):
        features = [
            feature("a", "active", "2020-01-01T00:00:00Z"),
            feature("b", "active", "2030-01-01T00:00:00Z"),
            feature("c", "completed", "2020-01-01T00:00:00Z"),
        ]
        active, past, undated = PublisherAgent.consistency(features, NOW)
        self.assertEqual((active, past, undated), (2, 1, 0))

    def test_undated_active_zones_are_separate_from_past(self):
        """Section 6.4: an undated active zone is reported separately and never
        contributes to the contradiction percentage."""
        features = [feature("a", "active", None), feature("b", "active", "not a date")]
        active, past, undated = PublisherAgent.consistency(features, NOW)
        self.assertEqual((active, past, undated), (2, 0, 2))

    def test_zero_active_is_reported_as_zero_not_hidden(self):
        """R4 over zero active zones is NOT_APPLICABLE. A caller given only the
        numerator cannot tell that from a clean pass."""
        active, past, _ = PublisherAgent.consistency([feature("a", "completed")], NOW)
        self.assertEqual((active, past), (0, 0))

    def test_content_hash_sees_event_status_wherever_the_version_puts_it(self):
        """Every live 4.x feed carries event_status beside core_details, not in
        it. Reading only core_details returned None on all real data, so an
        active-to-completed edit hashed identically and R5 saw no churn."""
        for nested in (False, True):
            before = feature("a", "active", nested=nested)
            after = feature("a", "completed", nested=nested)
            self.assertNotEqual(
                signals.content_hash([before]), signals.content_hash([after]), f"{nested=}"
            )

    def test_consistency_sees_event_status_wherever_the_version_puts_it(self):
        for nested in (False, True):
            active, _, _ = signals.consistency([feature("a", "active", nested=nested)], NOW)
            self.assertEqual(active, 1, f"{nested=}")

    def test_a_bare_date_end_date_is_undated_not_past(self):
        """`2020-01-01` is not a valid WZDx timestamp. Read as midnight UTC it
        would count as a past end date and manufacture R4's finding."""
        _, past, undated = signals.consistency([feature("a", "active", "2020-01-01")], NOW)
        self.assertEqual((past, undated), (0, 1))

    def test_a_malformed_feature_hashes_instead_of_crashing(self):
        """A document that parses as JSON but is not a feature collection is a
        conformance defect for R3 to score, not a crash that loses the whole
        observation and the R1 and R2 signals with it."""
        self.assertIsInstance(signals.content_hash([feature("a"), "not a feature", None]), str)
        self.assertEqual(signals.consistency(["not a feature", 7], NOW), (0, 0, 0))
        self.assertEqual(signals.feature_list({"features": "nope"}), [])
        self.assertEqual(signals.feature_list("nope"), [])

    def test_refuses_a_non_http_url_at_construction(self):
        """The URL comes from the federal registry, which is third-party data.
        Unchecked, a file:// entry would be fetched under this publisher's own
        identity."""
        with self.assertRaises(ValueError):
            PublisherAgent("Test|t", "file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
