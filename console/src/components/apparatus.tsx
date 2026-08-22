/**
 * The register's apparatus: the preamble block, the band ruler, the stamp.
 *
 * A rulemaking document opens by stating what it is a record of, when it was
 * current, and what it covers, before any of its content. That convention is
 * doing real work here rather than decorating: this console's whole argument is
 * that a claim has to arrive with its scope attached, and the preamble is the
 * one place on every screen where scope is stated before rows are.
 *
 * See DESIGN.md.
 */

import { Fragment, type ReactNode } from 'react'

import type { FleetState } from '@/lib/types'

/** One labeled row of a preamble. */
export interface PreambleRow {
  /** Rendered in apparatus caps. Short: ACTION, AS OF, SCOPE. */
  label: string
  value: ReactNode
}

/**
 * The labeled block every screen opens with.
 *
 * A description list rather than a table, because these are labels and their
 * values, and a screen reader should be told so.
 */
export function Preamble({ rows }: { rows: readonly PreambleRow[] }): ReactNode {
  const shown = rows.filter((row) => row.value !== null)
  if (shown.length === 0) return null
  return (
    <dl className="preamble">
      {/* A Fragment, not a wrapper element. `display: contents` on a real div
          places the pair on the grid correctly but leaves dt and dd as children
          of the div, so `.preamble > dd` never matched and every value carried
          the browser's default 40px indent. */}
      {shown.map((row) => (
        <Fragment key={row.label}>
          <dt className="apparatus">{row.label}</dt>
          <dd>{row.value}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

/**
 * Where else this document points.
 *
 * A rulemaking document carries its cross-references on its face rather than
 * expecting the reader to already know where it sits. Three screens linked INTO
 * an evidence packet and the packet linked nowhere at all, so a finding was a
 * dead end: from it you could not reach the publisher it concerns, the queue it
 * came from, or the transition that produced it.
 */
export function CrossRefs({ children }: { children: ReactNode }): ReactNode {
  return (
    <nav className="crossrefs" aria-label="Related records">
      <span className="apparatus">See also</span>
      {children}
    </nav>
  )
}

export interface Band {
  band: FleetState
  shown: number
  total: number
}

const BAND_WORD = new Map<FleetState, string>([
  ['ADMIT', 'admit'],
  ['WATCH', 'watch'],
  ['QUARANTINE', 'quarantine'],
  ['NO_ACCESS', 'no access'],
])

/**
 * Fleet composition as one proportional rule.
 *
 * Four pills sat side by side said nothing about proportion: 18 ADMIT and 2
 * QUARANTINE occupied the same width, so the shape of the fleet had to be
 * reconstructed by reading four numbers. The segments here are drawn from the
 * counts, which makes the ruler a measurement rather than a legend.
 *
 * Widths come from `total`, never from `shown`. A filter changes which rows are
 * listed below; it does not change what the fleet is, and a ruler that redrew
 * itself under a filter would be asserting a different fleet.
 */
export function BandRuler({
  bands,
  hero = false,
}: {
  bands: readonly Band[]
  /**
   * The front page's opening statement renders the same measurement at the
   * scale of a verdict; every other screen keeps the standing size. One flag on
   * one component, so the two can never drift into different rulers.
   */
  hero?: boolean
}): ReactNode {
  const fleet = bands.reduce((sum, b) => sum + b.total, 0)
  if (fleet === 0) return null
  // Whether the rule and the counts under it are measuring different sets. They
  // legitimately are, and that is exactly why it has to be said: a reader takes
  // a bar and the legend directly beneath it as one object, and under a filter
  // the legend went to "0 of 18 admit, 1 of 2 quarantine" while the rule stayed
  // pixel-for-pixel the whole fleet. Two true statements that read as one
  // contradicting itself.
  const filtered = bands.some((b) => b.shown !== b.total)
  return (
    <div className={hero ? 'ruler ruler-hero' : 'ruler'}>
      <div className="ruler-track" aria-hidden="true">
        {bands
          .filter((b) => b.total > 0)
          .map((b) => (
            <div
              key={b.band}
              className="ruler-seg"
              data-band={b.band}
              style={{ flexGrow: b.total }}
            />
          ))}
      </div>
      <ul className="ruler-keys">
        {bands.map((b) => (
          <li key={b.band}>
            <span className={`badge ${TONE.get(b.band) ?? 'tone-unknown'}`}>
              {b.band.replace('_', ' ')}
            </span>
            {/* Never the numerator alone, even in a legend. Under no filter the
                two halves are the same number, and "18 of 18 admit" reads as a
                ratio a reader stops to resolve. */}
            <span className={b.shown === b.total ? 'count' : 'count count-partial'}>
              {b.shown === b.total ? (
                <>
                  <strong>{b.shown}</strong> {BAND_WORD.get(b.band) ?? ''}
                </>
              ) : (
                <>
                  <strong>{b.shown}</strong> of {b.total} {BAND_WORD.get(b.band) ?? ''}
                </>
              )}
            </span>
          </li>
        ))}
      </ul>
      {filtered ? (
        <p className="empty ruler-note">
          The rule above is the whole fleet of {fleet} and does not move under a filter; the counts
          beside it are what your filter left.
        </p>
      ) : null}
    </div>
  )
}

const TONE = new Map<FleetState, string>([
  ['ADMIT', 'tone-pass'],
  ['WATCH', 'tone-warn'],
  ['QUARANTINE', 'tone-fail'],
  // Deliberately its own tone. NO_ACCESS is not a trust verdict, so painting it
  // with any of the other three would assert something never measured.
  ['NO_ACCESS', 'tone-unknown'],
])

/**
 * A packet's approval state, as the mark a decided document carries.
 *
 * `DRAFT` is drawn dashed and does not animate, because nothing has been
 * stamped yet. That distinction is the point: an undecided notice must never
 * look like a decided one at a glance.
 */
/**
 * Table footnotes: the register's own device for a qualification.
 *
 * Every caveat these carry used to be a serif paragraph stacked ABOVE its
 * table, so each screen opened with a block of qualifications before the
 * reader had seen the thing being qualified. A footnote attaches the sentence
 * to the exact header or count it qualifies and sets it at the foot, which is
 * where a reader of this register already expects it. Nothing is dropped in
 * the move: same claims, correct position.
 */
export function FootnoteMark({ n }: { n: number }): ReactNode {
  return (
    <sup className="fnmark">
      <a href={`#fn-${n}`} aria-label={`Note ${n}`}>
        {n}
      </a>
    </sup>
  )
}

/** The notes themselves, under a short rule at the foot of the table. */
export function Footnotes({ children }: { children: ReactNode }): ReactNode {
  return (
    <aside className="footnotes" aria-label="Notes">
      <ol>{children}</ol>
    </aside>
  )
}

export function Footnote({
  n,
  className,
  children,
}: {
  n: number
  className?: string
  children: ReactNode
}): ReactNode {
  return (
    <li id={`fn-${n}`} value={n} className={className}>
      {children}
    </li>
  )
}

export function Stamp({ state }: { state: 'DRAFT' | 'APPROVED' | 'WITHHELD' }): ReactNode {
  return (
    <span className="stamp" data-state={state}>
      {state}
    </span>
  )
}
