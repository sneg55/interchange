"""One publisher's contribution to a canonical zone. Section 6.6, section 7.

Split out of `cycle.py` when that file crossed the size limit, and it is the
right seam: everything here is a pure function of one feature plus what the
cycle already decided about its publisher. Nothing in this module knows about
grouping, adjudication or identity.

The interesting decision is which `update_date` a source carries, because
`republisher.mapping` ranks on it twice.
"""

from __future__ import annotations

from typing import Any

from .matching import (
    CandidatePair,
    data_source_id,
    date_range,
    feature_update_date,
    road_event_id,
)
from .records import SourceRef, UpdateDateScope


def source_ref(
    publisher_key: str,
    feature: dict[str, Any],
    trust_state: str,
    at: str,
    feed_update_date: str | None,
    pair: CandidatePair | None,
    tier: str,
) -> SourceRef:
    """One `SourceRef`, with its recency taken from the feature where possible.

    `feed_update_date` is the feed header's, which is what the publisher agent
    measures and what R6 scores. It is the fallback here, not the value:
    `resolve_field` and `primary_source` both rank sources by `update_date`, so
    using the header meant a field conflict was settled by which publisher
    regenerated their whole feed most recently rather than by who last touched
    the zone under discussion. Most features carry their own
    (`scripts/probe_update_date_scope.py` prints how many).

    `update_date_scope` records which was used, because "updated at 14:02
    according to the zone" and "according to the feed" are different claims and
    a conflict record that cannot distinguish them cannot be audited. A source
    offering neither keeps None on both, which is the third case rather than a
    silent fallback to the cycle's clock.
    """
    start, end = date_range(feature)
    props = feature.get("properties") or {}
    own = feature_update_date(feature)
    scope: UpdateDateScope = "FEATURE" if own else ("FEED" if feed_update_date else None)
    return SourceRef(
        publisher_key=publisher_key,
        road_event_id=road_event_id(feature),
        data_source_id=data_source_id(feature),
        trust_state=trust_state,  # type: ignore[arg-type]
        ingested_at=at,
        source_update_date=own or feed_update_date,
        update_date_scope=scope,
        distance_m=pair.distance_m if pair else None,
        coverage=pair.coverage if pair else None,
        merge_tier=tier,  # type: ignore[arg-type]
        work_zone_fields={
            k: v for k, v in props.items() if k != "core_details" and not isinstance(v, dict | list)
        }
        | {"start_date": start, "end_date": end},
    )
