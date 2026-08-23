"""The two places a model is allowed to appear. Sections 6.6 and 6.7.

Tier 2 duplicate adjudication and notice drafting. Nothing else, and nothing here
can reach `fleet_state`: both return values the caller uses downstream of the
gate, and neither is passed to the trust scorer.

Two properties are enforced here rather than remembered:

- **No confidence score is requested.** Section 6.6 says a scalar from a model
  invites a threshold, and a threshold puts the model back in the gate path
  section 2 keeps it out of. The response schema has no field for one.
- **`UNSURE` is a first-class outcome.** A model that cannot tell must not be
  pushed into guessing, and exhaustion after retries yields `UNSURE` rather than
  an exception. It resolves to DISTINCT for the merge: a wrong merge hides a real
  closure, while a wrong split merely double counts.

Text reaching either has already been screened. That is the caller's
responsibility and `fleet_cycle.py` screens source zones before reconciliation
for exactly this reason.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from src.features.reconciler.matching import CandidatePair, core, road_event_id

DEFAULT_MODEL = "gemini-3.5-flash"
PROMPT_VERSION = "adjudicate-v1"
MAX_ATTEMPTS = 2

# Deliberately no confidence, score or probability field. Adding one would be the
# single change that reintroduces a threshold, and a threshold is a gate.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["DUPLICATE", "DISTINCT", "UNSURE"]},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
}

ADJUDICATION_PROMPT = """You are comparing two work zone records published by two \
different transportation agencies. Decide whether they describe THE SAME physical \
work zone.

They have already passed a geometric test, so proximity is not evidence by itself: \
a ramp closure can lie inside a long corridor project and be a different zone.

Answer DUPLICATE only if they describe the same work. Answer DISTINCT if they \
describe different work. Answer UNSURE if the records do not let you tell. UNSURE \
is a correct and expected answer; do not guess.

Record A ({a_publisher}):
{a}

Record B ({b_publisher}):
{b}

