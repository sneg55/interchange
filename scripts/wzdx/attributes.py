"""Non-spatial attribute access and the deterministic corroboration checks.

WZDx moved `event_status`, `road_names`, `direction` and `description` from
the properties level in v4.0 into `core_details` from v4.1 onward, so every
accessor here checks both rather than preferring one version.
"""

import datetime
import re

# Order matters: the interstate and route prefixes must run before the
# single-letter compass rules, or "State Route N" loses its route token.
ROAD_SUB = [
    (r"\binterstate\b", "i"),
    (r"\bi[- ]?(?=\d)", "i "),
    (r"\bu s\b|\bus route\b|\bus highway\b|\bus hwy\b", "us"),
    (r"\bstate route\b|\bstate rte\b|\bstate highway\b|\bsr\b|\bsh\b", "sr"),
    (r"\bcounty road\b|\bcounty rd\b|\bcr\b", "cr"),
    (r"\bstreet\b", "st"),
    (r"\bavenue\b", "ave"),
    (r"\broad\b", "rd"),
    (r"\bdrive\b", "dr"),
    (r"\bhighway\b", "hwy"),
    (r"\bboulevard\b", "blvd"),
    (r"\bparkway\b", "pkwy"),
    (r"\bnorth\b", "n"),
    (r"\bsouth\b", "s"),
    (r"\beast\b", "e"),
    (r"\bwest\b", "w"),
]


def core(feature):
    props = feature.get("properties") or {}
    return props.get("core_details") or props


def normalize_road(name):
    s = re.sub(r"[^a-z0-9 ]+", " ", name.lower().strip())
    for pat, rep in ROAD_SUB:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def road_names(feature):
    return {normalize_road(n) for n in (core(feature).get("road_names") or []) if n}


def direction(feature):
    return core(feature).get("direction") or "unknown"


def description(feature):
    return (core(feature).get("description") or "").strip()


def date_range(feature):
    props = feature.get("properties") or {}
    return props.get("start_date"), props.get("end_date")


ISO_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(\.\d+)?([Zz]|[+-]\d{2}:?\d{2})$")


def parse_stamp(stamp):
    """Parse an RFC 3339 date-time as WZDx requires, raising ValueError otherwise.

    Strict about shape and lenient about precision, which is the opposite of
    what `datetime.fromisoformat` alone gives:

    - Utah DOT serves `2023-03-19T07:04:04.8614897-06:00`, seven fractional
      digits. RFC 3339 allows any number; `fromisoformat` accepts only 3 or 6
      before Python 3.11. The fraction is normalised to exactly six.
    - A date-only value, a missing offset, or missing seconds is NOT a valid
      WZDx timestamp. Accepting them and silently assuming UTC would let a
      malformed header pass as a good one and evade R6, which is the rule whose
      entire job is to notice that a timestamp is unusable.
    - RFC 3339 permits lowercase `t` and `z`, so both cases are accepted.
    - A non-string raises ValueError rather than AttributeError, because every
      caller guards on ValueError.
    """
    if not isinstance(stamp, str):
        raise ValueError(f"timestamp must be a string, got {type(stamp).__name__}")
    m = ISO_DATETIME.match(stamp.strip())
    if not m:
        raise ValueError(f"not an RFC 3339 date-time: {stamp!r}")
    date, clock, fraction, offset = m.groups()
    fraction = (fraction or ".0")[1:][:6].ljust(6, "0")
    offset = "+00:00" if offset in ("Z", "z") else offset
    if len(offset) == 5:  # +HHMM, which fromisoformat rejects before 3.11
        offset = offset[:3] + ":" + offset[3:]
    return datetime.datetime.fromisoformat(f"{date}T{clock}.{fraction}{offset}")


def ranges_overlap(a, b):
    """True when two [start, end] ranges overlap, or when either is unusable.

    Unknown counts as overlapping. A missing or unparseable date is not
    evidence that two zones are distinct, and scoring it as a mismatch would
    suppress real duplicates.
    """
    a0, a1 = a
    b0, b1 = b
    if not (a0 and a1 and b0 and b1):
        return True
    try:
        pa0, pa1 = parse_stamp(a0), parse_stamp(a1)
        pb0, pb1 = parse_stamp(b0), parse_stamp(b1)
    except ValueError:
        return True
    return pa0 <= pb1 and pb0 <= pa1
