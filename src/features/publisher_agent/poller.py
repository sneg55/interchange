"""One poll of one publisher, producing one Observation. Section 6.2.

Written against the ports in `src/services/ports.py` rather than against urllib,
so the same logic runs over the live fleet, over the checksummed fixture
snapshot, and over an injected-failure source in tests.

Takes the publisher's recent history rather than just its previous observation.
That is not a convenience: a failed poll retains the previous validators, so the
poll after a failure can legitimately return 304, and a carry-forward that looks
only one step back would find a failed observation with nothing on it and record
a 304 carrying no counts at all.
"""

from __future__ import annotations

import datetime
from typing import Any

from src.services.ports import BodySnapshots, FeedSource
from src.services.schema_registry import SCHEMA_UNKNOWN, SchemaRegistry
from src.utils.timestamps import age_seconds, iso, utc_now

from . import signals
from .observation import Observation


class Poller:
    """Polls one publisher. Holds no state: history comes from the caller.

    Keeping history out means a poll can be replayed against retained
    observations, which is how the 304 path is tested without waiting for a
    publisher to actually return one.
    """

    def __init__(
        self,
        source: FeedSource,
        schemas: SchemaRegistry | None = None,
        bodies: BodySnapshots | None = None,
    ) -> None:
        self._source = source
        self._schemas = schemas
        # Without a snapshot store a 304 still yields no body, which is the
        # behaviour that collapsed the merge. It stays optional because the
        # scorer's tests poll without one and care only about the observation,
        # but the fleet runner must pass one.
        self._bodies = bodies

    def poll(self, *args: Any, **kwargs: Any) -> Observation:
        """The observation alone, for callers that do not need the body."""
        return self.poll_with_body(*args, **kwargs)[0]

    def poll_with_body(
        self,
        publisher_key: str,
        url: str,
        declared_version: str | None = None,
        history: list[Observation] | None = None,
        send_conditional: bool = True,
        now: datetime.datetime | None = None,
        trace_id: str | None = None,
    ) -> tuple[Observation, dict[str, Any] | None]:
        """Poll once. `history` is newest-first and may be empty.

        Returns the observation AND the body it was computed from, so the
        reconciler can merge exactly what was scored. Fetching again downstream
        would let content nobody evaluated enter the merged feed, and a failure
        on the second request would silently withdraw a healthy publisher's
        zones.

        `send_conditional` is the caller's decision, not this function's. Section
        6.4 suspends conditional GET while a publisher is latched by a
        body-dependent rule, and only the scorer knows that. Defaulting it here
        would reintroduce the deadlock.
        """
        moment = now or utc_now()
        past = history or []
        # A conditional request with no body-bearing ancestor is a trap: the
        # publisher answers 304, there is nothing to carry forward, R6 fires on
        # the absent update_date, and R6 does not suspend conditional GET, so the
        # same exchange repeats forever and no poll is ever clean.
        conditional = send_conditional and _last_with_body_values(past) is not None
        validator = _last_with_validator(past) if conditional else None
        etag = validator.etag if validator else None
        stamp = validator.last_modified if validator else None
        result = self._source.fetch(url, etag=etag, last_modified=stamp)

        observation = Observation(
            publisher_key=publisher_key,
            polled_at=iso(moment),
            http_status=result.status,
            latency_ms=result.latency_ms,
            not_modified=result.not_modified,
            etag=result.etag or etag,
            last_modified=result.last_modified or stamp,
            trace_id=trace_id,
            error=result.error,
            # Carried through rather than reconstructed. Only the source knows
            # whether the request ever left this process.
            error_origin=result.error_origin,
        )
        if result.error is not None:
            return observation, None
        if result.not_modified:
            if result.status != 304:
                # The flag is not the authority; the status is. A source setting
                # not_modified on a 503 would otherwise yield a successful poll
                # that could count toward retiring a quarantine.
                observation.error = f"HTTPStatus: {result.status} with not_modified set"
                return observation, None
            self._apply_carry_forward(observation, past, moment)
            return observation, self._retained_body(publisher_key, observation)
        if not 200 <= result.status < 300:
            # A body alongside a non-2xx status is a failed poll. Left as a
            # success it could later count toward a clean-poll streak and retire
            # a quarantine on the strength of an error page.
            observation.error = f"HTTPStatus: {result.status}"
            return observation, None
        if result.body is None:
            # Neither an error, nor a 304, nor a body. A FeedSource returning
            # this is broken, and treating it as a clean poll would let a broken
            # source admit a publisher nobody fetched.
            observation.error = "EmptyResponse: fetch returned no body and no error"
            return observation, None

        self._apply_body(observation, result.body, declared_version, moment)
        if self._bodies is not None and observation.content_hash is not None:
            self._bodies.record(publisher_key, result.body, observation.content_hash)
        return observation, result.body

    # ------------------------------------------------------------------ detail

    def _retained_body(self, publisher_key: str, observation: Observation) -> dict[str, Any] | None:
        """The body a 304 refers to, or None if we cannot prove we still have it.

        A 304 means the copy already held is current, so the zones behind it are
        still the publisher's current zones and belong in the merge. Dropping
        them was the whole defect: one cycle looked correct, and from the second
        onward a fleet of publishers doing exactly the right thing merged
        nothing.

        The hash comparison is what keeps this honest. The observation's
        `content_hash` was carried forward from the last poll that measured a
        body; the retained body is only that same body if the hashes agree.
        Serving a body the observation does not describe would put content into
        the merged feed that no rule was ever evaluated against, which is the
        "not checked recorded as checked" failure in a new costume. If they
        disagree, or nothing was retained, there is no body, exactly as before.
        """
        if self._bodies is None or observation.content_hash is None:
            return None
        held = self._bodies.latest(publisher_key)
        if held is None or held[1] != observation.content_hash:
            return None
        return held[0]

    @staticmethod
    def _apply_carry_forward(
        observation: Observation, history: list[Observation], moment: datetime.datetime
    ) -> None:
        source = _last_with_body_values(history)
        if source is None:
            # A 304 with no measured ancestor anywhere in the retained history.
            # Fields stay absent rather than becoming zeros: a zero would tell R4
            # that a publisher has no contradictory zones when none were counted.
            observation.carried_forward = False
            return
        source.carry_forward_into(observation)
        observation.last_modified = observation.last_modified or source.last_modified
        # Age is recomputed against this poll, never carried. Section 6.2.
        observation.update_age_seconds = age_seconds(observation.update_date, moment)

    def _apply_body(
        self,
        observation: Observation,
        body: dict[str, Any],
        declared_version: str | None,
        moment: datetime.datetime,
    ) -> None:
        features = signals.feature_list(body)
        update_date = signals.feed_header(body).get("update_date")

        observation.update_date = update_date
        observation.update_age_seconds = age_seconds(update_date, moment)
        observation.feature_count = len(features)
        active, past, undated = signals.consistency(features, moment)
        observation.active_count = active
        observation.active_with_past_end_date = past
        observation.active_undated = undated
        observation.content_hash = signals.content_hash(features)
        observation.structural_hash = signals.structural_hash(features)

        if self._schemas is None:
            # No schema registry configured is "not checked", and it is recorded
            # as such. SCHEMA_UNKNOWN suppresses R3; it never fails a publisher.
            observation.schema_version_used = SCHEMA_UNKNOWN
            observation.schema_error_count = None
            return
        version, errors = self._schemas.error_count(body, declared_version)
        observation.schema_version_used = version
        observation.schema_error_count = errors


def _last_with_validator(history: list[Observation]) -> Observation | None:
    """The most recent observation carrying a conditional-request validator.

    Walks past failed polls. A transport failure tells us nothing about whether
    the publisher's content changed, so discarding a good etag because of one
    timeout would force a full re-fetch of the largest feeds in the fleet.
    """
    for observation in history:
        if observation.etag or observation.last_modified:
            return observation
    return None


def _last_with_body_values(history: list[Observation]) -> Observation | None:
    """The most recent observation carrying measured body-derived values.

    Includes carried-forward ones: their values did originate from a body, and
    excluding them would break carry-forward across any run of consecutive 304s.
    """
    for observation in history:
        if observation.content_hash is not None:
            return observation
    return None
