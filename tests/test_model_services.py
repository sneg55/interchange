"""Model Armor and the two Gemini integrations. Sections 6.5, 6.6, 6.7.

Run with: python3 -m unittest discover -s tests -v

No network. Every test drives an injected fake client, which is the point of
injecting one: the behaviour that matters here is what happens when the service
misbehaves, and that is unreachable if the tests need the real thing.

Two invariants carry the file. Model Armor fails closed on every failure mode,
including ones this build does not recognise. And the adjudicator never returns
a confidence score, never guesses, and never raises: `UNSURE` is a correct
answer and exhaustion produces it rather than an exception.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.reconciler.matching import CandidatePair
from src.features.screener.gate import ScreeningGate
from src.services.gemini import (
    MAX_ATTEMPTS,
    VERDICT_SCHEMA,
    GeminiAdjudicator,
    pair_key,
)
from src.services.model_armor import (
    MAX_TEXT_BYTES,
    ModelArmorScreener,
    ScreeningUnavailable,
    _interpret,
)
from src.services.screeners import REDACTION_PLACEHOLDER

AT = "2026-08-07T12:00:00+00:00"


class FakeState:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeResult:
    def __init__(self, state: str, filters: dict | None = None) -> None:
        self.filter_match_state = FakeState(state)
        self.filter_results = filters or {}


class FakeResponse:
    def __init__(self, result) -> None:
        self.sanitization_result = result


def feature(event_id="z-1", description="lane closed", source="TRANSCOM"):
    return {
        "id": event_id,
        "properties": {
            "start_date": "2026-08-01T00:00:00Z",
            "end_date": "2026-09-01T00:00:00Z",
            "core_details": {
                "data_source_id": source,
                "road_names": ["I-95"],
                "direction": "northbound",
                "description": description,
                "event_status": "active",
            },
        },
    }


def pair():
    return CandidatePair(
        left_index=0,
        right_index=1,
        left_publisher="A|a",
        right_publisher="B|b",
        distance_m=0.4,
        coverage=None,
        tier="TIER_2_ADJUDICATED",
    )


class TestModelArmorInterpretation(unittest.TestCase):
    def test_only_an_explicit_no_match_passes(self):
        self.assertEqual(_interpret(FakeResponse(FakeResult("NO_MATCH_FOUND"))), ("PASS", None))

    def test_a_match_blocks(self):
        verdict, _ = _interpret(FakeResponse(FakeResult("MATCH_FOUND")))
        self.assertEqual(verdict, "BLOCK")

    def test_an_unrecognised_state_blocks(self):
        """A response shape that changed upstream must fail closed rather than
        read as clean. This is the branch that silently opens if it is wrong."""
        verdict, category = _interpret(FakeResponse(FakeResult("SOMETHING_NEW")))
        self.assertEqual(verdict, "BLOCK")
        self.assertIn("unrecognised", category)

    def test_a_missing_result_blocks(self):
        verdict, category = _interpret(FakeResponse(None))
        self.assertEqual(verdict, "BLOCK")
        self.assertIn("E_SCREEN_001", category)

    def test_text_beyond_the_limit_is_blocked_not_truncated(self):
        """Screening a prefix and passing the whole string is a false negative
        dressed up as a check."""
        screener = ModelArmorScreener("p", "us-central1", "t", client=object())
        verdict, category = screener.screen("A" * (MAX_TEXT_BYTES + 1))
        self.assertEqual(verdict, "BLOCK")
        self.assertIn("E_SCREEN_004", category)


class TestModelArmorFailsClosed(unittest.TestCase):
    class Exploding:
        def sanitize_user_prompt(self, request=None, timeout=None):
            del request, timeout
            raise ConnectionError("model armor unreachable")

    def test_an_outage_raises_so_the_gate_can_refuse_to_cache_it(self):
        """Returning BLOCK here would let the gate cache an outage as a verdict
        and keep redacting long after the service came back."""
        screener = ModelArmorScreener("p", "us-central1", "t", client=self.Exploding())
        with self.assertRaises(ScreeningUnavailable):
            screener.screen("anything")

    def test_the_gate_redacts_an_outage_without_caching_it(self):
        gate = ScreeningGate(ModelArmorScreener("p", "us-central1", "t", client=self.Exploding()))
        result = gate.screen("lane closed", "A|a", "description", AT)
        self.assertFalse(result.passed)
        self.assertEqual(result.text, REDACTION_PLACEHOLDER)
        self.assertEqual(gate.new_results, [], "an outage is not a verdict")

    def test_the_policy_version_is_the_template_so_a_change_invalidates_the_cache(self):
        """Text screened under the old template has NOT been screened under the
        new one, and a cache keyed on the text alone could not tell."""
        screener = ModelArmorScreener("p", "us-central1", "strict-v2", client=object())
        self.assertIn("strict-v2", screener.policy_version)


class TestAdjudicator(unittest.TestCase):
    class Client:
        def __init__(self, payloads) -> None:
            self.payloads = list(payloads)
            self.calls = 0
            self.models = self

        def generate_content(self, model=None, contents=None, config=None):
            del model, contents, config
            self.calls += 1
            payload = self.payloads.pop(0)
            if isinstance(payload, Exception):
                raise payload

            class Response:
                text = payload
                usage_metadata = None

            return Response()

    def test_no_confidence_score_is_ever_requested(self):
        """A scalar from a model invites a threshold, and a threshold puts the
        model back in the gate path section 2 keeps it out of."""
        fields = set(VERDICT_SCHEMA["properties"])
        self.assertEqual(fields, {"verdict", "rationale"})
        for banned in ("confidence", "score", "probability", "certainty"):
            self.assertNotIn(banned, fields)

    def test_a_duplicate_verdict_is_returned(self):
        client = self.Client(['{"verdict": "DUPLICATE", "rationale": "same ramp"}'])
        adjudicator = GeminiAdjudicator(client=client)
        self.assertEqual(adjudicator.adjudicate(feature(), feature("z-2"), pair()), "DUPLICATE")

    def test_exhaustion_yields_unsure_rather_than_an_exception(self):
        """A model that cannot answer must not take the cycle down, and must not
        be pushed into guessing."""
        client = self.Client([RuntimeError("503"), RuntimeError("503")])
        verdict = GeminiAdjudicator(client=client).adjudicate(feature(), feature("z-2"), pair())
        self.assertEqual(verdict, "UNSURE")
        self.assertEqual(client.calls, MAX_ATTEMPTS)

    def test_an_unrecognised_verdict_is_retried_then_unsure(self):
        client = self.Client(['{"verdict": "PROBABLY", "rationale": "?"}', '{"verdict": "MAYBE"}'])
        self.assertEqual(
            GeminiAdjudicator(client=client).adjudicate(feature(), feature("z-2"), pair()),
            "UNSURE",
        )

    def test_a_retry_that_succeeds_is_reported_as_two_attempts(self):
        client = self.Client(
            [RuntimeError("429"), '{"verdict": "DISTINCT", "rationale": "different road"}']
        )
        adjudicator = GeminiAdjudicator(client=client)
        self.assertEqual(adjudicator.adjudicate(feature(), feature("z-2"), pair()), "DISTINCT")
        self.assertEqual(adjudicator.new_records[0].attempts, 2)

    def test_an_unchanged_pair_is_decided_once(self):
        """Keyed on the ordered pair of content hashes, so a re-run reuses the
        verdict rather than paying for it and possibly getting another one."""
        client = self.Client(['{"verdict": "DUPLICATE", "rationale": "same"}'])
        adjudicator = GeminiAdjudicator(client=client)
        left, right = feature(), feature("z-2")
        for _ in range(5):
            adjudicator.adjudicate(left, right, pair())
        self.assertEqual(client.calls, 1)

    def test_the_pair_key_is_symmetric(self):
        """The question is symmetric; caching it twice would allow two different
        answers to the same question."""
        left, right = feature(), feature("z-2")
        self.assertEqual(pair_key(left, right), pair_key(right, left))

    def test_the_record_carries_what_a_replay_needs(self):
        client = self.Client(['{"verdict": "DUPLICATE", "rationale": "same ramp"}'])
        adjudicator = GeminiAdjudicator(client=client)
        adjudicator.adjudicate(feature(), feature("z-2"), pair())
        record = adjudicator.new_records[0]
        self.assertTrue(record.model_id)
        self.assertTrue(record.prompt_version)
        self.assertNotIn("confidence", record.to_doc())

    def test_a_corrupted_cached_verdict_is_not_served(self):
        """A persisted record is a document like any other. Validated only on the
        way in, a corrupted or hand-edited one flows straight out as a verdict
        nobody checked."""
        from src.services.gemini import AdjudicationRecord, pair_key

        left, right = feature(), feature("z-2")
        key = pair_key(left, right)
        poisoned = AdjudicationRecord(
            pair_key=key,
            decided_at=AT,
            model_id="gemini-2.5-flash",
            prompt_version="adjudicate-v1",
            verdict="PROBABLY",
            rationale="corrupted",
            latency_ms=0.0,
            token_counts={},
            attempts=1,
        )
        client = self.Client(['{"verdict": "DISTINCT", "rationale": "re-asked"}'])
        adjudicator = GeminiAdjudicator(client=client, cache={key: poisoned})
        self.assertEqual(adjudicator.adjudicate(left, right, pair()), "DISTINCT")
        self.assertEqual(client.calls, 1, "it re-asked rather than serving the bad value")

    def test_the_key_carries_the_model_and_prompt_version(self):
        """A verdict reached under one model or prompt is not an answer to the
        question a different one asks. A key on feature bytes alone would serve
        last month's opinion after a prompt rewrite."""
        from src.services.gemini import pair_key

        left, right = feature(), feature("z-2")
        self.assertNotEqual(
            pair_key(left, right, "gemini-2.5-flash", "v1"),
            pair_key(left, right, "gemini-3.0-pro", "v1"),
        )
        self.assertNotEqual(
            pair_key(left, right, "gemini-2.5-flash", "v1"),
            pair_key(left, right, "gemini-2.5-flash", "v2"),
        )

    def test_a_malformed_record_is_unsure_rather_than_an_exception(self):
        """A reconciliation that dies on one bad zone is worse than one that
        declines to merge it."""

        class Unserialisable:
            def __getitem__(self, key):
                raise TypeError("not a mapping")

        adjudicator = GeminiAdjudicator(client=self.Client([]))
        self.assertEqual(adjudicator.adjudicate(None, feature(), pair()), "UNSURE")

    def test_the_prompt_carries_no_coordinates(self):
        """The geometric test already ran. Handing the model 65 vertices invites
        it to re-litigate a question that was answered deterministically."""
        captured = {}

        class Recorder(self.Client):
            def generate_content(self, model=None, contents=None, config=None):
                captured["prompt"] = contents
                return super().generate_content(model, contents, config)

        adjudicator = GeminiAdjudicator(
            client=Recorder(['{"verdict": "UNSURE", "rationale": "cannot tell"}'])
        )
        left = feature()
        left["geometry"] = {"type": "LineString", "coordinates": [[-74.1234, 40.5678]]}
        adjudicator.adjudicate(left, feature("z-2"), pair())
        self.assertNotIn("-74.1234", captured["prompt"])
        self.assertIn("TRANSCOM", captured["prompt"], "the declared source IS shown")


if __name__ == "__main__":
    unittest.main()
