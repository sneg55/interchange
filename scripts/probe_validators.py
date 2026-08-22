#!/usr/bin/env python3
"""Measure how many publishers actually support conditional GET.

Section 6.3 names conditional GET as one of three ingress reductions, ahead of
adaptive backoff. That claim rests entirely on publishers returning a validator,
and nothing in the research so far measured whether they do. Utah DOT, the
project's headline publisher, returns neither `ETag` nor `Last-Modified`, which
is what prompted this.

Two requests per publisher: one plain, and one carrying whatever validator came
back. A publisher that advertises a validator and then ignores it on the way back
is worse than one that advertises none, because the ingress model would count a
saving that never arrives.

    python3 scripts/probe_validators.py            # open feeds from the snapshot
    python3 scripts/probe_validators.py --live     # every open feed in the registry

Read-only. Two GETs per publisher, no writes anywhere.
"""

import argparse
import gzip
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.fixtures import FixtureSet

TIMEOUT = 45
HEADERS = {"User-Agent": "interchange-probe/0.1", "Accept-Encoding": "gzip"}


def fetch(url, etag=None, last_modified=None):
    """Return (status, headers, byte_count, body_sha256, error, seconds).

    The body digest is what lets a second `200` be told apart from a publisher
    ignoring its own validator: these feeds change constantly, so a `200` on the
    conditional request is CORRECT when the content really moved.
    """
    headers = dict(HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    started = time.time()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            digest = hashlib.sha256(raw).hexdigest()
            return resp.status, dict(resp.headers), len(raw), digest, None, time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), 0, "", None, time.time() - started
    except Exception as exc:
        return 0, {}, 0, "", f"{type(exc).__name__}: {exc}", time.time() - started


def probe(entries):
    rows = []
    for entry in entries:
        org = entry["issuingorganization"]
        feedname = entry.get("feedname")
        url = entry["url"]["url"] if isinstance(entry.get("url"), dict) else entry.get("url")
        status, headers, size, digest, error, elapsed = fetch(url)
        etag = headers.get("ETag")
        last_modified = headers.get("Last-Modified")
        row = {
            "publisher_key": f"{org}|{feedname}",
            "status": status,
            "bytes": size,
            "seconds": round(elapsed, 1),
            "etag": bool(etag),
            "last_modified": bool(last_modified),
            "compressed": headers.get("Content-Encoding") == "gzip",
            "honours_conditional": None,
            "error": error,
        }
        if error is None and status == 200 and (etag or last_modified):
            second, _, second_size, second_digest, second_error, _ = fetch(url, etag, last_modified)
            # 304 is the only answer that actually saves the bytes. A publisher
            # advertising a validator and then returning 200 is worse than one
            # advertising none, because the ingress model counts a saving that
            # never arrives.
            #
            # But a 200 is CORRECT when the resource genuinely changed between
            # the two requests, and these feeds change constantly. Classifying
            # every second 200 as non-compliance would conflate churn with a
            # broken validator and overstate the finding. Only an unchanged body
            # returned as 200 is evidence the publisher ignored what it sent.
            if second_error is not None:
                row["honours_conditional"] = None
            elif second == 304:
                row["honours_conditional"] = True
            elif second_digest == digest:
                row["honours_conditional"] = False
            else:
                row["honours_conditional"] = None
                row["changed_between_requests"] = True
            row["resend_bytes"] = second_size
        rows.append(row)
        flag = (
            "-"
            if row["honours_conditional"] is None
            else ("304" if row["honours_conditional"] else "200")
        )
        print(
            f"  {row['publisher_key'][:38]:40} {status:>3}  "
            f"etag={'y' if etag else 'n'} lm={'y' if last_modified else 'n'} "
            f"cond={flag:<3} {size / 1e6:6.2f} MB" + (f"  {error[:40]}" if error else "")
        )
    return rows


def summarise(rows):
    reachable = [r for r in rows if r["status"] == 200]
    with_validator = [r for r in reachable if r["etag"] or r["last_modified"]]
    honouring = [r for r in with_validator if r["honours_conditional"] is True]
    ignoring = [r for r in with_validator if r["honours_conditional"] is False]
    inconclusive = [
        r
        for r in with_validator
        if r["honours_conditional"] is None and r.get("changed_between_requests")
    ]
    total_bytes = sum(r["bytes"] for r in reachable)
    saved_bytes = sum(r["bytes"] for r in honouring)

    print(f"\n{len(rows)} probed, {len(reachable)} answered 200")
    print(f"  advertise a validator:      {len(with_validator)}")
    print(f"    honour it with a 304:     {len(honouring)}")
    print(f"    advertise but ignore it:  {len(ignoring)}")
    print(f"    changed mid-probe, unknown: {len(inconclusive)}")
    print(f"  no validator at all:        {len(reachable) - len(with_validator)}")
    if total_bytes:
        print(
            f"\n  one full sweep of the reachable set: {total_bytes / 1e6:.1f} MB"
            f"\n  conditional GET can avoid:           {saved_bytes / 1e6:.1f} MB "
            f"({100 * saved_bytes / total_bytes:.1f} percent)"
        )
    if ignoring:
        print("\n  advertise a validator and return 200 for an UNCHANGED body:")
        for r in ignoring:
            print(f"    {r['publisher_key']}")
    if inconclusive:
        print("\n  changed between the two requests, so the 200 was correct:")
        for r in inconclusive:
            print(f"    {r['publisher_key']}")
    return {
        "probed": len(rows),
        "reachable": len(reachable),
        "with_validator": len(with_validator),
        "honouring": len(honouring),
        "ignoring": len(ignoring),
        "inconclusive": len(inconclusive),
        "sweep_bytes": total_bytes,
        "avoidable_bytes": saved_bytes,
        "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--live",
        action="store_true",
        help="probe every open feed in the registry, not just the captured set",
    )
    ap.add_argument("--out", default="", help="write the full result as JSON")
    args = ap.parse_args()

    fixtures = FixtureSet()
    registry = [r for r in fixtures.registry() if r.get("active") and not r.get("needapikey")]
    if not args.live:
        captured = {e["url"] for e in fixtures.manifest["feeds"].values()}
        registry = [
            r
            for r in registry
            if (r["url"]["url"] if isinstance(r.get("url"), dict) else r.get("url")) in captured
        ]
    print(f"probing {len(registry)} publishers (2 requests each)\n")
    result = summarise(probe(registry))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
