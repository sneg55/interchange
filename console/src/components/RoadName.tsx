/**
 * A merged zone's road, and what the screening gate did to it. Spec 6.5, 6.6.
 *
 * The screening gate is the one part of this pipeline that touches a
 * publisher's free text, and it had no mark anywhere in the console. A road name
 * the gate did not pass was stored as the placeholder string and printed here
 * verbatim, sitting in a column of ordinary road names as though a publisher had
 * named a road `[redacted: failed screening]`. At one point 98.8% of the road
 * names in the live store were that placeholder.
 *
 * Redaction is stated as an absence with a cause, which is the same discipline
 * the rest of the product applies to a rule that could not be evaluated: the
 * screen says what it does not have and why, rather than printing the sentinel
 * and leaving the reader to recognise it.
 */

import type { ReactNode } from 'react'

import type { CanonicalZone } from '@/lib/types'
import { roadOf } from '@/lib/zones'

import { Term } from './legend'

export function RoadName({ zone }: { zone: CanonicalZone }): ReactNode {
  const road = roadOf(zone)
  return (
    <>
      {road.text === '' ? null : road.text}
      {road.redacted === 0 ? null : (
        <span className="badge tone-unchecked">
          <Term term="Redacted">
            {road.text === '' ? 'road name redacted' : `${String(road.redacted)} more redacted`}
          </Term>
        </span>
      )}
    </>
  )
}
