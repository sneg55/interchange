"""The human approval gate. Sections 3, 6.7 and 6.9.

Section 3 makes autonomous filing a non-goal, and this is what enforces it. The
terminal state of an approved notice is **"ready to send", not "sent"**, so
nothing here dispatches anything and nothing may be added that does.

`approved_by` comes from the verified identity of the caller, never from the
request body. A packet that recorded whoever the client claimed to be would make
the audit trail worthless at exactly the point it matters, which is why the
approve call takes the identity as a separate argument rather than reading it off
a payload.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from src.constants.error_ids import AppError, ErrorIds

from .packet import EvidencePacket

Decision = Literal["APPROVED", "WITHHELD"]


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    packet_id: str
    decision: Decision
    approved_by: str
    at: str
    note: str | None = None
    rendering_sha256: str | None = None

    @property
    def ready_to_send(self) -> bool:
        """Approved is 'ready to send', never 'sent'. Section 3."""
        return self.decision == "APPROVED"


def decide(
    packet: EvidencePacket,
    decision: Decision,
    verified_identity: str,
    at: str,
    note: str | None = None,
) -> ApprovalRecord:
    """Record a human decision on a packet.

    `verified_identity` must come from the authenticated token, not from the
    request body. Section 7 names `approved_by` as part of the packet, and a
    value the client supplied is not evidence of who approved anything.
    """
    if not verified_identity or not verified_identity.strip():
        raise AppError(
            ErrorIds.EVID_UNAUTHORIZED_APPROVAL,
            "approval requires a verified identity; refusing to record an anonymous decision",
            {"packet_id": packet.packet_id},
        )
    if packet.approval_state != "DRAFT":
        # Not idempotent on purpose: re-approving would overwrite who approved
        # and when, and the first decision is the one that was actually made.
        raise AppError(
            ErrorIds.EVID_ALREADY_RESOLVED,
            f"packet {packet.packet_id} is already {packet.approval_state}",
            {"packet_id": packet.packet_id, "approval_state": packet.approval_state},
        )
    if not packet.registry_rendering:
        # Approving a packet with nothing drafted would produce an approved
        # notice with no text, and the approval would attest to nothing.
        raise AppError(
            ErrorIds.EVID_PACKET_NOT_FOUND,
            f"packet {packet.packet_id} has no registry rendering to approve",
            {"packet_id": packet.packet_id},
        )
    packet.approval_state = decision
    packet.approved_by = verified_identity
    packet.approved_at = at
    # Bound to the exact text. Extending the packet clears this and returns it to
    # DRAFT, so an approval can never survive the evidence changing underneath
    # it and attest to something nobody read.
    digest = hashlib.sha256(packet.registry_rendering.encode("utf-8")).hexdigest()
    packet.approved_rendering_sha256 = digest
    return ApprovalRecord(packet.packet_id, decision, verified_identity, at, note, digest)
