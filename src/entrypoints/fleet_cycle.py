"""One full fleet cycle: registry, poll, score, screen, reconcile, republish.

This is the orchestration the components were written against. Every one of them
is a pure function or a class over ports, which is what lets this module be the
only place that knows about wiring, and lets the whole cycle run offline against
the checksummed snapshot with no cloud access at all. The CLI that does so is
`scripts/run_cycle.py`; this module stays free of I/O so it can be driven by a
scheduler, a test, or a terminal without changing.

Order matters and is not arbitrary. Screening runs before the republisher and
before anything a model sees, because section 6.5 puts exactly three egresses
behind it. Reconciliation runs after scoring, because a quarantined publisher's
zones must never enter the merge in the first place rather than being filtered
out of the output afterwards.
"""

from __future__ import annotations

import datetime
from typing import Any

from src.entrypoints.cycle_packets import transition_document
from src.entrypoints.cycle_publish import publish_feed
from src.entrypoints.cycle_report import CycleReport
from src.entrypoints.cycle_sources import (
    admitted_feeds,
    blocked_for_zones,
    carried_body,
    note_missing_bodies,
    screen_sources,
)
from src.features.evidence.packet import EvidencePacket
from src.features.publisher_agent.observation import Observation
from src.features.publisher_agent.poller import Poller
from src.features.publisher_agent.scheduler import (
    due,
    poll_interval_seconds,
    send_conditional,
)
from src.features.reconciler.cycle import ReconciliationCycle
from src.features.reconciler.identity import CanonicalIdentity
from src.features.registry_warden.records import PublisherRecord, entry_url
from src.features.registry_warden.warden import RegistryWarden
from src.features.republisher.publisher import Republisher
from src.features.screener.gate import ScreeningGate
from src.features.screener.prewarm import free_text, prewarm
from src.features.trust_scorer.churn import churn_detail, churn_status
from src.features.trust_scorer.records import RuleEvaluation
from src.features.trust_scorer.scorer import TrustScorer
from src.services.body_snapshots import InMemoryBodySnapshots
from src.services.ports import BodySnapshots, FeedSource, RegistrySource, Screener
from src.services.schema_registry import SchemaRegistry
from src.utils.timestamps import iso, utc_now


