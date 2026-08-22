"""How a packet's facts are SAID, for a reader who has never seen the schema.

Split out of `renderings.py` on the file size limit, and the split falls where a
real seam already was: every function here turns a stored value into the words a
stranger at a named public agency reads, and none of them decides anything. It
is the Python counterpart of the console's `format.ts`, and it exists for the
same reason that file does: one place deciding how an instant, an identity and a
count are read aloud, so the same fact cannot be stated two ways.

It has been stated two ways. `_moment` relabelled `-06:00` timestamps as UTC
while the console converted them, so one quarantine's evidence was published six
hours apart in two renderings of the same finding.
"""

from __future__ import annotations

import datetime
from typing import Any


def _window_note(data: dict[str, Any], polls_word: str) -> str:
    """The observation window, said rather than printed as two identical stamps.

    A finding resting on one poll produced "Observation window: T to T, 1 polls",
    which states a span that was never examined and does it ungrammatically. Say
    what it is instead: one poll, at a time.
    """
    count = data["observations_in_window"]
    if data["window_start"] == data["window_end"]:
        return f"Observation: a single poll at {_moment(data['window_start'])}."
    return (
        f"Observation window: {_moment(data['window_start'])} to "
        f"{_moment(data['window_end'])}, {count} {polls_word}{'' if count == 1 else 's'}."
    )


def _org(publisher_key: str) -> str:
    """The organization's name out of the `org|feedname` key.

    A notice is addressed to a body of people at a named organization. Our
    document id is not their name, and it was the salutation.
    """
    return publisher_key.split("|", 1)[0]


def _feed(publisher_key: str) -> str:
    """The feed's own name out of the `org|feedname` key, with the org beside it.

    `Utah DOT|udot` is a Firestore document id, and it was the value of the
    `Feed:` field in both renderings: this system's storage key, in the one
    artifact a stranger at a named agency reads. The identity still has to be
    exact, which is why the organization is here too; it is simply not joined to
    the feed name by the character the store happens to use. Qualified even when
    the line above already names the organization, because a duplication finding
    lists several feeds and positional correspondence with the line above is not
    an identity.
    """
    org, sep, feed = publisher_key.partition("|")
    if sep == "":
        return publisher_key
    return f"{feed} ({org})"


def _moment(stamp: str) -> str:
    """An instant to the second, converted to UTC, for a document a person reads.

    Converted, never relabelled. Utah DOT's feed reports
    `2023-03-19T07:04:04.861489-06:00`, which is 13:04:04 UTC. The previous
    implementation stripped a trailing `+00:00` or `Z` and then cut everything
    after the decimal point, so an offset that sat past the fractional second
    was discarded in silence and the publisher's local wall time went out under a
    UTC label. The console rendered the same field correctly on the screen
    beside it, so the evidence for a quarantine was stated two ways, six hours
    apart, in a notice naming a public agency.

    Six digits of fractional second are still a storage artifact and still go.
    Seconds are enough to match the notice back to the observation it cites.

    A stamp carrying no zone at all is NOT called UTC. It is not known to be
    UTC, and asserting it would be this system's cardinal error committed in its
    own furniture: recording "we do not know" as a measurement. Same for a stamp
    that cannot be read at all, which is kept verbatim, because a timestamp we
    cannot parse is still what the publisher said.
    """
    if not stamp:
        return "not recorded"
    try:
        parsed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return f"{stamp} (unreadable as a timestamp)"
    if parsed.tzinfo is None:
        return f"{parsed.replace(microsecond=0).isoformat(sep=' ')} (no time zone given)"
    at = parsed.astimezone(datetime.UTC).replace(microsecond=0, tzinfo=None)
    return f"{at.isoformat(sep=' ')} UTC"


def _latest_poll_clauses(data: dict[str, Any]) -> list[str]:
    """What the most recent poll measured, omitting what it never measured.

    The two clauses are separate because the rules behind them are. R4 abstains
    when there are no dated active zones (spec 6.4), and the joined sentence
    reported that abstention as "0 of 0 zones marked active have an end date in
    the past": a ratio standing where a measurement should be, in a notice sent
    to a named agency over an unrelated R2 finding. Hawaii DOT's notice carried
    it. Zero of zero is not a finding of zero, and a document read closely by
    someone deciding whether to act should not contain a clause about a rule
    that did not fire.
    """
    clauses: list[str] = []
    active = data["latest_active_count"]
    if active:
        clauses.append(
            f"{data['latest_active_with_past_end_date']} of {active} zones marked active "
            f"have an end date in the past"
        )
    if data["latest_update_date"]:
        clauses.append(f"the feed reports it was last updated {data['latest_update_date_utc']}")
    return clauses


def _coverage_note(data: dict[str, Any]) -> str:
    """Say when the evidence shown is a subset. Never truncate silently."""
    if not data["observations_truncated"]:
        count = data["observations_embedded"]
        return f"Evidence: all {count} observation{'' if count == 1 else 's'} in the window."
    return (
        f"Evidence: showing {data['observations_embedded']} of "
        f"{data['total_observations']} observations; the complete series is retained "
        f"separately."
    )
