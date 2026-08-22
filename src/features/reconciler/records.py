"""CanonicalZone, its provenance array, and ConflictRecord. Section 7.

`ConflictRecord` is embedded in `CanonicalZone.conflicts[]` rather than being its
own collection, because a conflict has no meaning apart from the zone it is a
conflict about.

Disagreement is preserved rather than silently resolved. A consumer needs to know
that two organizations disagree about a lane closure, and a merged feed that
picked a winner and discarded the loser would be asserting a consensus that does
not exist.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any, Literal

MergeTier = Literal["TIER_1_DETERMINISTIC", "TIER_2_ADJUDICATED", "SINGLETON"]
ConflictType = Literal["FIELD_DISAGREEMENT", "AMBIGUOUS_GROUPING"]
Resolution = Literal["MOST_RECENT_UPDATE_DATE", "EDGE_DROPPED"]
# Which clock `source_update_date` came off. Recorded rather than assumed,
# because the two answer different questions and a conflict resolved on the
# feed's regeneration time is not the same claim as one resolved on the zone's.
# None means the source offered neither, which is distinct from either.
UpdateDateScope = Literal["FEATURE", "FEED"] | None
TrustState = Literal["ADMIT", "WATCH", "QUARANTINE", "NO_ACCESS"]


@dataclass(slots=True)
class SourceRef:
    """One publisher's contribution to a canonical zone."""

    publisher_key: str
    road_event_id: str
    data_source_id: str | None = None
    trust_state: TrustState = "WATCH"
    ingested_at: str = ""
    source_update_date: str | None = None
    update_date_scope: UpdateDateScope = None
    distance_m: float | None = None
    coverage: float | None = None
    merge_tier: MergeTier = "SINGLETON"
    work_zone_fields: dict[str, Any] = dc_field(default_factory=dict)

    @property
    def source_id(self) -> tuple[str, str]:
        return (self.publisher_key, self.road_event_id)

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RejectedPair:
    """A pair inside the distance threshold that symmetric coverage refused.

    Retained rather than only counted. Section 6.6's negative control is the
    claim that distance alone would merge zones the coverage rule keeps apart,
    and a bare count cannot support it: the console has to be able to show which
    pairs were rejected, how close they were, and what their coverage was.

    Counting them and rendering a section titled "negative control" over an
    empty list is worse than having no section, because it reads as a control
    that ran and found nothing.
    """

    left_publisher: str
    left_road_event_id: str
    right_publisher: str
    right_road_event_id: str
    distance_m: float | None
    coverage: float | None

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DroppedEdge:
    """The edge an ambiguous grouping discarded.

    A record carrying only field/value pairs cannot express an edge between two
    source zones, and section 6.6 requires the conflict to name what it dropped.
    Without this the console can say a grouping was ambiguous but not why.
    """

    publisher_key: str
    road_event_id: str
    other_publisher_key: str
    other_road_event_id: str
    distance_m: float | None

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConflictRecord:
    type: ConflictType
    detected_at: str
    field: str | None = None
    values: list[dict[str, Any]] = dc_field(default_factory=list)
    emitted_value: Any = None
    resolution: Resolution | None = None
    dropped_edge: DroppedEdge | None = None

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["dropped_edge"] = self.dropped_edge.to_doc() if self.dropped_edge else None
        return doc


@dataclass(slots=True)
class CanonicalZone:
    canonical_id: str
    geometry: dict[str, Any] | None
    core_details: dict[str, Any]
    start_date: str | None
    end_date: str | None
    sources: list[SourceRef] = dc_field(default_factory=list)
    conflicts: list[ConflictRecord] = dc_field(default_factory=list)
    superseded_by: str | None = None
    supersedes: list[str] = dc_field(default_factory=list)
    first_merged_at: str = ""
    last_seen_cycle: str = ""
    bbox: list[float] | None = None
    geohash_prefixes: list[str] = dc_field(default_factory=list)

    @property
    def publisher_keys(self) -> list[str]:
        return [s.publisher_key for s in self.sources]

    @property
    def merged(self) -> bool:
        return len(self.sources) > 1

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["sources"] = [s.to_doc() for s in self.sources]
        doc["conflicts"] = [c.to_doc() for c in self.conflicts]
        # Written as a field, not left to be derived from `sources`. Firestore
        # cannot filter on the length of an array, so without it the only way to
        # find merged zones is to read every zone and count client-side. That is
        # what the console did: it read a UUID-ordered window of 2,000 out of
        # 8,839 and reported the merged zones inside that window as though they
        # were all of them.
        doc["source_count"] = len(self.sources)
        return doc


def bbox_of(vertices: list[tuple[float, float]]) -> list[float] | None:
    """`[min_lon, min_lat, max_lon, max_lat]`, or None for null geometry.

    None rather than a zero box: a zero box at the origin would place every
    geometry-less zone in the Gulf of Guinea, where the console's viewport query
    would happily return them.
    """
    if not vertices:
        return None
    lons = [v[0] for v in vertices]
    lats = [v[1] for v in vertices]
    return [min(lons), min(lats), max(lons), max(lats)]
