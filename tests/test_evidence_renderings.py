"""The two renderings of a packet, and every word in them. Section 6.7.

Run with: python3 -m unittest discover -s tests -v

A notice is read by someone at a named public agency who has never seen this
system\'s schema. So: no field names, no document ids, no microsecond stamps, no
figure the packet did not supply, and no instant relabelled into a zone it was
never in.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.evidence.packet import open_packet
from src.features.evidence.renderings import consumer_rendering, facts, registry_rendering
from src.features.publisher_agent.observation import Observation
from tests.evidence_support import START, obs, transition


class TestRenderings(unittest.TestCase):
    def setUp(self):
        self.packet = open_packet(transition(), [obs(i) for i in range(5)])

    def test_the_consumer_rendering_names_the_signal_that_triggered_it(self):
        text = consumer_rendering(self.packet)
        self.assertIn("R2", text)
        self.assertIn("R4", text)
        self.assertIn("744", text)

    def test_no_rendering_addresses_a_stranger_in_our_field_names(self):
        """A notice goes to someone at a named organization who has never seen
        this system's schema.

        `update_date` and `end_date` appeared in the queue's Asserts column, in
        the consumer decision record and in the outbound registry notice: this
        product's storage internals, addressed to a stranger.
        """
        for text in (consumer_rendering(self.packet), registry_rendering(self.packet)):
            self.assertNotIn("update_date", text)
            self.assertNotIn("end_date", text)
        self.assertIn("last-updated time", registry_rendering(self.packet))

    def test_a_notice_addresses_an_organization_not_a_document_id(self):
        """A notice goes to a body of people at a named organization.

        The salutation was our `org|feedname` document id, and the consumer
        record opened on the composite packet id with the publisher segment in
        it twice. Both keep the key as a labelled field, because the identity
        has to stay exact; neither leads with it.
        """
        registry = registry_rendering(self.packet)
        self.assertIn("WZDx Feed Registry listing: Utah DOT\n", registry)
        self.assertIn("Feed: udot (Utah DOT)", registry)
        consumer = consumer_rendering(self.packet)
        self.assertTrue(consumer.startswith("Decision record: Utah DOT\n"))
        self.assertIn("Feed: udot (Utah DOT)", consumer)
        self.assertNotIn("TRUST_TRANSITION", consumer)

    def test_no_rendering_prints_the_pipe_joined_storage_key(self):
        """`Utah DOT|udot` is a Firestore document id.

        It was the value of the `Feed:` field in both renderings, so the one
        artifact a stranger at a named agency reads carried this system's
        document id in it. The identity still has to be exact, so the feed name
        and the organization are both there; they are simply not joined by the
        character the store happens to use.
        """
        for text in (consumer_rendering(self.packet), registry_rendering(self.packet)):
            self.assertNotIn("Utah DOT|udot", text)
            self.assertIn("udot", text)
            self.assertIn("Utah DOT", text)

    def test_a_real_window_still_reads_as_one(self):
        text = registry_rendering(self.packet)
        self.assertIn("Observation window:", text)
        self.assertIn("5 polls", text)

    def test_the_registry_rendering_cites_the_policy_clause(self):
        text = registry_rendering(self.packet)
        self.assertIn("WZDx Feed Registry listing requires", text)
        self.assertIn("requires human approval before it is sent", text)

    def test_a_drafter_is_given_facts_and_nothing_else(self):
        """It cannot cite a figure it was never given, which is what stops a
        model inventing a number that appears under Interchange's name."""
        captured = {}

        class Recorder:
            def draft(self, data):
                captured.update(data)
                return "Dear registry owner, we write concerning a listed feed."

        registry_rendering(self.packet, Recorder())
        self.assertNotIn("observations", captured, "raw observation bodies are not handed over")
        self.assertEqual(captured["rule_ids"], ["R2", "R4"])
        self.assertEqual(captured["total_observations"], 5)

    def test_a_fabricated_figure_discards_the_draft(self):
        """The facts in a notice come from the packet, not the model. Appending
        a correct appendix does not make a fabricated figure acceptable: the
        notice goes out over Interchange's name, and a human reading "broken for
        9,999 days" has been told something the evidence does not say."""

        class Fabricator:
            def draft(self, data):
                del data
                return "This feed has been broken for 9,999 days."

        text = registry_rendering(self.packet, Fabricator())
        self.assertNotIn("9,999", text)
        self.assertEqual(text, registry_rendering(self.packet))

    def test_prose_quoting_the_packets_own_figures_is_kept(self):
        """The guard must not reject a model that did its job."""

        class Faithful:
            def draft(self, data):
                return (
                    f"This notice concerns {len(data['publisher_keys'])} publisher and "
                    f"{data['observations_in_window']} observations."
                )

        text = registry_rendering(self.packet, Faithful())
        self.assertIn("This notice concerns", text)
        self.assertIn("Facts of record:", text)

    def test_prose_with_no_figures_at_all_is_kept(self):
        class Vague:
            def draft(self, data):
                del data
                return "Dear registry owner, we write concerning a listed feed."

        self.assertIn("Dear registry owner", registry_rendering(self.packet, Vague()))

    def test_a_failing_drafter_does_not_block_the_finding(self):
        """A notice that cannot be written is a notice that does not go out. The
        facts are the part that matters."""

        class Broken:
            def draft(self, data):
                del data
                raise RuntimeError("gemini unavailable")

        self.assertEqual(registry_rendering(self.packet, Broken()), registry_rendering(self.packet))

    def test_facts_are_deterministic(self):
        self.assertEqual(facts(self.packet), facts(self.packet))

    def test_no_rendering_prints_a_microsecond_timestamp(self):
        """Six digits of fractional second are a storage artifact. Seconds are
        enough to match a notice back to the observation it cites."""
        for text in (consumer_rendering(self.packet), registry_rendering(self.packet)):
            self.assertNotRegex(text, r"\d{2}:\d{2}:\d{2}\.\d")
            self.assertNotIn("+00:00", text)

    def test_a_single_poll_is_not_written_as_a_window(self):
        """A finding resting on one poll produced "window T to T, 1 polls".

        That states a span that was never examined, and does it
        ungrammatically. Every notice in the queue read that way, because every
        packet was opened with the one observation that tripped the transition.
        """
        one = open_packet(transition(), [obs(0)])
        text = registry_rendering(one)
        self.assertIn("a single poll at", text)
        self.assertNotIn("1 polls", text)
        self.assertIn("all 1 observation in the window", text)


