"""Geometry for cross-publisher work zone matching.

Defines the distance measure the reconciler gates on. The choice matters and
is not arbitrary:

  - Live feeds use LineString and MultiPoint, and MultiPoint carries both
    single points (New York DOT, NJIT) and two-point segments (Iowa DOT,
    Mississippi DOT). Neither type can be assumed.
  - Distance between two features is the MINIMUM distance between their
    geometries, not the distance between centroids. Centroid distance is wrong
    for long LineStrings: two publishers describing the same kilometre of
    roadway with differently clipped extents have centroids far apart while
    the zones plainly coincide.
  - Coordinates project to local metres (equirectangular about the mean
    latitude of the pair) before segment math. Under a few kilometres the
    error against the true geodesic is below a tenth of a percent, immaterial
    at a 150 m threshold.
"""

import math
from itertools import pairwise

EARTH_R = 6371008.8  # mean Earth radius, metres


def vertices(feature):
    """Reduce any WZDx geometry to a list of (lon, lat) vertices.

    Returns [] for null geometry, which occurs in the wild: Quebec City serves
    four such features. A feature with no geometry cannot be matched spatially
    and must be counted separately rather than silently dropped.
    """
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if not coords:
        return []
    kind = geom.get("type")
    if kind in ("LineString", "MultiPoint"):
        return [(c[0], c[1]) for c in coords
                if isinstance(c, (list, tuple)) and len(c) >= 2]
    if kind == "Point":
        return [(coords[0], coords[1])]
    return []


def to_metres(lon, lat, lat0=None):
    """Project to local metres about the standard parallel `lat0`.

    `lat0` defaults to the point's own latitude, which is right for a single
    isolated measurement and WRONG for anything that compares two points: the
    x scale then differs per point and the result is not a coordinate system.
    Every caller comparing positions must pass an explicit shared `lat0`.
    """
    k = math.cos(math.radians(lat if lat0 is None else lat0))
    return EARTH_R * math.radians(lon) * k, EARTH_R * math.radians(lat)


def unwrap(lons, reference):
    """Shift longitudes into the same branch as `reference`.

    Without this, two points 140 m apart either side of the antimeridian
    project 30,000 km apart. No feed in the current registry crosses it, but a
    distance function that is catastrophically wrong on a whole meridian is not
    something to leave in place because today's data avoids it.
    """
    return [lon - 360.0 * round((lon - reference) / 360.0) for lon in lons]


def _projected(verts, lat0, reference_lon):
    lons = unwrap([v[0] for v in verts], reference_lon)
    return [to_metres(lon, v[1], lat0) for lon, v in zip(lons, verts, strict=True)]


def _point_seg_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _seg_seg_dist(a, b, c, d):
    """Exact minimum distance between segments ab and cd.

    Crossing segments are the case a vertex-to-segment minimum gets wrong, and
    gets wrong by a lot: two intersecting road geometries are zero metres apart
    while their nearest vertices can be kilometres apart. Intersection is tested
    first, and only non-intersecting pairs fall through to the endpoint minima.
    """
    d1, d2 = _cross(c, d, a), _cross(c, d, b)
    d3, d4 = _cross(a, b, c), _cross(a, b, d)
    collinear = d1 == 0 and d2 == 0 and d3 == 0 and d4 == 0
    if not collinear and d1 * d2 <= 0 and d3 * d4 <= 0:
        return 0.0
    return min(_point_seg_dist(a, c, d), _point_seg_dist(b, c, d),
               _point_seg_dist(c, a, b), _point_seg_dist(d, a, b))


def min_distance_m(va, vb):
    """Exact minimum distance in metres between two geometries, or None if either is empty.

    Handles point/point, point/polyline and polyline/polyline. Segment pairs are
    tested for intersection rather than approximated from vertices, because an
    earlier vertex-only version reported 2,554 m for two LineStrings that cross,
    where the true distance is zero.
    """
    if not va or not vb:
        return None
    lat0 = (sum(v[1] for v in va) / len(va) + sum(v[1] for v in vb) / len(vb)) / 2
    ref_lon = va[0][0]
    pa = _projected(va, lat0, ref_lon)
    pb = _projected(vb, lat0, ref_lon)
    if len(pa) == 1 and len(pb) == 1:
        return math.hypot(pa[0][0] - pb[0][0], pa[0][1] - pb[0][1])
    if len(pa) == 1:
        return min(_point_seg_dist(pa[0], c, d) for c, d in pairwise(pb))
    if len(pb) == 1:
        return min(_point_seg_dist(pb[0], a, b) for a, b in pairwise(pa))
    best = float("inf")
    for a, b in pairwise(pa):
        for c, d in pairwise(pb):
            best = min(best, _seg_seg_dist(a, b, c, d))
            if best == 0.0:
                return 0.0
    return best


