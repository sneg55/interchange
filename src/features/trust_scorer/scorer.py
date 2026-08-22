"""The trust scorer: evaluate, latch, escalate, de-escalate. Section 6.4.

Pure. Takes the observation, the publisher's history and its current state, and
returns a `RuleEvaluation` plus an optional `TrustTransition`. Nothing here reads
a clock or a store, so a ruleset change can be re-evaluated against retained
observations without rewriting history, and every decision is reproducible from
the records it wrote.

No model call appears in this path and none may be added. The deterministic gate
is the architecture claim the submission rests on, and a scalar confidence from a
model would invite a threshold, which is a gate.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from src.features.publisher_agent.observation import Observation
from src.utils.timestamps import try_parse

from . import rules
from .records import RuleEvaluation, RuleResult, TrustTransition
from .verdicts import (
    ADMIT,
    NOT_APPLICABLE,
    QUARANTINE,
    QUARANTINE_TO_WATCH_CLEAN_POLLS,
    QUARANTINE_TO_WATCH_MIN_HOURS,
    WATCH,
    WATCH_TO_ADMIT_CLEAN_POLLS,
    FleetState,
    is_more_severe,
    most_severe,
    severity,
)


@dataclass(slots=True)
class ScoreResult:
    """What one poll did to a publisher's standing.

    The counters are returned rather than stored because the scorer holds no
    state. The caller persists them on the `PublisherRecord` and hands them back
    on the next poll, which is what keeps a replay over retained observations
    producing the same answer as the original run.
    """

    evaluation: RuleEvaluation
    transition: TrustTransition | None
    latching_rule_ids: list[str]
    clean_streak: int
    streak_started_at: str | None = None

    @property
    def state(self) -> FleetState:
        return self.evaluation.resulting_state


def _latching_for(
    state: FleetState, results: list[RuleResult], previous: list[str]
) -> list[str]:
    """The rules holding the publisher at `state`.

    Union with the previous set rather than replacement. A rule that imposed the
    state and has since become unevaluable must stay latched, or a run of
    bodyless polls would quietly drop it and the state would decay on evidence
    nobody gathered. The set is cleared only by a completed de-escalation, which
    is the one event that means the publisher earned its way out.
    """
    if state == ADMIT:
        # Nothing holds a publisher at ADMIT. Latching every passing rule here
        # would suspend conditional GET for the whole healthy fleet, since the
        # set would always contain a body-dependent rule.
        return []
    firing = {
        r.rule_id
        for r in results
        if r.verdict != NOT_APPLICABLE and severity(r.verdict) >= severity(state)
    }
    return sorted(set(previous) | firing)


class TrustScorer:
    def __init__(self, ruleset_version: str = rules.RULESET_VERSION) -> None:
        self.ruleset_version = ruleset_version

    def evaluate(
        self,
        observation: Observation,
        history: list[Observation],
        declared_cadence_seconds: int,
        now: datetime.datetime,
    ) -> list[RuleResult]:
        """Run all six rules in fixed order. `history` is newest-first, excluding
        `observation` itself."""
        del now  # every window is measured from the observation being scored
        outcomes = [
            ("R1", rules.r1_unreachable(observation, history)),
            ("R2", rules.r2_stale(observation, declared_cadence_seconds)),
            ("R3", rules.r3_schema(observation)),
            ("R4", rules.r4_contradiction(observation)),
            ("R5", rules.r5_frozen(observation, history)),
            ("R6", rules.r6_undeterminable(observation)),
        ]
        return [
            RuleResult(rule_id, outcome.verdict, outcome.reason, outcome.detail)
            for rule_id, outcome in outcomes
        ]

    def score(
        self,
        observation: Observation,
        history: list[Observation],
        current_state: FleetState,
        declared_cadence_seconds: int,
        now: datetime.datetime,
        latching_rule_ids: list[str] | None = None,
        clean_streak: int = 0,
        clean_streak_started_at: str | None = None,
    ) -> ScoreResult:
        """Score one poll.

        `latching_rule_ids` are the rules that put the publisher in its current
        state. They are the reason a caller cannot just count clean polls: a poll
        is clean only if every currently-latching rule was evaluated WITH A BODY
        on it, and only the caller's persisted latching set makes that decidable.
        """
        latching = list(latching_rule_ids or [])
        results = self.evaluate(observation, history, declared_cadence_seconds, now)
        instantaneous = most_severe([r.verdict for r in results])
        clean = self._is_clean(observation, instantaneous, results, latching)

        if is_more_severe(instantaneous, current_state):
            # Escalation is immediate. A feed that has just gone dark should not
            # keep serving traffic for six hours while a counter fills.
            new_state = instantaneous
            new_latching = _latching_for(new_state, results, latching)
            new_streak, streak_started = 0, None
        else:
            new_streak = clean_streak + 1 if clean else 0
            streak_started = (clean_streak_started_at or observation.polled_at) if clean else None
            new_state = self._de_escalate(current_state, new_streak, streak_started, observation)
            if new_state != current_state:
                # A completed recovery clears the latch and the counter; the next
                # step down has to be earned from zero.
                new_latching, new_streak, streak_started = [], 0, None
            else:
                # Rules firing AT the current severity latch too, not only ones
                # that raised it. A publisher already at WATCH whose R3 starts
                # firing is held there by R3, and leaving the latch empty would
                # let bodyless polls make R3 not-applicable, count as clean, and
                # walk the publisher to ADMIT without anyone re-validating it.
                new_latching = _latching_for(new_state, results, latching)

        evaluation = RuleEvaluation(
            publisher_key=observation.publisher_key,
            observation_id=observation.doc_id,
            evaluated_at=observation.polled_at,
            ruleset_version=self.ruleset_version,
            results=results,
            instantaneous_verdict=instantaneous,
            resulting_state=new_state,
            clean=clean,
        )
        transition = self._transition(observation, current_state, new_state, results)
        return ScoreResult(evaluation, transition, new_latching, new_streak, streak_started)

    # ------------------------------------------------------------------ detail

    @staticmethod
    def _is_clean(
        observation: Observation,
        instantaneous: str,
        results: list[RuleResult],
        latching: list[str],
    ) -> bool:
        """Section 6.4's clean poll.

        A successful poll on which the instantaneous verdict is ADMIT *and* on
        which every rule currently latching the publisher was actually EVALUATED.

        "Evaluated" is the reason on the outcome, not merely the presence of a
        body. Three cases have to come apart and only the reason separates them:

        - **A 304.** R4 reports NO_BODY. Nothing was measured, so the poll is not
          clean: twelve 304s must not clear an R4 quarantine nobody re-measured.
        - **A body missing a field the rule needs**, for instance an observation
          from an older agent build with no `active_undated`. R4 reports
          MISSING_INPUT. Also not clean, for the same reason, and a body-only
          test would have accepted it.
        - **A body over which the rule genuinely does not apply.** The publisher
          moved every offending zone out of `active`, so R4 reports
          MEASURED_INAPPLICABLE. That IS clean: the publisher did what the
          finding asked, and refusing it would make complying a trap with no way
          out, since de-escalation needs the clean polls it can no longer
          produce.
        """
        if observation.failed or instantaneous != ADMIT:
            return False
        by_id = {r.rule_id: r for r in results}
        return all(
            by_id[rule_id].evaluated for rule_id in latching if rule_id in by_id
        )

    @staticmethod
    def _de_escalate(
        current: FleetState,
        clean_streak: int,
        streak_started_at: str | None,
        observation: Observation,
    ) -> FleetState:
        if current == QUARANTINE:
            if clean_streak < QUARANTINE_TO_WATCH_CLEAN_POLLS:
                return current
            # Polls alone are not enough: at the 60 minute ceiling twelve polls
            # span twelve hours, but at the five minute floor they span one, and
            # an hour of good behaviour should not retire a quarantine.
            started = try_parse(streak_started_at)
            ended = try_parse(observation.polled_at)
            if started is None or ended is None:
                return current
            hours = (ended - started).total_seconds() / 3600
            return WATCH if hours >= QUARANTINE_TO_WATCH_MIN_HOURS else current
        if current == WATCH:
            return ADMIT if clean_streak >= WATCH_TO_ADMIT_CLEAN_POLLS else current
        return current

    def _transition(
        self,
        observation: Observation,
        from_state: FleetState,
        to_state: FleetState,
        results: list[RuleResult],
    ) -> TrustTransition | None:
        if from_state == to_state:
            return None
        firing = sorted(
            (r for r in results if r.verdict not in (NOT_APPLICABLE, ADMIT)),
            key=lambda r: severity(r.verdict),
            reverse=True,
        )
        return TrustTransition(
            publisher_key=observation.publisher_key,
            at=observation.polled_at,
            from_state=from_state,
            to_state=to_state,
            rule_ids=[r.rule_id for r in firing],
            primary_rule_id=firing[0].rule_id if firing else None,
            direction="ESCALATION" if is_more_severe(to_state, from_state) else "DE_ESCALATION",
            ruleset_version=self.ruleset_version,
            observation_ids=[observation.doc_id],
        )
