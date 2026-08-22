"""Uniform grid index over work zone geometries. Section 6.6.

Separate from geometry.py because it answers a different question. geometry.py
computes how far apart two features are; this decides which pairs are worth
asking about at all. Its only contract is a negative one, and the whole design
follows from it:

    if two features are within `cell` metres, candidates() MUST return the pair

A false positive here costs one distance computation. A false negative silently
drops a duplicate and no downstream code can recover it, so every choice below
is biased toward returning too much.

Two earlier versions violated that contract in ways worth recording, because
both looked correct and both passed casual testing:

  - Projecting each vertex about its own latitude is not a coordinate system.
    Two points 141 m apart were displaced 184 m in x and shared no cell.
  - Unwrapping longitudes about the first indexed feature put the antimeridian
    wherever that feature happened to fall. Points 44 m apart across it landed
    360 degrees apart, and reordering the input changed the result.

Ported from `scripts/wzdx/spatial_index.py` (spec 16, prior-art tooling).
"""

from __future__ import annotations

import collections
import math
from itertools import pairwise
from typing import Any

from .geometry import EARTH_R, Vertex, to_metres, unwrap, vertices


class Grid:
    """Uniform grid keyed at the match threshold, wrapping correctly in longitude.

    Cells are indexed by (column, row). Columns wrap modulo a full revolution so
    the antimeridian is not a seam, and the standard parallel is never clamped
    below the data so a cell is never narrower than `cell` metres in true
    distance. Together those make the 3x3 neighbourhood scan a genuine upper
    bound on what the index can miss.
    """

    def __init__(self, features: list[dict[str, Any]], cell: float) -> None:
        self.cell = cell
        all_verts = [vertices(f) for f in features]
        flat = [v for vs in all_verts for v in vs]
        # A clamp below the data would make cos(lat0) > cos(lat), so projected x
        # would exceed true x and cells would be narrower than `cell` in true
        # metres. Two points 3.9 m apart at 89.999 degrees landed two cells
        # apart under an 89.9 clamp, which is exactly the false negative this
        # class exists to prevent.
        max_lat = max((abs(v[1]) for v in flat), default=0.0)
        self.lat0 = min(89.9999, max_lat + 1.0)
        revolution = EARTH_R * math.radians(360.0) * math.cos(math.radians(self.lat0))
        self.columns = max(1, math.ceil(revolution / cell))
        self.cells = collections.defaultdict(set)
        for i, verts in enumerate(all_verts):
            for key in self._keys(verts):
                self.cells[key].add(i)

    def _key(self, lon: float, lat: float) -> tuple[int, int]:
        """Cell for one position. Column wraps; row does not, latitude being bounded."""
        x, y = to_metres(lon, lat, self.lat0)
        return math.floor(x / self.cell) % self.columns, math.floor(y / self.cell)

    def _keys(self, verts: list[Vertex]) -> set[tuple[int, int]]:
        """Every cell the geometry touches, walking segments at half-cell steps.

        Indexing only vertices leaves a long segment absent from every cell in
        between: Missouri DOT publishes 33 km LineStrings with sparse vertices,
        and a zone in the middle of one was never a candidate.
        """
        if not verts:
            return set()
        lons = unwrap([v[0] for v in verts], verts[0][0])
        positions = list(zip(lons, (v[1] for v in verts), strict=True))
        walked = list(positions)
        step = self.cell / 2.0
        for (alon, alat), (blon, blat) in pairwise(positions):
            ax, ay = to_metres(alon, alat, self.lat0)
            bx, by = to_metres(blon, blat, self.lat0)
            dist = math.hypot(bx - ax, by - ay)
            for k in range(1, int(dist / step) + 1):
                t = (k * step) / dist
                walked.append((alon + (blon - alon) * t, alat + (blat - alat) * t))
        return {self._key(lon, lat) for lon, lat in walked}

    def candidates(self, feature: dict[str, Any]) -> set[int]:
        out: set[int] = set()
        for cx, cy in self._keys(vertices(feature)):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    out |= self.cells.get(((cx + dx) % self.columns, cy + dy), frozenset())
        return out
