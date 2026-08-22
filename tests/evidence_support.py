"""The one packet every evidence test is written against. Sections 3, 6.7.

Utah DOT, quarantined on R2 and R4, with a feed whose own last-updated time is
`2023-03-19T07:04:04.861489-06:00`. That offset is not decoration: it is 13:04
UTC, and the renderings printed it as 07:04 UTC for as long as they existed, so
every test that reads a moment out of a notice is reading this observation.

Shared rather than duplicated because three test modules assert against the same
packet, and a fixture copied three times is three fixtures that drift.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.trust_scorer.records import TrustTransition

START = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
AT = "2026-08-07T12:00:00+00:00"


def obs(i=0):
    moment = START + datetime.timedelta(minutes=30 * i)
    return Observation(
        publisher_key="Utah DOT|udot",
        polled_at=moment.isoformat(),
        http_status=200,
        update_date="2023-03-19T07:04:04.861489-06:00",
        update_age_seconds=1236 * 86400,
        feature_count=744,
        active_count=744,
        active_with_past_end_date=744,
        active_undated=0,
        schema_version_used="4.0",
        schema_error_count=0,
        content_hash="frozen",
    )


def transition(to_state="QUARANTINE", at=AT, rule_ids=("R2", "R4")):
    return TrustTransition(
        publisher_key="Utah DOT|udot",
        at=at,
        from_state="WATCH",
        to_state=to_state,
        rule_ids=list(rule_ids),
        primary_rule_id=rule_ids[0],
        direction="ESCALATION",
        ruleset_version="v1",
        observation_ids=["Utah DOT|udot@" + at],
    )
