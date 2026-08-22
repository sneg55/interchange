#!/usr/bin/env python3
"""Run N fleet cycles offline and dump the console's read model as JSON.

    python3 scripts/dump_console_data.py --cycles 6 --out /tmp/console-seed.json

Offline, no network, no credentials: the same checksummed snapshot
`scripts/run_cycle.py` uses. The output is what `console/scripts/seed-emulator.mjs`
loads into the Firestore emulator, so the console can be driven locally against
real pipeline output rather than hand-written fixtures.

Several cycles rather than one, because a single cycle gives every publisher
exactly one observation: no rollup series, no clean streak, no de-escalation, and
a fleet board where every sparkline is a single point. The clock is advanced by
the poll interval between cycles; the feed bodies do not change, which is itself
the realistic case for a fleet polling faster than its publishers update.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_cycle import offline_cycle
from src.entrypoints.cycle_docs import doc_id as _doc_id
from src.entrypoints.cycle_docs import observation_id as _observation_id
from src.features.reconciler.identity import CanonicalIdentity
from src.features.trust_scorer.churn import R5_MIN_POLLS, R5_WINDOW_SECONDS
from src.features.trust_scorer.rollup import roll_up_all
from src.services.fixtures import FixtureSet

DEFAULT_STEP_SECONDS = 300

# R5 cannot speak until the retained history spans its whole window AND holds
# enough polls inside it (`R5_WINDOW_SECONDS`, `R5_MIN_POLLS` in
# `src/features/trust_scorer/churn.py`). At the 300s default, six cycles give
# 25 minutes of history, so every publisher on the board reads
# INSUFFICIENT_HISTORY: correct, and useless for showing the column working.
# These two together are the cheapest seed that clears both gates.
CHURN_STEP_SECONDS = R5_WINDOW_SECONDS // R5_MIN_POLLS
CHURN_CYCLES = R5_MIN_POLLS + 1


"""Document IDs come from `cycle_docs`, not from here.

