"""Observation factories and the poll-threading helper for the scorer tests.

Shared by `test_trust_scorer.py` (latching, escalation, hysteresis) and
`test_trust_records.py` (what the evaluation record carries). Extracted when the
first outgrew the file size limit; both need the same fixtures and a second copy
would drift.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.trust_scorer.scorer import TrustScorer

START = datetime.datetime(2026, 8, 1, 0, 0, tzinfo=datetime.UTC)
CADENCE = 300


def clean_obs(index=0, minutes=30):
    """A poll on which every rule that can run returns ADMIT."""
    moment = START + datetime.timedelta(minutes=minutes * index)
    return Observation(
        publisher_key="p|f",
        polled_at=moment.isoformat(),
        http_status=200,
        update_date=(moment - datetime.timedelta(minutes=1)).isoformat(),
        update_age_seconds=60.0,
        feature_count=10,
        active_count=10,
        active_with_past_end_date=0,
        active_undated=0,
        schema_version_used="4.2",
        schema_error_count=0,
        content_hash=f"changing-{index}",
    )


def contradictory(index=0, minutes=30):
    o = clean_obs(index, minutes)
    o.active_with_past_end_date = o.active_count
    o.content_hash = "frozen"
    return o


def not_modified(index=0, minutes=30):
    """A 304 carrying forward a contradictory body. Values copied, not measured."""
    o = contradictory(index, minutes)
    o.http_status, o.not_modified, o.carried_forward = 304, True, True
    o.schema_error_count = None
    return o


def complied(index=0, minutes=60):
    """A publisher that moved every offending zone out of `active`.

    Zero active zones, so R4 is MEASURED_INAPPLICABLE rather than failing. This
    must count toward recovery: it is the publisher doing exactly what the
    finding asked.
    """
    o = clean_obs(index, minutes)
    o.active_count = 0
    o.active_with_past_end_date = 0
    o.active_undated = 0
    return o


def run_polls(count, state, latching, minutes=30, factory=clean_obs, scorer=None):
    """Feed `count` polls through the scorer, threading the counters forward.

    Threading them is the point: the scorer holds no state, so a caller that
    forgets to carry `clean_streak` and `streak_started_at` silently never
    recovers a publisher, and a test that reconstructs them per poll would not
    notice.
    """
    scorer = scorer or TrustScorer()
    streak, started, history, result = 0, None, [], None
    for i in range(count):
        observation = factory(i, minutes)
        result = scorer.score(
            observation,
            list(history),
            state,
            CADENCE,
            START,
            latching_rule_ids=latching,
            clean_streak=streak,
            clean_streak_started_at=started,
        )
        state, latching = result.state, result.latching_rule_ids
        streak, started = result.clean_streak, result.streak_started_at
        history.insert(0, observation)
    return result, state
