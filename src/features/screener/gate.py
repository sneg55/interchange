"""The screening gate. Section 6.5.

Every free-text field crossing one of exactly three egresses goes through here:
Gemini adjudication of a duplicate pair (6.6), notice drafting (6.7), and the
`description` and `road_names` fields of the republished feed (6.8).

Two invariants, and the code exists to make them structural rather than
remembered:

- **Injected text can never raise a trust score.** Nothing in this module writes
  a `fleet_state`, and the screening records are deliberately separate from the
  trust records so that a screening block cannot be wired into the scorer by
  accident.
- **It never reaches the summarizer.** `screen` returns a redacted string on a
  block, so a caller that forgets to check the verdict still forwards the
  placeholder rather than the payload.

Fails closed. If Model Armor is unavailable, unscreened text is treated exactly
as blocked text. A screener that returned PASS when it could not reach the
service would break the invariant the whole security claim rests on, and would
break it silently.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import ErrorIds
from src.services.ports import Screener
from src.services.screeners import REDACTION_PLACEHOLDER

from .records import (
    BlockedText,
    CacheKey,
    Screened,
    ScreenedField,
    ScreeningIncident,
    ScreeningResult,
    text_sha256,
)

# Concurrent screening calls during the pre-warm pass. Model Armor is a network
# round trip at roughly 0.191s, so the cost of a cycle is wall-clock waiting and
# not compute. Sixteen keeps a fifteen minute cadence comfortable without being
# an unannounced load test against someone else's quota.
WARM_WORKERS = 16


class ScreeningGate:
    """Screens text, caches verdicts, and records what it blocked.

    Holds the cache in memory and hands new records to the caller to persist,
    rather than writing them itself. The gate is called from the reconciler, the
    evidence packet and the republisher, and a gate that wrote to a store would
    make all three untestable without one.
    """

    def __init__(self, screener: Screener, cache: list[ScreeningResult] | None = None) -> None:
        self._screener = screener
        # A list, not a pre-keyed dict. A dict would let a caller install an
        # entry under a key whose policy version does not match the record's own,
        # bypassing the version check that is the entire point of the key.
        self._cache: dict[str, ScreeningResult] = {}
        self.new_results: list[ScreeningResult] = []
        self.new_incidents: list[ScreeningIncident] = []
        self.new_blocked_text: list[BlockedText] = []
        self._blocked_text_seen: set[str] = set()
        self.screened_count = 0  # calls that actually reached the screener
        if cache:
            self.load(cache)

    @property
    def policy_version(self) -> str:
        return self._screener.policy_version

    def load(self, results: list[ScreeningResult]) -> None:
        """Warm the cache from persisted verdicts.

        Only verdicts under the CURRENT policy and model version are consulted.
        Older ones are retained for audit and simply never match the key, so text
        is re-screened lazily on next sight rather than in one fleet-wide sweep.
        """
        for result in results:
            if (
                result.policy_version == self._screener.policy_version
                and result.model_version == self._screener.model_version
                and result.verdict in ("PASS", "BLOCK")
            ):
                # Keyed from the record's OWN fields, never from a caller-supplied
                # key, so a document claiming the current policy cannot be filed
                # under it unless it really carries it.
                self._cache[result.key.doc_id] = result
                if result.blocked:
                    # A warmed cache means the BlockedText already exists. Without
                    # this the first cached block after a restart re-emits it with
                    # a newer first_seen_at, overwriting the record of when the
                    # string was actually first seen.
                    self._blocked_text_seen.add(result.text_sha256)

    def unscreened(self, text: str) -> bool:
        """Whether this text still needs a call. For `prewarm`, which batches them."""
        key = CacheKey(text_sha256(text), self._screener.policy_version, self.model_version)
        return key.doc_id not in self._cache

    def record_verdict(self, text: str, publisher_key: str, verdict: str, category, at: str):
        """File a verdict obtained elsewhere. Only PASS or BLOCK is accepted.

        Anything else is dropped rather than stored, so it reaches `screen`'s
        fail-closed path on next sight. An outage is not a verdict and must never
        be cached as one.
        """
        if verdict not in ("PASS", "BLOCK"):
            return
        digest = text_sha256(text)
        result = ScreeningResult(
            text_sha256=digest,
            policy_version=self._screener.policy_version,
            model_version=self.model_version,
            verdict=verdict,
            category=category,
            screened_at=at,
            first_seen_publisher_keys=[publisher_key],
        )
        self._cache[result.key.doc_id] = result
        self.new_results.append(result)
        self.screened_count += 1

    @property
    def model_version(self) -> str:
        return self._screener.model_version

    @property
    def screener(self) -> Screener:
        return self._screener

    def screen(
        self,
        text: str | None,
        publisher_key: str,
        field: ScreenedField,
        at: str,
        road_event_id: str | None = None,
    ) -> Screened:
        """Screen one field. Returns text that is always safe to forward."""
        if not text:
            # Nothing to screen and nothing to leak. Not a PASS verdict on
            # anything, so no ScreeningResult is written.
            return Screened("", "PASS", None, text_sha256(""), cached=True)

        digest = text_sha256(text)
        key = CacheKey(digest, self._screener.policy_version, self._screener.model_version)
        cached = self._cache.get(key.doc_id)
        if cached is not None:
            return self._outcome(cached, text, publisher_key, field, at, road_event_id, True)

        try:
            verdict, category = self._screener.screen(text)
            if verdict not in ("PASS", "BLOCK"):
                # A screener returning something unrecognised has not screened
                # anything. Raised rather than stored, so it takes the
                # fail-closed path below instead of being cached as a verdict.
                raise ValueError(f"unrecognised verdict {verdict!r}")
        except Exception as exc:  # noqa: BLE001 - unavailable is treated as blocked
            # Deliberately NOT cached. An outage is not a verdict, and caching it
            # would keep redacting the text long after the service came back.
            unscreened = ScreeningResult(
                text_sha256=digest,
                policy_version=self._screener.policy_version,
                model_version=self._screener.model_version,
                # UNAVAILABLE, not BLOCK. It still redacts, because `blocked` is
                # anything but PASS, but it does not assert that this text was
                # hostile and so does not file it into `blocked_text`.
                verdict="UNAVAILABLE",
                category=f"{ErrorIds.SCREEN_UNAVAILABLE}: {type(exc).__name__}",
                screened_at=at,
                first_seen_publisher_keys=[publisher_key],
            )
            return self._outcome(unscreened, text, publisher_key, field, at, road_event_id, False)

        self.screened_count += 1
        result = ScreeningResult(
            text_sha256=digest,
            policy_version=self._screener.policy_version,
            model_version=self._screener.model_version,
            verdict=verdict,
            category=category,
            screened_at=at,
            first_seen_publisher_keys=[publisher_key],
        )
        self._cache[key.doc_id] = result
        self.new_results.append(result)
        return self._outcome(result, text, publisher_key, field, at, road_event_id, False)

    def screen_names(
        self,
        names: list[str] | None,
        publisher_key: str,
        at: str,
        road_event_id: str | None = None,
    ) -> tuple[list[str], list[Screened]]:
        """Screen `road_names` element by element.

        Per element rather than joined, because one blocked name must not redact
        the others: section 6.8 needs the field to stay schema-valid, and
        replacing the whole list would discard road identifiers that screened
        clean. Section 6.5 screens `road_names` at all because an invariant with
        an exception is not an invariant.
        """
        outcomes = [
            self.screen(name, publisher_key, "road_names", at, road_event_id)
            for name in (names or [])
        ]
        return [o.text for o in outcomes], outcomes

    # ------------------------------------------------------------------ detail

    def _outcome(
        self,
        result: ScreeningResult,
        text: str,
        publisher_key: str,
        field: ScreenedField,
        at: str,
        road_event_id: str | None,
        cached: bool,
    ) -> Screened:
        if not result.blocked:
            return Screened(text, "PASS", None, result.text_sha256, cached)

        # Every block occurrence is recorded, including cached ones: the cache is
        # about not re-screening bytes, not about not reporting that a publisher
        # served them again.
        incident = ScreeningIncident(
            publisher_key=publisher_key,
            road_event_id=road_event_id,
            field=field,
            text_sha256=result.text_sha256,
            category=result.category,
            policy_version=result.policy_version,
            at=at,
        )
        self.new_incidents.append(incident)
        blocked_text = None
        # Tracked across drains. Without this the second batch re-emits the same
        # BlockedText with a later `first_seen_at`, overwriting the record of when
        # the string was actually first seen.
        # Only a real BLOCK archives the payload. An outage redacts the text and
        # records the incident, which is true and useful, but storing the string
        # itself would assert that a screener nobody could reach had judged it
        # hostile. The incident carries SCREEN_UNAVAILABLE and is the honest
        # record of what happened.
        if result.verdict == "BLOCK" and result.text_sha256 not in self._blocked_text_seen:
            self._blocked_text_seen.add(result.text_sha256)
            blocked_text = BlockedText.create(text, at)
            self.new_blocked_text.append(blocked_text)
        if publisher_key not in result.first_seen_publisher_keys:
            # Which publishers served this string, not just the first. One
            # injected description appearing across several publishers is the
            # more interesting finding.
            result.first_seen_publisher_keys.append(publisher_key)
            if result not in self.new_results:
                # Queued for persistence. Mutating the cached object alone would
                # lose the addition at the next drain, so the record would never
                # name the second publisher.
                self.new_results.append(result)
        return Screened(
            text=REDACTION_PLACEHOLDER,
            # The result's own verdict, not a hardcoded BLOCK. Callers branch on
            # `passed`, which is `verdict == "PASS"` and so is unchanged, but a
            # screening outcome that reports BLOCK for an outage tells the reader
            # something that is not true.
            verdict=result.verdict,
            category=result.category,
            text_sha256=result.text_sha256,
            cached=cached,
            incident=incident,
            blocked_text=blocked_text,
        )

    def drain(self) -> dict[str, list[Any]]:
        """Hand over everything written since the last drain, and reset."""
        payload = {
            "results": self.new_results,
            "incidents": self.new_incidents,
            "blocked_text": self.new_blocked_text,
        }
        self.new_results, self.new_incidents, self.new_blocked_text = [], [], []
        return payload
