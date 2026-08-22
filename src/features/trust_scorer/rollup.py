"""PublisherDaily. Sections 6.9 and 7.

The console forces this record into existence. At the five minute floor a
publisher accumulates roughly 288 observations a day against 90 days of
retention, so charting raw observations would mean tens of thousands of document
reads per page view. This is 40 by 90 documents and it is what every chart reads.

Written by the trust scorer from M4 onward rather than with the console in M6.
A rollup written from M4 has history by M6; one written in M6 does not, and that
is a one-way door.

Percentiles are computed by nearest-rank on the sorted sample rather than by
interpolation. The sample is a day of latencies from one publisher, and an
interpolated p95 invents a number no poll actually produced.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any

from src.features.publisher_agent.observation import Observation

from .records import RuleEvaluation
from .verdicts import FleetState


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile, or None for an empty sample.

    None rather than 0.0. A publisher with no successful polls has no latency,
    and a zero would render as the fastest publisher in the fleet on exactly the
    day it was unreachable.
    """
    if not values:
        return None
    ordered = sorted(values)
    # ceil, not round. Python's round() is banker's rounding, which put p50 of a
    # two-sample set on the SECOND value and p95 of a hundred on the 96th. The
    # nearest-rank definition is ceil(p*n), and getting it wrong here is the
    # kind of error that never looks wrong on a chart.
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return ordered[rank - 1]


@dataclass(slots=True)
class PublisherDaily:
    publisher_key: str
    day: str  # UTC date, YYYY-MM-DD
    poll_count: int = 0
    failure_count: int = 0
    not_modified_count: int = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    max_update_age_seconds: float | None = None
    schema_error_count: int | None = None
    content_hash_changes: int = 0
    fired_rules: list[str] = dc_field(default_factory=list)
    end_of_day_state: FleetState = "WATCH"

    @property
    def doc_id(self) -> str:
        return f"{self.publisher_key}|{self.day}"

    @property
    def success_count(self) -> int:
        return self.poll_count - self.failure_count

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> PublisherDaily:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in doc.items() if k in known})


def day_of(polled_at: str) -> str:
    return polled_at[:10]


def roll_up(
    publisher_key: str,
    day: str,
    observations: list[Observation],
    evaluations: list[RuleEvaluation] | None = None,
    previous_hash: str | None = None,
) -> PublisherDaily:
    """One publisher's day.

    `previous_hash` is the last content hash from the PREVIOUS day. Without it
    the first poll of each day looks like a change, and a frozen publisher would
    report one content-hash change per day forever, which is exactly the signal
    R5 exists to say is absent.
    """
    ordered = sorted(observations, key=lambda o: o.polled_at)
    successes = [o for o in ordered if not o.failed]
    # `is not None`, not truthiness. A genuine sub-millisecond poll rounds to a
    # falsy 0.0 and was being dropped from the percentiles alongside the polls
    # that were never timed at all, which is the same conflation of "measured
    # zero" and "not measured" this record exists to keep apart.
    latencies = [o.latency_ms for o in successes if o.latency_ms is not None]
    ages = [o.update_age_seconds for o in ordered if o.update_age_seconds is not None]
    # Only body-bearing polls can contribute an error count. Summing over
    # carried-forward 304s would report a document nobody fetched as validating.
    body_polls = [o for o in ordered if o.has_body]
    errors = [o.schema_error_count for o in body_polls if o.schema_error_count is not None]
    # A day mixing validated and unvalidated body polls must NOT read as clean.
    # Filtering the Nones out and summing turns "3 validated clean, 200 never
    # checked" into a confident zero, which is the exact shape of "not checked"
    # recorded as "checked and passed".
    partially_validated = bool(body_polls) and len(errors) != len(body_polls)

    changes, last = 0, previous_hash
    for observation in ordered:
        if observation.content_hash is None:
            continue
        if last is not None and observation.content_hash != last:
            changes += 1
        last = observation.content_hash

    fired: list[str] = []
    end_state: FleetState = "WATCH"
    for evaluation in sorted(evaluations or [], key=lambda e: e.evaluated_at):
        for rule_id in evaluation.fired_rule_ids:
            if rule_id not in fired:
                fired.append(rule_id)
        end_state = evaluation.resulting_state

    return PublisherDaily(
        publisher_key=publisher_key,
        day=day,
        poll_count=len(ordered),
        failure_count=sum(1 for o in ordered if o.failed),
        not_modified_count=sum(1 for o in ordered if o.not_modified and not o.failed),
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        max_update_age_seconds=max(ages) if ages else None,
        # None, not zero: a non-empty list of zeros sums to zero and means
        # "validated and clean", while an empty list, or a day where only some
        # body polls were validated, means the day cannot be called clean.
        schema_error_count=None if (partially_validated or not errors) else sum(errors),
        content_hash_changes=changes,
        fired_rules=fired,
        end_of_day_state=end_state,
    )


def roll_up_all(
    observations: list[Observation],
    evaluations: list[RuleEvaluation] | None = None,
    carry_in: dict[str, str] | None = None,
) -> list[PublisherDaily]:
    """Group by (publisher, UTC day) and roll each up.

    `carry_in` maps publisher key to the content hash it ended the previous day
    with, so a day boundary is not mistaken for a content change.
    """
    by_key: dict[tuple[str, str], list[Observation]] = {}
    for observation in observations:
        by_key.setdefault((observation.publisher_key, day_of(observation.polled_at)), []).append(
            observation
        )
    evals_by_key: dict[tuple[str, str], list[RuleEvaluation]] = {}
    for evaluation in evaluations or []:
        evals_by_key.setdefault(
            (evaluation.publisher_key, day_of(evaluation.evaluated_at)), []
        ).append(evaluation)

    carried = dict(carry_in or {})
    out = []
    for key in sorted(by_key):
        publisher_key, day = key
        daily = roll_up(
            publisher_key, day, by_key[key], evals_by_key.get(key), carried.get(publisher_key)
        )
        out.append(daily)
        hashes = [
            o.content_hash
            for o in sorted(by_key[key], key=lambda o: o.polled_at)
            if o.content_hash is not None
        ]
        if hashes:
            carried[publisher_key] = hashes[-1]
    return out