The seed and the live fleet accumulate differently and always will, but they
must write one cycle under one set of IDs. Two spellings of the same ID would
give the console two of everything, and the first place it would show is a fleet
board with eighty publishers on it.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="where to write the seed JSON")
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument(
        "--step-seconds",
        type=int,
        default=DEFAULT_STEP_SECONDS,
        help="simulated seconds between cycles",
    )
    parser.add_argument(
        "--churn-window",
        action="store_true",
        help=(
            f"span R5's whole window: {CHURN_CYCLES} cycles at {CHURN_STEP_SECONDS}s. "
            "Overrides --cycles and --step-seconds."
        ),
    )
    args = parser.parse_args()
    cycles = CHURN_CYCLES if args.churn_window else args.cycles
    step_seconds = CHURN_STEP_SECONDS if args.churn_window else args.step_seconds

    span = (cycles - 1) * step_seconds
    in_window = min(cycles, span // step_seconds + 1)
    if span < R5_WINDOW_SECONDS or in_window < R5_MIN_POLLS:
        # Said up front rather than discovered on the board. The seed is still
        # written: a short one is the right choice most of the time, and the
        # console renders INSUFFICIENT_HISTORY honestly when it happens.
        print(
            f"note: {cycles} cycles x {step_seconds}s spans {span}s with {in_window} polls; "
            f"R5 needs {R5_WINDOW_SECONDS}s and {R5_MIN_POLLS} polls, so churn will read "
            "INSUFFICIENT_HISTORY for every publisher. Use --churn-window to clear both gates."
        )

    # One identity map across every cycle, so canonical ids are stable run to
    # run. A fresh map per cycle would mint new uuids and the console would show
    # total churn between two cycles that saw identical feeds.
    identity = CanonicalIdentity()
    cycle = offline_cycle(allow_unscreened_text=True, identity=identity)

    # The simulated clock starts when the snapshot was captured, not at a fixed
    # hour. A hardcoded start earlier than `captured_at` makes every feed look
    # like it updates in the future, and the console rendered that faithfully as
    # "age -10.0h". Feed freshness is measured against the poll, so the poll has
    # to happen after the bytes were fetched.
    start = datetime.datetime.fromisoformat(FixtureSet().captured_at)
    known: dict[str, Any] | None = None
    history: dict[str, list[Any]] = {}

    collections: dict[str, list[dict[str, Any]]] = {
        "publishers": [],
        "observations": [],
        "rule_evaluations": [],
        "publisher_daily": [],
        "trust_transitions": [],
        "registry_events": [],
        "canonical_zones": [],
        "reconciliation_snapshots": [],
        "output_artifacts": [],
        "evidence_packets": [],
    }
    reports = []

    for index in range(cycles):
        moment = start + datetime.timedelta(seconds=step_seconds * index)
        report, records, history = cycle.run(known, history, now=moment)
        known = records
        reports.append(report.to_doc())

        for evaluation in cycle.evaluations:
            doc = evaluation.to_doc()
            collections["rule_evaluations"].append(
                {"id": _doc_id(f"{doc['publisher_key']}@{doc['evaluated_at']}"), "doc": doc}
            )
        for transition in report.transitions:
            collections["trust_transitions"].append(
                {
                    "id": _doc_id(f"{transition['publisher_key']}@{transition['at']}"),
                    "doc": transition,
                }
            )
        for packet in cycle.packets:
            collections["evidence_packets"].append(
                {"id": _doc_id(packet.packet_id), "doc": packet.to_doc()}
            )
        for event in cycle.registry_events:
            collections["registry_events"].append(
                {
                    "id": _doc_id(f"{event['publisher_key']}@{event['at']}@{event['event']}"),
                    "doc": event,
                }
            )
        if cycle.snapshot is not None:
            snapshot = cycle.snapshot.to_doc()
            collections["reconciliation_snapshots"].append(
                {"id": _doc_id(snapshot["cycle_id"]), "doc": snapshot}
            )
            # `canonical_zones` is a persisted store, not a per-cycle log, and
            # nothing in the repo writes it, so its overwrite semantics are
            # undefined. Seeded from the last cycle that RECONCILED, not the last
            # that published.
            #
            # Gating on `published` was the conservative reading and it emptied
            # the store the moment a publisher shipped an invalid enum value:
            # 32,313 zones were reconciled, the republisher refused to emit them
            # because ten North Carolina features spell `direction` with a space,
            # and the reconciliation screen then showed nothing at all. But that
            # screen reports what the RECONCILER decided, and the reconciler
            # decided; withholding is the republisher's separate verdict and the
            # output screen is where it belongs. Two different questions, and
            # answering the first with the second's silence loses the merge
            # evidence exactly when an operator most needs to read it.
            collections["canonical_zones"] = [
                {"id": _doc_id(z.to_doc()["canonical_id"]), "doc": z.to_doc()} for z in cycle.zones
            ]
        if cycle.artifact is not None:
            artifact = cycle.artifact.to_doc()
            collections["output_artifacts"].append(
                {"id": _doc_id(artifact["cycle_id"]), "doc": artifact}
            )

    # Terminal state only: the console reads the current record, not its history.
    for key, record in sorted((known or {}).items()):
        collections["publishers"].append({"id": _doc_id(key), "doc": record.to_doc()})

    for key, series in sorted(history.items()):
        for observation in series:
            doc = observation.to_doc()
            collections["observations"].append(
                {"id": _observation_id(key, doc["polled_at"]), "doc": doc}
            )

    rollups = roll_up_all([o for series in history.values() for o in series], cycle.evaluations)
    for rollup in rollups:
        doc = rollup.to_doc()
        collections["publisher_daily"].append(
            {"id": _doc_id(f"{doc['publisher_key']}@{doc['day']}"), "doc": doc}
        )

    Path(args.out).write_text(
        json.dumps({"collections": collections, "reports": reports}, default=str)
    )
    for name, rows in collections.items():
        print(f"{name}: {len(rows)}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