def length_m(verts):
    if len(verts) < 2:
        return 0.0
    lat0 = sum(v[1] for v in verts) / len(verts)
    pts = _projected(verts, lat0, verts[0][0])
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(pts))


def densify(verts, step=25.0):
    """Resample a polyline every `step` metres of CUMULATIVE arc length.

    Sampling per segment, restarting at each vertex, is not length weighting: it
    emits at least one sample per segment regardless of that segment's length,
    so identical geometry scores differently depending only on how finely the
    publisher encoded it. Measured on a 1,000 m line against its first 600 m,
    a two-vertex encoding scored 0.73 and the same line with 400 extra vertices
    scored 0.41. Publishers vary enormously here, so the walk has to cross
    vertex boundaries rather than reset at them.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    if len(verts) < 2:
        return list(verts)
    lat0 = sum(v[1] for v in verts) / len(verts)
    # Interpolate in UNWRAPPED longitude. Measuring segment lengths on unwrapped
    # coordinates while interpolating on raw ones sent a line crossing the
    # antimeridian through the far side of the planet: successive samples on a
    # 22 km line came out 8,995 km apart.
    lons = unwrap([v[0] for v in verts], verts[0][0])
    flat = list(zip(lons, (v[1] for v in verts), strict=True))
    pts = [to_metres(lon, lat, lat0) for lon, lat in flat]
    seg_len = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(pts)]
    total = sum(seg_len)
    if total == 0:
        return [verts[0]]
    out, seg, walked = [], 0, 0.0
    target = 0.0
    # Strictly less than total, so the endpoint is appended exactly once. Sampling
    # at target == total and then appending the endpoint anyway duplicated it, and
    # on a line whose length is an exact multiple of step that one duplicate
    # skewed the coverage ratio enough to reject a boundary-valid match.
    while target < total:
        while seg < len(seg_len) and walked + seg_len[seg] < target:
            walked += seg_len[seg]
            seg += 1
        if seg >= len(seg_len):
            break
        t = (target - walked) / seg_len[seg] if seg_len[seg] else 0.0
        a, b = flat[seg], flat[seg + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        target += step
    out.append(flat[-1])
    return out


def coverage(va, vb, threshold):
    """Fraction of va's length lying within `threshold` metres of vb."""
    sample = densify(va)
    if not sample:
        return 0.0
    return sum(1 for p in sample if (min_distance_m([p], vb) or 0) <= threshold) / len(sample)


def symmetric_coverage(va, vb, threshold):
    return min(coverage(va, vb, threshold), coverage(vb, va, threshold))


# Below this length a feature is treated as a point and judged on distance alone.
POINT_LENGTH_M = 50.0
# Fraction of BOTH features' lengths that must coincide for a polyline match.
MIN_SYMMETRIC_COVERAGE = 0.6


def spatially_matches(va, vb, threshold):
    """The reconciler's spatial predicate. Returns (matched, distance, coverage).

    Minimum distance alone is sufficient for point-like features and actively
    wrong for long polylines: St. Charles County's 4.8 km ramp closure lies
    0.1 m from Missouri DOT's 33 km pavement project because the corridor runs
    straight through it, yet they are plainly different work zones. Requiring
    that a substantial fraction of BOTH features coincide rejects that pair
    (symmetric coverage 0.04) while leaving point-to-point matching untouched.
    """
    d = min_distance_m(va, vb)
    if d is None or d > threshold:
        return False, d, None
    if length_m(va) < POINT_LENGTH_M and length_m(vb) < POINT_LENGTH_M:
        return True, d, None
    cov = symmetric_coverage(va, vb, threshold)
    return cov >= MIN_SYMMETRIC_COVERAGE, d, cov
