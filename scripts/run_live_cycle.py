#!/usr/bin/env python3
"""Poll the live fleet on a cadence and write what happened. Build plan M3.

    python3 scripts/run_live_cycle.py --once
    python3 scripts/run_live_cycle.py --interval 900
    python3 scripts/run_live_cycle.py --store firestore --project my-project

Unlike `run_cycle.py` this reaches the internet: the registry at
datahub.transportation.gov and then every active publisher's feed. It is the
only entrypoint in the repository that does.

Three defaults are decisions rather than conveniences.

**Fifteen minutes, not five.** Section 17 measured 10.8 GB of ingress per day at
the 5 minute floor against 3.7 GB at 15 minutes, and 89 percent of it comes from
eleven publishers who serve no compression. R5 needs 12 polls inside a 24 hour
window and 15 minutes gives it 96. The cheaper cadence starves nothing.

**Screening fails closed, and is selectable.** `--screener fail-closed` is the
default and redacts every free text field, exactly as if it had been blocked.
That is the invariant in section 6.5, and the offline seed's `KeywordScreener` is
not available here on purpose: a live run is the one place where treating
unscreened text as screened would matter.

`--screener model-armor` selects the real screener, which still fails closed on
an outage. The flag exists because for a while it did not: `FailClosedScreener`
was constructed unconditionally and `ModelArmorScreener` was imported by nothing
but its own test, so the deployed fleet redacted 98.8 percent of road names with
no way to turn screening on. A default that cannot be changed is not a default.

**The two model seats are opt-in.** `--adjudicator gemini` and `--drafter gemini`
enable Tier 2 duplicate adjudication (6.6) and notice prose (6.7). Both default
to off HERE and are turned on by the reference deployment, because off has to be
what you get by accident: it is deterministic rather than degraded, since an
unadjudicated ambiguous pair is "not decided" and never "duplicate", and an
undrafted notice ships the packet's own rendering.

Both authenticate through Vertex AI using the runner's service account. There is
no API key, on the box or anywhere else: `genai.Client()` reads
GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION from
its own environment and uses application default credentials. A key would have
had to live in the systemd unit, readable by anyone with a shell on the VM.

**Schemas are pinned, not fetched.** Conformance is scored against the captured
schema set in `tests/fixtures/`, so R3 measures the publisher changing rather
than USDOT republishing a schema. An unresolvable version records
SCHEMA_UNKNOWN and suppresses R3; it never fails a publisher.
"""

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.fleet_cycle import FleetCycle
from src.entrypoints.live_fleet import ZONES_UNCHANGED, LiveFleet
from src.services.body_snapshots import FileBodySnapshots
from src.services.feed_sink import GcsFeedSink
from src.services.fixtures import FixtureSet
from src.services.live_sources import LiveFeedSource, LiveRegistrySource
from src.services.local_store import LocalStore
from src.services.schema_registry import FixtureSchemaLoader, SchemaRegistry
from src.services.screeners import FailClosedScreener
from src.utils.env import env

DEFAULT_INTERVAL_SECONDS = 900
DEFAULT_ROOT = ".fleet"

_stopping = False


def _stop(signum: int, frame: Any) -> None:
    """Finish the cycle in flight, then exit.

    A cycle killed between its poll and its write leaves observations that were
    taken and never recorded, which is a hole in the history rather than a short
    run. The second signal still kills it, because a runner that cannot be
    stopped is worse.
    """
    global _stopping
    del frame
    print(f"\nsignal {signum}: finishing this cycle, then stopping", flush=True)
    _stopping = True
    signal.signal(signum, signal.SIG_DFL)


def build_store(args: argparse.Namespace) -> Any:
    if args.store == "local":
        return LocalStore(Path(args.root) / "store")
    from src.services.firestore_store import FirestoreStore

    if not args.project:
        raise SystemExit("--project is required with --store firestore")
    return FirestoreStore(args.project, args.database)


