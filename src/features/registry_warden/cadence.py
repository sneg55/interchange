"""Parsing the registry's declared poll cadence, and clamping it. Section 6.3.

The field is `datafeed_frequency_update`. The similarly named `updatefrequency`
is absent from every live entry and must never be read: a parser that falls back
to it would silently produce the default for all 40 publishers while looking like
it worked.
"""

from __future__ import annotations

import re

from src.constants.error_ids import AppError, ErrorIds

# Values observed live: 1m, 2m, 3m, 4m, 5m, 10m, 15m, 30m, 60m, 60s, 72h, 168h.
_CADENCE = re.compile(r"^\s*(\d+)\s*([smh])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}

# Quebec City is the one active entry declaring nothing. Section 6.3.
DEFAULT_CADENCE_SECONDS = 3600

MIN_POLL_SECONDS = 300  # 5 minutes
MAX_POLL_SECONDS = 3600  # 60 minutes


def parse_cadence(declared: str | None) -> int | None:
    """Declared cadence in seconds, or None when nothing was declared.

    None and "unparseable" are deliberately not the same as the default. The
    caller applies the default; returning 3600 from here would make an absent
    cadence indistinguishable from a declared `60m`, and section 6.1 records a
    `CADENCE_CHANGED` event on exactly that difference.
    """
    if declared is None:
        return None
    text = str(declared).strip()
    if not text:
        return None
    match = _CADENCE.match(text)
    if match is None:
        raise AppError(
            ErrorIds.REG_BAD_SHAPE,
            f"unparseable datafeed_frequency_update: {declared!r}",
            {"declared": declared},
        )
    value, unit = int(match.group(1)), match.group(2).lower()
    seconds = value * _UNIT_SECONDS[unit]
    if seconds <= 0:
        raise AppError(
            ErrorIds.REG_BAD_SHAPE,
            f"non-positive cadence: {declared!r}",
            {"declared": declared},
        )
    return seconds


def cadence_or_default(declared: str | None) -> int:
    """Declared cadence, falling back to the default, never raising.

    A registry entry with a cadence this parser does not understand is a
    publisher we still have to poll. Refusing to provision it would let a typo in
    one field remove a whole organization from the fleet, and the fleet size is
    supposed to be derived from the registry rather than from what we could
    parse.
    """
    try:
        parsed = parse_cadence(declared)
    except AppError:
        return DEFAULT_CADENCE_SECONDS
    return DEFAULT_CADENCE_SECONDS if parsed is None else parsed


def clamp(seconds: int) -> int:
    """Hold every publisher to [5 min, 60 min] regardless of what it declares.

    The floor is an ingress decision measured in section 6.3: the declared
    cadences come to 23.2 GB/day and a five minute floor cuts that to 10.8. The
    ceiling exists so a publisher declaring 168h is still observed often enough
    for R1 and R5 to mean anything over a demo window.
    """
    return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, seconds))
