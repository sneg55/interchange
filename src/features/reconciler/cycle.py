"""One reconciliation cycle, end to end. Section 6.6.

Takes each admitted publisher's features and produces canonical zones plus a
snapshot of what happened. The adjudicator is injected: Tier 2 is the only place
a model appears in this path, and passing it in means the whole cycle runs
deterministically in tests with no model at all.

Components are recomputed from scratch each cycle. Only the identity mapping
persists, which is what keeps canonical IDs stable while group membership moves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any, Protocol

from .geometry import vertices
from .grouping import Group, build_groups, dropped_pairs
from .identity import CanonicalIdentity
from .matching import (
    MATCH_THRESHOLD_M,
    CandidatePair,
    candidate_pairs,
    core,
    date_range,
    road_event_id,
)
from .records import CanonicalZone, ConflictRecord, DroppedEdge, bbox_of
from .source_refs import source_ref


class Adjudicator(Protocol):
    """Tier 2. Gemini in production, a stub in tests.

    Returns DUPLICATE, DISTINCT or UNSURE. No confidence score is requested: a
    scalar from a model invites a threshold, and a threshold would put the model
    back in the gate path section 2 keeps it out of.
    """

    def adjudicate(
        self, left: dict[str, Any], right: dict[str, Any], pair: CandidatePair
    ) -> str: ...


@dataclass(slots=True)
class ReconciliationSnapshot:
    cycle_id: str
    at: str
    group_count: int
    conflict_count: int
    # Groups with more than one source. `group_count` includes singletons, so it
    # cannot answer "how many zones did we actually merge", and the console was
    # left computing that from whatever slice of `canonical_zones` it had
    # loaded: a complete-looking count of an arbitrary subset.
    merged_zone_count: int = 0
    tier_counts: dict[str, int] = dc_field(default_factory=dict)
    excluded_counts: dict[str, int] = dc_field(default_factory=dict)
    adjudication_counts: dict[str, int] = dc_field(default_factory=dict)
    # A bounded sample of the pairs the coverage rule refused, plus the total, so
    # the negative control renders measured pairs rather than an assertion.
    rejected_pairs: list[dict[str, Any]] = dc_field(default_factory=list)
    rejected_pair_total: int = 0
    output_validation_result: dict[str, Any] | None = None
    output_feed_uri: str | None = None
    grouping_uri: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CycleResult:
    zones: list[CanonicalZone]
    snapshot: ReconciliationSnapshot
    pairs: list[CandidatePair]


def _drop_duplicate_ids(
    features_by_publisher: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Keep the first feature per `(publisher, road_event_id)`, count the rest.

    Section 6.6's canonical mapping is one to one on `(publisher_key,
    road_event_id)`. A feed carrying the same `id` on two features breaks that:
    both features collapse onto one map key, the second group to reach it finds
    the id already claimed by the first, and `assign_all` mints it a fresh one.
    Every cycle. Measured on the snapshot, Kentucky (8 of 293 features) and
    Washington State (17 of 588) churned 25 canonical ids per cycle, forever,
    which is precisely the failure canonical identity exists to prevent.

    Dropping rather than renaming, because the two features are the publisher's
    own claim to be one road event: 4.2 requires `id` unique within a feed, and
    Interchange inventing a distinguishing suffix would manufacture a second
    work zone that the publisher never asserted. Keeping the FIRST in feed order
    over the last, because the choice has to be deterministic and nothing in the
    feed says which copy is the better one.

    Counted and reported, never silent. It is a data-quality fact about a
    publisher and it belongs on the screen with the other exclusions.
    """
    kept: dict[str, list[dict[str, Any]]] = {}
    dropped: dict[str, int] = {}
    for publisher_key, features in features_by_publisher.items():
        seen: set[str] = set()
        keep: list[dict[str, Any]] = []
        for feature in features:
            road_event_id = feature.get("id")
            # A feature with no id at all is left alone here. It has no map key
            # to collide on, and the republisher's required-field check is what
            # decides its fate.
            if road_event_id is None:
                keep.append(feature)
                continue
            if road_event_id in seen:
                dropped[publisher_key] = dropped.get(publisher_key, 0) + 1
                continue
            seen.add(road_event_id)
            keep.append(feature)
        kept[publisher_key] = keep
    return kept, dropped


