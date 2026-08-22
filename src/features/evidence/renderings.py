"""The two renderings of one packet. Section 6.7.

Both are built from the packet's own fields. Gemini drafts the *prose* of the
registry notice; the facts in it come from the packet, not the model. That split
is enforced structurally here: `facts()` is deterministic and returns the
citable quantities, and a drafter only ever receives that dict. A drafter cannot
invent a number it was never given, and if the model is unavailable the
deterministic rendering is what ships.

A human approval gate sits in front of every outbound notice, and given the
non-goal in section 3 the terminal state of an approved notice is "ready to
send", not "sent". Nothing in this module sends anything.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Protocol

from .packet import EvidencePacket
from .wording import (
    _coverage_note,
    _feed,
    _latest_poll_clauses,
    _moment,
    _org,
    _window_note,
)

# Any run of digits, with thousands separators and decimals folded in, so
# "9,999" and "9999" compare equal.
_NUMBER = re.compile(r"\d[\d,.]*")

# USDOT's published removal policy for the WZDx Feed Registry. Cited rather than
# paraphrased: a notice that summarises the clause it invokes is asking the
# recipient to take the citation on trust.
DEFAULT_POLICY_CLAUSE = (
    "WZDx Feed Registry listing requires the published feed to remain "
    "available and to conform to the WZDx specification version it declares."
)

# What each rule asserts, in the words that go to the registry owner.
#
# No storage field names. These sentences are read by someone at a named
# organization who has never seen this system's schema, and `update_date` and
# `end_date` in an outbound notice were this product's internals addressed to a
# stranger. Mirrored in `console/src/lib/glossary.ts` and asserted in both suites.
RULE_SUMMARIES = {
    "R1": "the feed did not respond across consecutive polls",
    "R2": "the feed reports a last-updated time older than its own declared cadence allows",
    "R3": "the feed does not validate against the WZDx version it declares",
    "R4": "the feed marks work zones active whose end date has already passed",
    "R5": "the feed content has not changed while its last-updated time advanced",
    "R6": "the feed reports a last-updated time that is missing, unreadable or in the future",
}

# What a finding IS, for a reader who has never seen this system's enums.
FINDING_WORDS = {
    "TRUST_TRANSITION": "a change of trust state",
    "DUPLICATION": "the same work zone published by more than one organization",
    "SCREENING": "free text withheld by the screening gate",
}

# The packet id is still on the record, as `packet_id` in the document itself.
# What it is not is the opening line of a notice.


class Drafter(Protocol):
    """Turns packet facts into prose. Gemini in production."""

    def draft(self, facts: dict[str, Any]) -> str: ...


def facts(packet: EvidencePacket) -> dict[str, Any]:
    """The citable quantities, deterministically.

    This is the ONLY thing a drafter is given. It cannot cite a figure that is
    not in here, which is what stops a model inventing a number that then appears
    in an outbound notice under Interchange's name.
    """
    observed = packet.observations
    return {
        "packet_id": packet.packet_id,
        "publisher_keys": list(packet.publisher_keys),
        "finding_type": packet.finding_type,
        "rule_ids": list(packet.rule_ids),
        "rule_summaries": [RULE_SUMMARIES[r] for r in packet.rule_ids if r in RULE_SUMMARIES],
        "ruleset_version": packet.ruleset_version,
        "window_start": packet.observation_window.start,
        "window_end": packet.observation_window.end,
        "observations_in_window": packet.observation_window.count,
        "observations_embedded": len(observed),
        "observations_truncated": packet.observations_truncated,
        "total_observations": packet.total_observations,
        "zone_ids": list(packet.zone_ids),
        "schema_result": packet.schema_result,
        "policy_clause": packet.policy_clause or DEFAULT_POLICY_CLAUSE,
        "resolved_at": packet.resolved_at,
        "latest_update_date": observed[-1].get("update_date") if observed else None,
        # The same instant as the deterministic block prints, so a drafter that
        # quotes the notice's own figure is not caught by `unsupported_numbers`.
        # Handed only the raw `2023-03-19T07:04:04.861489-06:00`, a model citing
        # the 13:04 the notice itself states would have had its draft discarded
        # for inventing the number 13.
        "latest_update_date_utc": (
            _moment(str(observed[-1].get("update_date") or "")) if observed else None
        ),
        "latest_active_count": observed[-1].get("active_count") if observed else None,
        "latest_active_with_past_end_date": (
            observed[-1].get("active_with_past_end_date") if observed else None
        ),
    }


def consumer_rendering(packet: EvidencePacket) -> str:
    """The admit/quarantine decision record, with the signal that triggered it."""
    data = facts(packet)
    publishers = ", ".join(_org(k) for k in data["publisher_keys"])
    lines = [
        # The organization first, then the machine identity as a labelled field.
        # The composite document id was the opening line of a record a data
        # consumer reads, with the publisher segment in it twice.
        f"Decision record: {publishers}",
        f"Feed: {', '.join(_feed(k) for k in data['publisher_keys'])}",
        (
            f"Finding: {FINDING_WORDS.get(data['finding_type'], data['finding_type'])}, "
            f"under ruleset {data['ruleset_version']}"
        ),
        f"Rules fired: {', '.join(data['rule_ids']) or 'none recorded'}",
    ]
    lines += [f"  - {summary}" for summary in data["rule_summaries"]]
    lines.append(_window_note(data, "poll"))
    recent = _latest_poll_clauses(data)
    if recent:
        lines.append(f"Most recent poll: {'; '.join(recent)}")
    lines.append(_coverage_note(data))
    lines.append(f"Status: {'resolved ' + data['resolved_at'] if data['resolved_at'] else 'open'}")
    return "\n".join(lines)


def registry_rendering(packet: EvidencePacket, drafter: Drafter | None = None) -> str:
    """A drafted notice citing the removal policy and the evidence supporting it.

    With no drafter, or if one fails, the deterministic rendering ships. A notice
    that cannot be written is a notice that does not go out, and the facts are
    the part that matters; the prose is the part a model helps with.
    """
    data = facts(packet)
    deterministic = "\n".join(
        [
            (
                f"Notice concerning WZDx Feed Registry listing: "
                f"{', '.join(_org(k) for k in data['publisher_keys'])}"
            ),
            f"Feed: {', '.join(_feed(k) for k in data['publisher_keys'])}",
            "",
            f"Policy clause: {data['policy_clause']}",
            "",
            "Observed:",
            *[f"  - {summary}" for summary in data["rule_summaries"]],
            "",
            _window_note(data, "poll"),
            _coverage_note(data),
            f"Ruleset {data['ruleset_version']}; rules {', '.join(data['rule_ids'])}.",
            "",
            "This notice is a draft and requires human approval before it is sent.",
        ]
    )
    if drafter is None:
        return deterministic
    try:
        prose = drafter.draft(data)
    except Exception:  # noqa: BLE001 - a drafting failure must not block the finding
        return deterministic
    if not prose or not prose.strip():
        return deterministic
    invented = unsupported_numbers(prose, data)
    if invented:
        # The facts in a notice come from the packet, not the model. Appending a
        # correct appendix does NOT make a fabricated figure acceptable: the
        # notice goes out over Interchange's name, and a human at the approval
        # gate reading "broken for 9,999 days" has been told something the
        # evidence does not say. A draft citing a number the packet never
        # supplied is discarded rather than annotated.
        return deterministic
    return f"{prose.strip()}\n\n---\nFacts of record:\n{deterministic}"


def _numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER.finditer(str(text))}


def unsupported_numbers(prose: str, data: dict[str, Any]) -> set[str]:
    """Figures the prose cites that the packet does not contain.

    Numeric because that is where a fabrication does concrete damage: a wrong
    adjective in a notice is a style problem, while a wrong day count or zone
    count is a false claim about a publisher. Dates and identifiers in the facts
    are decomposed the same way, so a model quoting them back is not flagged.
    """
    supported: set[str] = set()
    for value in data.values():
        supported |= _numbers_in(
            value if not isinstance(value, list) else " ".join(map(str, value))
        )
    return _numbers_in(prose) - supported
