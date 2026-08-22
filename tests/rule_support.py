"""Observation builders and rule shorthands shared by the ruleset tests.

Split out when `test_trust_rules.py` outgrew the file-size limit. Every rule test
needs the same well-formed observation to vary one field of, and duplicating
that builder is how two files end up disagreeing about what "well-formed" means.

The recurring assertion is that a rule which could not run returns
NOT_APPLICABLE rather than ADMIT. That is the invariant the whole system rests
on, and it is the one an implementation drifts away from silently: an ADMIT from
an unevaluable rule looks exactly like an ADMIT from a passing one.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.publisher_agent.observation import Observation
from src.features.trust_scorer import rules

NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.UTC)
DAY = 86400


def obs(minutes_ago=0, **kw):
    moment = NOW - datetime.timedelta(minutes=minutes_ago)
    fields = {
        "publisher_key": "p|f",
        "polled_at": moment.isoformat(),
        "http_status": 200,
        "update_date": (NOW - datetime.timedelta(minutes=1)).isoformat(),
        "update_age_seconds": 60.0,
        "feature_count": 10,
        "active_count": 10,
        "active_with_past_end_date": 0,
        "active_undated": 0,
        "schema_version_used": "4.2",
        "schema_error_count": 0,
        "content_hash": "same",
        "structural_hash": "same",
    }
    fields.update(kw)
    return Observation(**fields)


def failed(minutes_ago=0):
    return Observation(
        publisher_key="p|f",
        polled_at=(NOW - datetime.timedelta(minutes=minutes_ago)).isoformat(),
        http_status=0,
        error="Injected",
        error_origin="PUBLISHER",
    )


def unreached(minutes_ago=0):
    """A poll that never left Interchange. Not evidence about the publisher."""
    return Observation(
        publisher_key="p|f",
        polled_at=(NOW - datetime.timedelta(minutes=minutes_ago)).isoformat(),
        http_status=0,
        error="Interchange has no captured response for https://example/feed",
        error_origin="INTERCHANGE",
    )


# Rules return a RuleOutcome (verdict + reason + measured detail). These unwrap
# the verdict where a test is only about the verdict; the reason-sensitive tests
# call the rule directly.
def r1(*a):
    return rules.r1_unreachable(*a).verdict


def r2(*a):
    return rules.r2_stale(*a).verdict


def r3(*a):
    return rules.r3_schema(*a).verdict


def r4(*a):
    return rules.r4_contradiction(*a).verdict


def r6(*a):
    return rules.r6_undeterminable(*a).verdict


def r5(current, history):
    outcome = rules.r5_frozen(current, history)
    return outcome.verdict, outcome.detail


