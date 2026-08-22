#!/usr/bin/env python3
"""Measure cross-publisher work zone duplication between two WZDx feeds.

Companion to wzdx_feed_health.py, which answers "is this publisher fresh and
conformant". This one answers the question a consumer of several feeds has:
how much of what I am ingesting is the same physical work zone counted twice.

The matcher is deterministic. Its distance measure is defined in wzdx/geometry.py
rather than assumed, and its corroboration checks are in wzdx/attributes.py.
Nothing here calls a model; this is the evidence the reconciler design rests on.

Usage:
    python3 scripts/wzdx_dedup_probe.py --a "New York DOT" --b "New Jersey Institute of Technology" --sample-descriptions
    python3 scripts/wzdx_dedup_probe.py --a "CivicLink" --b "Missouri DOT"          # negative control
    python3 scripts/wzdx_dedup_probe.py --a "Missouri DOT" --b "St. Charles County" # negative control

Only reads public federal data. No API keys, no writes.
"""

import argparse
import collections
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wzdx import attributes as attr
from wzdx import (
    feeds,
    geometry,
)
from wzdx.geometry import min_distance_m, spatially_matches, vertices
from wzdx.spatial_index import Grid

BANDS = (1, 10, 50, 150)


def match(fa, fb, threshold, distance_only=False):
    """Return (pairs, matched_b, rejected).

    `pairs` is [(distance_m, i, j)] for accepted matches. `rejected` is
    [(distance_m, coverage, i, j)] for pairs inside the distance threshold that
    the coverage rule threw out. The rejected list carries its distances and
    coverages rather than a bare count, because the negative control's claim is
    about those values and not about how many there were.
    """
    grid = Grid(fa, threshold)
    pairs, matched_b, rejected = [], set(), []
    considered = 0
    for j, f in enumerate(fb):
        vb = vertices(f)
        if not vb:
            continue
        for i in grid.candidates(f):
            considered += 1
            va = vertices(fa[i])
            if distance_only:
                d = min_distance_m(va, vb)
                ok, cov = d is not None and d <= threshold, None
            else:
                ok, d, cov = spatially_matches(va, vb, threshold)
                if not ok and d is not None and d <= threshold:
                    rejected.append((d, cov, i, j))
            if ok:
                pairs.append((d, i, j))
                matched_b.add(j)
    return pairs, matched_b, rejected, considered


def report_sources(fa, fb, label_a, label_b):
    """Report data_source_id composition, with coverage rather than mere presence.

    Set intersection alone would print the same line whether one feature or
    every feature declared a shared upstream, so the share of features carrying
    each ID is what gets reported.
    """
    ca = collections.Counter(attr.core(f).get("data_source_id") for f in fa)
    cb = collections.Counter(attr.core(f).get("data_source_id") for f in fb)
    shared = {s for s in set(ca) & set(cb) if s}
    print("\ndata_source_id composition:")
    for label, counter in ((label_a, ca), (label_b, cb)):
        total = sum(counter.values()) or 1
        top = ", ".join(f"{k!r} {100.0 * v / total:.1f}%" for k, v in counter.most_common(3))
        print(f"  {label}: {len(counter)} distinct, {top}")
    if shared:
        for s in sorted(shared):
            pa = 100.0 * ca[s] / (sum(ca.values()) or 1)
            pb = 100.0 * cb[s] / (sum(cb.values()) or 1)
            print(f"  shared {s!r}: {pa:.1f}% of A and {pb:.1f}% of B declare it")
        print("  duplication between these publishers is declared upstream, not inferred")
    return shared


def report_direction_coverage(fa, fb, label_a, label_b):
    """Direction usability per feed, independent of which pairs matched.

    The per-pair figure below only describes pairs that matched. A claim about a
    whole feed's direction data has to be measured over the whole feed.
    """
    print("\ndirection usability per feed:")
    for label, feats in ((label_a, fa), (label_b, fb)):
        c = collections.Counter(attr.direction(f) for f in feats)
        unknown = c.get("unknown", 0)
        total = sum(c.values()) or 1
        print(f"  {label}: {100.0 * unknown / total:.1f}% unknown "
              f"({unknown}/{total}), {len(c)} distinct values")


def report_corroboration(fa, fb, pairs):
    agree_road = agree_dir = agree_dates = dir_comparable = 0
    for _, i, j in pairs:
        if attr.road_names(fa[i]) & attr.road_names(fb[j]):
            agree_road += 1
        da, db = attr.direction(fa[i]), attr.direction(fb[j])
        if da != "unknown" and db != "unknown":
            dir_comparable += 1
            agree_dir += da == db
        if attr.ranges_overlap(attr.date_range(fa[i]), attr.date_range(fb[j])):
            agree_dates += 1
    n = len(pairs) or 1
    print("\ndeterministic corroboration over those pairs:")
    print(f"  shared normalized road name:   {agree_road} ({100.0 * agree_road / n:.1f}%)")
    print(f"  direction usable on both sides: {dir_comparable} "
          f"({100.0 * dir_comparable / n:.1f}%)"
          + (f", agreeing {agree_dir}" if dir_comparable else ""))
    print(f"  date ranges overlap:           {agree_dates} ({100.0 * agree_dates / n:.1f}%)")


