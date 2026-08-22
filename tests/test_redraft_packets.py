"""The backfill that re-renders packets nobody has decided on. Section 6.7.

Run with: python3 -m unittest discover -s tests -v

A packet's renderings are written once, in `cycle_packets`, and nothing in the
cycle rewrites them. So a defect in the renderer is fixed for packets opened
afterwards and never for the ones already in the store, which are exactly the
ones sitting in the queue waiting for a human to approve and file them.

What the backfill must NOT touch is the part worth testing.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.evidence.packet import open_packet
from src.features.evidence.renderings import consumer_rendering, registry_rendering
from tests.evidence_support import obs, transition


class TestRedraftingStoredPackets(unittest.TestCase):
    """A renderer fix reaches only packets opened after it.

    Renderings are written once, in `cycle_packets`, and nothing rewrites them,
    so the notices already sitting in the queue keep whatever the renderer said
    when they were opened. `scripts/redraft_packets.py` closes that gap, and
    what it must NOT touch is the part worth testing.
    """

    def setUp(self):
        from scripts.redraft_packets import redraft

        self.redraft = redraft
        self.packet = open_packet(transition(), [obs(0)])

    def stored(self, **overrides):
        doc = self.packet.to_doc()
        doc["consumer_rendering"] = "Most recent poll: ... last updated 2023-03-19 07:04:04 UTC"
        doc["registry_rendering"] = None
        return {**doc, **overrides}

    def test_a_draft_with_stale_text_is_rewritten(self):
        changed = self.redraft(self.stored())
        self.assertIn("2023-03-19 13:04:04 UTC", changed["consumer_rendering"])

    def test_a_decided_packet_is_never_rewritten(self):
        """`approved_rendering_sha256` is a hash of the exact text a named human
        read. Rewriting it would break the one auditable act in this product."""
        for state in ("APPROVED", "WITHHELD"):
            self.assertIsNone(self.redraft(self.stored(approval_state=state)), state)

    def test_prose_from_a_drafter_is_flagged_rather_than_replaced(self):
        """Re-rendering would swap a model's wording for the deterministic
        notice, which is a different document, not a corrected one.

        Recognised by the `Facts of record:` block `registry_rendering` appends
        under a model's prose, which is the only thing that distinguishes the
        two: a deterministic notice never carries it.
        """
        drafted = "Dear registry owner, ...\n\n---\nFacts of record:\nRuleset v1."
        changed = self.redraft(self.stored(registry_rendering=drafted))
        self.assertTrue(changed["_model_drafted"])
        self.assertNotIn("registry_rendering", changed)

    def test_a_deterministic_notice_from_an_older_renderer_is_rewritten(self):
        """The first version asked whether the stored text differed from what we
        would render now, and every packet does: that is why the backfill
        exists. Against production it classified all fifteen drafts as
        model-written and would have fixed none of them, which is the failure
        mode where a safety check quietly does nothing and reports success.
        """
        old = registry_rendering(self.packet).replace("udot (Utah DOT)", "Utah DOT|udot")
        changed = self.redraft(self.stored(registry_rendering=old))
        self.assertNotIn("_model_drafted", changed)
        self.assertIn("udot (Utah DOT)", changed["registry_rendering"])

    def test_a_packet_already_correct_is_left_alone(self):
        doc = self.stored()
        doc["consumer_rendering"] = consumer_rendering(self.packet)
        doc["registry_rendering"] = registry_rendering(self.packet)
        self.assertIsNone(self.redraft(doc))

    def test_a_document_carrying_an_unknown_field_still_loads(self):
        """A store written by a later revision must stay readable, or a backfill
        dies on the first record carrying a field it has not heard of."""
        changed = self.redraft(self.stored(some_field_added_later=1))
        self.assertIn("consumer_rendering", changed)
