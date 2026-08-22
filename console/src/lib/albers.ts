/**
 * The projection the basemap is already in, so zone geometry can be drawn on
 * it.
 *
 * `us-map-data.ts` is us-atlas geometry, pre-projected with d3's
 * `geoAlbersUsa().scale(1300).translate([487.5, 305])` into a 975x610 frame.
 * This is that projection's lower-48 component (Albers equal-area conic,
 * standard parallels 29.5 and 45.5, rotated to 96 degrees west, centered at
 * [-0.6, 38.7]) implemented directly, so a WZDx longitude/latitude lands on
 * the same pixel the Census state outline occupies. Verified against the
 * basemap in `albers.test.ts`: projected geographic state centroids fall on
 * the path centroids of the generated shapes.
 *
 * Lower 48 only, deliberately: the projection's Alaska and Hawaii insets are
 * separate scaled projections, and no merged zone can occur there today
 * (Hawaii has one publisher; a merge needs two). A coordinate outside the
 * frame still projects, it just lands off the basemap, and the caller counts
 * what it draws rather than assuming.
 */

const RAD = Math.PI / 180

/** Standard parallels 29.5 / 45.5 degrees. */
const N = (Math.sin(29.5 * RAD) + Math.sin(45.5 * RAD)) / 2
const C = Math.cos(29.5 * RAD) ** 2 + 2 * N * Math.sin(29.5 * RAD)
const RHO_0 = Math.sqrt(C) / N

const SCALE = 1300
const TRANSLATE_X = 487.5
const TRANSLATE_Y = 305

/** The raw conic, in rotated coordinates (longitude already shifted +96). */
function raw(lambda: number, phi: number): [number, number] {
  const gamma = N * lambda
  const rho = Math.sqrt(C - 2 * N * Math.sin(phi)) / N
  return [rho * Math.sin(gamma), RHO_0 - rho * Math.cos(gamma)]
}

/** d3's center([-0.6, 38.7]), in the rotated frame. */
const [CENTER_X, CENTER_Y] = raw(-0.6 * RAD, 38.7 * RAD)

/** Longitude/latitude in degrees to the basemap's 975x610 frame. */
export function projectAlbersUsa(lon: number, lat: number): [number, number] {
  const [x, y] = raw((lon + 96) * RAD, lat * RAD)
  return [TRANSLATE_X + SCALE * (x - CENTER_X), TRANSLATE_Y - SCALE * (y - CENTER_Y)]
}
