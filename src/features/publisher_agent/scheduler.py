"""Poll scheduling: the clamp, adaptive backoff, and when to send a conditional
request. Section 6.3, with the suspension rule from 6.4.

This is an ingress decision with measured inputs. Honouring every declared
cadence is 23.2 GB/day; the five minute floor takes it to 10.8; backoff and
conditional GET do most of the rest. The ceiling is not a saving so much as a
guarantee: a publisher declaring 168h is still observed often enough for R1 and
R5 to mean anything.
"""

from __future__ import annotations

import datetime

from src.features.registry_warden.cadence import MAX_POLL_SECONDS, clamp
from src.utils.timestamps import try_parse

from .observation import Observation

# Consecutive unchanged content hashes before dropping to the ceiling. Matches
# R5's minimum window, so a publisher that has just tripped backoff is exactly at
# the point where R5 can first speak about it.
BACKOFF_AFTER_UNCHANGED_POLLS = 12

# Rules that cannot be evaluated without a body. While one of these is latching a
# publisher, conditional GET is suspended: otherwise a genuinely-unchanged
# publisher answers 304 forever and can never accumulate a clean poll, and the
# quarantine becomes permanent for a reason unrelated to its behaviour.
BODY_DEPENDENT_RULES = frozenset({"R3", "R4", "R5"})


def unchanged_streak(history: list[Observation]) -> int:
    """How many consecutive recent polls carried the same content hash.

    `history` is newest first. Failed polls end the streak rather than extending
    it: an unreachable publisher is not a publisher whose content is stable, and
    counting it as one would back off exactly when observation matters most.

    A 304 extends the streak. That is the point of conditional GET: the publisher
    has told us the content is unchanged, and re-fetching to confirm it would
    spend the bytes the mechanism exists to save.
    """
    newest = next((o for o in history if not o.failed), None)
    if newest is None or newest.content_hash is None:
        return 0
    streak = 0
    for observation in history:
        if observation.failed or observation.content_hash != newest.content_hash:
            break
        streak += 1
    return streak


def poll_interval_seconds(
    declared_cadence_seconds: int,
    history: list[Observation],
    demo_pinned: bool = False,
) -> int:
    """The next interval for one publisher, in seconds.

    Demo pinning exempts a publisher from backoff only. It never escapes the
    clamp, because the floor is the ingress bound and a demo is not a reason to
    triple the fleet's daily traffic.
    """
    base = clamp(declared_cadence_seconds)
    if demo_pinned:
        return base
    if unchanged_streak(history) >= BACKOFF_AFTER_UNCHANGED_POLLS:
        # Utah has been frozen for over three years and does not warrant a poll
        # every five minutes. Backed-off publishers are also the uncompressed
        # ones, which is why this saves more than the floor does.
        return MAX_POLL_SECONDS
    return base


def due(
    last_polled_at: object,
    interval_seconds: int,
    now: datetime.datetime,
    cycle_interval_seconds: int = 0,
) -> bool:
    """Whether this publisher's next poll has come round.

    The counterpart to `poll_interval_seconds`, and for a long time the missing
    one: the interval was computed, written onto the record and rendered in the
    console as `backoff_active`, and nothing read it back. Every cycle polled
    every pollable publisher, so the reduction section 6.3 calls the largest of
    the three was not happening at all.

    Never polled, no interval decided, or an unparseable stamp all return True.
    A missing measurement is not evidence that a poll happened recently, and the
    failure of erring that way is a publisher that silently stops being observed.

    `cycle_interval_seconds` is how often the FLEET runs, which is a different
    thing from how often this publisher should be polled and is why a strict
    comparison is wrong. A fleet cycling every 900s never lands exactly on a
    3600s interval: it sees 2700 and 3600, and a strict test defers the poll to
    3600 only if the cycles happen to align, drifting to 4500 the moment they do
    not. So a poll is due when this cycle is CLOSER to the deadline than the next
    one would be, which is `elapsed >= interval - cycle/2`. The average interval
    then comes out right instead of running systematically late, and late is the
    expensive direction: R1 and R5 count polls inside a window.

    Zero, the default, means the caller has not said, and the comparison is
    strict. That keeps the clamp floor exact for a caller that steps time itself,
    which is what the seed and the tests do.
    """
    if interval_seconds <= 0:
        return True
    last = try_parse(last_polled_at)
    if last is None:
        return True
    tolerance = max(0, cycle_interval_seconds) / 2
    return (now - last).total_seconds() >= interval_seconds - tolerance


def send_conditional(latching_rule_ids: list[str] | set[str] | None) -> bool:
    """Whether this poll may carry `If-None-Match`.

    False while any body-dependent rule is latching the publisher. The cost is
    real bandwidth, spent only on publishers already known to be a problem, which
    is the right place for it.
    """
    if not latching_rule_ids:
        return True
    return not (BODY_DEPENDENT_RULES & set(latching_rule_ids))
