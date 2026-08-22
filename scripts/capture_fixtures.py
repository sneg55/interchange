#!/usr/bin/env python3
"""Capture a dated, checksummed snapshot of the live feeds and schemas.

Closes the provenance gap that three review rounds flagged and none closed. The
probes read mutable endpoints and schemas from a moving branch, so they reproduce
the SHAPE of every finding but not any specific run. A published number could
not be re-derived by anyone, including its author, once a publisher pushed an
update.

A snapshot fixes three things at once:

  1. Tests get real feeds without network flakiness or drift (spec 12).
  2. The demo survives a network failure at record time (spec 10, Fallback).
  3. A quoted figure becomes checkable against the exact bytes it came from.

Every artifact is stored with its SHA-256 and the URL and UTC timestamp it came
from. `--verify` re-hashes what is on disk against the manifest, so a corrupted
or hand-edited fixture is loud rather than silent.

Usage:
    python3 scripts/capture_fixtures.py                 # capture the default set
    python3 scripts/capture_fixtures.py --all-open      # every non-key-gated feed
    python3 scripts/capture_fixtures.py --verify        # re-hash, no network
    python3 scripts/capture_fixtures.py --list

Only reads public federal data. No API keys, no writes outside tests/fixtures/.
"""

import argparse
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wzdx import feeds, schemas
from wzdx.snapshot_store import (
    FIXTURE_DIR,
    MANIFEST,
    read_artifact,
    sha256,
    utc_now,
    write_artifact,
)

# The publishers the spec's claims and tests actually rest on. Each is here for a
# stated reason, so a future reader can tell which fixtures are load-bearing.
DEFAULT_FEEDS = {
    "Utah DOT": "R2 and R4: 1,236 days stale, 744/744 active zones ended in the past",
    "Hawaii DOT": "R2 against a different schema version; no event_status at all",
    "New York DOT": "duplication pair A; direction unknown on 100% of features",
    "New Jersey Institute of Technology": "duplication pair B; same TRANSCOM upstream",
    "Missouri DOT": "negative control: long corridors that must not swallow a ramp",
    "St. Charles County": "negative control: 4.8 km ramp inside those corridors",
    "CivicLink": "negative control: overlapping bbox, zero candidate pairs",
    "Quebec City": "null geometry, v3.1 schema, no declared cadence",
}


def capture_registry() -> tuple[dict, list]:
    raw = json.dumps(feeds.fetch_json(feeds.REGISTRY), sort_keys=True).encode()
    entry = write_artifact("registry", raw)
    entry.update(url=feeds.REGISTRY, captured_at=utc_now())
    return entry, [r for r in json.loads(raw) if r.get("active")]


def capture_feed(reg_entry: dict, why: str) -> dict | None:
    org = reg_entry["issuingorganization"]
    name = reg_entry.get("feedname") or org
    url = reg_entry["url"]["url"]
    try:
        raw = json.dumps(feeds.fetch_json(url, timeout=60), sort_keys=True).encode()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  SKIP {org}: {type(exc).__name__}: {exc}")
        return None
    entry = write_artifact(f"feeds/{name}", raw)
    doc = json.loads(raw)
    header = doc.get("feed_info") or doc.get("road_event_feed_info") or {}
    entry.update(
        org=org,
        feedname=reg_entry.get("feedname"),
        url=url,
        declared_version=str(reg_entry.get("version")),
        declared_cadence=reg_entry.get("datafeed_frequency_update"),
        feed_update_date=header.get("update_date"),
        feature_count=len(doc.get("features") or []),
        captured_at=utc_now(),
        why=why,
    )
    print(f"  {org}: {entry['feature_count']} features, {entry['bytes']:,} bytes")
    return entry


def capture_schemas() -> dict:
    """Pin the official schemas too.

    Validation reads the `main` branch of usdot-jpo-ode/wzdx, so a schema change
    upstream silently changes what "passes the official validator" means. The
    Utah result is the spec's headline claim and it must be reproducible against
    the exact schema it was measured with.

    The GeoJSON documents are pinned for a second reason: without them in hand,
    validation resolves them over the network on every call, which makes an
    offline conformance check impossible and makes an online one depend on a
    third party's uptime for a result attributed to the publisher.
    """
    out = {}
    wanted = [
        (version, member, f"{schemas.SCHEMA_ROOT}/{version}/{member}.json")
        for version, (_, members) in schemas.SCHEMA_SETS.items()
        for member in members
    ]
    wanted += [("geojson", member, url) for member, url in schemas.GEOJSON_SCHEMAS.items()]
    for version, member, url in wanted:
        try:
            raw = json.dumps(feeds.fetch_json(url), sort_keys=True).encode()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  SKIP schema {version}/{member}: {type(exc).__name__}")
            continue
        entry = write_artifact(f"schemas/{version}/{member}", raw)
        entry.update(url=url, captured_at=utc_now())
        out[f"{version}/{member}"] = entry
    print(f"  {len(out)} schema documents pinned")
    return out


