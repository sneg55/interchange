/**
 * The projection must agree with the basemap it draws onto, or every zone is
 * quietly in the wrong place. Ground truth: Census geographic centroids of
 * three compact states, projected, must land on the path centroids of the
 * generated shapes in `us-map-data.ts`. Compact states only: a state whose
 * geographic centroid sits in water (Michigan) or whose largest ring is a
 * fraction of it (Maryland) would test the shape of the state, not the
 * projection.
 *
 * Run with: npm test
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { projectAlbersUsa } from './albers'
import { US_STATES } from './us-map-data'

/** Census geographic centroids (lon, lat), from the 2020 gazetteer. */
const CENTROIDS: Record<string, [number, number]> = {
  CO: [-105.5478, 38.9986],
  KS: [-98.3804, 38.4985],
  UT: [-111.6703, 39.3055],
}

void test('projected geographic centroids land on the basemap path centroids', () => {
  for (const [id, [lon, lat]] of Object.entries(CENTROIDS)) {
    const shape = US_STATES.find((s) => s.id === id)
    assert.notEqual(shape, undefined)
    if (shape === undefined) continue
    const [x, y] = projectAlbersUsa(lon, lat)
    const off = Math.hypot(x - shape.cx, y - shape.cy)
    assert.ok(
      off < 8,
      `${id}: projected (${x.toFixed(1)}, ${y.toFixed(1)}) is ${off.toFixed(1)} map units from path centroid (${shape.cx}, ${shape.cy})`,
    )
  }
})
