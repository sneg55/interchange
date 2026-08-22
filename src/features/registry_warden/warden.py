"""The Registry Warden. Section 6.1.

Diffs a registry pull against the running fleet and returns what changed. It does
not write: the caller persists a `ReconcileResult`. That split is what lets a
short read be rejected without having already half-applied a decommission.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import AppError, ErrorIds
from src.features.publisher_agent.observation import AGENT_IDENTITY
from src.services.ports import RegistrySource

from .cadence import clamp
from .records import (
    ABSENT_PULLS_BEFORE_DECOMMISSION,
    PublisherRecord,
    ReconcileResult,
    RegistryEvent,
    declared_cadence_changed,
    entry_key,
    entry_url,
    needs_api_key,
    record_from_entry,
)

# A pull returning fewer than this fraction of the known fleet is a bad read.
SHORT_READ_FRACTION = 0.5


class RegistryWarden:
    def __init__(self, source: RegistrySource) -> None:
        self._source = source

    def pull(self) -> list[dict[str, Any]]:
        return self._source.active_entries()

    def reconcile(
        self,
        entries: list[dict[str, Any]],
        known: dict[str, PublisherRecord],
        now: str,
    ) -> ReconcileResult:
        """Diff one pull against the fleet.

        `known` is keyed by publisher_key and is not mutated; the result carries
        copies. Callers replay this against retained pulls, which a mutating
        version would make impossible.
        """
        by_key = self._index(entries)

        # Compared against the LIVE fleet, not everything ever seen. Delisting
        # disables rather than deletes, so decommissioned records accumulate
        # forever; counting them would eventually make a complete registry pull
        # look like a short read and freeze the warden permanently.
        live = sum(1 for r in known.values() if r.decommissioned_at is None)
        if len(by_key) < SHORT_READ_FRACTION * live:
            return ReconcileResult(
                records=dict(known),
                rejected=(
                    f"short read: {len(by_key)} entries against {live} live "
                    f"({ErrorIds.REG_SHORT_READ}). Not diffed; retry."
                ),
            )

        result = ReconcileResult(records={k: _copy(v) for k, v in known.items()})
        for key, entry in by_key.items():
            existing = result.records.get(key)
            if existing is None:
                fresh = record_from_entry(entry, now)
                result.records[key] = fresh
                result.events.append(RegistryEvent(key, now, "PROVISIONED", None, entry_url(entry)))
                if fresh.fleet_state == "NO_ACCESS":
                    # Provisioned already key-gated. Recorded, or a replay shows
                    # a publisher at WATCH that was never reachable.
                    result.events.append(RegistryEvent(key, now, "ACCESS_LOST", None, "NO_ACCESS"))
                continue
            self._update_present(existing, entry, now, result)
        self._mark_absent(by_key, result, now)
        return result

    # ------------------------------------------------------------------ detail

    @staticmethod
    def _index(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Key the pull, refusing a duplicate rather than letting one win.

        The pair is supposed to be unique across the registry. If it ever is not,
        last-write-wins would silently bind one agent to whichever row Socrata
        happened to serve second, and the fleet would look complete.
        """
        indexed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            key = entry_key(entry)
            if key in indexed:
                raise AppError(
                    ErrorIds.REG_DUPLICATE_KEY,
                    f"duplicate publisher key in one registry pull: {key}",
                    {"publisher_key": key},
                )
            indexed[key] = entry
        return indexed

    @staticmethod
    def _update_present(
        record: PublisherRecord,
        entry: dict[str, Any],
        now: str,
        result: ReconcileResult,
    ) -> None:
        key = record.publisher_key
        was_absent = record.absent_pull_count > 0 or record.decommissioned_at is not None
        record.last_seen_in_registry = now
        record.absent_pull_count = 0
        # Which agent build is responsible for this publisher NOW, refreshed
        # every pass rather than frozen at provisioning. Set only here and in
        # `record_from_entry`, every publisher provisioned before the field
        # existed keeps a null forever, which is how all 41 came to report "not
        # provisioned" while being polled on schedule.
        record.agent_identity = AGENT_IDENTITY
        if was_absent:
            # Reappearance clears the decommission. History is intact because a
            # delisting only ever set a timestamp; it never deleted anything.
            record.decommissioned_at = None
            result.events.append(RegistryEvent(key, now, "REAPPEARED"))

        url = entry_url(entry)
        if url and url != record.url:
            # A URL change is an attribute change, not a new publisher. Treating
            # it as decommission-plus-provision would destroy the history that is
            # the entire product.
            result.events.append(RegistryEvent(key, now, "URL_CHANGED", record.url, url))
            record.url = url

        version = None if entry.get("version") is None else str(entry["version"])
        if version != record.declared_version:
            result.events.append(
                RegistryEvent(key, now, "VERSION_CHANGED", record.declared_version, version)
            )
            record.declared_version = version

        new_cadence = declared_cadence_changed(record, entry)
        if new_cadence is not None:
            result.events.append(
                RegistryEvent(
                    key, now, "CADENCE_CHANGED", record.declared_cadence_seconds, new_cadence
                )
            )
            record.declared_cadence_seconds = new_cadence
            if record.is_pollable:
                record.poll_interval_seconds = clamp(new_cadence)

        gated = needs_api_key(entry)
        if gated != record.needs_api_key:
            record.needs_api_key = gated
            if gated:
                # NO_ACCESS is not a trust verdict, so it must not erase one.
                # A publisher that was QUARANTINE has not recovered by becoming
                # unreachable.
                if record.fleet_state != "NO_ACCESS":
                    record.state_before_no_access = record.fleet_state
                record.fleet_state = "NO_ACCESS"
                record.poll_interval_seconds = 0
                result.events.append(
                    RegistryEvent(
                        key, now, "ACCESS_LOST", record.state_before_no_access, "NO_ACCESS"
                    )
                )
            elif record.fleet_state == "NO_ACCESS":
                # Restore the state the publisher held before it went gated, or
                # WATCH if it was never observed. Never ADMIT: nothing about a
                # publisher we have not polled has been checked.
                restored = record.state_before_no_access or "WATCH"
                result.events.append(
                    RegistryEvent(key, now, "ACCESS_GAINED", "NO_ACCESS", restored)
                )
                record.fleet_state = restored
                record.state_before_no_access = None
                record.poll_interval_seconds = clamp(record.declared_cadence_seconds)

    @staticmethod
    def _mark_absent(present: dict[str, dict[str, Any]], result: ReconcileResult, now: str) -> None:
        for key, record in result.records.items():
            if key in present or record.decommissioned_at is not None:
                continue
            record.absent_pull_count += 1
            if record.absent_pull_count >= ABSENT_PULLS_BEFORE_DECOMMISSION:
                record.decommissioned_at = now
                result.events.append(
                    RegistryEvent(key, now, "DECOMMISSIONED", record.absent_pull_count)
                )
            else:
                result.events.append(RegistryEvent(key, now, "ABSENT", record.absent_pull_count))


def _copy(record: PublisherRecord) -> PublisherRecord:
    return PublisherRecord(**{f: getattr(record, f) for f in PublisherRecord.__slots__})
