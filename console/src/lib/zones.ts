/**
 * How a merged zone is read: its road, and whether that road name survived
 * screening. Spec 6.5, 6.6.
 *
 * Split out of `ReconciliationView.tsx` so both halves can be tested without a
 * renderer. Both existed inline and both were wrong in the same way: they
 * printed a stored value verbatim and left the reader to work out what it was.
 */

import type { CanonicalZone } from './types'

/**
 * What the screening gate writes in place of text it did not pass.
 *
 * Mirrors `REDACTION_PLACEHOLDER` in `src/services/screeners.py`. It is a
 * sentinel, not a road name, and the reconciliation table rendered it as one
 * among ordinary road names. At one point 98.8% of road names in the live store
 * were this string and no screen said so once.
 */
export const REDACTION_PLACEHOLDER = '[redacted: failed screening]'

export interface RoadLabel {
  /** What to print. Empty when every name was redacted. */
  text: string
  /** How many of this zone's road names the screening gate did not pass. */
  redacted: number
  /** Distinct names beyond the first, for a zone whose sources disagree. */
  alternatives: number
}

/**
 * A human-readable anchor for a canonical zone, deduplicated.
 *
 * A merged zone accumulates a road name per source, so a zone claimed by two
 * publishers that agree rendered as `Tices Ln, Tices Ln`, which reads as a
 * duplication bug rather than as agreement. Distinct names are kept, because a
 * disagreement between two publishers about what road this is is exactly the
 * thing this screen exists to show; identical ones are said once.
 *
 * Case- and space-insensitive on the comparison only. `TICES LN` and `Tices Ln`
 * are the same road, and the first spelling is the one printed.
 */
export function roadOf(zone: CanonicalZone): RoadLabel {
  const raw = zone.core_details.road_names
  const names = Array.isArray(raw) ? raw.map(String) : []
  const kept: string[] = []
  const seen = new Set<string>()
  let redacted = 0
  for (const name of names) {
    if (name === REDACTION_PLACEHOLDER) {
      redacted += 1
      continue
    }
    const key = name.trim().toLowerCase()
    if (key === '' || seen.has(key)) continue
    seen.add(key)
    kept.push(name.trim())
  }
  if (kept.length > 0) {
    return { text: kept.join(' / '), redacted, alternatives: kept.length - 1 }
  }
  if (redacted > 0) return { text: '', redacted, alternatives: 0 }
  // No name at all. Direction is the only other thing on a zone a reader can
  // recognise, and `unknown` is a stored value rather than a direction.
  const direction = zone.core_details.direction
  const text =
    typeof direction === 'string' && direction !== 'unknown' && direction !== ''
      ? direction
      : 'unnamed'
  return { text, redacted: 0, alternatives: 0 }
}

/** The plain string, for search and for anything that cannot render a label. */
export function roadText(zone: CanonicalZone): string {
  return roadOf(zone).text
}

/**
 * Geometry as GeoJSON again, decoded from the storage contract.
 *
 * Firestore cannot store an array whose elements are arrays, so spec section 7
 * makes the encoding explicit: any array-of-arrays is written as a JSON string
 * (`encode_for_firestore` in `src/services/firestore_store.py`, mirrored by the
 * emulator seeder). `coordinates` is therefore a string for a LineString and a
 * plain array only for shapes with no nesting, and until the console drew
 * geometry nothing here decoded it. A string that does not parse returns null
 * rather than a half-decoded shape, and the caller counts it as undrawable.
 */
export function decodeGeometry(
  geometry: { type: string; coordinates: unknown } | null,
): { type: string; coordinates: unknown } | null {
  if (geometry === null) return null
  const coordinates = geometry.coordinates
  if (typeof coordinates !== 'string') return geometry
  try {
    return { type: geometry.type, coordinates: JSON.parse(coordinates) as unknown }
  } catch {
    return null
  }
}

/** The merge tiers a reader can filter this screen by. Spec 6.6. */
export const MERGE_TIERS = ['TIER_1_DETERMINISTIC', 'TIER_2_ADJUDICATED'] as const

export type MergeTier = (typeof MERGE_TIERS)[number]

/** How a tier reads in a filter control, in the words the table's badges use. */
export const TIER_WORD: ReadonlyMap<string, string> = new Map([
  ['TIER_1_DETERMINISTIC', 'same upstream source'],
  ['TIER_2_ADJUDICATED', 'adjudicated'],
])

/**
 * Whether any source of this zone was merged on the given tier.
 *
 * Any, not the first. The table badges `sources[0]`, which is enough for a
 * column and not enough for a filter: a zone whose second source was the
 * adjudicated one would be filtered out of the very view an operator opened to
 * find adjudicated merges.
 */
export function mergedOn(zone: CanonicalZone, tier: string): boolean {
  return zone.sources.some((s) => s.merge_tier === tier)
}
