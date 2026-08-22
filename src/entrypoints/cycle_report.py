"""What one fleet cycle did. Spec 6.9.

Split from the cycle itself so a scheduler, a test or the console API can read a
report without importing the orchestration.

Every count here is reported rather than inferred. `withheld_source_zones` is
the one that needs saying: quarantined publishers are excluded BEFORE the merge,
so the republisher's own exclusion counters can only count among the zones it
receives. Withholding thousands of zones while reporting zero exclusions would
be a silent drop by arithmetic rather than by omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CycleReport:
    """What one cycle did. Every count here is reported, never inferred."""

    at: str
    cycle_id: str
    publishers_in_registry: int
    publishers_polled: int
    states: dict[str, str]
    transitions: list[dict[str, Any]]
    packets_opened: int
    screening_blocks: int
    # Zones held out of the merge because their publisher is quarantined or
    # unreachable. Reported here because the republisher can only count
    # exclusions among the zones it receives, so excluding upstream would make
    # its own counter permanently zero.
    withheld_source_zones: dict[str, int]
    canonical_zones: int
    source_zones: int
    published: bool
    validation: dict[str, Any]
    excluded: dict[str, int]
    # Pollable publishers whose next poll had not come round, and whose zones
    # entered the merge from the body they last served. Distinct from
    # `publishers_polled` and from the NO_ACCESS count, because "backed off" is
    # neither a poll nor an absence. Without it a cycle that backs off half the
    # fleet reports the same shape as one where half the fleet went unreachable.
    publishers_not_due: int = 0

    def to_doc(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "cycle_id": self.cycle_id,
            "publishers_in_registry": self.publishers_in_registry,
            "publishers_polled": self.publishers_polled,
            "publishers_not_due": self.publishers_not_due,
            "states": self.states,
            "transitions": self.transitions,
            "packets_opened": self.packets_opened,
            "screening_blocks": self.screening_blocks,
            "withheld_source_zones": self.withheld_source_zones,
            "withheld_total": sum(self.withheld_source_zones.values()),
            "canonical_zones": self.canonical_zones,
            "source_zones": self.source_zones,
            "published": self.published,
            "validation": self.validation,
            "excluded": {k: v for k, v in self.excluded.items() if v},
        }
