"""RFC 3339 parsing, strict about shape and lenient about precision.

WZDx requires RFC 3339 date-times. `datetime.fromisoformat` alone is the wrong
tool in both directions: it rejects timestamps that are valid (Utah DOT serves
seven fractional digits) and accepts ones that are not (a bare date, an offset
that is simply missing).

Both directions matter to the product. Rejecting Utah's timestamp would make the
publisher whose frozen feed is the headline finding look merely unparseable.
Accepting a bare date, and silently assuming UTC, would let a malformed header
pass as a good one and evade R6, the rule whose entire job is to notice that a
timestamp is unusable.
"""

from __future__ import annotations

import datetime
import re

ISO_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:?\d{2})$"
)


def parse_stamp(stamp: object) -> datetime.datetime:
    """Parse an RFC 3339 date-time, raising ValueError otherwise.

    A non-string raises ValueError rather than AttributeError, because every
    caller guards on ValueError and an AttributeError from here would escape as
    an unhandled failure on a poll that should simply have recorded a bad
    timestamp.
    """
    if not isinstance(stamp, str):
        raise ValueError(f"timestamp must be a string, got {type(stamp).__name__}")
    match = ISO_DATETIME.match(stamp.strip())
    if not match:
        raise ValueError(f"not an RFC 3339 date-time: {stamp!r}")
    date, clock, fraction, offset = match.groups()
    # RFC 3339 allows any number of fractional digits; normalise to six.
    fraction = (fraction or ".0")[1:][:6].ljust(6, "0")
    offset = "+00:00" if offset in ("Z", "z") else offset
    if len(offset) == 5:  # +HHMM, which fromisoformat rejects before 3.11
        offset = offset[:3] + ":" + offset[3:]
    return datetime.datetime.fromisoformat(f"{date}T{clock}.{fraction}{offset}")


def try_parse(stamp: object) -> datetime.datetime | None:
    try:
        return parse_stamp(stamp)
    except ValueError:
        return None


def age_seconds(stamp: object, now: datetime.datetime) -> float | None:
    """Seconds since `stamp`, or None when it cannot be parsed.

    None is not zero and not a large number. It means the age is unknown, and
    section 6.4 requires R2 to be NOT_APPLICABLE on it rather than treating an
    unparseable timestamp as either fresh or stale. R6 is the rule that reacts to
    the unparseability itself.

    A future timestamp yields a negative age rather than being clamped to zero.
    Section 6.4 makes a forward-dated header an R6 condition, and clamping here
    would erase the evidence R6 reads: a publisher could then evade R2 simply by
    dating its header into next week.
    """
    parsed = try_parse(stamp)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.UTC).isoformat()
