"""Time, injected rather than read from the wall.

Spec section 12 requires it: staleness (R2), the churn window (R5) and hysteresis
all depend on elapsed time, and none of them is testable if the code calls
`datetime.now()` directly. Utah is 1,236 days stale and R5 needs a 24-hour
window; no test suite can wait for either.

Every component takes a Clock. Production passes SystemClock; tests pass
FrozenClock and advance it explicitly.
"""

from __future__ import annotations

import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime.datetime:
        """Current time, always timezone-aware and always UTC."""
        ...


class SystemClock:
    """Wall clock. The only implementation that reads real time."""

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.UTC)


class FrozenClock:
    """Test clock. Starts where you put it and moves only when told.

    Naive datetimes are rejected rather than coerced: a rule comparing an aware
    feed timestamp against a naive test clock raises TypeError deep inside a
    comparison, and the resulting failure points at the rule instead of the test.
    """

    def __init__(self, start: datetime.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self._now = start.astimezone(datetime.UTC)

    def now(self) -> datetime.datetime:
        return self._now

    def advance(self, **kwargs: float) -> datetime.datetime:
        """Move forward. Accepts any timedelta keyword: seconds, hours, days."""
        delta = datetime.timedelta(**kwargs)
        if delta < datetime.timedelta(0):
            raise ValueError("FrozenClock only moves forward")
        self._now += delta
        return self._now

    def set(self, when: datetime.datetime) -> datetime.datetime:
        if when.tzinfo is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self._now = when.astimezone(datetime.UTC)
        return self._now
