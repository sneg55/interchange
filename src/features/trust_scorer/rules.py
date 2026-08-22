"""Ruleset v1: R1 through R6. Section 6.4. R5 lives in `churn.py`.

Each rule is a pure function returning a `RuleOutcome`: a verdict, a reason, and
the measured inputs. No model call appears in this file or anywhere it is called
from, and none may be added: the deterministic gate is the architecture claim the
submission rests on.

Every rule returns NOT_APPLICABLE rather than ADMIT when its input is missing,
**and says why**. The reason matters as much as the verdict, because "measured,
and the rule does not apply here" must count toward a publisher's recovery while
"we could not evaluate it" must not. See `outcomes.py`.

Evaluation order is fixed R1..R6 and does not affect the verdict, which is a
maximum over independent rules; it fixes only the order of `fired_rules[]`, which
is what makes an evidence packet reproducible.
"""

from __future__ import annotations

from src.features.publisher_agent.observation import Observation
from src.services.schema_registry import SCHEMA_UNKNOWN
from src.utils.timestamps import try_parse

from .churn import R5_MIN_POLLS, R5_QUARANTINE_ADVANCES, R5_WINDOW_SECONDS, r5_frozen
from .outcomes import RuleOutcome, evaluated, inapplicable
from .verdicts import ADMIT, QUARANTINE, WATCH

# The single source of truth. Bump whenever a rule's VERDICT changes for any
# input, not only when a rule is added or removed: the version travels with every
# transition so that a decision made last month can be read against the rules
# that were in force then, and that promise fails silently if two different
# behaviours both call themselves the same version.
#
# v2: R6 gained `R6_CLOCK_SKEW_SECONDS` plus the poll's own latency as an
# allowance. Feeds between zero and roughly six seconds ahead scored WATCH under
# v1 and score ADMIT under v2. Draft packets opened under v1 therefore assert
# findings v2 does not make, which `superseded_ruleset` on the packet view
# reports rather than leaving for an approver to notice.
RULESET_VERSION = "v2"
RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6")

# Every ruleset version in order, oldest first, and which rules changed VERDICT
# in each. A version with no entry changed no rule's verdict.
RULESET_HISTORY = ("v1", "v2")
RULESET_CHANGES: dict[str, frozenset[str]] = {"v2": frozenset({"R6"})}


def rules_changed_since(version: str) -> frozenset[str]:
    """Rules whose verdict changed in any ruleset AFTER `version`.

    Used to decide whether a draft notice still asserts something this system
    makes. Per-rule rather than a bare version comparison, because the first cut
    of this flagged every packet opened under an older ruleset and that is too
    blunt: bumping v2 for a change to R6 marked a Hawaii DOT R2 quarantine
    superseded, when R2 reaches exactly the same verdict on exactly the same
    evidence under both versions. Blocking a still-true finding is its own kind
    of wrong answer.

    An unknown version returns every rule that ever changed. It cannot be placed
    in the history, so it cannot be shown to predate or postdate anything, and
    the safe direction is to make a human look.
    """
    if version not in RULESET_HISTORY:
        return frozenset().union(*RULESET_CHANGES.values()) if RULESET_CHANGES else frozenset()
    after = RULESET_HISTORY[RULESET_HISTORY.index(version) + 1 :]
    return frozenset().union(*(RULESET_CHANGES.get(v, frozenset()) for v in after), frozenset())


R1_WATCH_FAILURES = 3
R1_QUARANTINE_FAILURES = 12

R2_WATCH_ABSOLUTE_SECONDS = 7 * 86400
R2_QUARANTINE_ABSOLUTE_SECONDS = 30 * 86400
R2_WATCH_CADENCE_MULTIPLE = 3
R2_QUARANTINE_CADENCE_MULTIPLE = 10

R4_WATCH_FRACTION = 0.05
R4_QUARANTINE_FRACTION = 0.50

