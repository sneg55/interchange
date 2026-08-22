/**
 * Shared UI primitives. Spec 6.9.
 *
 * Two of these exist because of a spec invariant rather than for looks:
 *
 * - `Denominator` renders a count that is always paired with its total, so a
 *   filtered or capped view can never be mistaken for the whole.
 * - `Verdict` renders NOT_APPLICABLE distinctly from a pass. A viewer shown
 *   only a green tick cannot tell "we did not check" from "we checked and it
 *   passed", and that confusion is exactly what this product exists to catch in
 *   other people's feeds.
 */

import Link from 'next/link'
import type { ReactNode } from 'react'

import { since, stateLabel } from '@/lib/format'
import { termAnchor, termMeans } from '@/lib/glossary'
import type { FleetState, RuleResult } from '@/lib/types'
import { verdictLabel } from '@/lib/views'

// Re-exported so the many callers that already import `since` from here keep
// working; the move into `format.ts` is a file-size and one-idiom concern, not
// an API change.
export { since }

const STATE_TONE = new Map<FleetState, string>([
  ['ADMIT', 'tone-pass'],
  ['WATCH', 'tone-warn'],
  ['QUARANTINE', 'tone-fail'],
  // Deliberately its own tone. NO_ACCESS is not a trust verdict, so painting it
  // with any of the other three would assert something the system never
  // measured.
  ['NO_ACCESS', 'tone-unknown'],
])

export function StateBadge({ state }: { state: FleetState }): ReactNode {
  const label = stateLabel(state)
  // What the word means, on the word itself, and a route to the long version.
  // Four states drive every gate in this product. They carried a hover tooltip
  // and nothing else, while the rule codes beside them in the same row had been
  // links into the glossary all along: the codes were resolvable and the words
  // they produce were not. A tooltip is not a route.
  const badge = (
    <span
      className={`badge ${STATE_TONE.get(state) ?? 'tone-unknown'}`}
      data-state={state}
      title={termMeans(label) ?? undefined}
    >
      {label}
    </span>
  )
  const href = termAnchor(label)
  if (href === null) return badge
  return (
    <Link className="termlink" href={href}>
      {badge}
    </Link>
  )
}

export function Verdict({ result }: { result: RuleResult }): ReactNode {
  const { tone, text } = verdictLabel(result)
  return (
    <span className={`badge tone-${tone}`} data-rule={result.rule_id}>
      {result.rule_id}: {text}
    </span>
  )
}

/**
 * Why a count is short of its total.
 *
 * `filtered` means the viewer narrowed it and can widen it again. `capped`
 * means the rest were never loaded or never retained, so there is nothing to
 * clear and no way to see them from here. Conflating the two told an operator
 * to go looking for a filter that did not exist, over rows that did not exist
 * either.
 */
export type Shortfall = 'filtered' | 'capped'

function shortfallLabel(shortfall: Shortfall): string {
  return shortfall === 'filtered' ? ' (filtered)' : ' (capped)'
}

/**
 * A count that always carries what it is out of.
 *
 * There is no variant that renders the numerator alone. That is the point: the
 * component makes the invariant unavoidable rather than remembered.
 *
 * `shortfall` is required rather than defaulted, for the same reason. A default
 * of "filtered" is what produced "50 of 60 rejected pairs shown (filtered)" on
 * a screen whose own prose said the other ten were not retained.
 */
export function Denominator({
  shown,
  total,
  noun,
  one,
  shortfall,
}: {
  shown: number
  total: number
  noun: string
  /** The same noun in the singular, for a count of one. Falls back to `noun`. */
  one?: string
  /** What accounts for the gap when `shown` is less than `total`. */
  shortfall: Shortfall
}): ReactNode {
  const partial = shown !== total
  const word = shown === 1 ? (one ?? noun) : noun
  if (partial) {
    return (
      <span className="count count-partial">
        {/* The numerator carries the weight and the qualification stays quiet,
            so the pair still reads as one fact rather than two competing ones. */}
        <strong>{shown}</strong> of {total} {noun}
        {shortfallLabel(shortfall)}
      </span>
    )
  }
  // "all", not "N of N". The invariant is that a count never appears without
  // its scope, and "all 40 publishers" states the scope affirmatively: there is
  // no subset here to be mistaken for the whole. "40 of 40 publishers", "18 of
  // 18 admit" and "1 of 1 observations embedded" read as ratios whose two
  // halves a reader then tries to tell apart, and on an unfiltered board that
  // is what nearly every reader sees first. A bare numerator is still never
  // rendered: the word carries the completeness claim the denominator did.
  return (
    <span className="count">
      {shown === 1 ? '' : 'all '}
      <strong>{shown}</strong> {word}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }): ReactNode {
  return <p className="empty">{children}</p>
}

