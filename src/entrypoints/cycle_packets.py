"""Turning a scored transition into a transition document and, on an escalation,
an evidence packet. Sections 6.4 and 6.7.

Extracted from `fleet_cycle` when that module crossed the file size limit. The
seam is a real one rather than a convenience: everything here runs only when a
publisher's state actually changed, and it is the only place in the cycle where a
model may write anything at all.
"""

from __future__ import annotations

from typing import Any

from src.features.evidence.packet import EvidencePacket, open_packet
from src.features.evidence.renderings import consumer_rendering, registry_rendering
from src.features.publisher_agent.observation import Observation


def transition_document(
    score: Any,
    observation: Observation,
    prior: list[Observation],
    drafter: Any = None,
) -> tuple[dict[str, Any], EvidencePacket | None]:
    """The transition's document, and the packet it points at when it escalates.

    Returns the packet separately rather than only stamping its id on the
    document, because the caller owns the packet list and a packet that is
    referenced but never stored leaves the console with a dead link on a
    transition that looks explained.
    """
    doc = score.transition.to_doc()
    if score.transition.direction != "ESCALATION":
        return doc, None

    # The observations the FIRED rules actually rested on, not only the one that
    # tripped the transition. Every packet embedded a single poll, so every
    # notice asserted behaviour "across consecutive polls" over a window whose
    # start equalled its end.
    depth = score.evaluation.evidence_depth
    packet = open_packet(score.transition, [observation, *prior[: max(0, depth - 1)]])
    packet.consumer_rendering = consumer_rendering(packet)
    # Section 6.7 lets Gemini write the registry prose from the packet's facts.
    # With no drafter the facts ARE the notice: `registry_rendering` falls back to
    # its deterministic rendering, so an unconfigured model degrades the wording
    # and never the content. A drafter that fails does the same.
    packet.registry_rendering = registry_rendering(packet, drafter)
    # The transition points AT the packet. A null here leaves every "explain this
    # decision" link in the console dead.
    doc["evidence_packet_id"] = packet.packet_id
    return doc, packet