class TestMoments(unittest.TestCase):
    """An instant in a notice is converted to UTC, never relabelled as UTC.

    Utah DOT's feed reports `2023-03-19T07:04:04.861489-06:00`, which is
    13:04:04 UTC. Both renderings printed "2023-03-19 07:04:04 UTC": the offset
    survived only past the decimal point, so stripping the fractional second
    took it with it and the local wall time was published under a UTC label.
    The console's own `format.ts` rendered the same field correctly on the
    screen beside it, so the product stated the evidence for a quarantine two
    ways, six hours apart.
    """

    def test_an_offset_timestamp_is_converted_to_utc(self):
        """Asserted on the consumer record, which is where the observed instant
        is quoted; the registry notice states what the rule asserts rather than
        the figure behind it."""
        packet = open_packet(transition(), [obs(0)])
        text = consumer_rendering(packet)
        self.assertIn("2023-03-19 13:04:04 UTC", text)
        self.assertNotIn("07:04:04 UTC", text)
        self.assertNotIn("07:04:04 UTC", registry_rendering(packet))

    def test_a_utc_timestamp_is_unchanged(self):
        from src.features.evidence.renderings import _moment

        self.assertEqual(_moment("2024-02-22T19:32:06Z"), "2024-02-22 19:32:06 UTC")
        self.assertEqual(_moment("2024-02-22T19:32:06+00:00"), "2024-02-22 19:32:06 UTC")

    def test_a_timestamp_with_no_zone_is_not_claimed_to_be_utc(self):
        """A naive stamp is not known to be UTC, and saying so would be this
        system's cardinal error: recording "we do not know" as a measurement."""
        from src.features.evidence.renderings import _moment

        said = _moment("2024-02-22T19:32:06")
        self.assertIn("2024-02-22 19:32:06", said)
        self.assertNotIn("UTC", said)

    def test_an_unreadable_timestamp_is_said_to_be_unreadable(self):
        from src.features.evidence.renderings import _moment

        said = _moment("last Tuesday")
        self.assertIn("last Tuesday", said)
        self.assertNotIn("UTC", said)

    def test_a_drafter_may_cite_the_utc_instant_the_notice_prints(self):
        """The deterministic block prints 13:04:04; a drafter handed only the
        raw `-06:00` string would have "13" rejected as a fabricated figure."""

        class Faithful:
            def draft(self, data):
                return f"The feed was last updated {data['latest_update_date_utc']}."

        text = registry_rendering(self.packet_for_drafter(), Faithful())
        self.assertIn("2023-03-19 13:04:04 UTC", text)
        self.assertIn("Facts of record:", text)

    def packet_for_drafter(self):
        return open_packet(transition(), [obs(0)])


class TestClausesForRulesThatDidNotFire(unittest.TestCase):
    def test_zero_of_zero_contradictory_zones_is_not_reported(self):
        """R4 abstains when there are no dated active zones (spec 6.4), so
        "0 of 0 zones marked active have an end date in the past" states a
        measurement that was never made. Hawaii DOT's notice carried it under an
        R2-only finding."""
        quiet = Observation(
            publisher_key="Hawaii DOT|hidot",
            polled_at=START.isoformat(),
            http_status=200,
            update_date="2024-02-22T19:32:06Z",
            update_age_seconds=898 * 86400,
            feature_count=0,
            active_count=0,
            active_with_past_end_date=0,
            active_undated=0,
            schema_version_used="4.1",
            schema_error_count=0,
            content_hash="frozen",
        )
        packet = open_packet(transition(rule_ids=("R2",)), [quiet])
        text = consumer_rendering(packet)
        self.assertNotIn("0 of 0", text)
        self.assertIn("last updated 2024-02-22 19:32:06 UTC", text)

    def test_a_real_count_is_still_reported(self):
        packet = open_packet(transition(), [obs(0)])
        self.assertIn(
            "744 of 744 zones marked active have an end date in the past",
            consumer_rendering(packet),
        )
