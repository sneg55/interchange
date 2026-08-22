"""Which clock a source's recency comes off. Sections 6.6 and 6.8.

Run with: python3 -m unittest discover -s tests

`republisher.mapping` ranks sources by `source_update_date` twice: to pick the
primary source and to settle every field disagreement. WZDx publishes two
different timestamps that both answer to that name, and the reconciler was using
the wrong one for every source: `feed_info.update_date`, which says when the
publisher last regenerated the whole feed, rather than the feature's own
`core_details.update_date`, which says when this work zone last changed.

Most features carry their own. `scripts/probe_update_date_scope.py` prints how
many, over the committed fixtures and over whatever the live runner captured.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.records import CanonicalZone
from src.features.reconciler.source_refs import source_ref
from src.features.republisher.mapping import primary_source, resolve_field

AT = "2026-08-14T12:00:00+00:00"
FEED_STAMP = "2026-08-14T11:59:00Z"


def feature(road_event_id: str, own_update: str | None, lanes: int) -> dict:
    core: dict = {"road_names": ["I-15"], "direction": "northbound"}
    if own_update is not None:
        core["update_date"] = own_update
    return {
        "id": road_event_id,
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[-111.9, 40.7], [-111.8, 40.6]]},
        "properties": {"core_details": core, "vehicle_impact": "all-lanes-closed", "lanes": lanes},
    }


def ref(publisher: str, road_event_id: str, own_update: str | None) -> object:
    return source_ref(
        publisher, feature(road_event_id, own_update, 2), "ADMIT", AT, FEED_STAMP, None, "SINGLETON"
    )


class TestUpdateDateScope(unittest.TestCase):
    def test_the_features_own_date_wins_over_the_feeds(self):
        source = ref("Utah DOT|udot", "z-1", "2026-08-13T09:00:00Z")
        self.assertEqual(source.source_update_date, "2026-08-13T09:00:00Z")
        self.assertEqual(source.update_date_scope, "FEATURE")

    def test_the_feed_date_is_the_fallback_and_says_so(self):
        source = ref("Missouri DOT|modot", "z-2", None)
        self.assertEqual(source.source_update_date, FEED_STAMP)
        self.assertEqual(
            source.update_date_scope,
            "FEED",
            "a conflict settled on the feed's regeneration time is a different "
            "claim from one settled on the zone's, and has to be auditable as one",
        )

    def test_neither_is_neither(self):
        """Absence is not the cycle's clock and not the feed's. A source that
        offered no date must not out-rank one that did."""
        source = source_ref("X|x", feature("z-3", None, 2), "ADMIT", AT, None, None, "SINGLETON")
        self.assertIsNone(source.source_update_date)
        self.assertIsNone(source.update_date_scope)


class TestItChangesWhoWins(unittest.TestCase):
    """The point of the fix. Both publishers regenerated their feed at the same
    moment, so on the feed header this conflict is a coin toss decided by the
    publisher-key tie-break. On the features' own dates it is not close."""

    def zone(self) -> CanonicalZone:
        stale = source_ref(
            "A publisher|a",
            feature("z-a", "2026-01-01T00:00:00Z", 4),
            "ADMIT",
            AT,
            FEED_STAMP,
            None,
            "TIER_1_DETERMINISTIC",
        )
        fresh = source_ref(
            "Z publisher|z",
            feature("z-z", "2026-08-14T08:00:00Z", 1),
            "ADMIT",
            AT,
            FEED_STAMP,
            None,
            "TIER_1_DETERMINISTIC",
        )
        return CanonicalZone(
            canonical_id="c-1",
            geometry=None,
            core_details={},
            start_date=None,
            end_date=None,
            sources=[stale, fresh],
        )

    def test_the_recently_updated_zone_supplies_the_value(self):
        value, losers = resolve_field(self.zone(), "lanes")
        self.assertEqual(value, 1)
        self.assertEqual([loser["publisher_key"] for loser in losers], ["A publisher|a"])

    def test_the_recently_updated_zone_is_the_primary_source(self):
        self.assertEqual(primary_source(self.zone()).publisher_key, "Z publisher|z")


if __name__ == "__main__":
    unittest.main()
