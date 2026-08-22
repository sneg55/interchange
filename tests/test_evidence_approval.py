"""The human approval gate in front of every outbound notice. Section 3.

Run with: python3 -m unittest discover -s tests -v

Approved means ready to send, never sent: autonomous filing is a non-goal, and
the terminal state enforces it rather than a comment asserting it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.evidence_support import AT, START, obs, transition

from src.constants.error_ids import AppError
from src.features.evidence.approval import decide
from src.features.evidence.packet import open_packet
from src.features.evidence.renderings import registry_rendering


class TestApprovalGate(unittest.TestCase):
    def packet(self):
        p = open_packet(transition(), [obs(0)])
        p.registry_rendering = registry_rendering(p)
        return p

    def test_approved_means_ready_to_send_not_sent(self):
        """Section 3 makes autonomous filing a non-goal and this enforces it."""
        record = decide(self.packet(), "APPROVED", "operator@example", AT)
        self.assertTrue(record.ready_to_send)
        self.assertFalse(hasattr(record, "sent_at"))

    def test_approved_by_cannot_be_anonymous(self):
        """A value the client supplied is not evidence of who approved anything."""
        for identity in ("", "   ", None):
            with self.assertRaises(AppError):
                decide(self.packet(), "APPROVED", identity, AT)

    def test_a_decision_cannot_be_overwritten(self):
        """Re-approving would overwrite who approved and when, and the first
        decision is the one that was actually made."""
        packet = self.packet()
        decide(packet, "APPROVED", "first@example", AT)
        with self.assertRaises(AppError):
            decide(packet, "WITHHELD", "second@example", AT)
        self.assertEqual(packet.approved_by, "first@example")

    def test_nothing_can_be_approved_before_it_is_drafted(self):
        """An approved notice with no text would attest to nothing."""
        with self.assertRaises(AppError):
            decide(open_packet(transition(), [obs(0)]), "APPROVED", "operator@example", AT)

    def test_withholding_is_recorded_as_a_decision(self):
        packet = self.packet()
        record = decide(packet, "WITHHELD", "operator@example", AT, note="not our call")
        self.assertFalse(record.ready_to_send)
        self.assertEqual(packet.approval_state, "WITHHELD")
        self.assertEqual(packet.approved_by, "operator@example")


if __name__ == "__main__":
    unittest.main()
