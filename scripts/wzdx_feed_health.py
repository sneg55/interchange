#!/usr/bin/env python3
"""Probe every feed in the federal WZDx Feed Registry and report health.

Reproduces the central claim of the Interchange spec: some registered feeds
serve years-stale work zones as currently active while passing the official
USDOT JSON schema, and one of them contradicts itself on every single zone.

Four signal families, matching sections 6.2 and 6.4 of the spec:

    reachability  HTTP status, error class
    freshness     update_date age
    conformance   official schema for the feed's OWN declared version
    consistency   zones marked active whose end_date has already passed

It also measures the untrusted free-text surface, since that is the input the
screener exists to handle and the spec quotes numbers for it.

Usage:
    python3 scripts/wzdx_feed_health.py
    python3 scripts/wzdx_feed_health.py --validate-stale
    python3 scripts/wzdx_feed_health.py --text-surface

Only reads public federal data. No API keys, no writes.
"""

import argparse
import collections
import concurrent.futures
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wzdx import attributes as attr
from wzdx import (
    feeds,
    schemas,
)


def event_status(feature):
    core = attr.core(feature)
    props = feature.get("properties", {})
    return core.get("event_status") or props.get("event_status")


def consistency(features, now):
    """Count zones asserting `active` whose end_date has already passed.

    This is R4 in the spec, and it is the signal that survives a publisher
    refreshing its timestamp: a feed can be seconds old and still assert 744
    active work zones that all ended years ago.
    """
    active = past = undated = 0
    for f in features:
        if event_status(f) != "active":
            continue
        active += 1
        end = (f.get("properties") or {}).get("end_date")
        if not end:
            undated += 1
            continue
        try:
            if attr.parse_stamp(end) < now:
                past += 1
        except ValueError:
            undated += 1
    return active, past, undated


def text_surface(features):
    """Measure the untrusted free-text the screener must handle."""
    with_desc = max_desc = road_name_count = 0
    for f in features:
        d = attr.description(f)
        if d:
            with_desc += 1
            max_desc = max(max_desc, len(d))
        road_name_count += len(attr.core(f).get("road_names") or [])
    return with_desc, max_desc, road_name_count


def probe(entry, now):
    org = entry["issuingorganization"]
    rec = {
        "org": org,
        "feedname": entry.get("feedname"),
        "version": str(entry.get("version")),
        "cadence": entry.get("datafeed_frequency_update"),
    }
    if entry.get("needapikey"):
        rec["status"] = "NEEDS_KEY"
        return rec
    try:
        doc = feeds.fetch_json(entry["url"]["url"], timeout=25)
    except Exception as exc:
        rec.update(status="DOWN", error=f"{type(exc).__name__}: {exc}")
        return rec
    # v4.0 names the header road_event_feed_info; v4.1 and v4.2 renamed it feed_info.
    header = doc.get("feed_info") or doc.get("road_event_feed_info") or {}
    features = doc.get("features", []) or []
    active, past, undated = consistency(features, now)
    with_desc, max_desc, road_names = text_surface(features)
    rec.update(
        status="OK",
        features=len(features),
        active=active,
        active_past_end=past,
        active_undated=undated,
        statuses=collections.Counter(event_status(f) for f in features),
        with_desc=with_desc,
        max_desc=max_desc,
        road_names=road_names,
        update_date=header.get("update_date"),
        doc=doc,
    )
    return rec


def age_days(stamp, now):
    return (now - attr.parse_stamp(stamp)).total_seconds() / 86400


def report_reachability(results, ok, registry):
    tally = collections.Counter(r["status"] for r in results)
    print(f"probed {datetime.date.today()}: {len(registry)} active registered entries")
    print(f"  parsed={tally['OK']} down={tally['DOWN']} key-gated={tally['NEEDS_KEY']}")
    print(f"  work zone features served: {sum(r['features'] for r in ok):,}")
    if not tally["DOWN"]:
        return
    print("\ndown right now:")
    for r in results:
        if r["status"] == "DOWN":
            print(f"  {r['org']}: {r['error']}")


