"""RuleEvaluation and TrustTransition. Section 7.

`RuleEvaluation` is separate from `Observation` because the two have different
authors. Putting `fired_rules[]` inside an append-only observation, as an earlier
revision did, required the scorer to mutate a record the agent owns and the spec
calls immutable. Splitting them also means a ruleset change can be re-evaluated
against retained observations without rewriting history.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .outcomes import UNEVALUATED_REASONS, Reason
from .verdicts import NOT_APPLICABLE, FleetState, Verdict

Direction = Literal["ESCALATION", "DE_ESCALATION"]


@dataclass(slots=True)
class RuleResult:
    rule_id: str
    verdict: Verdict
    # Why, not just what. A NOT_APPLICABLE that was measured (the rule genuinely
    # does not apply) and one that could not be evaluated look identical without
    # this, and they must be treated oppositely when deciding whether a poll was
    # clean. See outcomes.py.
    reason: Reason = "EVALUATED"
    # Free-form measured inputs, so an evidence packet can show what the rule saw
    # rather than assert what it concluded. R5's advance count lives here.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def evaluated(self) -> bool:
        return self.reason not in UNEVALUATED_REASONS


@dataclass(slots=True)
class RuleEvaluation:
    publisher_key: str
    observation_id: str
    evaluated_at: str
    ruleset_version: str
    results: list[RuleResult]
    instantaneous_verdict: Verdict
    resulting_state: FleetState
    # Not in the section 7 field list, and derived rather than authoritative: it
    # is recomputable from `results` plus the latching set. Stored because the
    # hysteresis counters walk back over evaluations and recomputing cleanliness
    # for each one requires knowing which rules were latching at that time, which
    # is exactly the state a replay does not have.
    clean: bool = False

    @property
    def fired_rule_ids(self) -> list[str]:
        """Rules returning a verdict more severe than ADMIT, in fixed R1..R6 order.

        Order does not affect the verdict, which is a maximum over independent
        rules. It fixes only this list, which is what makes an evidence packet
        reproducible.
        """
        return [r.rule_id for r in self.results if r.verdict not in (NOT_APPLICABLE, "ADMIT")]

    @property
    def evaluated_rule_ids(self) -> list[str]:
        """Rules that actually ran. The complement is 'not checked', not 'passed'."""
        return [r.rule_id for r in self.results if r.verdict != NOT_APPLICABLE]

    @property
    def evidence_depth(self) -> int:
        """How many observations back the FIRED rules actually looked.

        An evidence packet is built from this many, newest first, so the window
        it cites covers the polls its own assertion is about. Every packet used
        to embed the single observation that tripped the transition, which made
        every notice say "the feed did not respond across consecutive polls" over
        a window whose start equalled its end and one poll of evidence. The
        publisher's own page listed the thirteen consecutive failures that would
        have supported the claim; none of them was in the packet.

        Read off the rules' own `detail` rather than a constant, so a threshold
        change cannot leave the packet citing a window the rule no longer uses.
        """
        depth = 1
        for result in self.results:
            if result.verdict in (NOT_APPLICABLE, "ADMIT"):
                continue
            for key in ("consecutive_failures", "polls_in_window"):
                value = result.detail.get(key)
                if isinstance(value, int) and value > depth:
                    depth = value
        return depth

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["fired_rules"] = self.fired_rule_ids
        return doc


@dataclass(slots=True)
class TrustTransition:
    publisher_key: str
    at: str
    from_state: FleetState
    to_state: FleetState
    rule_ids: list[str]  # all rules firing, most severe first
    primary_rule_id: str | None
    direction: Direction
    ruleset_version: str
    observation_ids: list[str]
    evidence_packet_id: str | None = None

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)
