"""Emit the merged feed, and refuse to publish output that fails its own gate.

Section 6.8. Interchange publishes an honest `update_date` and must pass the gate
it applies to others. If its own output fails validation it does not publish, and
the refusal is recorded as an `OutputArtifact` with `published: false` rather than
appearing as an absence.

That is the one failure this project cannot ship: a merged feed that would
quarantine its own publisher.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import ErrorIds
from src.features.reconciler.records import CanonicalZone
from src.features.republisher.artifact import OutputArtifact, RepublishResult
from src.services.schema_registry import SchemaRegistry

from .mapping import (
    DROPPED_FEED_INFO_FIELDS,
    OUTPUT_VERSION,
    feed_info,
    missing_required,
    to_feature,
)


def _exclude(
    counts: dict[str, int], ids: dict[str, list[str]], reason: str, canonical_id: str
) -> None:
    """Count an exclusion and name the zone behind it, up to the sample cap."""
    counts[reason] += 1
    if len(ids[reason]) < ZONE_ID_SAMPLE_CAP:
        ids[reason].append(canonical_id)


def _offending_features(errors: list[Any]) -> tuple[set[int], int]:
    """Feature indices the errors name, and how many errors named no feature.

    A validation error carries the path to the instance that failed. When that
    path starts `features[i]` the error belongs to one zone and dropping that
    zone answers it. When it does not, the error is about the feed as a whole
    and no amount of dropping will fix it, so it is counted instead: one such
    error is enough to stop the retry and refuse to publish.
    """
    indices: set[int] = set()
    unattributed = 0
    for error in errors:
        path = list(getattr(error, "absolute_path", []))
        if len(path) >= 2 and path[0] == "features" and isinstance(path[1], int):
            indices.add(path[1])
        else:
            unattributed += 1
    return indices, unattributed


# Every exclusion reason is counted and reported. Section 6.8: excluding a zone
# is never a silent drop.
EXCLUSION_REASONS = (
    "quarantined_sources_only",
    "decommissioned_sources_only",
    "null_geometry",
    "missing_required_field",
    "failed_schema_validation",
    "failed_self_validation",
)

# How many zone ids one reason will name. The count beside them is the total and
# is never capped, so a reader is told what the sample is a sample of.
#
# Naming every zone is the invariant; naming all of them in one document is not
# always possible. A feed-level failure withholds every emitted zone, and at the
# current snapshot that is 32,313 canonical ids of 36 characters each, which is
# about 1.23 MB and over Firestore's 1 MiB document limit. The choice is a capped
# sample or a write that fails, and a write that fails records nothing at all.
ZONE_ID_SAMPLE_CAP = 200


class Republisher:
    """Builds the merged feed and validates it before emitting."""

    def __init__(self, schemas: SchemaRegistry) -> None:
        self._schemas = schemas

    def build(
        self,
        zones: list[CanonicalZone],
        trust_states: dict[str, str],
        decommissioned: set[str] | None = None,
        blocked_fields: dict[str, set[str]] | None = None,
        cycle_id: str = "",
        at: str = "",
        withheld_source_zones: dict[str, int] | None = None,
        withheld_reasons: dict[str, str] | None = None,
    ) -> RepublishResult:
        """One republish cycle.

        `trust_states` maps publisher key to fleet state. A zone whose only
        sources are quarantined contributes nothing, which is what makes
        QUARANTINE a gate rather than a label: a trust state that does not change
        what gets published is decoration.
        """
        retired = decommissioned or set()
        blocked = blocked_fields or {}
        excluded_counts = dict.fromkeys(EXCLUSION_REASONS, 0)
        excluded_ids: dict[str, list[str]] = {reason: [] for reason in EXCLUSION_REASONS}
        missing_fields: dict[str, int] = {}
        emitted: list[CanonicalZone] = []

        for zone in zones:
            reason = self._exclusion_reason(zone, trust_states, retired)
            if reason:
                _exclude(excluded_counts, excluded_ids, reason, zone.canonical_id)
                if reason == "missing_required_field":
                    # WHICH field. `missing_required` already computes the list
                    # and it was being discarded, so the console could report a
                    # count and nothing an operator could act on.
                    for name in missing_required(zone) or ["sources"]:
                        missing_fields[name] = missing_fields.get(name, 0) + 1
                continue
            emitted.append(zone)

        feed = self._feed(emitted, blocked, at)
        errors = self._schemas.errors(feed, OUTPUT_VERSION)

        # A zone whose merged record still fails schema validation is excluded
        # from the output (6.8) rather than vetoing it. Ten malformed features
        # out of 32,313 must not withhold the other 32,303: that would let one
        # publisher's bad enum decide what the whole fleet publishes, and it is
        # the opposite of what a gate is for.
        #
        # Only errors that name a feature can be answered this way. An error
        # about the header, the bounding box or the root type is not a zone's
        # fault and dropping zones would not fix it, so its presence stops the
        # retry and the cycle fails closed.
        if not isinstance(errors, str) and errors:
            invalid, unattributed = _offending_features(errors)
            if invalid and not unattributed:
                for index in sorted(invalid):
                    _exclude(
                        excluded_counts,
                        excluded_ids,
                        "failed_schema_validation",
                        emitted[index].canonical_id,
                    )
                emitted = [z for i, z in enumerate(emitted) if i not in invalid]
                feed = self._feed(emitted, blocked, at)
                # Validated again rather than assumed clean. Dropping features
                # rewrites the header this rebuilds, and one drop pass is all
                # there is: a second failure is not a second licence to drop.
                errors = self._schemas.errors(feed, OUTPUT_VERSION)

        error_list = [] if isinstance(errors, str) else errors
        validation = {
            "schema_version": OUTPUT_VERSION,
            "error_count": len(error_list) if not isinstance(errors, str) else None,
            "unresolvable": isinstance(errors, str),
            "errors": [str(e)[:300] for e in error_list[:20]],
            "errors_truncated": len(error_list) > 20,
        }
        # An unresolvable schema is NOT a pass. Interchange cannot claim to have
        # validated output against a schema it could not load, and publishing on
        # that basis is the precise failure the whole product exists to catch.
        published = not validation["unresolvable"] and validation["error_count"] == 0
        if not published:
            # Every emitted zone is withheld, and they are named as far as the
            # cap allows. Recording the count alone left this one bucket holding
            # a number with no zones behind it, which is the same silent drop
            # the other four reasons are careful not to be.
            excluded_counts["failed_self_validation"] = len(emitted)
            excluded_ids["failed_self_validation"] = [
                z.canonical_id for z in emitted[:ZONE_ID_SAMPLE_CAP]
            ]
            validation["error_id"] = str(ErrorIds.PUB_SELF_VALIDATION_FAILED)

        artifact = OutputArtifact(
            cycle_id=cycle_id,
            at=at,
            feed_uri=None,
            # Nothing writes the feed anywhere yet, so its size was never
            # measured. See the field's comment for why that is not zero.
            byte_size=None,
            input_zone_count=len(zones),
            canonical_zone_count=len(emitted),
            source_zone_count=sum(len(z.sources) for z in emitted),
            validation_result=validation,
            published=published,
            excluded_counts=excluded_counts,
            excluded_zone_ids=excluded_ids,
            withheld_source_zones=dict(withheld_source_zones or {}),
            withheld_reasons=dict(withheld_reasons or {}),
            missing_field_counts=missing_fields,
        )
        # The feed is returned either way so an operator can inspect what failed;
        # `published` is what decides whether it is emitted.
        return RepublishResult(feed=feed, artifact=artifact)

    @staticmethod
    def _feed(
        emitted: list[CanonicalZone], blocked: dict[str, set[str]], at: str
    ) -> dict[str, Any]:
        """The feed as it stands for a given set of zones.

        Built in one place because it is built twice: dropping an invalid zone
        changes `feed_info`, and a header derived from the pre-drop set would
        describe a feed that was never emitted.
        """
        return {
            "type": "FeatureCollection",
            "feed_info": _scrub(feed_info(emitted, update_date=at)),
            "features": [to_feature(z, blocked.get(z.canonical_id)) for z in emitted],
        }

    @staticmethod
    def _exclusion_reason(
        zone: CanonicalZone, trust_states: dict[str, str], decommissioned: set[str]
    ) -> str | None:
        if not zone.geometry:
            return "null_geometry"
        if not zone.sources:
            return "missing_required_field"
        live = [s for s in zone.sources if s.publisher_key not in decommissioned]
        if not live:
            # Delisting stops polling, and a publisher no longer in the federal
            # registry must not keep contributing to Interchange's output
            # indefinitely.
            return "decommissioned_sources_only"
        if all(trust_states.get(s.publisher_key, "WATCH") == "QUARANTINE" for s in live):
            return "quarantined_sources_only"
        if missing_required(zone):
            return "missing_required_field"
        return None


def _scrub(header: dict[str, Any]) -> dict[str, Any]:
    """Drop publisher contact fields rather than republishing them.

    At least one live feed carries a named individual's details in `feed_info`.
    Passing those through would republish a person's contact information under
    Interchange's name, which is not something a merged feed should do on their
    behalf.
    """
    return {k: v for k, v in header.items() if k not in DROPPED_FEED_INFO_FIELDS}
