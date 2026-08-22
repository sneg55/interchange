"""PublisherRecord and RegistryEvent, plus the derivation from a registry row.

Section 7 makes field names the contract between components, so these are
dataclasses with `to_doc()` rather than free-form dicts: a component reading
`publisher.feed_name` instead of `publisher.feedname` should fail at the
attribute, not silently read None out of a dict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.constants.error_ids import AppError, ErrorIds
from src.features.publisher_agent.observation import AGENT_IDENTITY

from .cadence import cadence_or_default, clamp, parse_cadence

FleetState = Literal["ADMIT", "WATCH", "QUARANTINE", "NO_ACCESS"]
ChurnStatus = Literal["OK", "INSUFFICIENT_HISTORY"]
RegistryEventType = Literal[
    "PROVISIONED",
    "URL_CHANGED",
    "CADENCE_CHANGED",
    "VERSION_CHANGED",
    "ABSENT",
    "DECOMMISSIONED",
    "REAPPEARED",
    # Access, not trust. Emitted so replay can reconstruct why a publisher was
    # never polled at a past instant: NO_ACCESS is applied directly to the
    # mutable PublisherRecord and never produces a TrustTransition, so without
    # this event a replay would show it as WATCH, which is a trust claim about a
    # publisher nobody ever reached.
    "ACCESS_GAINED",
    "ACCESS_LOST",
]

# Absent from this many consecutive pulls before decommissioning. Section 6.1: a
# single absence is a partial Socrata response, not a delisting.
ABSENT_PULLS_BEFORE_DECOMMISSION = 3


def publisher_key(org: str, feedname: str) -> str:
    """The identity: (issuingorganization, feedname).

    `issuingorganization` alone is not unique. Colorado DOT appears twice, as
    `cdot` (WZDx 4.2) and `cdot_cwz` (CWZ 1.0), and a fleet keyed on the
    organization collapses two different feeds into one agent.
    """
    return f"{org}|{feedname}"


def entry_key(entry: dict[str, Any]) -> str:
    org = entry.get("issuingorganization")
    feedname = entry.get("feedname")
    if not org or not feedname:
        raise AppError(
            ErrorIds.REG_BAD_SHAPE,
            "registry entry missing issuingorganization or feedname",
            {"entry": {k: entry.get(k) for k in ("issuingorganization", "feedname", "url")}},
        )
    return publisher_key(str(org), str(feedname))


def entry_url(entry: dict[str, Any]) -> str:
    """Socrata serves `url` as a nested object, `{"url": "https://..."}`."""
    raw = entry.get("url")
    if isinstance(raw, dict):
        raw = raw.get("url")
    return str(raw or "")


def needs_api_key(entry: dict[str, Any]) -> bool:
    """Absence means no key needed. 26 of 40 active entries omit the field.

    Reading absence as "unknown, assume gated" would drop two thirds of the
    fleet; reading it as True would be the same mistake in the other direction.
    The registry's own convention is that the flag is only written when set.
    """
    return bool(entry.get("needapikey"))


@dataclass(slots=True)
class PublisherRecord:
    publisher_key: str
    org: str
    feedname: str
    us_state: str | None = None
    registered_since: str | None = None
    url: str = ""
    declared_version: str | None = None
    declared_cadence_seconds: int = 0
    needs_api_key: bool = False
    fleet_state: FleetState = "WATCH"
    # The trust state held before a key requirement forced NO_ACCESS. NO_ACCESS
    # is not a trust verdict, so it must not erase one: a publisher that was
    # QUARANTINE before going key-gated has not recovered by becoming
    # unreachable, and resetting it to WATCH on the way back would discard both
    # the finding and its recovery hysteresis.
    state_before_no_access: FleetState | None = None
    churn_status: ChurnStatus = "INSUFFICIENT_HISTORY"
    # What R5 measured, when it could. None means it could not, which is not the
    # same as a measured zero: `churn_status` says whether a measurement
    # happened and this says what it found, and without the second the console
    # could print only the word "measured" in a column headed Churn.
    churn_detail: dict[str, int] | None = None
    ruleset_version: str = "v1"
    # The trust scorer holds no state, so its three carry-over values live here.
    # They cannot be recomputed from the retained records: whether a past poll
    # counted as clean depends on which rules were latching AT THAT TIME, and a
    # replay does not have that. Dropping them across a restart would either
    # strand a publisher forever or let it recover without the body-dependent
    # latch that section 6.4 requires.
    latching_rule_ids: list[str] = field(default_factory=list)
    clean_poll_streak: int = 0
    clean_streak_started_at: str | None = None
    first_seen: str = ""
    last_seen_in_registry: str = ""
    absent_pull_count: int = 0
    decommissioned_at: str | None = None
    agent_identity: str | None = None
    poll_interval_seconds: int = 0
    # When this publisher was last actually polled. Null means never, which is
    # the honest reading for a NO_ACCESS or decommissioned publisher and must not
    # be rendered as "just now".
    #
    # On the record rather than derived from the observation series, because the
    # fleet board subscribes to publishers alone: deriving it would mean a
    # per-publisher observation query behind every row, and without it the board
    # cannot say how fresh it is. A monitoring surface that cannot distinguish a
    # cycle that ran a minute ago from one that stopped yesterday is the exact
    # unlabelled staleness this system exists to catch elsewhere.
    last_polled_at: str | None = None

    @property
    def is_pollable(self) -> bool:
        """NO_ACCESS publishers are never fetched and never counted as passing.

        They hold an identity and a registry record because "we are required to
        consume this organization and cannot authenticate to it" is a governance
        condition worth reporting, not a gap to hide. Section 6.1.
        """
        return self.decommissioned_at is None and self.fleet_state != "NO_ACCESS"

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> PublisherRecord:
        known = set(cls.__slots__)
        return cls(**{k: v for k, v in doc.items() if k in known})


@dataclass(slots=True)
class RegistryEvent:
    publisher_key: str
    at: str
    event: RegistryEventType
    from_value: Any = None
    to_value: Any = None

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReconcileResult:
    """What one registry pull changed. Returned rather than written, so the
    warden stays testable without a store and a rejected short read cannot have
    already mutated anything."""

    records: dict[str, PublisherRecord] = field(default_factory=dict)
    events: list[RegistryEvent] = field(default_factory=list)
    rejected: str | None = None  # set when the pull was refused as a short read

    @property
    def accepted(self) -> bool:
        return self.rejected is None


def record_from_entry(entry: dict[str, Any], now: str) -> PublisherRecord:
    """Build a fresh record for a newly registered publisher."""
    key = entry_key(entry)
    gated = needs_api_key(entry)
    declared = entry.get("datafeed_frequency_update")
    cadence = cadence_or_default(declared)
    return PublisherRecord(
        publisher_key=key,
        org=str(entry["issuingorganization"]),
        feedname=str(entry["feedname"]),
        us_state=entry.get("state"),
        registered_since=entry.get("sdate"),
        url=entry_url(entry),
        declared_version=None if entry.get("version") is None else str(entry["version"]),
        declared_cadence_seconds=cadence,
        needs_api_key=gated,
        # A key-gated publisher starts NO_ACCESS, which is not a trust verdict.
        # Everyone else starts WATCH: nothing has been observed yet, and ADMIT
        # would be recording "not checked" as "passed".
        fleet_state="NO_ACCESS" if gated else "WATCH",
        first_seen=now,
        last_seen_in_registry=now,
        poll_interval_seconds=0 if gated else clamp(cadence),
        # Set here AND refreshed on every warden pass, because a field written
        # only at provisioning is null for every publisher provisioned before it
        # existed. It sat at its dataclass default on all 41 and the console
        # rendered that default as the sentence "not provisioned", directly above
        # the poll history of the agent it was denying.
        #
        # A key-gated publisher has an agent too. It is provisioned into
        # NO_ACCESS and polls nothing, which is a statement about the feed rather
        # than about whether anything is watching it.
        agent_identity=AGENT_IDENTITY,
    )


def declared_cadence_changed(record: PublisherRecord, entry: dict[str, Any]) -> int | None:
    """The new declared cadence when it differs, else None.

    Compares the declared value, not the clamped one. Two publishers declaring
    1m and 60s are the same post-clamp and a comparison after clamping would
    report no change on a real registry edit.
    """
    try:
        parsed = parse_cadence(entry.get("datafeed_frequency_update"))
    except AppError:
        return None
    new = cadence_or_default(entry.get("datafeed_frequency_update")) if parsed is None else parsed
    return new if new != record.declared_cadence_seconds else None