/**
 * A failed Firestore listener, surfaced rather than swallowed.
 *
 * A dropped listener that silently stops updating leaves an operator reading a
 * stale board believing it is live.
 */
export function ListenerError({ error }: { error: Error | null }): ReactNode {
  if (error === null) return null
  return (
    <p className="error" role="alert">
      Live updates stopped: {error.message}. This view is no longer current.
    </p>
  )
}

/**
 * Whether what is on screen is still being confirmed by the server.
 *
 * This is the one banner the product could not do without and did not have. A
 * dropped connection does not raise in the Firestore SDK: it retries quietly and
 * serves the local cache, so the board carried on showing forty publishers'
 * trust states with the backend gone. Rendered on every screen, above the
 * content, and loudly, because a stale trust verdict presented as current is the
 * failure this product exists to catch in other people's feeds.
 *
 * When the data IS live this renders the confirmation rather than nothing. A
 * freshness indicator that only appears on failure teaches an operator to read
 * "no banner" as "fine", which is indistinguishable from "not rendered".
 */
export function Liveness({
  live,
  confirmedAt,
  now,
  hasData,
}: {
  live: boolean
  confirmedAt: number | null
  now: number
  /**
   * Whether rows are actually on screen underneath this banner.
   *
   * The connecting banner said "Nothing on this screen has been confirmed by the
   * server yet" over a fully populated forty-row table, because moving between
   * sections tears down one listener and starts another while the previous
   * screen's content is still painted. The sentence was true about the listener
   * and false about the screen, and a banner that reads as wrong is a banner an
   * operator learns to skip. This is the one channel the product has for saying
   * data is stale, so it says which of the two situations it is in.
   */
  hasData: boolean
}): ReactNode {
  if (live) {
    return (
      <p className="liveness">
        {confirmedAt === null
          ? // Not "Live." with a denial after it. "Live. The server has not
            // confirmed this view yet." pairs the word for a working connection
            // with the statement that nothing has arrived over it, in five
            // words, which reads as the product contradicting itself.
            'Connected. Waiting for the server’s first snapshot; nothing below has been confirmed yet.'
          : // "Last change confirmed", not "confirmed". A live listener on a
            // collection nobody is writing to gets no further snapshots, so the
            // age climbs without bound and "Live. Confirmed 45m ago." read as a
            // contradiction. What is 45m old is the last change, not the
            // connection.
            `Live. Last change confirmed by the server ${since(confirmedAt, now)}.`}
      </p>
    )
  }
  // Never confirmed and not live is CONNECTING, not dropped. Rendering the full
  // red alarm before a first snapshot has had time to arrive fires it on
  // ordinary navigation, and an alarm that cries wolf on every page load is
  // exactly how an operator learns to ignore the one that matters. Still not a
  // pass: it is amber, and it says nothing has been confirmed.
  if (confirmedAt === null) {
    return (
      <p className="notice" role="status">
        {hasData
          ? 'Connecting. What is below came from this browser’s cache and has not been confirmed by the server yet.'
          : 'Connecting to Interchange. Until the first snapshot arrives, treat anything on screen as unconfirmed.'}
      </p>
    )
  }
  return (
    <p className="error" role="alert">
      NOT LIVE: the connection to Interchange has dropped and this view has stopped updating. Last
      confirmed {since(confirmedAt, now)}. Everything below is that old or older.
    </p>
  )
}

export function Section({
  title,
  children,
  aside,
}: {
  title: string
  children: ReactNode
  aside?: ReactNode
}): ReactNode {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  )
}