class FleetCycle:
    def __init__(
        self,
        registry: RegistrySource,
        feeds: FeedSource,
        schemas: SchemaRegistry,
        screener: Screener,
        identity: CanonicalIdentity | None = None,
        bodies: BodySnapshots | None = None,
        adjudicator: Any = None,
        drafter: Any = None,
        feed_sink: Any = None,
    ) -> None:
        self.warden = RegistryWarden(registry)
        # Injected for the same reason as the identity map below: a 304 is
        # answerable only by a process that still holds what the publisher last
        # served. Constructed here when the caller supplies nothing, so the
        # store at least survives between cycles of one run, which is where the
        # collapse showed up.
        self.bodies = bodies or InMemoryBodySnapshots()
        self.poller = Poller(feeds, schemas, self.bodies)
        self.scorer = TrustScorer()
        self.gate = ScreeningGate(screener)
        self.republisher = Republisher(schemas)
        # Injected, because canonical IDs are only stable if the mapping
        # OUTLIVES the process. A cycle that always constructed an empty one
        # would mint fresh UUIDs for every source on every restart, and every
        # downstream consumer would see total churn: precisely the failure the
        # mapping exists to prevent. The caller loads it from CanonicalSourceMap.
        self.identity = identity or CanonicalIdentity()
        # The two model seats, both optional and both outside the gate. Tier 2
        # adjudication (6.6) and notice prose (6.7) are the only places a model
        # appears in this system, and passing None leaves each one deterministic:
        # an unadjudicated pair is "not decided" and never "duplicate", and an
        # undrafted notice ships the packet's own rendering. They are constructor
        # arguments rather than imports so that no code path can acquire a model
        # the caller did not hand it.
        self._adjudicator = adjudicator
        self._drafter = drafter
        # Where the merged feed goes once it has passed its own gate. None means
        # nothing is written and the artifact says so with a null feed_uri, which
        # is the honest reading of a deployment with no bucket configured.
        self._feed_sink = feed_sink
        self._feeds = feeds
        # The read model, kept rather than counted. Section 6.9's console needs
        # every one of these, and a runner that reduced them to integers would
        # leave the notice queue empty, replay with no data, and every
        # transition pointing at a packet id of null.
        self.evaluations: list[RuleEvaluation] = []
        self.packets: list[EvidencePacket] = []
        self.registry_events: list[dict[str, Any]] = []
        self.zones: list[Any] = []
        self.snapshot: Any = None
        self.artifact: Any = None

    def run(
        self,
        known: dict[str, PublisherRecord] | None,
        history: dict[str, list[Observation]] | None = None,
        now: datetime.datetime | None = None,
        cycle_interval_seconds: int = 0,
    ) -> tuple[CycleReport, dict[str, PublisherRecord], dict[str, list[Observation]]]:
        moment = now or utc_now()
        at = iso(moment)
        cycle_id = f"cycle-{at}"
        past = {k: list(v) for k, v in (history or {}).items()}
        # Cleared at the TOP, before anything is appended. Reusing a FleetCycle
        # otherwise makes packets_opened and screening_blocks cumulative, so a
        # cycle that opened nothing reports what an earlier run opened.
        self.evaluations.clear()
        self.packets.clear()
        self.registry_events.clear()
        self.gate.drain()

        entries = self.warden.pull()
        reconciled = self.warden.reconcile(entries, known or {}, at)
        if not reconciled.accepted:
            # A short read is not a fleet change. Nothing downstream runs on it,
            # because a partial registry would decommission publishers that are
            # simply missing from one response.
            raise RuntimeError(reconciled.rejected)
        records = reconciled.records
        self.registry_events.extend(e.to_doc() for e in reconciled.events)
        urls = {f"{e['issuingorganization']}|{e['feedname']}": entry_url(e) for e in entries}

        observations: dict[str, Observation] = {}
        bodies: dict[str, list[dict[str, Any]]] = {}
        # The `update_date` behind each publisher's contribution, collected here
        # rather than derived from `observations` at the end, because a publisher
        # that was not polled this cycle still contributes zones and the stamp
        # that goes with them is the one its last poll measured.
        update_dates: dict[str, str | None] = {}
        states: dict[str, str] = {}
        transitions: list[dict[str, Any]] = []
        not_due = 0
        for key, record in sorted(records.items()):
            if not record.is_pollable:
                # NO_ACCESS and decommissioned publishers are never fetched and
                # never counted as passing. Section 6.1.
                states[key] = record.fleet_state
                continue
            url = urls.get(key, record.url)
            prior = past.get(key, [])
            # WHEN THIS POLL HAPPENS, not when the cycle began. A live cycle walks
            # the fleet over several minutes, so the cycle's start time made
            # `update_age_seconds` the age at a moment already past: publishers
            # late in the polling order came out NEGATIVE and R6 called 15 of 25
            # real organizations `forward_dated` for a clock error that was ours.
            # `now` still wins when the caller pins it, so seeds and replays stay
            # deterministic.
            at_poll = now or utc_now()
            if not due(
                record.last_polled_at,
                record.poll_interval_seconds,
                at_poll,
                cycle_interval_seconds,
            ):
                # Backed off, not absent. No observation is recorded, because no
                # poll happened and a fabricated one would feed R1's consecutive
                # counting and R5's window with a measurement nobody took.
                not_due += 1
                states[key] = record.fleet_state
                carried = carried_body(self.bodies, key, prior)
                if carried is not None:
                    bodies[key], update_dates[key] = carried
                continue
            observation, body = self.poller.poll_with_body(
                key,
                url,
                record.declared_version,
                history=prior,
                send_conditional=send_conditional(record.latching_rule_ids),
                now=at_poll,
            )
            score = self.scorer.score(
                observation,
                prior,
                record.fleet_state,
                record.declared_cadence_seconds,
                # The same instant the poll was stamped with. R2 compares the
                # feed's age against the declared cadence, so scoring against the
                # cycle's start would understate every age by however long the
                # fleet took to reach this publisher.
                at_poll,
                latching_rule_ids=record.latching_rule_ids,
                clean_streak=record.clean_poll_streak,
                clean_streak_started_at=record.clean_streak_started_at,
            )
            # The scorer holds no state, so the carry-over lands on the record.
            record.fleet_state = score.state
            record.latching_rule_ids = score.latching_rule_ids
            record.clean_poll_streak = score.clean_streak
            record.clean_streak_started_at = score.streak_started_at
            # Written here for the same reason as the four above, and it was the
            # one nobody wrote. `churn_status` sat at its dataclass default on
            # every publisher for the life of the fleet, so the console's churn
            # column rendered "INSUFFICIENT_HISTORY" as though it had been
            # measured. R5 decides this on every poll; it just had no way back
            # onto the record.
            record.churn_status = churn_status(score.evaluation.results)
            # R5's own figures, alongside the OK/INSUFFICIENT flag. The scorer
            # had them on every poll and nothing wrote them down, so a column
            # headed Churn could say only the word "measured": that a
            # measurement happened, never what it found.
            record.churn_detail = churn_detail(score.evaluation.results)
            record.poll_interval_seconds = poll_interval_seconds(
                record.declared_cadence_seconds, [observation, *prior]
            )
            # Set for every poll, including a failed one. "We tried and could not
            # reach it" is still contact; leaving it at the last SUCCESSFUL poll
            # would make an unreachable publisher look untouched rather than
            # failing, which inverts the reading.
            record.last_polled_at = observation.polled_at
            self.evaluations.append(score.evaluation)
            observations[key] = observation
            states[key] = score.state
            # The body that was SCORED, returned by the same call that scored
            # it. An extra fetch here, in either direction, means the merge and
            # the observation describe different responses.
            if body is not None:
                bodies[key] = body.get("features") or []
            update_dates[key] = observation.update_date
            past.setdefault(key, []).insert(0, observation)

            if score.transition is not None:
                doc, packet = transition_document(score, observation, prior, self._drafter)
                if packet is not None:
                    self.packets.append(packet)
                transitions.append(doc)

        # Screening runs BEFORE reconciliation, not after, and it REDACTS rather
        # than merely recording a verdict. Section 6.5 puts Tier 2 adjudication
        # behind the screener, and Tier 2 hands both source records to a model:
        # computing a verdict and passing the ORIGINAL on would leave the output
        # correctly redacted while the model had already read the payload, which
        # is the invariant failing in the one place nobody inspects.
        raw_feeds, withheld, withheld_reasons = admitted_feeds(records, states, bodies)
        note_missing_bodies(records, raw_feeds, withheld, withheld_reasons, past)
        # Every distinct string first, concurrently, so the serial pass below is
        # all cache hits. Optimisation only: it files no verdict `screen_sources`
        # would not have reached on its own, and an outage still fails closed
        # there rather than here. Without it, switching the fleet from
        # fail-closed to a real screener took the cycle from nine minutes to
        # unfinished at twenty-nine.
        prewarm(self.gate, free_text(raw_feeds), at)
        feeds_for_merge, screened_fields = screen_sources(self.gate, raw_feeds, at)

        cycle = ReconciliationCycle(self.identity, adjudicator=self._adjudicator)
        merged = cycle.run(
            feeds_for_merge,
            states,
            cycle_id,
            at,
            update_dates,
        )
        blocked = blocked_for_zones(merged.zones, screened_fields)
        self.zones = merged.zones
        self.snapshot = merged.snapshot
        output = self.republisher.build(
            merged.zones,
            states,
            decommissioned={k for k, r in records.items() if r.decommissioned_at},
            blocked_fields=blocked,
            cycle_id=cycle_id,
            at=at,
            # Carried onto the artifact, not only onto the report. The console
            # reads output_artifacts; a count that lives only on CycleReport is
            # a count no operator ever sees.
            withheld_source_zones=withheld,
            withheld_reasons=withheld_reasons,
        )

        publish_feed(self._feed_sink, output, cycle_id)

        self.artifact = output.artifact
        report = CycleReport(
            at=at,
            cycle_id=cycle_id,
            publishers_in_registry=len(records),
            publishers_polled=len(observations),
            states=states,
            transitions=transitions,
            packets_opened=len(self.packets),
            screening_blocks=len(self.gate.new_incidents),
            withheld_source_zones=withheld,
            canonical_zones=output.artifact.canonical_zone_count,
            source_zones=output.artifact.source_zone_count,
            published=output.published,
            validation=output.artifact.validation_result,
            excluded=output.artifact.excluded_counts,
            publishers_not_due=not_due,
        )
        return report, records, past
