"""The notice queue read model, and when a draft is superseded. Screen 5.

Run with: python3 -m unittest discover -s tests -v

Split from `test_console_api.py` on the file size limit. The supersession tests
are the substance here: a draft notice that cites a rule whose verdict has since
changed may assert something this system no longer makes, and approving it would
record a human decision against a named organization on that basis.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.console_api.notice_queue import notice_queue
from src.features.evidence.packet import open_packet
from src.features.trust_scorer import rules
from src.features.trust_scorer.verdicts import QUARANTINE
from tests.support import CONSOLE_AT as AT
from tests.support import observation as obs
from tests.support import trust_transition as transition


class TestNoticeQueue(unittest.TestCase):
    def packet(self, at, state="DRAFT"):
        p = open_packet(transition("A|a", at, QUARANTINE), [obs(0)])
        p.approval_state = state
        p.registry_rendering = "draft text"
        return p

    def test_only_drafts_appear_oldest_first(self):
        """An approved packet has had its decision made; leaving it in the queue
        would invite a second one."""
        packets = [
            self.packet("2026-08-03T00:00:00Z"),
            self.packet("2026-08-01T00:00:00Z"),
            self.packet("2026-08-02T00:00:00Z", "APPROVED"),
        ]
        queue = notice_queue(packets)
        self.assertEqual(
            [e.created_at for e in queue], ["2026-08-01T00:00:00Z", "2026-08-03T00:00:00Z"]
        )

    def test_each_entry_says_what_it_asserts(self):
        """The human gate as an actual queue, not an implied step."""
        entry = notice_queue([self.packet(AT)])[0]
        # The sentence a registry owner reads, in their words rather than in
        # this system's field names.
        self.assertIn("last-updated time", entry.asserts)
        self.assertNotIn("update_date", entry.asserts)
        self.assertEqual(entry.publisher_keys, ["A|a"])

    def test_a_draft_citing_a_changed_rule_is_flagged_superseded(self):
        """R6's verdict changed in v2, so a v1 packet citing R6 may assert a
        finding this system no longer makes. It is badged and not approvable."""
        p = self.packet(AT)
        p.ruleset_version = "v1"
        p.rule_ids = ["R6"]
        self.assertTrue(notice_queue([p])[0].superseded_ruleset)

    def test_but_a_draft_citing_an_unchanged_rule_is_not(self):
        """The first cut of this compared versions alone and flagged everything
        opened under v1, including a Hawaii DOT R2 quarantine. R2 reaches the
        same verdict on the same evidence under both versions, so blocking it
        would refuse a still-true finding: a wrong answer in the other direction.
        """
        p = self.packet(AT)
        p.ruleset_version = "v1"
        p.rule_ids = ["R2", "R4"]
        self.assertFalse(notice_queue([p])[0].superseded_ruleset)

    def test_a_current_packet_is_never_superseded(self):
        p = self.packet(AT)
        p.ruleset_version = rules.RULESET_VERSION
        p.rule_ids = ["R6"]
        self.assertFalse(notice_queue([p])[0].superseded_ruleset)

    def test_an_unplaceable_version_is_flagged_rather_than_assumed_current(self):
        """A version not in the history cannot be shown to predate or postdate
        anything, so the safe direction is to make a human look."""
        p = self.packet(AT)
        p.ruleset_version = "v0-experimental"
        p.rule_ids = ["R6"]
        self.assertTrue(notice_queue([p])[0].superseded_ruleset)


if __name__ == "__main__":
    unittest.main()
