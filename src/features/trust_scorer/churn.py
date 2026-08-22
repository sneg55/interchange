"""R5, frozen content. Section 6.4.

Split from `rules.py` because it is the only rule needing history, and the only
one whose window has edge cases worth isolating.

R5 reads the **structural** hash, not the content hash. That is a security
property, not an optimisation: `content_hash` covers `description`, which is
publisher-controlled free text that Model Armor may block. A publisher could
otherwise hold its road zones frozen while rotating injected descriptions, and
every rotation would move the content hash, clear the frozen-content signal, and
raise its standing. Section 6.5's invariant is that injected text can never raise
a trust score, and text reaching a rule through a hash is still text reaching a
rule.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from typing import Any

from src.features.publisher_agent.observation import Observation
from src.utils.timestamps import try_parse

from .outcomes import RuleOutcome, evaluated, inapplicable
from .verdicts import ADMIT, QUARANTINE, WATCH

R5_WINDOW_SECONDS = 24 * 3600
R5_MIN_POLLS = 12
R5_QUARANTINE_ADVANCES = 3

# Spec 7's `churn_status`, derived here rather than in the runner because this
# module owns the question. The field is a separate axis from `fleet_state`
# precisely so "we have not measured churn yet" cannot masquerade as a trust
# verdict, and it stayed at its dataclass default on every publisher forever
# because nothing ever wrote it: the console's churn column was rendering a
# default and reading as a measurement.
CHURN_OK = "OK"
CHURN_INSUFFICIENT = "INSUFFICIENT_HISTORY"


def churn_detail(results: Iterable[Any]) -> dict[str, int] | None:
    """R5's own measurement, for a screen to show. None when R5 did not run.

    None rather than a dict of zeros. A publisher R5 could not evaluate has not
    been measured as having zero churn, and a reader shown four zeros would take
    the second reading. The four keys are R5's `detail` verbatim.
    """
    for result in results:
        if getattr(result, "rule_id", None) != "R5":
            continue
        if getattr(result, "reason", None) != "EVALUATED":
            return None
        detail = getattr(result, "detail", {}) or {}
        return {
            key: int(detail.get(key, 0))
            for key in ("polls_in_window", "advances", "regressions", "span_seconds")
        }
    return None


def churn_status(results: Iterable[Any]) -> str:
    """`OK` when R5 spoke on this poll, `INSUFFICIENT_HISTORY` when it could not.

    Read off R5's REASON rather than its verdict. A verdict of NOT_APPLICABLE
    covers both "measured, and it does not apply" and "could not measure", and
    collapsing them here would report a publisher as churn-measured on the
    strength of a poll that measured nothing: the same conflation
    `outcomes.Reason` exists to undo.

    Anything other than a rule that ran is INSUFFICIENT_HISTORY, including a 304
    (`NO_BODY`). That is the honest reading: a publisher polled only through
    conditional requests has not had its structure re-measured, whatever its
    retained history looks like.
    """
    for result in results:
        if getattr(result, "rule_id", None) == "R5":
            return CHURN_OK if result.reason == "EVALUATED" else CHURN_INSUFFICIENT
    # No R5 result at all means the ruleset did not run it. Absence is not a
    # pass, here as everywhere.
    return CHURN_INSUFFICIENT


def _usable_history(current: Observation, history: list[Observation]) -> list[Observation]:
    """Successful polls carrying a structural hash, newest first, deduplicated.

    Sorted rather than trusted: an out-of-order record would otherwise truncate
    the window at the first old timestamp, and duplicates (a retried write,
    section 19.6) would inflate the poll count toward R5's minimum without any
    new observation having been made.

    Observations with no structural hash are dropped rather than treated as a
    change. A missing hash is absence of evidence; reading it as evidence of a
    change would let a single orphan 304 clear the frozen-content signal.
    """
    seen: dict[str, Observation] = {}
    for observation in [current, *history]:
        if observation.failed or observation.structural_hash is None:
            continue
        if try_parse(observation.polled_at) is None:
            continue
        seen.setdefault(observation.polled_at, observation)
    return sorted(seen.values(), key=lambda o: try_parse(o.polled_at), reverse=True)


def _count_movement(window: list[Observation], baseline: Observation | None) -> dict[str, int]:
    """Advances and regressions of `update_date` across the window.

    `baseline` is the successful poll immediately before the window opens. It is
    included as the starting comparison so an advance occurring across the cutoff
    is counted. Without it the first in-window observation has nothing to compare
    against and one advance is silently lost, which at exactly the quarantine
    threshold is the difference between WATCH and QUARANTINE.
    """
    detail = {"advances": 0, "regressions": 0}
    previous = try_parse(baseline.update_date) if baseline is not None else None
    for observation in reversed(window):  # oldest first
        stamp = try_parse(observation.update_date)
        if stamp is None:
            continue
        if previous is not None:
            if stamp > previous:
                detail["advances"] += 1
            elif stamp < previous:
                # Usually a publisher serving from inconsistent replicas. Counted
                # as a change but never as an advance, or a flapping replica set
                # would manufacture the quarantine condition.
                detail["regressions"] += 1
        previous = stamp
    return detail


def r5_frozen(current: Observation, history: list[Observation]) -> RuleOutcome:
    """Frozen structure, and the harder adversary it exists to catch.

    A publisher whose content and timestamp are both frozen is merely stale and
    R2 already has it. A publisher whose content is frozen while its timestamp
    advances is asserting freshness it does not have, and that is the quarantine
    case: no timestamp check sees it and no schema check can.

    The window is measured from the observation being scored rather than from a
    wall clock, so replaying retained observations reaches the verdict the live
    run reached.
    """
    detail = {"polls_in_window": 0, "advances": 0, "regressions": 0, "span_seconds": 0}
    if not current.has_body or current.structural_hash is None:
        return inapplicable("NO_BODY", **detail)

    ordered = _usable_history(current, history)
    newest = try_parse(ordered[0].polled_at)
    cutoff = newest - datetime.timedelta(seconds=R5_WINDOW_SECONDS)

    run: list[Observation] = []
    for observation in ordered:
        if observation.structural_hash != current.structural_hash:
            break
        run.append(observation)
    detail["span_seconds"] = int((newest - try_parse(run[-1].polled_at)).total_seconds())
    in_window = [o for o in run if try_parse(o.polled_at) >= cutoff]
    detail["polls_in_window"] = len(in_window)

    if try_parse(ordered[-1].polled_at) > cutoff:
        # The retained history does not reach back a full window. A distinct
        # console state, not a demerit and not a pass. Without this every
        # publisher looks frozen at fleet launch.
        return inapplicable("INSUFFICIENT_HISTORY", **detail)
    if try_parse(run[-1].polled_at) > cutoff:
        # History reaches back far enough AND the structure demonstrably changed
        # inside the window. A measured negative, so ADMIT.
        return evaluated(ADMIT, **detail)
    if len(in_window) < R5_MIN_POLLS:
        # Static across the window but too sparsely observed to say so.
        return inapplicable("INSUFFICIENT_HISTORY", **detail)

    # The predecessor is POSITIONAL, not same-hash. Searching the run for it
    # fails whenever the run ends exactly at the cutoff, and fails again when the
    # structure changed immediately before the window opened: both leave the run
    # with nothing behind it, and one advance vanishes. At exactly the
    # three-advance threshold that is the difference between WATCH and
    # QUARANTINE.
    baseline = ordered[len(in_window)] if len(ordered) > len(in_window) else None
    detail.update(_count_movement(in_window, baseline))
    if detail["advances"] >= R5_QUARANTINE_ADVANCES:
        return evaluated(QUARANTINE, **detail)
    return evaluated(WATCH, **detail)