def build_screener(args: argparse.Namespace) -> Any:
    """Fail closed unless a real screener is both selected AND configured.

    Selecting `model-armor` without a template is a hard exit rather than a quiet
    fall back to fail-closed. Both produce redacted output, so a silent fallback
    is indistinguishable from the screener working and finding everything hostile,
    which is the single most misleading state this system can be in.
    """
    if args.screener == "fail-closed":
        return FailClosedScreener()

    from src.services.model_armor import ModelArmorScreener

    project = env.gcp_project_id or args.project
    if not project or not env.model_armor_template_id:
        raise SystemExit(
            "--screener model-armor needs GCP_PROJECT_ID (or --project) and "
            "MODEL_ARMOR_TEMPLATE_ID; refusing to fall back to fail-closed "
            "silently, because redacted output would look identical either way"
        )
    return ModelArmorScreener(
        project=project,
        location=env.gcp_region,
        template_id=env.model_armor_template_id,
        revision=env.model_armor_policy_version,
    )


def build_models(args: argparse.Namespace) -> tuple[Any, Any]:
    """The adjudicator and the drafter. Neither may touch the trust gate."""
    adjudicator = drafter = None
    if args.adjudicator == "gemini":
        from src.services.gemini import GeminiAdjudicator

        adjudicator = GeminiAdjudicator(model=env.gemini_model)
    if args.drafter == "gemini":
        from src.services.gemini import GeminiDrafter

        drafter = GeminiDrafter(model=env.gemini_model)
    return adjudicator, drafter


def build_feed_sink(args: argparse.Namespace) -> Any:
    """Where the merged feed is written, or None.

    None is a legitimate production state and means the feed is validated and
    then discarded: `feed_uri` and `byte_size` stay null and say so. What is not
    legitimate is writing a feed that failed its own gate, which is why the sink
    is never consulted about whether to publish.
    """
    bucket = args.output_bucket or env.gcs_bucket_output
    return GcsFeedSink(bucket) if bucket else None


def build_cycle(args: argparse.Namespace) -> FleetCycle:
    adjudicator, drafter = build_models(args)
    return FleetCycle(
        registry=LiveRegistrySource(),
        feeds=LiveFeedSource(),
        # The schema set is the captured one. See the module docstring.
        schemas=SchemaRegistry(FixtureSchemaLoader(FixtureSet())),
        screener=build_screener(args),
        # Retained across restarts, or the poll after every restart answers a
        # 304 with no body and drops that publisher out of the merged feed.
        bodies=FileBodySnapshots(Path(args.root) / "bodies"),
        adjudicator=adjudicator,
        drafter=drafter,
        feed_sink=build_feed_sink(args),
    )


def summarise(report: Any, written: dict[str, int], seconds: float) -> str:
    states = report.states
    bands = {
        state: sum(1 for v in states.values() if v == state)
        for state in sorted(set(states.values()))
    }
    band_text = ", ".join(f"{state} {count}" for state, count in bands.items())
    counts = dict(written)
    # Not a collection, so it is not printed as one. A zone the throttle skipped
    # is a document that was checked and found identical, which is a different
    # claim from a document that was written and a very different one from a
    # document that quietly went missing. Stated either way, including when it is
    # zero, because "the throttle saved nothing this cycle" is the reading that
    # would otherwise be indistinguishable from the field being absent.
    unchanged = counts.pop(ZONES_UNCHANGED, None)
    skipped = (
        ""
        if unchanged is None
        else f"\n    canonical zones unchanged and not rewritten: {unchanged}"
    )
    return (
        f"{report.at}  {report.publishers_polled}/{report.publishers_in_registry} polled  "
        # Named, because a cycle that polled 12 of 40 having backed 15 of them
        # off looks identical to one where 15 publishers went unreachable.
        f"({report.publishers_not_due} not due)  "
        f"[{band_text}]  zones {report.canonical_zones}  published {report.published}  "
        f"packets {report.packets_opened}  {seconds:.1f}s\n"
        # Every collection, every cycle, including the expensive one. Section 8
        # sizes observations at roughly 8 MB per day and says nothing about
        # canonical zones, which measured 50,169 documents on the first live
        # cycle: at a 15 minute cadence that is the fleet's dominant write cost
        # and it should be visible in the log rather than on the bill.
        f"    wrote "
        + ", ".join(f"{name} {count}" for name, count in sorted(counts.items()) if count)
        + skipped
    )