class ReconciliationCycle:
    def __init__(
        self,
        identity: CanonicalIdentity,
        adjudicator: Adjudicator | None = None,
        threshold: float = MATCH_THRESHOLD_M,
    ) -> None:
        self._identity = identity
        self._adjudicator = adjudicator
        self._threshold = threshold

    def run(
        self,
        features_by_publisher: dict[str, list[dict[str, Any]]],
        trust_states: dict[str, str],
        cycle_id: str,
        at: str,
        update_dates: dict[str, str | None] | None = None,
    ) -> CycleResult:
        """One cycle. `features_by_publisher` should already exclude quarantined
        publishers; what remains is admitted or watched."""
        features_by_publisher, duplicate_ids = _drop_duplicate_ids(features_by_publisher)
        flat = [
            (publisher_key, feature)
            for publisher_key, features in sorted(features_by_publisher.items())
            for feature in features
            if vertices(feature)
        ]
        pairs, excluded, rejected = candidate_pairs(features_by_publisher, self._threshold)
        excluded["duplicate_source_id"] = sum(duplicate_ids.values())
        accepted, adjudications = self._adjudicate(pairs, flat)
        groups = build_groups(pairs, accepted, all_indices=list(range(len(flat))))
        zones = self._build_zones(groups, flat, trust_states, cycle_id, at, update_dates or {})

        tier_counts = {"TIER_1_DETERMINISTIC": 0, "TIER_2_ADJUDICATED": 0}
        for pair in pairs:
            if pair.tier in tier_counts:
                tier_counts[pair.tier] += 1
        snapshot = ReconciliationSnapshot(
            cycle_id=cycle_id,
            at=at,
            group_count=len(groups),
            conflict_count=sum(len(z.conflicts) for z in zones),
            merged_zone_count=sum(1 for z in zones if len(z.sources) > 1),
            tier_counts=tier_counts,
            excluded_counts=excluded,
            adjudication_counts=adjudications,
            rejected_pairs=[p.to_doc() for p in rejected],
            rejected_pair_total=excluded["rejected_by_coverage"],
        )
        return CycleResult(zones=zones, snapshot=snapshot, pairs=pairs)

    # ------------------------------------------------------------------ detail

    def _adjudicate(
        self, pairs: list[CandidatePair], flat: list[tuple[str, dict[str, Any]]]
    ) -> tuple[set[tuple[int, int]], dict[str, int]]:
        """Tier 2 only. Tier 1 never reaches a model.

        UNSURE resolves to DISTINCT for the merge while being counted separately.
        A model that cannot tell must not be pushed into guessing: a wrong merge
        hides a real closure, while a wrong split merely double counts.
        """
        counts = {"DUPLICATE": 0, "DISTINCT": 0, "UNSURE": 0, "NOT_RUN": 0}
        accepted: set[tuple[int, int]] = set()
        for pair in sorted(pairs, key=lambda p: p.sort_key):
            if pair.tier != "TIER_2_ADJUDICATED":
                continue
            if self._adjudicator is None:
                # No adjudicator configured is "not decided", never "duplicate".
                # Defaulting to a merge would hide a real closure on the strength
                # of a call nobody made.
                counts["NOT_RUN"] += 1
                continue
            verdict = self._adjudicator.adjudicate(
                flat[pair.left_index][1], flat[pair.right_index][1], pair
            )
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict == "DUPLICATE":
                accepted.add((pair.left_index, pair.right_index))
        return accepted, counts

    def _build_zones(
        self,
        groups: list[Group],
        flat: list[tuple[str, dict[str, Any]]],
        trust_states: dict[str, str],
        cycle_id: str,
        at: str,
        update_dates: dict[str, str | None],
    ) -> list[CanonicalZone]:
        source_groups = [
            [(flat[i][0], road_event_id(flat[i][1])) for i in group.members] for group in groups
        ]
        assignments = self._identity.assign_all(source_groups, at, cycle_id)

        zones = []
        for group, assignment in zip(groups, assignments, strict=True):
            # The lowest-indexed member supplies the emitted geometry and core
            # details. Which member wins is decided again in the republisher on
            # recency; this is only the record's shape, and it is deterministic
            # because `group.members` is sorted.
            _, feature = flat[group.members[0]]
            sources = [
                source_ref(
                    flat[i][0],
                    flat[i][1],
                    trust_states.get(flat[i][0], "WATCH"),
                    at,
                    update_dates.get(flat[i][0]),
                    next((e for e in group.edges if i in (e.left_index, e.right_index)), None),
                    "TIER_1_DETERMINISTIC" if group.edges else "SINGLETON",
                )
                for i in group.members
            ]
            conflicts = [
                ConflictRecord(
                    type="AMBIGUOUS_GROUPING",
                    detected_at=at,
                    resolution="EDGE_DROPPED",
                    dropped_edge=DroppedEdge(
                        publisher_key=flat[left][0],
                        road_event_id=road_event_id(flat[left][1]),
                        other_publisher_key=flat[right][0],
                        other_road_event_id=road_event_id(flat[right][1]),
                        distance_m=distance,
                    ),
                )
                for left, right, distance in dropped_pairs(group)
            ]
            verts = vertices(feature)
            zones.append(
                CanonicalZone(
                    canonical_id=assignment.canonical_id,
                    geometry=feature.get("geometry"),
                    core_details=dict(core(feature)),
                    start_date=date_range(feature)[0],
                    end_date=date_range(feature)[1],
                    sources=sources,
                    conflicts=conflicts,
                    supersedes=list(assignment.supersedes),
                    # When this canonical identity began, not when this cycle
                    # ran. `at` here made the field mean its own opposite.
                    first_merged_at=assignment.first_mapped_at or at,
                    last_seen_cycle=cycle_id,
                    bbox=bbox_of(verts),
                )
            )
        return zones