# How far into the future a feed's `update_date` may sit before R6 calls it
# forward-dated. Not zero, and the reason is measured rather than assumed.
#
# A publisher stamps `update_date` when it begins building the response; we stamp
# the poll when the response arrives. The gap between those two instants is the
# generation time plus the network, which is why the poll's own `latency_ms` is
# part of the allowance rather than a guess. On top of that, two servers that
# have never met keep clocks that differ by seconds.
#
# Measured on the live fleet after the per-poll timestamp fix: seven of nineteen
# publishers came in between 0.19 and 1.7 seconds ahead, against poll latencies
# of 0.8 to 0.9 seconds. With a zero tolerance every one of them was a WATCH and
# a notice accusing a named state DOT of publishing a future timestamp.
#
# Five seconds on top of the measured latency keeps that purpose intact. The
# branch exists to stop a publisher evading R2 by dating its header forward, and
# evading R2 means hiding staleness measured against a declared cadence of a
# minute at the very least. Five seconds hides nothing.
R6_CLOCK_SKEW_SECONDS = 5.0

# Rules that cannot be evaluated without a body. Section 6.4 keys two mechanisms
# off this set: a bodyless poll is never clean while one of them latches, and
# conditional GET is suspended in that state so a body can arrive.
BODY_DEPENDENT_RULES = frozenset({"R3", "R4", "R5"})

__all__ = [
    "BODY_DEPENDENT_RULES",
    "R1_QUARANTINE_FAILURES",
    "R1_WATCH_FAILURES",
    "R2_QUARANTINE_ABSOLUTE_SECONDS",
    "R2_WATCH_ABSOLUTE_SECONDS",
    "R4_QUARANTINE_FRACTION",
    "R4_WATCH_FRACTION",
    "R5_MIN_POLLS",
    "R5_QUARANTINE_ADVANCES",
    "R5_WINDOW_SECONDS",
    "RULESET_VERSION",
    "RULE_IDS",
    "r1_unreachable",
    "r2_stale",
    "r3_schema",
    "r4_contradiction",
    "r5_frozen",
    "r6_undeterminable",
]


def r1_unreachable(current: Observation, history: list[Observation]) -> RuleOutcome:
    """Consecutive polls the PUBLISHER did not answer.

    A 304 is a successful poll and ends the streak. Collapsing 304 into failure
    would make every well-behaved conditional-GET publisher look unreachable.

    A poll that never left Interchange is not evidence about the publisher and is
    not counted. This rule used to count any failed poll, so an Interchange-side
    outage or a missing offline capture produced a QUARANTINE and drafted a
    notice to the registry owner asserting a feed had been unreachable, when the
    only thing that had failed was at this end. Not-applicable rather than a
    pass: we did not learn that the feed was up, and R1 is the one rule that
    could otherwise clear a latch on our own gap.
    """
    if current.unreached:
        return inapplicable("MISSING_INPUT")
    streak = 0
    for observation in [current, *history]:
        if observation.unreached:
            # Neither a failure nor a success. The run of failures cannot be
            # established across a poll that says nothing, so counting stops
            # here rather than treating our gap as the publisher recovering.
            break
        if not observation.failed:
            break
        streak += 1
    if streak >= R1_QUARANTINE_FAILURES:
        return evaluated(QUARANTINE, consecutive_failures=streak)
    if streak >= R1_WATCH_FAILURES:
        return evaluated(WATCH, consecutive_failures=streak)
    return evaluated(ADMIT, consecutive_failures=streak)


def r2_stale(current: Observation, declared_cadence_seconds: int) -> RuleOutcome:
    """Age of `update_date` against both an absolute floor and the declared cadence.

    Relative as well as absolute because a flat bound is wrong at both ends of
    this fleet: Hawaii DOT declares a 168h cadence and a seven day rule would
    libel it, while 14 publishers declare 1m.

    Not-applicable when the age is unknown. R6 is the rule that reacts to the
    unusable timestamp itself; treating it as merely unevaluable everywhere would
    make the finding disappear.
    """
    if current.failed:
        return inapplicable("NO_BODY")
    age = current.update_age_seconds
    if age is None:
        return inapplicable("MISSING_INPUT")
    if age < 0:
        # Forward-dated header. R6's condition, not a negative staleness, and
        # clamping to zero here would let a publisher evade R2 by dating ahead.
        return inapplicable("MISSING_INPUT", update_age_seconds=int(age))
    cadence = max(1, declared_cadence_seconds)
    detail = {"update_age_seconds": int(age), "declared_cadence_seconds": cadence}
    if age > max(R2_QUARANTINE_ABSOLUTE_SECONDS, R2_QUARANTINE_CADENCE_MULTIPLE * cadence):
        return evaluated(QUARANTINE, **detail)
    if age > max(R2_WATCH_ABSOLUTE_SECONDS, R2_WATCH_CADENCE_MULTIPLE * cadence):
        return evaluated(WATCH, **detail)
    return evaluated(ADMIT, **detail)


