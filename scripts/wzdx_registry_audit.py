#!/usr/bin/env python3
"""Audit the WZDx Feed Registry's structure and the fleet's transfer economics.

Two questions the health probe does not answer, both of which the Interchange
spec makes load-bearing decisions on:

1. What does the registry actually guarantee? Whether `issuingorganization` is
   unique decides what a publisher agent is keyed on (spec 6.1), and which
   cadence field exists decides how freshness is bounded (spec 6.3).

2. What does polling this fleet cost? Sections 6.3 and 8 clamp the poll cadence
   on the basis of sweep bytes, and that clamp is only defensible if the bytes
   were measured rather than assumed.

Usage:
    python3 scripts/wzdx_registry_audit.py
    python3 scripts/wzdx_registry_audit.py --transfer

Only reads public federal data. No API keys, no writes.
"""

import argparse
import collections
import concurrent.futures
import gzip
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wzdx import (
    feeds,
    schemas,
)

CADENCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_cadence(value):
    """Parse `datafeed_frequency_update` into seconds.

    Live values mix units: 60s, 1m, 5m, 30m, 72h, 168h. A parser assuming
    minutes would read 168h as under three hours instead of a week.
    """
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd])\s*", str(value), re.I)
    if not m:
        return None
    return float(m.group(1)) * CADENCE_UNITS[m.group(2).lower()]


def measure(entry):
    """Fetch one feed and record wire versus decompressed byte counts."""
    if entry.get("needapikey"):
        return None
    try:
        req = urllib.request.Request(entry["url"]["url"], headers=feeds.UA)
        with urllib.request.urlopen(req, timeout=30, context=feeds.CTX) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding")
        plain = gzip.decompress(raw) if encoding == "gzip" else raw
        return {"org": entry["issuingorganization"], "feedname": entry.get("feedname"),
                "wire": len(raw), "plain": len(plain), "compressed": encoding == "gzip",
                "cadence_s": parse_cadence(entry.get("datafeed_frequency_update"))}
    except Exception:
        return None


def report_structure(registry):
    n = len(registry)
    print(f"registry: {n} active entries\n")

    orgs = collections.Counter(r["issuingorganization"] for r in registry)
    dupes = {o: c for o, c in orgs.items() if c > 1}
    print(f"distinct issuingorganization values: {len(orgs)} across {n} entries")
    if dupes:
        print("  NOT UNIQUE, so it cannot be the publisher key:")
        for org in dupes:
            for r in registry:
                if r["issuingorganization"] == org:
                    print(f"    {org!r} feedname={r.get('feedname')!r} "
                          f"v{r.get('version')} key={bool(r.get('needapikey'))}")
    keys = collections.Counter((r["issuingorganization"], r.get("feedname"))
                               for r in registry)
    collisions = {k: c for k, c in keys.items() if c > 1}
    print(f"\n(issuingorganization, feedname) pairs: {len(keys)} across {n} entries")
    print("  unique, usable as the publisher key" if not collisions
          else f"  COLLIDES on {collisions}")

    print("\ncadence field coverage:")
    for field in ("datafeed_frequency_update", "updatefrequency"):
        present = sum(1 for r in registry if r.get(field))
        print(f"  {field}: present on {present}/{n}")
    cadences = collections.Counter(r.get("datafeed_frequency_update") for r in registry)
    print(f"  distinct values: {dict(cadences)}")
    parsed = [(r["issuingorganization"], parse_cadence(r.get("datafeed_frequency_update")))
              for r in registry]
    unparsed = [o for o, s in parsed if s is None]
    if unparsed:
        print(f"  not parseable to seconds ({len(unparsed)}): {unparsed}")

    print("\nAPI key gating:")
    print(f"  {dict(collections.Counter(str(r.get('needapikey')) for r in registry))}")
    print("  absence means no key required, so it must not be read as False-only")

    print("\ndeclared versions:")
    versions = collections.Counter(str(r.get("version")) for r in registry)
    for v, c in versions.most_common():
        known = "schema published" if schemas.is_known(v) else schemas.SCHEMA_UNKNOWN
        print(f"  {v!r}: {c} entries, {known}")
    unknown = sum(c for v, c in versions.items() if not schemas.is_known(v))
    print(f"  entries with no resolvable schema: {unknown}/{n}")


def report_transfer(registry):
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        rows = [r for r in pool.map(measure, registry) if r]
    if not rows:
        print("\nno feeds responded; transfer economics not measured")
        return
    rows.sort(key=lambda r: -r["wire"])
    wire = sum(r["wire"] for r in rows)
    plain = sum(r["plain"] for r in rows)
    print(f"\ntransfer economics over {len(rows)} responding feeds")
    print(f"  one full sweep: {wire / 1e6:.1f} MB on the wire, "
          f"{plain / 1e6:.1f} MB decompressed")

    uncompressed = [r for r in rows if not r["compressed"]]
    share = 100.0 * sum(r["wire"] for r in uncompressed) / wire if wire else 0.0
    print(f"  feeds serving no compression: {len(uncompressed)}, "
          f"{share:.0f}% of sweep bytes")
    for r in sorted(uncompressed, key=lambda r: -r["wire"])[:5]:
        print(f"    {r['org']}: {r['wire'] / 1e6:.2f} MB")

    print("  largest on the wire:")
    for r in rows[:5]:
        ratio = f", compresses {r['plain'] / r['wire']:.0f}x" if r["compressed"] else ""
        print(f"    {r['org']}: {r['wire'] / 1e6:.2f} MB wire / "
              f"{r['plain'] / 1e6:.2f} MB plain{ratio}")

    print("\n  daily ingress by poll policy:")
    declared = sum(r["wire"] * 86400 / r["cadence_s"] for r in rows if r["cadence_s"])
    print(f"    at each publisher's declared cadence: {declared / 1e9:6.1f} GB/day")
    for floor_s, label in ((300, "5 min floor"), (900, "15 min floor"), (3600, "60 min")):
        total = sum(r["wire"] * 86400 / max(r["cadence_s"] or floor_s, floor_s)
                    for r in rows)
        print(f"    clamped to a {label:11}: {total / 1e9:6.1f} GB/day")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transfer", action="store_true",
                    help="fetch every open feed and measure sweep bytes")
    args = ap.parse_args()
    registry = feeds.active_registry()
    report_structure(registry)
    if args.transfer:
        report_transfer(registry)


if __name__ == "__main__":
    main()
