/**
 * Why this publisher is where it is, in one sentence, under the badge. Spec 6.4.
 *
 * The page carried every fact and joined none of them. Utah DOT read
 * `QUARANTINE` as a badge in the top right, `LATCHING R2, R4` in the fourth row
 * of a key-value table, a feed timestamp 1,238 days old in the sixth, and
 * `744 of 744` contradictory zones in the seventh. Nothing said that the sixth
 * and seventh rows were what R2 and R4 mean, so reading the page required
 * already knowing the ruleset it is written in.
 *
 * The sentence itself is not new. `assertsFor` is what the notice queue's
 * Asserts column prints and what goes into the outbound notice, so the operator
 * reads the same words here, in the queue, and in the document they approve.
 * One source, three screens; two copies of this sentence have drifted apart in
 * this product before.
 */

import type { ReactNode } from 'react'

import { assertsFor } from '@/lib/glossary'
import type { FleetState, PublisherRecord } from '@/lib/types'

const OPENING = new Map<FleetState, string>([
  ['QUARANTINE', 'Quarantined, so none of its zones reach the merged feed, because'],
  ['WATCH', 'On watch, still contributing to the merged feed, because'],
])

export function WhyThisState({ record }: { record: PublisherRecord }): ReactNode {
  if (record.fleet_state === 'NO_ACCESS') {
    return (
      <p className="empty">
        Behind an API key Interchange does not hold, so it has never been polled. This is not a
        trust verdict: nothing here has passed and nothing has failed, and this publisher is left
        out of coverage denominators rather than counted either way.
      </p>
    )
  }
  const latching = record.latching_rule_ids
  if (latching.length === 0) {
    // ADMIT, or a state whose cause has already retired. Said rather than left
    // blank: an empty space under a badge reads as a screen that has not
    // finished loading, not as "no rule is holding this publisher back".
    return (
      <p className="empty">
        No rule is currently holding this publisher back. Every rule that could be evaluated on its
        recent polls passed; a rule that could not be evaluated is recorded as unchecked rather than
        as a pass.
      </p>
    )
  }
  const opening = OPENING.get(record.fleet_state)
  return (
    <p className="empty">
      {opening ?? 'Held out of Admit because'} {assertsFor(latching)}.{' '}
      {/* The way out, not only the way in. `clean streak 0` appeared in the
          table below with nothing saying what it counted toward. */}
      {record.clean_poll_streak === 0
        ? 'It has no run of clean polls yet, so nothing has begun to clear this.'
        : `It has ${String(record.clean_poll_streak)} clean poll${
            record.clean_poll_streak === 1 ? '' : 's'
          } toward clearing it.`}
    </p>
  )
}
