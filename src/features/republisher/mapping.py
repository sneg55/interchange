"""CanonicalZone to a conformant WZDx 4.2 feature. Section 6.8.

`CanonicalZone` is an internal record and is not itself a valid WZDx feature, so
the mapping has to be stated rather than assumed. The required set below is read
from the published 4.2 schema, including the four either/or verification pairs
that `WorkZoneRoadEvent` declares inside an `allOf` branch. Reading the
definition's own `required` key returns nothing and the type looks unconstrained.
It is not, and an earlier revision of the spec missed exactly that.

Nothing here invents a value. A zone missing a required field is excluded and
counted, because publishing a guessed `vehicle_impact` would be inventing a fact
about a lane closure, which is worse than omitting the zone and saying so.
"""

from __future__ import annotations

from typing import Any

from src.features.reconciler.records import CanonicalZone, SourceRef
from src.services.screeners import REDACTION_PLACEHOLDER
from src.utils.timestamps import try_parse

OUTPUT_VERSION = "4.2"
EXTENSION_KEY = "interchange"

# Read from the published 4.2 schema, not assumed.
CORE_REQUIRED = ("event_type", "data_source_id", "direction", "road_names")
WORK_ZONE_REQUIRED = ("start_date", "end_date", "vehicle_impact", "location_method")
# Each pair is satisfied by EITHER member. Declared inside an allOf branch, which
# is why they are easy to miss.
VERIFICATION_PAIRS = (
    ("is_start_date_verified", "start_date_accuracy"),
    ("is_start_position_verified", "beginning_accuracy"),
    ("is_end_date_verified", "end_date_accuracy"),
    ("is_end_position_verified", "ending_accuracy"),
)

# Dropped rather than republished under Interchange's name. At least one live
# feed carries a named individual's contact details here.
DROPPED_FEED_INFO_FIELDS = ("contact_name", "contact_email", "contact_phone")


def _recency_key(source: SourceRef) -> tuple[float, str]:
    """Sort key placing the winner FIRST under a plain ascending sort.

    Most recent `update_date` wins, ties broken by the LOWEST publisher key. The
    timestamp is negated rather than the sort reversed, because reversing would
    also reverse the tie-break and hand the win to the highest key. That is the
    kind of inversion that produces a stable, deterministic, wrong answer, and it
    is exactly what an earlier version of this function did.

    An unparseable date sorts oldest rather than raising, so one malformed
    timestamp cannot decide the winner by accident.
    """
    parsed = try_parse(source.source_update_date)
    return (-(parsed.timestamp() if parsed else float("-inf")), source.publisher_key)


def primary_source(zone: CanonicalZone) -> SourceRef | None:
    """The source whose values win.

    `core_details.data_source_id` is singular but a canonical zone has several
    sources, so one has to be named. Defined for the unconflicted case rather
    than left to fall through it: the most recent `update_date` under the same
    tie-break the conflict rule uses.
    """
    if not zone.sources:
        return None
    return min(zone.sources, key=_recency_key)


def resolve_field(zone: CanonicalZone, name: str) -> tuple[Any, list[dict[str, Any]]]:
    """The emitted value for one field, and the values that lost.

    Recency wins because a work zone is a live thing and the fresher assertion is
    the better default. It is NOT because the fresher publisher is more
    trustworthy; trust is the scorer's job and it is kept out of this.
    """
    offered = [
        {
            "publisher_key": s.publisher_key,
            "value": s.work_zone_fields.get(name),
            "update_date": s.source_update_date,
        }
        for s in sorted(zone.sources, key=_recency_key)
        if s.work_zone_fields.get(name) is not None
    ]
    if not offered:
        return None, []
    return offered[0]["value"], offered[1:]


def missing_required(zone: CanonicalZone) -> list[str]:
    """Required fields no source supplies. Empty means the zone can be emitted."""
    missing = [name for name in WORK_ZONE_REQUIRED if resolve_field(zone, name)[0] is None]
    for pair in VERIFICATION_PAIRS:
        if all(resolve_field(zone, member)[0] is None for member in pair):
            missing.append(" or ".join(pair))
    if not zone.geometry:
        missing.append("geometry")
    if not zone.sources:
        missing.append("sources")
    return missing


def to_feature(
    zone: CanonicalZone,
    blocked_fields: set[str] | None = None,
) -> dict[str, Any]:
    """One WZDx 4.2 RoadEventFeature. Assumes `missing_required` returned empty.

    `blocked_fields` names free-text fields the screener refused. A blocked
    `road_names` is emitted as a redaction placeholder rather than dropped:
    the field is REQUIRED by the schema, so dropping it would fail validation and
    passing it through would break the screening invariant. That is what settles
    the question rather than leaving it to taste.
    """
    blocked = blocked_fields or set()
    primary = primary_source(zone)
    core = dict(zone.core_details)
    core["data_source_id"] = primary.publisher_key if primary else "interchange"
    core["direction"] = core.get("direction") or "unknown"
    names = core.get("road_names") or []
    core["road_names"] = (
        [REDACTION_PLACEHOLDER] if "road_names" in blocked else [str(n) for n in names]
    ) or [REDACTION_PLACEHOLDER]
    if "description" in blocked:
        core["description"] = REDACTION_PLACEHOLDER
    core.setdefault("event_type", "work-zone")

    properties: dict[str, Any] = {"core_details": core}
    conflicts: list[dict[str, Any]] = []
    for name in (*WORK_ZONE_REQUIRED, *(m for pair in VERIFICATION_PAIRS for m in pair)):
        value, losers = resolve_field(zone, name)
        if value is None:
            continue
        properties[name] = value
        if losers:
            conflicts.append(
                {
                    "type": "FIELD_DISAGREEMENT",
                    "field": name,
                    "emitted_value": value,
                    "values": losers,
                    "resolution": "MOST_RECENT_UPDATE_DATE",
                }
            )

    return {
        "id": zone.canonical_id,
        "type": "Feature",
        "geometry": zone.geometry,
        "properties": {
            **properties,
            # Provenance rides in one namespaced extension object. The 4.2 schemas
            # do not define a named extension space; what they do is decline to
            # forbid additional properties, so this survives validation by
            # permission rather than by sanction. An `additionalProperties: false`
            # added upstream would break it, which is why the self-validation gate
            # has to catch that rather than the mapping assuming it away.
            EXTENSION_KEY: {
                "sources": [s.to_doc() for s in zone.sources],
                "conflicts": [c.to_doc() for c in zone.conflicts] + conflicts,
                "supersedes": list(zone.supersedes),
                "first_merged_at": zone.first_merged_at,
            },
        },
    }


def feed_info(
    zones: list[CanonicalZone], update_date: str, publisher: str = "Interchange"
) -> dict[str, Any]:
    """`feed_info`, with one `data_sources[]` entry per contributing publisher.

    Interchange's own output is therefore auditable by exactly the method section
    6.6 applies to others: a reader can see which organizations a zone came from
    without having to take the merged record on trust.
    """
    organizations = {}
    for zone in zones:
        for source in zone.sources:
            organizations.setdefault(source.publisher_key, source.publisher_key.split("|")[0])
    return {
        "update_date": update_date,
        "version": OUTPUT_VERSION,
        "publisher": publisher,
        "data_sources": [
            {"data_source_id": key, "organization_name": org}
            for key, org in sorted(organizations.items())
        ],
    }
