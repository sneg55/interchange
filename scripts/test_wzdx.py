#!/usr/bin/env python3
"""Regression tests for the probe geometry, index and parsing.

Every test here corresponds to a defect that was actually shipped and found by
review, not to a hypothetical. The comment on each names what broke, because a
test whose purpose is forgotten gets deleted the next time it is inconvenient.

Run offline, no network:
    python3 scripts/test_wzdx.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wzdx.attributes import parse_stamp
from wzdx.geometry import (
    densify,
    min_distance_m,
    spatially_matches,
    symmetric_coverage,
)
from wzdx.spatial_index import Grid

FAILURES = []


def check(name, got, want, tol=None):
    ok = abs(got - want) <= tol if tol is not None else got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILURES.append(name)


def ls(coords):
    return {"geometry": {"type": "LineString", "coordinates": [list(p) for p in coords]}}


def mp(point):
    return {"geometry": {"type": "MultiPoint", "coordinates": [list(point)]}}


def test_distance():
    print("min_distance_m")
    # Shipped bug: vertex-to-segment minimum reported 2,554 m for geometries
    # that intersect, because it never tested segment crossing.
    a = [(-74.00, 40.00), (-74.00, 40.05)]
    b = [(-74.03, 40.025), (-73.97, 40.025)]
    check("crossing LineStrings are 0 m", min_distance_m(a, b), 0.0)
    check("touching endpoints are 0 m",
          min_distance_m([(-74.0, 40.0), (-74.0, 40.01)],
                         [(-74.0, 40.01), (-74.0, 40.02)]), 0.0)
    check("collinear disjoint is the gap",
          min_distance_m([(-74.0, 40.0), (-74.0, 40.01)],
                         [(-74.0, 40.02), (-74.0, 40.03)]), 1112.0, tol=2.0)
    check("point on a line is 0 m",
          min_distance_m([(-74.0, 40.025)], a), 0.0, tol=1e-6)
    # Shipped bug: 30,000 km reported across the antimeridian.
    check("antimeridian pair is metres",
          min_distance_m([(179.999, 40.0), (179.9995, 40.0)],
                         [(-179.9995, 40.0), (-179.999, 40.0)]), 85.0, tol=5.0)
    check("empty geometry returns None", min_distance_m([], [(-74.0, 40.0)]), None)


def test_grid():
    print("Grid")
    # Shipped bug: projecting each vertex about its own latitude displaced two
    # points 141 m apart by 184 m in x, so they shared no cell.
    p1, p2 = (-75.0, 40.0), (-74.9988260198, 40.0008993210)
    check("141 m apart at differing latitudes are candidates",
          Grid([mp(p1)], 150.0).candidates(mp(p2)), {0})
    # Shipped bug: unwrapping about the first indexed longitude put the
    # antimeridian wherever that feature fell, and reordering changed the answer.
    near = [mp((0.0, 0.0)), mp((179.9998, 0.0))]
    check("across the antimeridian, first ordering",
          Grid(near, 150.0).candidates(mp((-179.9998, 0.0))), {1})
    check("across the antimeridian, reversed ordering",
          Grid(list(reversed(near)), 150.0).candidates(mp((-179.9998, 0.0))), {0})
    # Shipped bug: clamping lat0 to 89.9 made cells narrower than the threshold
    # above the cap, so near-polar neighbours were two cells apart.
    check("near-polar neighbours are candidates",
          Grid([mp((0.0, 89.999))], 150.0).candidates(mp((2.0, 89.999))), {0})
    # Shipped bug: indexing only vertices left long sparse segments absent from
    # every cell between their endpoints.
    check("point mid-way along a 34 km 2-vertex corridor",
          Grid([ls([(-74.20, 40.0), (-73.80, 40.0)])], 150.0).candidates(mp((-74.0, 40.0001))),
          {0})
    check("far-away feature is not a candidate",
          Grid([mp((-74.0, 40.0))], 150.0).candidates(mp((-10.0, 10.0))), set())


def test_densify():
    print("densify and coverage")
    # Shipped bug: sampling restarted per segment, so identical geometry scored
    # differently purely by how finely it was encoded.
    plain = [(-74.0, 40.0), (-74.0, 40.009)]
    dense = [(-74.0, 40.0)] + [(-74.0, 40.0054 + 0.0036 * k / 400) for k in range(401)]
    target = [(-74.0, 40.0), (-74.0, 40.0054)]
    check("coverage is independent of vertex density",
          round(symmetric_coverage(plain, target, 150.0), 6),
          round(symmetric_coverage(dense, target, 150.0), 6))
    # Shipped bug: the endpoint was sampled and then appended again, so a line
    # whose length is an exact multiple of the step carried a duplicate.
    exact = densify([(-74.0, 40.0), (-74.0, 40.0008993)], step=25.0)
    check("no duplicated endpoint on an exact multiple",
          exact.count(exact[-1]), 1)
    check("single vertex survives", len(densify([(-74.0, 40.0)])), 1)
    check("identical points collapse", len(densify([(-74.0, 40.0), (-74.0, 40.0)])), 1)
    try:
        densify(plain, step=0)
        check("step=0 raises", "no raise", "ValueError")
    except ValueError:
        check("step=0 raises", "ValueError", "ValueError")


def test_matching():
    print("spatially_matches")
    # The live negative control: St. Charles County's 4.8 km ramp closure lies
    # on top of Missouri DOT's 33 km corridor project. Zero distance, different
    # zones. Coverage must reject it.
    ramp = [(-90.70, 38.80), (-90.66, 38.80)]
    corridor = [(-91.00, 38.80), (-90.40, 38.80)]
    ok, dist, cov = spatially_matches(ramp, corridor, 150.0)
    check("long corridor containing a short ramp does not match", ok, False)
    check("  and its distance really is ~0", dist, 0.0, tol=1.0)
    check("  rejected on coverage, not distance", cov is not None and cov < 0.6, True)
    near = [(-74.0, 40.0)], [(-74.0, 40.0001)]
    check("two nearby points match", spatially_matches(*near, 150.0)[0], True)


def test_parse_stamp():
    print("parse_stamp")
    # Shipped bug: Utah serves seven fractional digits, which fromisoformat
    # rejects before Python 3.11, so the documented command failed elsewhere.
    check("seven fractional digits",
          parse_stamp("2023-03-19T07:04:04.8614897-06:00").microsecond, 861489)
    check("trailing Z", parse_stamp("2024-02-22T19:32:06Z").tzinfo is not None, True)
    check("lowercase t and z",
          parse_stamp("2024-02-22t19:32:06z").hour, 19)
    check("numeric offset without colon",
          parse_stamp("2024-02-22T19:32:06+0530").utcoffset().total_seconds(), 19800.0)
    for bad, why in [("2026-08-06", "date only"),
                     ("2026-08-06T06:00:42", "no offset"),
                     ("not a date", "garbage"),
                     (None, "non-string"),
                     (12345, "integer")]:
        try:
            parse_stamp(bad)
            check(f"rejects {why}", "accepted", "ValueError")
        except ValueError:
            check(f"rejects {why}", "ValueError", "ValueError")


def main():
    for fn in (test_distance, test_grid, test_densify, test_matching, test_parse_stamp):
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all regression tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
