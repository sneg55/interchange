"""Candidate pairs and their merge tier. Section 6.6.

Three tiers, and the model is consulted only in the middle one:

- **Tier 1** is deterministic: shared `data_source_id` and sub-metre distance.
  When two publishers both declare they are republishing TRANSCOM and their zones
  are on the same spot, the duplication is declared upstream rather than
  inferred, which is the strongest evidence available anywhere in this system.
- **Tier 2** is spatially matched but missing one of those two conditions, and
  goes to the adjudicator.
- **Tier 3** is rejected by the coverage rule.

Road name and direction are corroborators, never requirements. That is a
correction forced by measurement: across the 1,489 matched New York / NJIT pairs
normalized road names agree on 0.3 percent and direction is usable on zero
percent, because New York DOT publishes `direction: "unknown"` for all 6,848 of
its features and the two publishers use `road_names` for different things. A gate
requiring agreement would reject essentially every true duplicate in the flagship
pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.utils.timestamps import try_parse

from .geometry import spatially_matches, vertices
from .records import MergeTier, RejectedPair
from .spatial_index import Grid

MATCH_THRESHOLD_M = 150.0
# Tier 1 demands the zones be on the same spot, not merely near it. A metre is
# well inside any plausible difference in how two republishers of the same
# upstream record round coordinates.
TIER_1_MAX_DISTANCE_M = 1.0

# How many coverage-rejected pairs are retained for the negative control. A
# sample, never the whole set: the live fleet rejects thousands, and the
# authoritative total stays in `excluded["rejected_by_coverage"]` so the caller
# can always report how many were not kept.
REJECTED_SAMPLE_CAP = 50

# Order matters: the interstate and route prefixes must run before the
# single-letter compass rules, or "State Route N" loses its route token.
ROAD_SUBSTITUTIONS = [
    (r"\binterstate\b", "i"),
    (r"\bi[- ]?(?=\d)", "i "),
    (r"\bu s\b|\bus route\b|\bus highway\b|\bus hwy\b", "us"),
    (r"\bstate route\b|\bstate rte\b|\bstate highway\b|\bsr\b|\bsh\b", "sr"),
    (r"\bcounty road\b|\bcounty rd\b|\bcr\b", "cr"),
    (r"\bstreet\b", "st"),
    (r"\bavenue\b", "ave"),
    (r"\broad\b", "rd"),
    (r"\bdrive\b", "dr"),
    (r"\bhighway\b", "hwy"),
    (r"\bboulevard\b", "blvd"),
    (r"\bparkway\b", "pkwy"),
    (r"\bnorth\b", "n"),
    (r"\bsouth\b", "s"),
    (r"\beast\b", "e"),
    (r"\bwest\b", "w"),
]


def core(feature: dict[str, Any]) -> dict[str, Any]:
    props = feature.get("properties") or {}
    nested = props.get("core_details")
    return nested if isinstance(nested, dict) else props


def normalize_road(name: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", name.lower().strip())
    for pattern, replacement in ROAD_SUBSTITUTIONS:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def road_names(feature: dict[str, Any]) -> set[str]:
    return {normalize_road(n) for n in (core(feature).get("road_names") or []) if n}


def direction(feature: dict[str, Any]) -> str:
    return core(feature).get("direction") or "unknown"


def data_source_id(feature: dict[str, Any]) -> str | None:
    return core(feature).get("data_source_id")


def feature_update_date(feature: dict[str, Any]) -> str | None:
    """The feature's own `update_date`, or None if it does not carry one.

    Distinct from the feed header's `update_date`, which is when the publisher
    last regenerated the whole feed. Recency decides field conflicts and which
    source is named primary, and the feed header answers a different question:
    a publisher who republishes everything every five minutes would out-rank a
    publisher who actually updated the zone in question an hour ago.

    `scripts/probe_update_date_scope.py` prints how often the better value is
    there: 41,764 of 49,833 features across 18 of 25 committed fixture feeds.
    Absent is None rather than the feed's, so the caller decides what to fall
    back to and can record which one it used.
    """
    value = core(feature).get("update_date")
    return value if isinstance(value, str) and value else None


def road_event_id(feature: dict[str, Any]) -> str:
    return str(feature.get("id") or "")


def date_range(feature: dict[str, Any]) -> tuple[str | None, str | None]:
    props = feature.get("properties") or {}
    return props.get("start_date"), props.get("end_date")


def ranges_overlap(a: tuple[Any, Any], b: tuple[Any, Any]) -> bool:
    """True when two [start, end] ranges overlap, or when either is unusable.

    Unknown counts as overlapping. A missing or unparseable date is not evidence
    that two zones are distinct, and scoring it as a mismatch would suppress real
    duplicates.
    """
    a0, a1 = a
    b0, b1 = b
    if not (a0 and a1 and b0 and b1):
        return True
    pa0, pa1, pb0, pb1 = (try_parse(x) for x in (a0, a1, b0, b1))
    if None in (pa0, pa1, pb0, pb1):
        return True
    return pa0 <= pb1 and pb0 <= pa1


@dataclass(slots=True)
class CandidatePair:
    """One spatially matched pair, with everything the tiers and the console need."""

    left_index: int
    right_index: int
    left_publisher: str
    right_publisher: str
    distance_m: float | None
    coverage: float | None
    tier: MergeTier | None  # None until classified, "TIER_3_REJECTED" never stored
    shared_data_source_id: str | None = None
    road_names_agree: bool = False
    direction_agrees: bool = False
    dates_overlap: bool = True

    @property
    def sort_key(self) -> tuple[int, float, str, str, int, int]:
        """A TOTAL order. Section 6.6 requires one and a partial one is not it.

        Tier first: a deterministic Tier 1 edge, where both publishers declare
        the same upstream source, must never be dropped in favour of an
        adjudicated Tier 2 edge that merely happens to be closer.

        Then distance, then publisher keys, then the source indices. The indices
        matter: two equal-distance edges between the same publisher pair are
        otherwise tied, and Python's stable sort leaves them in caller order, so
        reversing the input changes which zone joins which. That is precisely the
        non-determinism the ordering exists to remove.
        """
        return (
            0 if self.tier == "TIER_1_DETERMINISTIC" else 1,
            float("inf") if self.distance_m is None else self.distance_m,
            self.left_publisher,
            self.right_publisher,
            self.left_index,
            self.right_index,
        )


def corroborators(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Signals that raise confidence when they agree and are ignored when they do not."""
    left_names, right_names = road_names(left), road_names(right)
    left_dir, right_dir = direction(left), direction(right)
    return {
        "road_names_agree": bool(left_names & right_names),
        "direction_agrees": (
            left_dir == right_dir and left_dir != "unknown" and right_dir != "unknown"
        ),
        "dates_overlap": ranges_overlap(date_range(left), date_range(right)),
    }


