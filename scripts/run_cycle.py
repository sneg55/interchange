#!/usr/bin/env python3
"""Run one fleet cycle against the checksummed snapshot and print what happened.

    python3 scripts/run_cycle.py
    python3 scripts/run_cycle.py --out cycle.json

Offline, no network, no credentials. This is the reproduction path for every
claim in the demo: the same components the fleet runs, wired to the same feed
bodies the measurements were taken from.

Exit code is non-zero when Interchange's own output fails its schema validation,
so a cycle that would publish a feed it would itself quarantine fails the build
rather than printing a warning nobody reads.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.fleet_cycle import FleetCycle
from src.features.reconciler.identity import CanonicalIdentity
from src.features.trust_scorer.rollup import roll_up_all
from src.services.fixtures import FixtureFeedSource, FixtureRegistrySource, FixtureSet
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry
from src.services.screeners import FailClosedScreener, KeywordScreener


def offline_cycle(
    allow_unscreened_text: bool = False, identity: CanonicalIdentity | None = None
) -> FleetCycle:
    """A cycle wired entirely to the checksummed snapshot.

    `KeywordScreener` rather than `FailClosedScreener` by default here, because
    a fail-closed screener redacts every description and the offline run exists
    to show the pipeline working end to end. It is not a security control and
    its policy version says so.
    """
    fixtures = FixtureSet()
    return FleetCycle(
        registry=FixtureRegistrySource(fixtures),
        feeds=FixtureFeedSource(fixtures),
        schemas=SchemaRegistry(FixtureSchemaLoader(fixtures)),
        screener=KeywordScreener() if allow_unscreened_text else FailClosedScreener(),
        # Passed in so a caller can load a persisted CanonicalSourceMap. Left to
        # default, every run mints fresh UUIDs and canonical identity is stable
        # only within one process, which is not stability.
        identity=identity,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="write the cycle report as JSON")
    parser.add_argument(
        "--fail-closed",
        action="store_true",
        help="use the fail-closed screener, which redacts all free text",
    )
    args = parser.parse_args()

    cycle = offline_cycle(allow_unscreened_text=not args.fail_closed)
    report, records, history = cycle.run(known=None)
    # The rollup is fed BOTH the observations and the evaluations. Without the
    # evaluations every day reports no fired rules and an end-of-day state of
    # WATCH, including for publishers that were quarantined that day.
    rollups = roll_up_all(
        [o for series in history.values() for o in series], cycle.evaluations
    )

    print(json.dumps(report.to_doc(), indent=2, default=str))
    print(f"\n{len(records)} publishers known, {len(rollups)} daily rollups")
    print(f"{report.publishers_polled} polled, {report.packets_opened} evidence packets opened")
    print(
        f"{len(cycle.zones)} canonical zones, {len(cycle.registry_events)} registry events, "
        f"{len(cycle.evaluations)} rule evaluations"
    )
    withheld = sum(report.withheld_source_zones.values())
    if withheld:
        print(f"{withheld} source zones withheld from the merge (quarantined publishers)")
    if not report.published:
        print("OUTPUT NOT PUBLISHED: it failed its own schema validation.")
    if args.out:
        Path(args.out).write_text(json.dumps(report.to_doc(), indent=2, default=str))
    return 0 if report.published else 1


if __name__ == "__main__":
    sys.exit(main())
