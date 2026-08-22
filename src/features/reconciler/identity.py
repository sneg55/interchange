"""Canonical identity across cycles. Section 6.6.

The merged feed is republished every cycle. If canonical IDs were recomputed from
group membership then a single publisher adding one zone would rewrite the IDs of
everything near it, and every downstream consumer would see total churn.

A canonical ID is a UUID minted once and held in `CanonicalSourceMap`, a
persisted `(publisher_key, road_event_id) -> canonical_id` mapping. Each cycle a
group inherits the **oldest** ID held by any of its members. When a group splits,
the largest surviving fragment keeps the ID and the others mint new ones, with
the supersession recorded on both.

The mapping is one to one. That is what makes the grouping caps in `grouping.py`
necessary rather than fussy: duplicating a source zone across two groups would
make this unsatisfiable.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any

SourceId = tuple[str, str]


@dataclass(slots=True)
class CanonicalSourceMapEntry:
    publisher_key: str
    road_event_id: str
    canonical_id: str
    first_mapped_at: str
    last_cycle_id: str = ""

    @property
    def source_id(self) -> SourceId:
        return (self.publisher_key, self.road_event_id)

    @property
    def doc_id(self) -> str:
        return f"{self.publisher_key}|{self.road_event_id}"

    def to_doc(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Assignment:
    canonical_id: str
    minted: bool
    supersedes: list[str]
    # When this identity began, carried out so the caller does not have to guess.
    # `CanonicalZone.first_merged_at` was set to the current cycle's clock, which
    # made a field named "first" mean "most recent" and republished it as such:
    # `republisher/mapping.py` puts it in the merged feed, so every consumer saw
    # it change every cycle. The ID it belongs to is stable across cycles by
    # design, and this is the timestamp that says since when.
    first_mapped_at: str = ""


class CanonicalIdentity:
    """Assigns canonical IDs to groups, preserving them across cycles.

    Holds the mapping in memory and reports what changed; the caller persists it.
    Minting is injectable so a test can assert ID STABILITY rather than assert
    against a random value, which is the only property that actually matters
    here.
    """

    def __init__(
        self,
        entries: list[CanonicalSourceMapEntry] | None = None,
        mint: Any = None,
    ) -> None:
        self._by_source: dict[SourceId, CanonicalSourceMapEntry] = {
            e.source_id: e for e in (entries or [])
        }
        self._mint = mint or (lambda: str(uuid.uuid4()))
        self.new_entries: list[CanonicalSourceMapEntry] = []
        self.superseded: dict[str, str] = {}  # old canonical id -> new one

    def canonical_id_for(self, source: SourceId) -> str | None:
        entry = self._by_source.get(source)
        return entry.canonical_id if entry else None

    def assign(self, sources: list[SourceId], at: str, cycle_id: str) -> Assignment:
        """The canonical ID for one group this cycle.

        Inherits the OLDEST ID any member already holds, so a group that gains a
        member does not adopt the newcomer's identity and republish everything
        under a new ID. Ties on `first_mapped_at` break on the ID itself, because
        two zones first mapped in the same cycle share a timestamp and the
        outcome still has to be deterministic.
        """
        held = [self._by_source[s] for s in sources if s in self._by_source]
        if held:
            oldest = min(held, key=lambda e: (e.first_mapped_at, e.canonical_id))
            canonical_id, minted = oldest.canonical_id, False
            # The inherited ID's own age, not this cycle's clock. Same field the
            # inheritance was decided on, so the two cannot disagree.
            first_mapped_at = oldest.first_mapped_at
        else:
            canonical_id, minted = self._mint(), True
            first_mapped_at = at

        # Every other ID in the group is superseded by the inherited one. This is
        # the merge case: two zones previously canonical on their own become one.
        supersedes = sorted({e.canonical_id for e in held if e.canonical_id != canonical_id})
        for old in supersedes:
            self.superseded[old] = canonical_id

        for source in sources:
            entry = self._by_source.get(source)
            if entry is None:
                entry = CanonicalSourceMapEntry(
                    publisher_key=source[0],
                    road_event_id=source[1],
                    canonical_id=canonical_id,
                    first_mapped_at=at,
                    last_cycle_id=cycle_id,
                )
                self._by_source[source] = entry
                self.new_entries.append(entry)
            else:
                if entry.canonical_id != canonical_id:
                    entry.canonical_id = canonical_id
                    self.new_entries.append(entry)
                entry.last_cycle_id = cycle_id
        return Assignment(canonical_id, minted, supersedes, first_mapped_at)

    def assign_all(self, groups: list[list[SourceId]], at: str, cycle_id: str) -> list[Assignment]:
        """Assign every group, largest first.

        Order matters on a split: the largest surviving fragment must claim the
        inherited ID before the smaller ones look, or a two-zone remnant could
        take an ID that the five-zone remnant should have kept and five consumers
        would see churn instead of two.
        """
        ordered = sorted(range(len(groups)), key=lambda i: (-len(groups[i]), groups[i]))
        results: list[Assignment | None] = [None] * len(groups)
        claimed: set[str] = set()
        for index in ordered:
            assignment = self.assign(groups[index], at, cycle_id)
            if assignment.canonical_id in claimed:
                # A fragment tried to inherit an ID a larger fragment already
                # took. It mints instead; the mapping stays one to one.
                fresh = self._mint()
                for source in groups[index]:
                    entry = self._by_source[source]
                    entry.canonical_id = fresh
                    # A minted ID is a NEW identity, so it takes a new
                    # first_mapped_at. Keeping the old one would let a fragment
                    # created today out-rank a genuinely older canonical zone in
                    # a later merge, and inheritance is decided on that field.
                    entry.first_mapped_at = at
                    self.new_entries.append(entry)
                self.superseded.pop(assignment.canonical_id, None)
                assignment = Assignment(fresh, True, [assignment.canonical_id], at)
            claimed.add(assignment.canonical_id)
            results[index] = assignment
        return [r for r in results if r is not None]

    def entries(self) -> list[CanonicalSourceMapEntry]:
        return sorted(self._by_source.values(), key=lambda e: e.doc_id)