Measured: minimum distance {distance} m, symmetric length coverage {coverage}.
"""


@dataclass(slots=True)
class AdjudicationRecord:
    """Section 7. Keyed on the ordered pair of source content hashes.

    Persisted so an unchanged pair is decided once and reused, and so a re-run
    can be explained. Without a stored verdict and a prompt version a re-run
    silently produces different groups and nothing can tell you why.
    """

    pair_key: str
    decided_at: str
    model_id: str
    prompt_version: str
    verdict: str
    rationale: str
    latency_ms: float
    token_counts: dict[str, int]
    attempts: int

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


VERDICTS = frozenset({"DUPLICATE", "DISTINCT", "UNSURE"})


def pair_key(
    left: dict[str, Any],
    right: dict[str, Any],
    model: str = DEFAULT_MODEL,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """Ordered pair of source content hashes, plus the decision context.

    Ordered so (A, B) and (B, A) are the same key: the question is symmetric and
    caching it twice would double the spend and allow two different answers to
    the same question.

    Model and prompt version are IN the key. The prompt carries publisher
    identities and framing, so a verdict reached under one model or prompt is not
    an answer to the question a different one asks. A key on feature bytes alone
    would silently serve last month's model's opinion after a prompt rewrite,
    which is exactly the drift the AdjudicationRecord exists to make visible.
    """
    digests = sorted(
        hashlib.sha256(json.dumps(f, sort_keys=True, default=str).encode()).hexdigest()
        for f in (left, right)
    )
    return f"{digests[0]}|{digests[1]}|{model}|{prompt_version}"


def _summarise(feature: dict[str, Any]) -> str:
    """What the model is shown. Screened text only, and no geometry dump.

    Coordinates are excluded deliberately: the geometric test already ran, and
    handing the model 65 vertices invites it to re-litigate a question that was
    answered deterministically.
    """
    details = core(feature)
    props = feature.get("properties") or {}
    return json.dumps(
        {
            "road_event_id": road_event_id(feature),
            "data_source_id": details.get("data_source_id"),
            "road_names": details.get("road_names"),
            "direction": details.get("direction"),
            "description": details.get("description"),
            "start_date": props.get("start_date"),
            "end_date": props.get("end_date"),
            "event_status": details.get("event_status") or props.get("event_status"),
        },
        indent=2,
        default=str,
    )


class GeminiAdjudicator:
    """Tier 2. Returns DUPLICATE, DISTINCT or UNSURE, and never raises."""

    def __init__(
        self,
        client: Any = None,
        model: str = DEFAULT_MODEL,
        cache: dict[str, AdjudicationRecord] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._cache = cache or {}
        self.new_records: list[AdjudicationRecord] = []

    def _connect(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    def adjudicate(self, left: dict[str, Any], right: dict[str, Any], pair: CandidatePair) -> str:
        """Never raises. A malformed feature is UNSURE, not an exception.

        The claim has to hold for the INPUT as well as the response. A record
        this code cannot serialise would otherwise take the whole cycle down,
        and a reconciliation that dies on one bad zone is worse than one that
        declines to merge it.
        """
        try:
            return self._adjudicate(left, right, pair)
        except Exception:  # noqa: BLE001 - a bad record is UNSURE, not a crash
            return "UNSURE"

    def _adjudicate(
        self, left: dict[str, Any], right: dict[str, Any], pair: CandidatePair
    ) -> str:
        key = pair_key(left, right, self._model, PROMPT_VERSION)
        cached = self._cache.get(key)
        if cached is not None:
            # Validated on the way OUT, not only on the way in. A persisted
            # record is a document like any other: corrupted, hand-edited or
            # written by a build that allowed a value this one does not, it would
            # otherwise flow straight through as a verdict nobody checked.
            if cached.verdict in VERDICTS:
                return cached.verdict
            del self._cache[key]

        prompt = ADJUDICATION_PROMPT.format(
            a_publisher=pair.left_publisher,
            b_publisher=pair.right_publisher,
            a=_summarise(left),
            b=_summarise(right),
            distance="unknown" if pair.distance_m is None else f"{pair.distance_m:.1f}",
            coverage="not computed" if pair.coverage is None else f"{pair.coverage:.2f}",
        )
        started = time.time()
        verdict, rationale, tokens, attempts = self._ask(prompt)
        record = AdjudicationRecord(
            pair_key=key,
            decided_at=_now(),
            model_id=self._model,
            prompt_version=PROMPT_VERSION,
            verdict=verdict,
            rationale=rationale,
            latency_ms=(time.time() - started) * 1000,
            token_counts=tokens,
            attempts=attempts,
        )
        self._cache[key] = record
        self.new_records.append(record)
        return verdict

    def _ask(self, prompt: str) -> tuple[str, str, dict[str, int], int]:
        """Bounded at MAX_ATTEMPTS. Exhaustion is UNSURE, never an exception."""
        last = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._connect().models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": VERDICT_SCHEMA,
                        # Deterministic as the API allows. A duplicate decision
                        # that changes between identical runs cannot be explained
                        # after the fact, and section 6.6 requires replay.
                        "temperature": 0.0,
                    },
                )
                parsed = json.loads(response.text or "{}")
                verdict = parsed.get("verdict")
                if verdict not in VERDICTS:
                    raise ValueError(f"unrecognised verdict {verdict!r}")
                usage = getattr(response, "usage_metadata", None)
                return (
                    verdict,
                    str(parsed.get("rationale", "")),
                    {
                        "prompt": getattr(usage, "prompt_token_count", 0) or 0,
                        "response": getattr(usage, "candidates_token_count", 0) or 0,
                    },
                    attempt,
                )
            except Exception as exc:  # noqa: BLE001 - exhaustion is a verdict, not a crash
                last = f"{type(exc).__name__}: {exc}"
        # Not DISTINCT and not an exception. UNSURE is the honest answer, it is
        # surfaced separately on the console, and it resolves to a split for the
        # merge because a wrong merge hides a real closure.
        return (
            "UNSURE",
            f"adjudication failed after {MAX_ATTEMPTS} attempts: {last}",
            {},
            MAX_ATTEMPTS,
        )


class GeminiDrafter:
    """Notice prose for section 6.7. The FACTS come from the packet, not here.

    `registry_rendering` hands this only the deterministic facts dict and
    discards any draft citing a figure that dict does not contain, so a
    fabrication cannot reach an outbound notice. This class does not need to be
    trusted; it needs to be checked, and it is.
    """

    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def draft(self, facts: dict[str, Any]) -> str:
        from google import genai

        client = self._client or genai.Client()
        self._client = client
        prompt = (
            "Write two short paragraphs of a formal notice to the owner of a "
            "federal data registry about a listed feed. Use ONLY the facts "
            "below. Do not introduce any number, date or quantity that does not "
            "appear in them. Do not recommend a specific action.\n\n"
            f"{json.dumps(facts, indent=2, default=str)}"
        )
        response = client.models.generate_content(
            model=self._model, contents=prompt, config={"temperature": 0.2}
        )
        return response.text or ""


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()