# Large enough that only the collection worth watching says anything, and often
# enough that a stall is visible while it is happening rather than afterwards.
# The first Firestore write path blocked on 50,000 zones having printed nothing.
PROGRESS_EVERY = 10_000


def progress(collection: str, done: int, total: int) -> None:
    if total < PROGRESS_EVERY or (done % PROGRESS_EVERY and done != total):
        return
    print(f"    {collection}: {done}/{total}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", choices=("local", "firestore"), default="local")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="local store and body snapshot root")
    parser.add_argument("--project", default="", help="GCP project, with --store firestore")
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--once", action="store_true", help="one cycle, then exit")
    parser.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = forever)")
    parser.add_argument(
        "--screener",
        choices=("fail-closed", "model-armor"),
        default="fail-closed",
        help="fail-closed redacts all free text; model-armor still fails closed on outage",
    )
    parser.add_argument(
        "--adjudicator",
        choices=("none", "gemini"),
        default="none",
        help="Tier 2 duplicate adjudication (6.6). Never touches the trust gate.",
    )
    parser.add_argument(
        "--output-bucket",
        default="",
        help="GCS bucket for the merged feed. Empty means validate and discard.",
    )
    parser.add_argument(
        "--drafter",
        choices=("none", "gemini"),
        default="none",
        help="notice prose (6.7). Facts always come from the packet.",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    store = build_store(args)
    fleet = LiveFleet(store, build_cycle(args), args.interval)
    print(
        f"fleet: {len(fleet.records)} publishers known, retaining {fleet.retain} polls each "
        f"at a {args.interval}s cadence",
        flush=True,
    )
    # Named at startup, every run. Fail-closed screening and working screening
    # that blocks everything produce identical output, and for a whole deployment
    # nobody could tell which one was running.
    print(
        f"screening: {args.screener}"
        + (" (every free text field will be redacted)" if args.screener == "fail-closed" else "")
        + f"; adjudicator: {args.adjudicator}; drafter: {args.drafter}",
        flush=True,
    )
    # The warmed cache size, every start. A screening cache that silently stops
    # loading does not raise anything: it re-screens everything and shows up as a
    # bill, which is the slowest possible way to notice. Zero here on a fleet that
    # has been running is the signal.
    unreadable = (
        "" if not fleet.screening_unreadable else f", {fleet.screening_unreadable} unreadable"
    )
    print(
        f"screening cache: {fleet.screening_cached} verdicts warmed{unreadable}",
        flush=True,
    )

    completed = 0
    while not _stopping:
        started = time.monotonic()
        try:
            report, written = fleet.run_once(progress=progress)
        except Exception as exc:
            # Printed and retried rather than raised. A registry blip, a DNS
            # failure or a store timeout ends this cycle; ending the fleet would
            # turn a five-minute outage into a permanent hole in the history,
            # and the history is the whole point of running this for weeks.
            print(f"cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        else:
            print(summarise(report, written, time.monotonic() - started), flush=True)
        completed += 1
        if args.once or (args.cycles and completed >= args.cycles):
            break
        # Measured from the start of the cycle, so a slow cycle does not push the
        # cadence out. A cycle longer than the interval starts the next one
        # immediately rather than accumulating drift.
        remaining = args.interval - (time.monotonic() - started)
        while remaining > 0 and not _stopping:
            time.sleep(min(remaining, 1.0))
            remaining = args.interval - (time.monotonic() - started)

    print(f"stopped after {completed} cycle(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
