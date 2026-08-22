"""EvidencePacket: one per finding, where a finding is a transition. Section 6.7.

The record that both renderings are built from. It embeds immutable copies of the
observations that triggered it rather than only their IDs, because observations
are retained 90 days and a packet may be cited long after: a dangling reference
would hollow out a filed finding.

Cardinality is plural because findings are. `publisher_keys[]` and `rule_ids[]`,
never singular. A duplication finding names two publishers by nature, and Utah's
transition has two independent causes (R2 and R4). Singular fields would force a
convention to be invented at implementation time, and two implementers would
invent different ones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any, Literal

from src.features.publisher_agent.observation import Observation
from src.features.trust_scorer.records import TrustTransition

ApprovalState = Literal["DRAFT", "APPROVED", "WITHHELD"]
FindingType = Literal["TRUST_TRANSITION", "DUPLICATION", "SCREENING"]

# Embedded copies are capped at the observations that satisfied the rule, which
# is what the finding actually rests on. A packet open for 1,236 days would
# otherwise accumulate past Firestore's 1 MiB document limit. The complete series
# goes to GCS under `observations_uri`.
MAX_EMBEDDED_OBSERVATIONS = 50


def packet_id(publisher_key: str, to_state: str, opening_transition_id: str) -> str:
    """Keyed on (publisher_key, to_state, opening_transition_id). Section 6.7.

    Including the opening transition is what makes re-entering the same state
    after a recovery open a NEW packet rather than reopening the old one, so each
    episode stays separately citable.
    """
    return f"{publisher_key}|{to_state}|{opening_transition_id}"


@dataclass(slots=True)
class ObservationWindow:
    start: str
    end: str
    count: int

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidencePacket:
    packet_id: str
    publisher_keys: list[str]
    finding_type: FindingType
    created_at: str
    opening_transition_id: str
    rule_ids: list[str]
    ruleset_version: str
    observation_window: ObservationWindow
    observation_ids: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    observations_uri: str | None = None
    observations_truncated: bool = False
    total_observations: int = 0
    zone_ids: list[str] = field(default_factory=list)
    schema_result: dict[str, Any] | None = None
    policy_clause: str | None = None
    consumer_rendering: str | None = None
    registry_rendering: str | None = None
    approval_state: ApprovalState = "DRAFT"
    approved_by: str | None = None
    approved_at: str | None = None
    # SHA-256 of the registry rendering that was approved. An approval that named
    # only a packet would not say WHICH text a human read, and the packet is
    # mutable while the finding persists.
    approved_rendering_sha256: str | None = None
    resolved_at: str | None = None
    closing_transition_id: str | None = None

    @property
    def open(self) -> bool:
        return self.resolved_at is None

    def to_doc(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["observation_window"] = self.observation_window.to_doc()
        return doc

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> EvidencePacket:
        """A stored packet, back as a packet.

        Needed because a packet's renderings are written once, when it is
        opened, and nothing in the cycle rewrites them. So a defect in the
        renderer is fixed only for packets opened afterwards, and the packets
        already in the store are exactly the ones waiting in the queue for a
        human to approve and file. `scripts/redraft_packets.py` uses this to
        re-render the ones nobody has decided on yet.

        Unknown keys are dropped rather than raising. A document written by a
        later revision of this dataclass must still be readable by an older one
        for exactly as long as both are deployed, and the alternative is a
        backfill that dies on the first record carrying a field it has not heard
        of.
        """
        fields = {f.name for f in dataclass_fields(cls)}
        kwargs = {k: v for k, v in doc.items() if k in fields}
        window = kwargs.get("observation_window") or {}
        if isinstance(window, dict):
            kwargs["observation_window"] = ObservationWindow(
                start=window.get("start", ""),
                end=window.get("end", ""),
                count=window.get("count", 0),
            )
        return cls(**kwargs)


def _window(observations: list[Observation]) -> ObservationWindow:
    stamps = sorted(o.polled_at for o in observations)
    return ObservationWindow(
        start=stamps[0] if stamps else "",
        end=stamps[-1] if stamps else "",
        count=len(observations),
    )


def _embed(observations: list[Observation]) -> tuple[list[dict[str, Any]], bool]:
    """The most recent MAX_EMBEDDED_OBSERVATIONS, newest last.

    Truncation is reported on the packet rather than applied silently, because
    "showing 50 of 8,412" and "there were 50" are different claims and a reader
    cannot tell them apart from the array alone.
    """
    ordered = sorted(observations, key=lambda o: o.polled_at)
    truncated = len(ordered) > MAX_EMBEDDED_OBSERVATIONS
    kept = ordered[-MAX_EMBEDDED_OBSERVATIONS:] if truncated else ordered
    return [o.to_doc() for o in kept], truncated


def open_packet(
    transition: TrustTransition,
    observations: list[Observation],
    zone_ids: list[str] | None = None,
    schema_result: dict[str, Any] | None = None,
    policy_clause: str | None = None,
    transition_id: str | None = None,
) -> EvidencePacket:
    """Create a packet for a transition into a more severe state."""
    opening = transition_id or f"{transition.publisher_key}@{transition.at}"
    embedded, truncated = _embed(observations)
    return EvidencePacket(
        packet_id=packet_id(transition.publisher_key, transition.to_state, opening),
        publisher_keys=[transition.publisher_key],
        finding_type="TRUST_TRANSITION",
        created_at=transition.at,
        opening_transition_id=opening,
        rule_ids=list(transition.rule_ids),
        ruleset_version=transition.ruleset_version,
        observation_window=_window(observations),
        observation_ids=[o.doc_id for o in observations],
        observations=embedded,
        observations_truncated=truncated,
        total_observations=len(observations),
        zone_ids=list(zone_ids or []),
        schema_result=schema_result,
        policy_clause=policy_clause,
    )


def extend(packet: EvidencePacket, observations: list[Observation]) -> EvidencePacket:
    """Widen an open packet's window rather than creating another one.

    A rule that keeps firing for 1,236 days produces one packet, not one per
    poll. Extending a closed packet is refused: the episode ended, and folding
    later evidence into it would make the closed finding cite observations from
    after its own resolution.
    """
    if not packet.open:
        raise ValueError(f"packet {packet.packet_id} is closed; open a new one instead")
    if not observations:
        return packet
    if packet.approval_state != "DRAFT":
        # New evidence invalidates the approval. A human approved specific text
        # citing a specific window; widening the window behind an APPROVED state
        # would leave that approval attesting to something nobody read. Reset to
        # DRAFT so the notice is re-drafted and re-approved.
        packet.approval_state = "DRAFT"
        packet.approved_by = None
        packet.approved_at = None
        packet.approved_rendering_sha256 = None
        packet.registry_rendering = None
    # Deduplicated on the OBSERVATIONS, not only on their ids. A redelivery of
    # an observation already embedded would otherwise leave the id list correct
    # while the embedded array holds a duplicate, silently pushing a genuine
    # observation out of the cap and marking the packet truncated when it is not.
    existing = [_ObservationView(d) for d in packet.observations]
    seen = {view.doc_id for view in existing}
    fresh = [o for o in observations if o.doc_id not in seen]
    by_id = {o.doc_id: o for o in observations}
    merged_ids = list(dict.fromkeys([*packet.observation_ids, *by_id]))
    embedded, truncated = _embed([*existing, *fresh])  # type: ignore[list-item]
    packet.observation_ids = merged_ids
    packet.observations = embedded
    packet.observations_truncated = truncated or packet.observations_truncated
    packet.total_observations = len(merged_ids)
    starts = [packet.observation_window.start, *(o.polled_at for o in observations)]
    ends = [packet.observation_window.end, *(o.polled_at for o in observations)]
    packet.observation_window = ObservationWindow(
        start=min(s for s in starts if s),
        end=max(e for e in ends if e),
        count=len(merged_ids),
    )
    return packet


def close(
    packet: EvidencePacket, transition: TrustTransition, transition_id: str | None = None
) -> EvidencePacket:
    """Close on recovery. Closed packets stay queryable.

    A publisher that was quarantined and recovered is exactly the history a
    consumer wants, so this sets `resolved_at` rather than deleting anything.
    """
    packet.resolved_at = transition.at
    packet.closing_transition_id = transition_id or f"{transition.publisher_key}@{transition.at}"
    return packet


class _ObservationView:
    """Adapts an embedded observation dict back to what `_embed` needs.

    Embedded copies are plain dicts by design: they are immutable snapshots, and
    rehydrating them into Observation objects would invite a later revision to
    mutate them and silently rewrite filed evidence.
    """

    __slots__ = ("_doc",)

    def __init__(self, doc: dict[str, Any]) -> None:
        self._doc = doc

    @property
    def polled_at(self) -> str:
        return self._doc.get("polled_at", "")

    @property
    def doc_id(self) -> str:
        return f"{self._doc.get('publisher_key', '')}@{self.polled_at}"

    def to_doc(self) -> dict[str, Any]:
        return self._doc
