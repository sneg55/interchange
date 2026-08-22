"""Shared helpers for tests that read the checksummed snapshot.

Lookup goes through the manifest rather than through the registry, because the
manifest already keys feeds by `org|feedname` and knows exactly which ones were
captured. Searching the registry and catching the miss would swallow a genuine
fixture corruption as "not captured", which is the one failure the checksums
exist to surface.
"""

from __future__ import annotations

import unittest
from typing import Any

from src.services.fixtures import FixtureSet


def captured_keys(fixtures: FixtureSet) -> list[str]:
    return sorted(fixtures.manifest["feeds"])


def feed_entry(fixtures: FixtureSet, org: str) -> dict[str, Any] | None:
    for key, entry in sorted(fixtures.manifest["feeds"].items()):
        if key.split("|", 1)[0] == org:
            return entry
    return None


def features_for(
    fixtures: FixtureSet, org: str, optional: bool = False
) -> list[dict[str, Any]]:
    """The captured features for one organization.

    FAILS by default when the publisher is absent, and only skips when the
    caller says the feed is optional. A skip is the wrong default here: the
    negative controls in section 6.6 are the tests that make the matching rule
    believable, and a refreshed snapshot missing St. Charles County would turn
    them into no-ops while the suite stayed green. A control that can silently
    stop running is not a control.
    """
    entry = feed_entry(fixtures, org)
    if entry is not None:
        return fixtures.body_for_url(entry["url"])["features"]
    if optional:
        raise unittest.SkipTest(f"{org} is not in the snapshot")
    raise AssertionError(
        f"{org} is required by this test but is not in the snapshot. "
        f"Captured: {', '.join(captured_keys(fixtures))}"
    )


# ---------------------------------------------------------------- republisher

AT = "2026-08-07T12:00:00Z"

# The WZDx 4.2 required set for a work zone, including one member of each of the
# four either/or verification pairs. Anything missing from here is what section
# 6.8 excludes rather than invents.
WORK_ZONE_FIELDS = {
    "start_date": "2026-08-01T00:00:00Z",
    "end_date": "2026-09-01T00:00:00Z",
    "vehicle_impact": "all-lanes-open",
    "location_method": "channel-device-method",
    "is_start_date_verified": False,
    "is_end_date_verified": False,
    "is_start_position_verified": False,
    "is_end_position_verified": False,
}


def source(
    publisher: str = "Utah DOT|udot",
    event_id: str = "z-1",
    updated: str = "2026-08-06T00:00:00Z",
    **overrides: Any,
):
    from src.features.reconciler.records import SourceRef

    return SourceRef(
        publisher_key=publisher,
        road_event_id=event_id,
        source_update_date=updated,
        work_zone_fields={**WORK_ZONE_FIELDS, **overrides},
    )


def zone(
    canonical_id: str = "11111111-1111-1111-1111-111111111111",
    sources: list[Any] | None = None,
    **overrides: Any,
):
    from src.features.reconciler.records import CanonicalZone

    base = {
        "geometry": {"type": "LineString", "coordinates": [[-111.0, 40.0], [-111.1, 40.1]]},
        "core_details": {
            "event_type": "work-zone",
            "road_names": ["I-15"],
            "direction": "northbound",
            "description": "Right lane closed for bridge deck repair.",
        },
        "start_date": WORK_ZONE_FIELDS["start_date"],
        "end_date": WORK_ZONE_FIELDS["end_date"],
    }
    base.update(overrides)
    return CanonicalZone(canonical_id=canonical_id, sources=sources or [source()], **base)


def republisher():
    from src.features.republisher.publisher import Republisher
    from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry

    return Republisher(SchemaRegistry(FixtureSchemaLoader(FixtureSet())))


def build(pub, zones, trust=None, **kw):
    states = trust or {"Utah DOT|udot": "ADMIT"}
    return pub.build(zones, states, cycle_id="c1", at=AT, **kw)


# ------------------------------------------------------------------- console

CONSOLE_AT = "2026-08-07T12:00:00+00:00"


def publisher(key: str = "A|a", state: str = "ADMIT", **kw: Any):
    from src.features.registry_warden.records import PublisherRecord

    base = {
        "publisher_key": key,
        "org": key.split("|")[0],
        "feedname": key.split("|")[1],
        "fleet_state": state,
        "declared_version": "4.2",
        "us_state": "UT",
        "declared_cadence_seconds": 300,
        "poll_interval_seconds": 300,
    }
    base.update(kw)
    return PublisherRecord(**base)


def observation(i: int = 0, day: int = 1, **kw: Any):
    import datetime

    from src.features.publisher_agent.observation import Observation

    moment = datetime.datetime(2026, 8, day, tzinfo=datetime.UTC) + datetime.timedelta(
        minutes=5 * i
    )
    fields = {
        "publisher_key": "A|a",
        "polled_at": moment.isoformat(),
        "http_status": 200,
        "latency_ms": 100.0 + i,
        "update_age_seconds": 60.0,
        "content_hash": "same",
        "structural_hash": "same",
        "schema_version_used": "4.2",
        "schema_error_count": 0,
    }
    fields.update(kw)
    return Observation(**fields)


def trust_transition(key: str, at: str, to_state: str):
    from src.features.trust_scorer.records import TrustTransition

    return TrustTransition(
        publisher_key=key,
        at=at,
        from_state="WATCH",
        to_state=to_state,
        rule_ids=["R2"],
        primary_rule_id="R2",
        direction="ESCALATION",
        ruleset_version="v1",
        observation_ids=[],
    )