def classify(pair_distance: float | None, shared_source: str | None) -> MergeTier:
    """Tier 1 when the duplication is declared upstream, Tier 2 otherwise."""
    if shared_source and pair_distance is not None and pair_distance <= TIER_1_MAX_DISTANCE_M:
        return "TIER_1_DETERMINISTIC"
    return "TIER_2_ADJUDICATED"


def candidate_pairs(
    features_by_publisher: dict[str, list[dict[str, Any]]],
    threshold: float = MATCH_THRESHOLD_M,
    keep_rejected: int = REJECTED_SAMPLE_CAP,
) -> tuple[list[CandidatePair], dict[str, int], list[RejectedPair]]:
    """Every cross-publisher pair passing the spatial predicate.

    Returns the pairs, a count of what was excluded and why, and a bounded
    sample of the pairs symmetric coverage refused. Null geometry is counted
    rather than dropped: Quebec City serves four such features, and a reconciler
    that silently discarded them would report a coverage figure that quietly
    excluded them from its own denominator.

    The rejected sample is what section 6.6's negative control is made of. It is
    a sample and never the whole set, because on the live fleet the rejected
    count runs to thousands; `excluded["rejected_by_coverage"]` remains the
    authoritative total, so the caller can always say how many were not kept.
    """
    flat: list[tuple[str, dict[str, Any]]] = []
    rejected: list[RejectedPair] = []
    excluded = {"null_geometry": 0, "rejected_by_coverage": 0, "same_publisher_skipped": 0}
    for publisher_key, features in sorted(features_by_publisher.items()):
        for feature in features:
            if not vertices(feature):
                excluded["null_geometry"] += 1
                continue
            flat.append((publisher_key, feature))

    grid = Grid([f for _, f in flat], threshold)
    pairs: list[CandidatePair] = []
    seen: set[tuple[int, int]] = set()
    for i, (publisher_key, feature) in enumerate(flat):
        for j in grid.candidates(feature):
            if j == i:
                continue
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            seen.add(key)
            other_publisher, other = flat[j]
            if other_publisher == publisher_key:
                # Two zones from one publisher are the publisher asserting they
                # are distinct. Interchange defers rather than second-guessing.
                excluded["same_publisher_skipped"] += 1
                continue
            matched, distance, cov = spatially_matches(
                vertices(feature), vertices(other), threshold
            )
            if not matched:
                if distance is not None and distance <= threshold:
                    # Inside the distance threshold and refused anyway: this is
                    # the negative control's whole content. Distance alone would
                    # have merged these two.
                    excluded["rejected_by_coverage"] += 1
                    if len(rejected) < keep_rejected:
                        rejected.append(
                            RejectedPair(
                                left_publisher=publisher_key,
                                left_road_event_id=road_event_id(feature),
                                right_publisher=other_publisher,
                                right_road_event_id=road_event_id(other),
                                distance_m=distance,
                                coverage=cov,
                            )
                        )
                continue
            left, right = key
            left_publisher, left_feature = flat[left]
            right_publisher, right_feature = flat[right]
            shared = data_source_id(left_feature)
            shared = shared if shared and shared == data_source_id(right_feature) else None
            pairs.append(
                CandidatePair(
                    left_index=left,
                    right_index=right,
                    left_publisher=left_publisher,
                    right_publisher=right_publisher,
                    distance_m=distance,
                    coverage=cov,
                    tier=classify(distance, shared),
                    shared_data_source_id=shared,
                    **corroborators(left_feature, right_feature),
                )
            )
    return pairs, excluded, rejected
