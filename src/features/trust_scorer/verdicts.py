"""Verdicts and their severity ordering. Section 6.4.

`NOT_APPLICABLE` is a first-class verdict, not a missing one. It contributes
nothing to the maximum, is recorded distinctly on the `RuleEvaluation`, and is
what stops "we did not check" being stored as "we checked and it passed".

That distinction is the reason this file exists rather than a bare string
comparison: a naive `max()` over an ordering that ranks NOT_APPLICABLE alongside
ADMIT would silently admit a publisher on the strength of rules that never ran.
"""

from __future__ import annotations

from typing import Literal

Verdict = Literal["NOT_APPLICABLE", "ADMIT", "WATCH", "QUARANTINE"]
FleetState = Literal["ADMIT", "WATCH", "QUARANTINE", "NO_ACCESS"]

NOT_APPLICABLE: Verdict = "NOT_APPLICABLE"
ADMIT: Verdict = "ADMIT"
WATCH: Verdict = "WATCH"
QUARANTINE: Verdict = "QUARANTINE"

# NOT_APPLICABLE sits BELOW ADMIT so it can never raise the maximum, and is
# excluded entirely when nothing else was evaluable.
_SEVERITY: dict[str, int] = {NOT_APPLICABLE: -1, ADMIT: 0, WATCH: 1, QUARANTINE: 2}

# De-escalation hysteresis. Section 6.4: any non-clean poll resets the counter.
QUARANTINE_TO_WATCH_CLEAN_POLLS = 12
QUARANTINE_TO_WATCH_MIN_HOURS = 6
WATCH_TO_ADMIT_CLEAN_POLLS = 6


def severity(verdict: str) -> int:
    return _SEVERITY[verdict]


def most_severe(verdicts: list[str]) -> Verdict:
    """The instantaneous verdict: the most severe outcome any rule returns.

    Returns WATCH when every rule was not-applicable. A publisher on which
    nothing could be evaluated has not passed anything, and returning ADMIT would
    be the exact failure this project exists to catch: an unmeasured publisher
    admitted because no rule spoke against it.
    """
    evaluated = [v for v in verdicts if v != NOT_APPLICABLE]
    if not evaluated:
        return WATCH
    return max(evaluated, key=severity)  # type: ignore[return-value]


def is_more_severe(candidate: str, current: str) -> bool:
    return severity(candidate) > severity(current)
