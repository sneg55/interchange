"""The notice queue read model. Screen 5, section 6.9.

Split from `views.py` on the file size limit. The queue is the human gate, and it
is the one screen whose entries carry a decision the operator is about to make,
so it earns its own module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.features.evidence.packet import EvidencePacket
from src.features.trust_scorer.rules import rules_changed_since


@dataclass(slots=True)
class NoticeQueueEntry:
    packet_id: str
    publisher_keys: list[str]
    created_at: str
    rule_ids: list[str]
    finding_type: str
    asserts: str
    has_rendering: bool
    # True when the packet was drafted under a ruleset that is no longer in
    # force. The rules it names may no longer reach the same verdict on the same
    # evidence, so an approver is deciding on a finding the system would not make
    # today. Carried on the queue entry as well as the packet, because the queue
    # is where a batch of them gets worked through.
    superseded_ruleset: bool

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


def notice_queue(packets: list[EvidencePacket]) -> list[NoticeQueueEntry]:
    """Screen 5. Every DRAFT packet, oldest first.

    The human gate as an actual queue rather than an implied step. Only DRAFT
    appears: an APPROVED or WITHHELD packet has had its decision made, and
    leaving it in the queue would invite a second one.
    """
    from src.features.evidence.renderings import RULE_SUMMARIES

    drafts = [p for p in packets if p.approval_state == "DRAFT" and p.open]
    return [
        NoticeQueueEntry(
            packet_id=p.packet_id,
            publisher_keys=list(p.publisher_keys),
            created_at=p.created_at,
            rule_ids=list(p.rule_ids),
            finding_type=p.finding_type,
            asserts="; ".join(RULE_SUMMARIES[r] for r in p.rule_ids if r in RULE_SUMMARIES),
            has_rendering=bool(p.registry_rendering),
            superseded_ruleset=bool(set(p.rule_ids) & rules_changed_since(p.ruleset_version)),
        )
        for p in sorted(drafts, key=lambda p: (p.created_at, p.packet_id))
    ]