def do_capture_schemas_only() -> int:
    """Refresh the pinned schemas, leaving the feed snapshot alone.

    Separate from a full capture because the feeds and the schemas move for
    different reasons. Adding a schema document should not silently re-date every
    feed fixture underneath a measurement that cited them.
    """
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}; run a full capture first")
        return 1
    with MANIFEST.open() as fh:
        manifest = json.load(fh)
    print("schemas")
    manifest["schemas"] = capture_schemas()
    manifest["schemas_captured_at"] = utc_now()
    with MANIFEST.open("w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"\nwrote {MANIFEST} ({len(manifest['schemas'])} schemas, feeds untouched)")
    return 0


def do_capture(all_open: bool) -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    print("registry")
    registry_entry, registry = capture_registry()

    # --all-open widens the set; it must not narrow the provenance. A generic
    # `why` for every feed erased the stated reason each default one is
    # load-bearing, so a reader could no longer tell which fixtures a claim
    # rests on. Default reasons win where they exist.
    generic = "captured under --all-open"
    wanted = (
        {
            r["issuingorganization"]: DEFAULT_FEEDS.get(r["issuingorganization"], generic)
            for r in registry
            if not r.get("needapikey")
        }
        if all_open
        else dict(DEFAULT_FEEDS)
    )
    print(f"\nfeeds ({len(wanted)} requested)")
    captured = {}
    for org, why in wanted.items():
        matches = [
            r for r in registry if r["issuingorganization"] == org and not r.get("needapikey")
        ]
        if not matches:
            print(f"  SKIP {org}: not in the registry, or key-gated")
            continue
        for reg_entry in matches:
            entry = capture_feed(reg_entry, why)
            if entry:
                captured[f"{org}|{reg_entry.get('feedname')}"] = entry

    print("\nschemas")
    schema_entries = capture_schemas()

    manifest = {
        "captured_at": utc_now(),
        "note": (
            "Snapshot of live public data. Feeds move; this is the record of "
            "what they said when a figure was measured. Verify with --verify."
        ),
        "registry": registry_entry,
        "feeds": captured,
        "schemas": schema_entries,
    }
    with MANIFEST.open("w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    total = sum(e["bytes"] for e in captured.values())
    print(f"\nwrote {MANIFEST}")
    print(
        f"  {len(captured)} feeds, {len(schema_entries)} schemas, {total / 1e6:.1f} MB uncompressed"
    )
    return 0


def do_verify() -> int:
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}; run without --verify first")
        return 1
    with MANIFEST.open() as fh:
        manifest = json.load(fh)
    entries = [("registry", manifest["registry"])]
    entries += [(f"feed {k}", v) for k, v in manifest["feeds"].items()]
    entries += [(f"schema {k}", v) for k, v in manifest["schemas"].items()]
    bad = 0
    for label, entry in entries:
        try:
            actual = sha256(read_artifact(entry["path"]))
        except OSError as exc:
            print(f"  MISSING {label}: {exc}")
            bad += 1
            continue
        if actual != entry["sha256"]:
            print(f"  CORRUPT {label}: {entry['sha256'][:12]} != {actual[:12]}")
            bad += 1
    print(f"verified {len(entries)} artifacts captured {manifest['captured_at']}")
    print("  all checksums match" if not bad else f"  {bad} FAILED")
    return 1 if bad else 0


def do_list() -> int:
    if not MANIFEST.exists():
        print("no snapshot captured yet")
        return 1
    with MANIFEST.open() as fh:
        manifest = json.load(fh)
    print(f"snapshot captured {manifest['captured_at']}\n")
    for _key, e in sorted(manifest["feeds"].items()):
        print(
            f"  {e['org']:38} v{e['declared_version']:<8} "
            f"{e['feature_count']:>6} features  updated {e['feed_update_date']}"
        )
        print(f"      {e['why']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--all-open",
        action="store_true",
        help="capture every non-key-gated feed, not just the default set",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="re-hash on-disk fixtures against the manifest; no network",
    )
    ap.add_argument("--list", action="store_true", help="show what is in the snapshot")
    ap.add_argument(
        "--schemas-only",
        action="store_true",
        help="re-pin the schemas without re-dating the feed snapshot",
    )
    args = ap.parse_args()
    if args.verify:
        return do_verify()
    if args.list:
        return do_list()
    if args.schemas_only:
        return do_capture_schemas_only()
    return do_capture(args.all_open)


if __name__ == "__main__":
    sys.exit(main())
