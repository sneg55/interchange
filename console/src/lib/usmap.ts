/**
 * Resolves a publisher's declared `us_state` onto the map's geometry.
 *
 * The registry field is free text. The live capture holds lowercase full names
 * ("colorado"), one capitalised name ("Illinois"), the literal strings "n/a"
 * and "nps", and null; test fixtures use USPS codes ("UT"). A resolver that
 * only matched one spelling would silently drop publishers from the figure,
 * which is the unlabelled-truncation failure this product exists to catch, so
 * anything unresolvable is returned as null for the caller to LIST, not lose.
 */

import { type StateShape, US_STATES } from './us-map-data'

const BY_ID = new Map(US_STATES.map((s) => [s.id, s]))
const BY_NAME = new Map(US_STATES.map((s) => [s.name.toLowerCase(), s]))

export function resolveState(usState: string | null): StateShape | null {
  if (usState === null) return null
  const raw = usState.trim()
  if (raw.length === 2) return BY_ID.get(raw.toUpperCase()) ?? null
  return BY_NAME.get(raw.toLowerCase()) ?? null
}
