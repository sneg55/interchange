"""Historical replay, scoped to what is actually reconstructible. Section 6.9.

An earlier revision of the spec claimed replay needed no new storage. That is
true only for trust state: `TrustTransition` is append-only, so the fleet state
at any time T is the last transition at or before T for each publisher. It is
false for everything else. `PublisherRecord` and `CanonicalZone` are mutable and
raw observations expire at 90 days, so registry membership, publisher metadata
and canonical group membership at T cannot be recovered from them.

Two append-only records close the gap: `RegistryEvent` for fleet membership and
`ReconciliationSnapshot` for grouping. Everything here reads only those and
`PublisherDaily`.

What replay does NOT cover is raw per-poll detail beyond retention, and the
scrubber says so at its left edge rather than showing an empty chart. That is
the whole reason `horizon()` exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.features.registry_warden.records import RegistryEvent
from src.features.trust_scorer.records import TrustTransition
from src.features.trust_scorer.rollup import PublisherDaily
from src.features.trust_scorer.verdicts import FleetState

# Registry events that put a publisher into the fleet, and those that remove it.
JOINING_EVENTS = frozenset({"PROVISIONED", "REAPPEARED"})
LEAVING_EVENTS = frozenset({"DECOMMISSIONED"})
# Access changes are registry-visible and never produce a TrustTransition, so
# replay reads them here. Without this a key-gated publisher replays as WATCH,
# which is a trust claim about a feed nobody ever reached.
ACCESS_LOST = "ACCESS_LOST"
ACCESS_GAINED = "ACCESS_GAINED"


@dataclass(slots=True)
class ReplayHorizon:
    """How far back the scrubber can honestly go, and why."""

    earliest_daily: str | None
    earliest_transition: str | None
    earliest_registry_event: str | None

    @property
    def earliest(self) -> str | None:
        stamps = [
            s
            for s in (
                self.earliest_daily,
                self.earliest_transition,
                self.earliest_registry_event,
            )
            if s
        ]
        return min(stamps) if stamps else None

    @property
    def note(self) -> str:
        """Shown at the scrubber's left edge. Never an empty chart."""
        if self.earliest is None:
            return "No retained history yet."
        return (
            f"History begins {self.earliest}. Raw per-poll detail is retained for "
            f"90 days; earlier points are daily rollups only."
        )


@dataclass(slots=True)
class FleetSnapshot:
    """The fleet as it stood at one instant."""

    at: str
    states: dict[str, FleetState]
    members: set[str]

    def band(self, state: FleetState) -> list[str]:
        return sorted(k for k, v in self.states.items() if v == state)

    @property
    def coverage_denominator(self) -> int:
        """Publishers that could be assessed at all.

        NO_ACCESS is excluded. It is not a trust verdict, and counting a
        key-gated publisher as passing or failing would misstate coverage in
        whichever direction happened to flatter the number.
        """
        return sum(1 for k in self.members if self.states.get(k) != "NO_ACCESS")

    def to_doc(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "states": dict(sorted(self.states.items())),
            "members": sorted(self.members),
            "coverage_denominator": self.coverage_denominator,
        }


def horizon(
    dailies: list[PublisherDaily],
    transitions: list[TrustTransition],
    events: list[RegistryEvent],
) -> ReplayHorizon:
    return ReplayHorizon(
        earliest_daily=min((d.day for d in dailies), default=None),
        earliest_transition=min((t.at for t in transitions), default=None),
        earliest_registry_event=min((e.at for e in events), default=None),
    )


def membership_at(events: list[RegistryEvent], at: str) -> set[str]:
    """Which publishers were in the fleet at `at`.

    Replayed from `RegistryEvent` rather than read off `PublisherRecord`, which
    is mutable and only ever describes now. Without this, a replay of last month
    would show today's fleet with last month's trust states attached, which is a
    more convincing lie than showing nothing.
    """
    members: set[str] = set()
    for event in sorted(events, key=lambda e: (e.at, e.publisher_key)):
        if event.at > at:
            break
        if event.event in JOINING_EVENTS:
            members.add(event.publisher_key)
        elif event.event in LEAVING_EVENTS:
            members.discard(event.publisher_key)
    return members


def states_at(transitions: list[TrustTransition], at: str) -> dict[str, FleetState]:
    """Each publisher's trust state at `at`: its last transition at or before it.

    A publisher with no transition before `at` is absent from the result rather
    than defaulting to ADMIT. It had not been assessed yet, and defaulting would
    record "not checked" as "passed" at replay time, which is the same error the
    scorer refuses at evaluation time.
    """
    states: dict[str, FleetState] = {}
    for transition in sorted(transitions, key=lambda t: (t.at, t.publisher_key)):
        if transition.at > at:
            break
        states[transition.publisher_key] = transition.to_state
    return states


def access_at(events: list[RegistryEvent], at: str) -> set[str]:
    """Publishers that were key-gated at `at`.

    Read from `RegistryEvent` because NO_ACCESS is applied directly to the
    mutable `PublisherRecord` and never produces a `TrustTransition`.
    Reconstructing it from transitions alone is impossible, and letting it
    default to WATCH would state a trust verdict about a publisher that was
    never polled.
    """
    gated: set[str] = set()
    for event in sorted(events, key=lambda e: (e.at, e.publisher_key)):
        if event.at > at:
            break
        if event.event == ACCESS_LOST:
            gated.add(event.publisher_key)
        elif event.event == ACCESS_GAINED:
            gated.discard(event.publisher_key)
    return gated


def snapshot_at(
    transitions: list[TrustTransition],
    events: list[RegistryEvent],
    at: str,
    initial_state: FleetState = "WATCH",
) -> FleetSnapshot:
    """The fleet board as it stood at `at`.

    A member with no transition yet takes `initial_state`, which is WATCH by the
    same argument section 6.4 makes at provisioning: a publisher with no history
    has not earned admission. A key-gated member takes NO_ACCESS regardless,
    because that is not a trust verdict and must not be overwritten by one.
    """
    members = membership_at(events, at)
    states = states_at(transitions, at)
    gated = access_at(events, at)
    return FleetSnapshot(
        at=at,
        states={
            key: "NO_ACCESS" if key in gated else states.get(key, initial_state)
            for key in members
        },
        members=members,
    )


def snapshot_for_cycle(
    snapshots: list[dict[str, Any]], cycle_id: str
) -> dict[str, Any] | None:
    """The `ReconciliationSnapshot` for one cycle. Section 6.9's replay.

    The grouping itself is NOT in the document: with roughly 47,000 source
    features the canonical IDs alone exceed Firestore's 1 MiB limit. The document
    holds counts and a `grouping_uri`, and the console loads the artifact for the
    one cycle being inspected.
    """
    return next((s for s in snapshots if s.get("cycle_id") == cycle_id), None)


def daily_series(
    dailies: list[PublisherDaily], publisher_key: str, start: str, end: str
) -> list[PublisherDaily]:
    """One publisher's rollups over a window, oldest first.

    Gaps are left as gaps. Interpolating a missing day would draw a line through
    an outage, and an outage is the thing an operator most needs to see.
    """
    return sorted(
        (d for d in dailies if d.publisher_key == publisher_key and start <= d.day <= end),
        key=lambda d: d.day,
    )