def r3_schema(current: Observation) -> RuleOutcome:
    """Errors against the feed's OWN declared version. Never quarantines alone.

    A schema error is a defect, not a lie. An unresolvable version records
    SCHEMA_UNKNOWN and suppresses this rule rather than failing the publisher: no
    one may be penalised for publishing a specification Interchange has not
    implemented.
    """
    if not current.has_body:
        return inapplicable("NO_BODY")
    if current.schema_version_used in (None, SCHEMA_UNKNOWN):
        return inapplicable("SCHEMA_UNKNOWN")
    if current.schema_error_count is None:
        return inapplicable("MISSING_INPUT")
    errors = current.schema_error_count
    return evaluated(WATCH if errors > 0 else ADMIT, schema_error_count=errors)


def r4_contradiction(current: Observation) -> RuleOutcome:
    """Share of `active` zones whose `end_date` has already passed.

    Zero active zones is not-applicable, not a pass: Hawaii DOT publishes no
    `event_status` at all on any of its 80 features, and a percentage over zero
    is undefined.
    """
    if not current.has_body:
        return inapplicable("NO_BODY")
    active, past = current.active_count, current.active_with_past_end_date
    if active is None or past is None:
        return inapplicable("MISSING_INPUT")
    if active > 0 and current.active_undated is None:
        # An absent undated count is not a measured zero. With 100 active zones
        # and 5 past, the dated denominator could be 100 or 5, and the rule
        # cannot tell 5 percent from 100 percent. MISSING_INPUT rather than
        # MEASURED_INAPPLICABLE, so a run of such polls cannot retire a latch.
        return inapplicable("MISSING_INPUT", active_count=active)
    dated = active - (current.active_undated or 0)
    detail = {"active_count": active, "dated_active": dated, "past_end_date": past}
    if dated <= 0:
        # Genuinely nothing to judge, measured over a real body. This is the
        # publisher that complied by moving every offending zone out of `active`,
        # and it must count toward recovery.
        return inapplicable("MEASURED_INAPPLICABLE", **detail)
    fraction = past / dated
    if fraction > R4_QUARANTINE_FRACTION:
        return evaluated(QUARANTINE, **detail)
    if fraction > R4_WATCH_FRACTION:
        return evaluated(WATCH, **detail)
    return evaluated(ADMIT, **detail)


def r6_undeterminable(current: Observation) -> RuleOutcome:
    """Missing, unparseable or forward-dated `update_date`. Never quarantines alone.

    Suppressed on a failed poll: a transport failure has no document and
    therefore no timestamp, and letting R6 fire there would raise WATCH on the
    first failed poll, making R1's three-poll threshold meaningless.
    """
    if current.failed:
        return inapplicable("SUPPRESSED")
    if current.update_date is None:
        return evaluated(WATCH, cause="missing")
    if try_parse(current.update_date) is None:
        return evaluated(WATCH, cause="unparseable")
    if current.update_age_seconds is not None:
        # A feed claiming to be fresher than now is malformed. This is the branch
        # that stops a publisher evading R2 by dating its header forward.
        #
        # The allowance is the poll's own latency plus a clock-skew constant, and
        # both halves are load-bearing: a timestamp written when the response
        # began is legitimately ahead of the moment it arrived, and independent
        # servers disagree by seconds. Reported with the verdict, so a notice can
        # say what it was measured against instead of asserting a bare verdict.
        allowance = R6_CLOCK_SKEW_SECONDS + (current.latency_ms or 0.0) / 1000.0
        if current.update_age_seconds < -allowance:
            return evaluated(
                WATCH,
                cause="forward_dated",
                seconds_ahead=round(-current.update_age_seconds, 3),
                allowance_seconds=round(allowance, 3),
            )
    return evaluated(ADMIT)
