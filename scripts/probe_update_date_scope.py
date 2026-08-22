#!/usr/bin/env python3
"""Measure how many features carry their own `update_date`.

Section 6.6 resolves a field conflict by `MOST_RECENT_UPDATE_DATE`, and section 7
carries an `update_date` per `SourceRef`. Which timestamp that is decides real
outcomes: WZDx puts an `update_date` in `feed_info` (when the publisher last
regenerated the whole feed) and, optionally, one in each feature's `core_details`
(when that work zone last changed). Resolving on the feed header means a
publisher who republishes everything every five minutes out-ranks a publisher who
actually updated the zone in question an hour ago.

The reconciler used the feed header for every source, because that is the value
the publisher agent already had. This measures how often the better value is
sitting in the feature.

    python3 scripts/probe_update_date_scope.py              # committed fixtures
    python3 scripts/probe_update_date_scope.py --bodies .fleet/bodies

Read-only, offline, stdlib only. The fixture path is reproducible by anyone with
the repository; `--bodies` reads whatever the live runner last captured.
"""

import argparse
import gzip
import json
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_FEEDS = ROOT / "tests" / "fixtures" / "feeds"


def _load(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
        if path.suffix == ".gz":
            raw = gzip.decompress(raw)
        body = json.loads(raw)
    except (OSError, ValueError, gzip.BadGzipFile):
        # Named, not swallowed: a body this probe cannot read is reported as
        # unreadable rather than counted as a feed with no per-feature dates.
        print(f"  unreadable: {path.name}", file=sys.stderr)
        return None
    return body if isinstance(body, dict) else None


def _bodies(directory: Path) -> Iterator[tuple[str, dict]]:
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".gz", ".json"):
            continue
        body = _load(path)
        if body is not None:
            yield path.name, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bodies", default=str(FIXTURE_FEEDS))
    args = parser.parse_args()
    directory = Path(args.bodies)
    if not directory.is_dir():
        raise SystemExit(f"no such directory: {directory}")

    feeds = features = with_own = 0
    feeds_with_any = 0
    for name, body in _bodies(directory):
        items = body.get("features")
        if not isinstance(items, list) or not items:
            continue
        own = sum(
            1
            for f in items
            if isinstance(f, dict)
            and isinstance((f.get("properties") or {}).get("core_details"), dict)
            and (f["properties"]["core_details"].get("update_date") or "")
        )
        feeds += 1
        feeds_with_any += 1 if own else 0
        features += len(items)
        with_own += own
        print(f"  {name}: {own}/{len(items)} features carry their own update_date")

    if not feeds:
        raise SystemExit(f"no readable feeds in {directory}")
    print(
        f"\n{with_own}/{features} features carry their own update_date, "
        f"across {feeds_with_any}/{feeds} feeds in {directory}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
