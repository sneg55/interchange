"""What the republisher produces: the artifact record and the result wrapper.

Section 6.8 and section 7. Extracted from `publisher.py` when that module crossed
the file size limit; these two are the output contract and the rest of that file
is the machinery that fills them in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any


@dataclass(slots=True)
class OutputArtifact:
    cycle_id: str
    at: str
    feed_uri: str | None
    # None when the feed was never written anywhere, which is every cycle until
    # an object store is configured. Not 0: a zero is a measured size, and a
    # 32,000 zone feed that measured zero bytes would be a serious defect rather
    # than a deployment without a bucket. Same error as the `latency_ms = 0.0`
    # that stood in for a latency never taken.
    byte_size: int | None
    # Every canonical zone the merge handed the republisher, before any of them
    # were excluded. Without it the three counts on the output screen could not
    # be reconciled by a reader: 32,278 published, 16,151 missing a required
    # field and 10 failing validation add to the number this field holds, and
    # nothing on screen said so.
    input_zone_count: int
    canonical_zone_count: int
    # Source zones behind the EMITTED canonical zones, not the cycle's input.
    # Named here because "32,278 canonical zones from 32,819 source zones" read
    # as a funnel from the latter to the former, which it is not.
    source_zone_count: int
    validation_result: dict[str, Any]
    published: bool
    excluded_counts: dict[str, int] = dc_field(default_factory=dict)
    excluded_zone_ids: dict[str, list[str]] = dc_field(default_factory=dict)
    # Source zones a quarantined publisher never contributed, by publisher key.
    #
    # These are withheld UPSTREAM, before the merge, so `excluded_counts`
    # cannot see them: it can only count among zones the republisher received,
    # which makes `quarantined_sources_only` permanently zero and reads as
    # "quarantine excluded nothing". Reporting zero while withholding hundreds
    # is a silent drop by arithmetic rather than by omission, and it is the one
    # exclusion an operator most needs to see.
    withheld_source_zones: dict[str, int] = dc_field(default_factory=dict)
    # Why each of those publishers was held back. The table listing them gave a
    # publisher and a count and no reason, so the most consequential fact on the
    # screen had to be reconstructed by clicking through to each publisher.
    withheld_reasons: dict[str, str] = dc_field(default_factory=dict)
    # Which required fields were missing, and from how many zones. The exclusion
    # bucket said "missing required field: 16151 zones" and named the field
    # nowhere, so an operator could not tell a publisher what to fix.
    missing_field_counts: dict[str, int] = dc_field(default_factory=dict)

    @property
    def withheld_source_zone_count(self) -> int:
        return sum(self.withheld_source_zones.values())

    def to_doc(self) -> dict[str, Any]:
        # The total is written rather than left to the reader. A per-publisher
        # map is easy to render as a table and easy to forget to add up, and the
        # sum is the number that belongs next to the other exclusion counts.
        return asdict(self) | {"withheld_source_zone_count": self.withheld_source_zone_count}


@dataclass(slots=True)
class RepublishResult:
    feed: dict[str, Any] | None
    artifact: OutputArtifact

    @property
    def published(self) -> bool:
        return self.artifact.published
