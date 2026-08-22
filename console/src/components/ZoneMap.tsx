/**
 * Figure 2: the merged zones drawn on their geometry. Spec 6.9.
 *
 * The reconciliation screen said "map plus provenance panel" in its own
 * docstring and rendered only the table, so the one screen about roads never
 * drew one. This draws exactly the rows the table lists (same filters, same
 * cap, already stated on the section), as ink line work over the same Census
 * basemap Figure 1 uses: zone coordinates go through the identical Albers
 * projection, verified against the basemap in `albers.test.ts`.
 *
 * Selecting a zone here is the same selection as clicking its row: one state,
 * so the map, the table and the provenance panel can never disagree about
 * which zone is open. The map is a POINTER affordance only; the keyboard path
 * to selection is the table's row buttons, which cover the identical set, so
 * 250 zone paths do not become 250 tab stops.
 *
 * Anything undrawable (no geometry, or a type this does not render) is
 * counted in the caption, never silently missing from the figure.
 */

'use client'

import type { ReactNode } from 'react'
import { useMemo } from 'react'

import { projectAlbersUsa } from '@/lib/albers'
import type { CanonicalZone } from '@/lib/types'
import { US_MAP_VIEWBOX, US_STATES } from '@/lib/us-map-data'
import { decodeGeometry, roadText } from '@/lib/zones'

interface DrawnZone {
  id: string
  d: string
  title: string
}

interface Fitted {
  drawn: DrawnZone[]
  undrawable: number
  viewBox: string
  /** viewBox width over height, so the element can take the same shape. */
  aspect: number
}

const r2 = (n: number) => Math.round(n * 100) / 100

/** One projected "M...L..." run per coordinate line. */
function linePath(line: unknown, extend: (x: number, y: number) => void): string {
  if (!Array.isArray(line)) return ''
  let d = ''
  for (const position of line) {
    if (
      !Array.isArray(position) ||
      typeof position[0] !== 'number' ||
      typeof position[1] !== 'number'
    ) {
      continue
    }
    const [x, y] = projectAlbersUsa(position[0], position[1])
    extend(x, y)
    d += `${d === '' ? 'M' : 'L'}${r2(x)} ${r2(y)}`
  }
  return d
}

function zonePath(
  geometry: { type: string; coordinates: unknown } | null,
  extend: (x: number, y: number) => void,
): string | null {
  if (geometry === null) return null
  const c = geometry.coordinates
  switch (geometry.type) {
    case 'LineString':
      return linePath(c, extend) || null
    case 'MultiLineString':
      return Array.isArray(c) ? c.map((line) => linePath(line, extend)).join('') || null : null
    case 'Point':
      // A dot, as a zero-length stroke with a round cap.
      return linePath([c, c], extend) || null
    case 'MultiPoint':
      return Array.isArray(c) ? c.map((p) => linePath([p, p], extend)).join('') || null : null
    default:
      return null
  }
}

function fit(zones: readonly CanonicalZone[]): Fitted {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  const extend = (x: number, y: number): void => {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  const drawn: DrawnZone[] = []
  let undrawable = 0
  for (const zone of zones) {
    // Through the storage contract's decode: `coordinates` arrives as a JSON
    // string for any nested shape (spec 7), so the raw field never draws.
    const d = zonePath(decodeGeometry(zone.geometry), extend)
    if (d === null) {
      undrawable += 1
      continue
    }
    const road = roadText(zone)
    drawn.push({
      id: zone.canonical_id,
      d,
      title: `${road === '' ? zone.canonical_id : road} · ${zone.sources.length} sources`,
    })
  }
  if (drawn.length === 0) {
    return { drawn, undrawable, viewBox: US_MAP_VIEWBOX, aspect: 975 / 610 }
  }
  // Pad the fitted box, and hold a floor on its size: a single short zone
  // zoomed to fill the frame is a line with no geography left to read.
  const spanX = Math.max(maxX - minX, 90)
  const spanY = Math.max(maxY - minY, 60)
  const pad = Math.max(24, 0.08 * Math.max(spanX, spanY))
  let w = spanX + 2 * pad
  let h = spanY + 2 * pad
  // Bound the box's shape, then hand that exact shape to the element. The
  // live data is a tall narrow corridor (New Jersey), and a corridor-shaped
  // viewBox inside a sheet-wide element letterboxed into half the eastern
  // seaboard: the frame showed geography the data never touches, which reads
  // as a map of nothing. Widening is symmetric about the data's centre, so
  // the added ground is the zones' own neighbourhood.
  if (w / h < 1.3) w = 1.3 * h
  if (w / h > 2.1) h = w / 2.1
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  return {
    drawn,
    undrawable,
    viewBox: `${r2(cx - w / 2)} ${r2(cy - h / 2)} ${r2(w)} ${r2(h)}`,
    aspect: w / h,
  }
}

export function ZoneMap({
  zones,
  selected,
  onSelect,
}: {
  /** Exactly the rows the table lists: same filters, same stated cap. */
  zones: readonly CanonicalZone[]
  selected: string | null
  onSelect: (id: string) => void
}): ReactNode {
  const { drawn, undrawable, viewBox, aspect } = useMemo(() => fit(zones), [zones])
  if (zones.length === 0 || drawn.length === 0) return null
  const selectedZone = drawn.find((z) => z.id === selected)
  return (
    <figure className="fig">
      <svg
        className="zonemap"
        viewBox={viewBox}
        // The element takes the viewBox's own shape, capped by height. Without
        // this the SVG spanned the sheet at a clamped height and the mismatch
        // filled the frame with map far outside the fitted box.
        style={{
          aspectRatio: String(r2(aspect)),
          maxWidth: `${String(r2(30 * aspect))}rem`,
        }}
        role="img"
        aria-label={`Map of ${drawn.length} merged zones drawn on their geometry. Selection is also available from the table below.`}
      >
        {US_STATES.map((s) => (
          <path key={s.id} d={s.d} className="zonemap-state" vectorEffect="non-scaling-stroke" />
        ))}
        {drawn.map((z) => (
          <path
            key={z.id}
            d={z.d}
            className={z.id === selected ? 'zone zone-selected' : 'zone'}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {/* The selected zone's ink again, over its neighbours. */}
        {selectedZone === undefined ? null : (
          <path
            d={selectedZone.d}
            className="zone zone-selected"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {/* Hit strokes last, wide and invisible: a 1.75px line is not a
            target. aria-hidden, because the table is the accessible route to
            the same selection. */}
        {drawn.map((z) => (
          <path
            key={`hit-${z.id}`}
            d={z.d}
            className="zone-hit"
            vectorEffect="non-scaling-stroke"
            aria-hidden="true"
            onClick={() => {
              onSelect(z.id)
            }}
          >
            <title>{z.title}</title>
          </path>
        ))}
      </svg>
      <figcaption className="fig-caption">
        <span className="apparatus">Fig. 2</span>{' '}
        <span>
          The merged zones listed below, drawn on their geometry. Selecting a zone opens its
          provenance, exactly as selecting its row does.
          {undrawable === 0
            ? ''
            : ` ${undrawable} of the ${zones.length} listed carry no drawable geometry and appear in the table only.`}
        </span>
      </figcaption>
    </figure>
  )
}
