"""A rule's outcome, and why. Section 6.4.

`NOT_APPLICABLE` alone turned out not to be enough. It conflates two situations
that must be treated oppositely when deciding whether a poll was clean:

- **Measured and inapplicable.** R4 over a publisher that moved every offending
  zone out of `active` has zero active zones. The rule genuinely does not apply,
  over a real body, and the publisher has complied. This must count toward
  recovery, or complying with a finding is a trap.
- **Unevaluable.** R4 on a 304, or on an observation missing `active_undated`
  because it came from an older agent build. Nothing was measured. This must NOT
  count toward recovery, or a run of such polls retires a quarantine nobody
  re-measured.

Both were `NOT_APPLICABLE` and the difference was invisible, which let the second
case clear a latch. The reason is what separates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .verdicts import NOT_APPLICABLE, Verdict

Reason = Literal[
    "EVALUATED",  # the rule ran and its verdict is a measurement
    "MEASURED_INAPPLICABLE",  # ran over a real body; the rule does not apply here
    "NO_BODY",  # a 304 or a failed poll; nothing to measure
    "MISSING_INPUT",  # a body, but a field the rule needs is absent
    "SCHEMA_UNKNOWN",  # no schema published for the declared version
    "SUPPRESSED",  # deliberately silenced, e.g. R6 on a failed poll
    "INSUFFICIENT_HISTORY",  # not enough retained observations to speak
]

# Reasons meaning "we did not check". A rule latching a publisher and reporting
# one of these leaves the latch in place: the poll is not clean.
UNEVALUATED_REASONS = frozenset(
    {"NO_BODY", "MISSING_INPUT", "SCHEMA_UNKNOWN", "SUPPRESSED", "INSUFFICIENT_HISTORY"}
)


@dataclass(slots=True)
class RuleOutcome:
    verdict: Verdict
    reason: Reason = "EVALUATED"
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def evaluated(self) -> bool:
        """Whether this outcome rests on a measurement.

        True for a real verdict AND for measured-inapplicable, because both mean
        the rule looked at a body. False for every reason in
        UNEVALUATED_REASONS.
        """
        return self.reason not in UNEVALUATED_REASONS


def evaluated(verdict: Verdict, **detail: Any) -> RuleOutcome:
    return RuleOutcome(verdict, "EVALUATED", detail)


def inapplicable(reason: Reason, **detail: Any) -> RuleOutcome:
    return RuleOutcome(NOT_APPLICABLE, reason, detail)
