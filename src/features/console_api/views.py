"""Read models for the console's six screens. Section 6.9.

Pure functions over the records. The console reads Firestore directly for live
data, so these exist for two other reasons: to define what each screen means in
one place that is testable without a browser, and to serve the replay path, where
the answer is computed from append-only history rather than read from a mutable
document.

The recurring rule here is that a filtered or capped view always states what it
is a subset of. Silent truncation is the same failure this product exists to
catch, so a count that could be mistaken for the whole fleet is not allowed to
appear without its denominator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any

from src.features.registry_warden.records import PublisherRecord
from src.features.republisher.publisher import OutputArtifact

BANDS = ("ADMIT", "WATCH", "QUARANTINE", "NO_ACCESS")


@dataclass(slots=True)
class BandCount:
    band: str
    shown: int
    total: int

    @property
    def filtered(self) -> bool:
        return self.shown != self.total

    def to_doc(self) -> dict[str, Any]:
        return {**asdict(self), "filtered": self.filtered}


@dataclass(slots=True)
class FleetBoard:
    rows: list[dict[str, Any]]
    bands: list[BandCount]
    fleet_total: int
    shown_total: int
    filters_applied: dict[str, Any] = dc_field(default_factory=dict)

    @property
    def is_filtered(self) -> bool:
        return self.shown_total != self.fleet_total

    def to_doc(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "bands": [b.to_doc() for b in self.bands],
            "fleet_total": self.fleet_total,
            "shown_total": self.shown_total,
            "is_filtered": self.is_filtered,
            "filters_applied": self.filters_applied,
        }


def fleet_board(
    records: list[PublisherRecord],
    state: str | None = None,
    schema_version: str | None = None,
    us_state: str | None = None,
    search: str | None = None,
) -> FleetBoard:
    """Screen 1. Four bands, with every count shown against the fleet total.

    `NO_ACCESS` is its own band rather than folded into a trust state, because
    it is not a trust verdict. `INSUFFICIENT_HISTORY` rides alongside the state
    rather than replacing it, so an absent churn signal is visible rather than
    implied.
    """
    live = [r for r in records if r.decommissioned_at is None]
    matching = live
    if state:
        matching = [r for r in matching if r.fleet_state == state]
    if schema_version:
        matching = [r for r in matching if (r.declared_version or "") == schema_version]
    if us_state:
        matching = [r for r in matching if (r.us_state or "") == us_state]
    if search:
        needle = search.lower()
        matching = [r for r in matching if needle in r.org.lower() or needle in r.feedname.lower()]

    rows = [
        {
            "publisher_key": r.publisher_key,
            "org": r.org,
            "feedname": r.feedname,
            "us_state": r.us_state,
            "fleet_state": r.fleet_state,
            "churn_status": r.churn_status,
            "declared_version": r.declared_version,
            "declared_cadence_seconds": r.declared_cadence_seconds,
            "poll_interval_seconds": r.poll_interval_seconds,
            "backoff_active": (
                r.poll_interval_seconds > 0
                and r.poll_interval_seconds != _clamped(r.declared_cadence_seconds)
            ),
            "agent_identity": r.agent_identity,
            "latching_rule_ids": list(r.latching_rule_ids),
        }
        for r in sorted(matching, key=lambda r: r.publisher_key)
    ]
    return FleetBoard(
        rows=rows,
        # Every band carries its total. A filtered view can never be mistaken
        # for the whole fleet.
        bands=[
            BandCount(
                band=band,
                shown=sum(1 for r in matching if r.fleet_state == band),
                total=sum(1 for r in live if r.fleet_state == band),
            )
            for band in BANDS
        ],
        fleet_total=len(live),
        shown_total=len(matching),
        filters_applied={
            k: v
            for k, v in {
                "state": state,
                "schema_version": schema_version,
                "us_state": us_state,
                "search": search,
            }.items()
            if v
        },
    )


def _clamped(seconds: int) -> int:
    from src.features.registry_warden.cadence import clamp

    return clamp(seconds)


@dataclass(slots=True)
class OutputHealth:
    published: bool
    headline: str
    validation: dict[str, Any]
    # Every canonical zone the merge produced this cycle. The exclusion counts
    # subtract from THIS number, and without it the three quantities on the
    # screen could not be reconciled by a reader.
    input_zone_count: int
    canonical_zone_count: int
    source_zone_count: int
    excluded_counts: dict[str, int]
    feed_uri: str | None
    at: str

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


def output_health(artifact: OutputArtifact) -> OutputHealth:
    """Screen 6. If Interchange's own output fails validation, this says so first.

    The headline is computed rather than left to the template, so a screen that
    forgets to check `published` still cannot render a failure as a success.
    """
    if artifact.published:
        # Against the cycle's INPUT, not on its own. "N canonical zones from M
        # source zones" read as a funnel from M to N, which it is not: M counts
        # the publisher records behind the zones that were published. The number
        # a reader needs is how many zones the merge produced, because the
        # exclusion counts underneath subtract from that one, and it appeared
        # nowhere on the screen.
        # "this cycle produced" was wrong about `input_zone_count`, which counts
        # what the republisher RECEIVED from the canonical zone store. That store
        # persists across cycles, so the number accumulates: it grew from 47,521
        # to 49,326 over a few cycles while the zones actually emitted stayed near
        # 31,000. A quantity that only ever goes up is not a per-cycle figure, and
        # the reconciliation screen says as much about the same store two clicks
        # away.
        headline = (
            f"Published {artifact.canonical_zone_count} of the "
            f"{artifact.input_zone_count} canonical zones the merge handed the "
            f"republisher, drawn from {artifact.source_zone_count} publisher records. "
            f"The rest are accounted for below."
        )
    elif artifact.validation_result.get("unresolvable"):
        headline = (
            "NOT PUBLISHED: the output schema could not be resolved, so nothing was validated."
        )
    else:
        headline = (
            f"NOT PUBLISHED: output failed its own schema validation with "
            f"{artifact.validation_result.get('error_count')} errors."
        )
    return OutputHealth(
        published=artifact.published,
        headline=headline,
        validation=dict(artifact.validation_result),
        input_zone_count=artifact.input_zone_count,
        canonical_zone_count=artifact.canonical_zone_count,
        source_zone_count=artifact.source_zone_count,
        excluded_counts={k: v for k, v in artifact.excluded_counts.items() if v},
        feed_uri=artifact.feed_uri,
        at=artifact.at,
    )


@dataclass(slots=True)
class ViewportResult:
    """Rendered features plus what was left out. Section 6.9's hard cap."""

    features: list[dict[str, Any]]
    shown: int
    matched: int

    @property
    def capped(self) -> bool:
        return self.shown < self.matched

    @property
    def note(self) -> str:
        """ "showing N of M in view", never silence."""
        if not self.capped:
            return f"Showing all {self.shown} in view."
        return f"Showing {self.shown} of {self.matched} in view."

    def to_doc(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "shown": self.shown,
            "matched": self.matched,
            "capped": self.capped,
            "note": self.note,
        }


def viewport(zones: list[Any], bbox: list[float], cap: int = 2000) -> ViewportResult:
    """Screen 3's map. Viewport-bounded, publisher-scoped, hard-capped.

    New York DOT alone serves close to 7,000 features. The cap is not a
    performance nicety: rendering everything would hang the browser, and
    truncating without saying so would be the same failure this product exists
    to catch.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    inside = [
        z
        for z in zones
        if z.bbox
        and z.bbox[0] <= max_lon
        and z.bbox[2] >= min_lon
        and z.bbox[1] <= max_lat
        and z.bbox[3] >= min_lat
    ]
    ordered = sorted(inside, key=lambda z: z.canonical_id)
    return ViewportResult(
        features=[z.to_doc() for z in ordered[:cap]],
        shown=min(len(ordered), cap),
        matched=len(ordered),
    )