def report_contradiction(ok):
    """R4: zones asserting `active` whose end_date has already passed."""
    print("\ninternal contradiction (R4): zones marked active whose end_date has passed")
    flagged = [r for r in ok if r["active"] and r["active_past_end"]]
    if not flagged:
        print("  none")
    for r in sorted(flagged, key=lambda x: -x["active_past_end"] / max(1, x["active"])):
        pct = 100.0 * r["active_past_end"] / r["active"]
        print(
            f"  {r['org']}: {r['active_past_end']}/{r['active']} active zones "
            f"({pct:.1f}%) ended in the past"
            + (f", {r['active_undated']} undated" if r["active_undated"] else "")
        )


def split_by_freshness(ok, now, stale_days):
    """Partition into (stale, undeterminable). R2 and R6 respectively."""
    stale, unreadable = [], []
    for r in ok:
        if not r.get("update_date"):
            unreadable.append((r["org"], "no update_date in feed header"))
            continue
        try:
            days = age_days(r["update_date"], now)
        except ValueError as exc:
            unreadable.append((r["org"], f"unparseable update_date: {exc}"))
            continue
        if days > stale_days:
            stale.append((days, r))
    return stale, unreadable


def report_freshness(stale, unreadable, stale_days, validate):
    if unreadable:
        print(f"\nfreshness not determinable (R6): {len(unreadable)}")
        for org, why in unreadable:
            print(f"  {org}: {why}")
    print(f"\nstale beyond {stale_days:g} days (R2): {len(stale)}")
    for days, r in sorted(stale, reverse=True, key=lambda x: x[0]):
        print(
            f"  {r['org']}: {days:.0f} days old, {r['features']} zones, "
            f"{r['active']} marked active (updated {r['update_date']})"
        )
        print(
            f"    declared cadence {r['cadence']!r}, "
            f"event_status distribution {dict(r['statuses'])}"
        )
        if validate:
            report_schema(r)


def report_schema(record):
    errors = schemas.validate(record["doc"], record["version"], feeds.fetch_json)
    if errors == schemas.SCHEMA_UNKNOWN:
        # Never a pass and never a failure: the publisher is not penalized for
        # publishing a schema version this tool has not implemented.
        print(
            f"    official schema v{record['version']}: not published, "
            f"{schemas.SCHEMA_UNKNOWN} (publisher not penalized)"
        )
        return
    verdict = "PASS, 0 errors" if not errors else f"FAIL, {len(errors)} errors"
    print(f"    official USDOT WZDx v{record['version']} schema: {verdict}")


def report_text_surface(ok):
    feats = sum(r["features"] for r in ok)
    desc = sum(r["with_desc"] for r in ok)
    names = sum(r["road_names"] for r in ok)
    print(f"\nuntrusted free-text surface across {len(ok)} parsed feeds:")
    print(f"  features: {feats:,}")
    print(f"  with a description: {desc:,} ({100.0 * desc / max(1, feats):.1f}%)")
    print(f"  road_names entries: {names:,}")
    print(f"  total untrusted strings: {desc + names:,}")
    print(f"  longest description: {max((r['max_desc'] for r in ok), default=0):,} chars")
    universal = [r for r in ok if r["features"] and r["with_desc"] == r["features"]]
    print(f"  feeds where every feature carries a description: {len(universal)} of {len(ok)}")
    for r in sorted(universal, key=lambda x: -x["features"]):
        print(f"    {r['org']}: {r['features']:,} features, longest {r['max_desc']:,} chars")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--validate-stale",
        action="store_true",
        help="run the official schema for its own version against each stale feed",
    )
    ap.add_argument(
        "--text-surface",
        action="store_true",
        help="report the untrusted free-text surface across the fleet",
    )
    ap.add_argument("--stale-days", type=float, default=7.0)
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.UTC)
    registry = feeds.active_registry()
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda e: probe(e, now), registry))
    ok = [r for r in results if r["status"] == "OK"]

    report_reachability(results, ok, registry)
    report_contradiction(ok)
    stale, unreadable = split_by_freshness(ok, now, args.stale_days)
    report_freshness(stale, unreadable, args.stale_days, args.validate_stale)
    if args.text_surface:
        report_text_surface(ok)


if __name__ == "__main__":
    main()