def report_descriptions(fa, fb, pairs):
    sims = []
    for _, i, j in pairs:
        ta, tb = attr.description(fa[i]), attr.description(fb[j])
        if ta and tb:
            sims.append(difflib.SequenceMatcher(None, ta, tb).ratio())
    if not sims:
        print("\nno pairs carry description text on both sides")
        return
    sims.sort()
    hi = sum(1 for s in sims if s >= 0.90)
    mid = sum(1 for s in sims if 0.60 <= s < 0.90)
    print(f"\ndescription similarity over {len(sims)} pairs with text on both sides:")
    print(f"  median ratio: {sims[len(sims) // 2]:.2f}")
    print(f"  >= 0.90 near-identical: {hi} ({100.0 * hi / len(sims):.1f}%)")
    print(f"  0.60-0.89 related:      {mid} ({100.0 * mid / len(sims):.1f}%)")
    print(f"  <  0.60 unrelated text: {len(sims) - hi - mid}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="issuing organization, feed A")
    ap.add_argument("--b", required=True, help="issuing organization, feed B")
    ap.add_argument("--threshold", type=float, default=150.0, help="match threshold, metres")
    ap.add_argument("--sample-descriptions", action="store_true",
                    help="report the description-similarity distribution over matched pairs")
    ap.add_argument("--distance-only", action="store_true",
                    help="skip the symmetric-coverage rule, matching on distance alone")
    args = ap.parse_args()

    registry = feeds.active_registry()
    entry_a, dup_a, fa = feeds.load_feed(registry, args.a)
    entry_b, dup_b, fb = feeds.load_feed(registry, args.b)
    for entry, dups in ((entry_a, dup_a), (entry_b, dup_b)):
        if len(dups) > 1:
            print(f"note: registry lists {len(dups)} active entries for "
                  f"{entry['issuingorganization']!r}; probing the first", file=sys.stderr)

    null_a = sum(1 for f in fa if not vertices(f))
    null_b = sum(1 for f in fb if not vertices(f))
    print(f"A  {entry_a['issuingorganization']}: {len(fa)} features "
          f"(v{entry_a.get('version')}, {null_a} without geometry)")
    print(f"B  {entry_b['issuingorganization']}: {len(fb)} features "
          f"(v{entry_b.get('version')}, {null_b} without geometry)")

    label_a, label_b = entry_a["issuingorganization"], entry_b["issuingorganization"]
    report_sources(fa, fb, label_a, label_b)
    report_direction_coverage(fa, fb, label_a, label_b)

    pairs, matched_b, rejected, considered = match(fa, fb, args.threshold, args.distance_only)
    geo_b = len(fb) - null_b
    pct = 100.0 * len(matched_b) / geo_b if geo_b else 0.0
    rule = "distance only" if args.distance_only else "distance + symmetric coverage"
    print(f"\nthreshold {args.threshold:g} m ({rule}) over {geo_b} geometry-bearing B features")
    print(f"  B features with at least one A counterpart: {len(matched_b)} ({pct:.1f}%)")
    print(f"  grid candidate pairs examined:              {considered}")
    print(f"  matched pairs:                              {len(pairs)}")
    if not args.distance_only:
        print(f"  pairs inside {args.threshold:g} m rejected by coverage: {len(rejected)}")
        for d, cov, i, j in sorted(rejected)[:10]:
            la = geometry.length_m(vertices(fa[i]))
            lb = geometry.length_m(vertices(fb[j]))
            print(f"    rejected: distance {d:7.1f} m, symmetric coverage {cov:.2f}, "
                  f"lengths {la:.0f} m / {lb:.0f} m")
        if len(rejected) > 10:
            print(f"    ... and {len(rejected) - 10} more, not shown")

    bands = collections.Counter()
    for d, _, _ in pairs:
        for edge in BANDS:
            if d <= edge:
                bands[edge] += 1
                break
    print("  pair distance distribution:")
    for edge in BANDS:
        if edge <= args.threshold:
            share = 100.0 * bands[edge] / len(pairs) if pairs else 0.0
            print(f"    <= {edge:>3} m: {bands[edge]} ({share:.1f}% of matched pairs)")

    report_corroboration(fa, fb, pairs)
    if args.sample_descriptions:
        report_descriptions(fa, fb, pairs)


if __name__ == "__main__":
    main()
