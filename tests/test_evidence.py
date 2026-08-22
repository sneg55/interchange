"""Evidence packets: one per finding, not one per poll. Section 6.7.

Run with: python3 -m unittest discover -s tests -v

A rule firing for 1,236 days produces one packet that grows rather than 356,000
packets, the embedded copies are capped and the cap is stated, and a resolved
packet closes rather than being deleted.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.evidence_support import AT, START, obs, transition

from src.features.evidence.packet import (
    MAX_EMBEDDED_OBSERVATIONS,
    close,
    extend,
    open_packet,
    packet_id,
)
from src.features.evidence.renderings import registry_rendering


class TestPacketLifecycle(unittest.TestCase):
    def test_a_finding_is_a_transition_not_a_poll(self):
        """A rule firing for 1,236 days produces ONE packet that grows, not one
        per poll."""
        packet = open_packet(transition(), [obs(0)])
        for i in range(1, 200):
            extend(packet, [obs(i)])
        self.assertEqual(packet.total_observations, 200)
        self.assertEqual(packet.observation_window.count, 200)

    def test_the_embedded_copies_are_capped_and_the_cap_is_reported(self):
        """A packet open for 1,236 days would otherwise pass Firestore's 1 MiB
        limit. 'Showing 50 of 8,412' and 'there were 50' are different claims."""
        packet = open_packet(transition(), [obs(i) for i in range(500)])
        self.assertEqual(len(packet.observations), MAX_EMBEDDED_OBSERVATIONS)
        self.assertTrue(packet.observations_truncated)
        self.assertEqual(packet.total_observations, 500)
        self.assertIn("showing 50 of 500", registry_rendering(packet))

    def test_evidence_outlives_the_observations_it_cites(self):
        """Observations are retained 90 days; a packet may be cited long after.
        A dangling reference cannot hollow out a filed finding."""
        packet = open_packet(transition(), [obs(0), obs(1)])
        self.assertTrue(packet.observations, "the copies, not just the ids")
        self.assertTrue(packet.observation_ids, "the ids too, for joining while they exist")
        self.assertEqual(packet.observations[0]["active_with_past_end_date"], 744)

    def test_recovery_closes_rather_than_deletes(self):
        """A publisher that was quarantined and recovered is exactly the history
        a consumer wants."""
        packet = open_packet(transition(), [obs(0)])
        self.assertTrue(packet.open)
        close(packet, transition("WATCH", at="2026-09-01T00:00:00+00:00"))
        self.assertFalse(packet.open)
        self.assertEqual(packet.resolved_at, "2026-09-01T00:00:00+00:00")
        self.assertTrue(packet.observations, "still queryable")

    def test_a_closed_packet_refuses_later_evidence(self):
        """Folding it in would make a closed finding cite observations from
        after its own resolution."""
        packet = open_packet(transition(), [obs(0)])
        close(packet, transition("WATCH"))
        with self.assertRaises(ValueError):
            extend(packet, [obs(1)])

    def test_re_entering_the_same_state_opens_a_new_packet(self):
        """Each episode stays separately citable."""
        first = open_packet(transition(at="2026-08-01T00:00:00+00:00"), [obs(0)])
        second = open_packet(transition(at="2026-09-01T00:00:00+00:00"), [obs(1)])
        self.assertNotEqual(first.packet_id, second.packet_id)

    def test_the_id_is_keyed_on_publisher_state_and_opening_transition(self):
        self.assertEqual(packet_id("p|f", "QUARANTINE", "t1"), "p|f|QUARANTINE|t1")


class TestCardinality(unittest.TestCase):
    def test_publishers_and_rules_are_plural(self):
        """Utah's transition has two independent causes. Singular fields would
        force a convention to be invented at implementation time, and two
        implementers would invent different ones."""
        packet = open_packet(transition(rule_ids=("R2", "R4")), [obs(0)])
        self.assertEqual(packet.rule_ids, ["R2", "R4"])
        self.assertIsInstance(packet.publisher_keys, list)
