/**
 * Screen 1, composed as the register's front page. Spec 6.9.
 *
 * Order of reading: the fleet's shape (the hero ruler), where it publishes
 * (Fig. 1), what changed (the transitions log), then the record itself, the
 * full forty-row table. The previous composition opened with the table, which
 * asked a cold reader to reconstruct all three of those from rows.
 *
 * Four bands, with `NO_ACCESS` shown separately because it is not a trust
 * verdict: a key-gated publisher has not passed or failed anything, and folding
 * it into either would misstate coverage in whichever direction flattered the
 * number.
 *
 * Every band count is rendered against the fleet total, so a filtered view can
 * never be mistaken for the whole fleet. That is not a nicety. A console that
 * says "3 QUARANTINE" while a filter is active, and means "3 of the 12 shown",
 * is making the same kind of unlabelled partial claim the product exists to
 * catch in publisher feeds.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'
import { useCallback, useMemo, useState } from 'react'

import { duration, publisherHref } from '@/lib/format'
import type { FleetState, PublisherRecord, TrustTransition } from '@/lib/types'
import { backoffActive, churnSummary, fleetBoard, type SortKey } from '@/lib/views'

import { BandRuler, Footnote, FootnoteMark, Footnotes } from './apparatus'
import { DocketLog } from './DocketLog'
import { FleetFilters } from './FleetFilters'
import { FleetMap } from './FleetMap'
import { PublisherName, RuleCodes, Timestamp } from './legend'
import { Denominator, Empty, Section, StateBadge, since } from './primitives'
import { SortableHeader } from './SortableHeader'

export function FleetBoard({
  records,
  transitions,
  transitionCap,
  now,
}: {
  records: readonly PublisherRecord[]
  /** Newest transitions fleet-wide, already capped by the query. */
  transitions: readonly TrustTransition[]
  transitionCap: number
  /** Clock passed in rather than read here, so the render is deterministic. */
  now: number
}): ReactNode {
  const [state, setState] = useState<FleetState | ''>('')
  const [version, setVersion] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({
    key: 'publisher',
    descending: false,
  })

  const board = useMemo(
    () =>
      fleetBoard(
        records,
        {
          ...(state === '' ? {} : { state }),
          ...(version === '' ? {} : { schemaVersion: version }),
          ...(search === '' ? {} : { search }),
        },
        sort,
      ),
    [records, state, version, search, sort],
  )

  // Clicking the active column flips direction; clicking another switches to
  // it ascending. Anything else makes the second click on a new column feel
  // random, because it would inherit the previous column's direction.
  const toggle = useCallback((key: SortKey) => {
    setSort((current) =>
      current.key === key ? { key, descending: !current.descending } : { key, descending: false },
    )
  }, [])

  const churnMeasured = board.rows.filter((r) => r.churn_status === 'OK').length

  const versions = useMemo(
    () => [...new Set(records.map((r) => r.declared_version ?? 'unknown'))].sort(),
    [records],
  )

  return (
    <>
      <Section
        title="Fleet"
        aside={
          <span className="count">
            <strong>{board.fleetTotal}</strong> publishers under continuous evaluation
          </span>
        }
      >
        {/* One proportional rule rather than four pills. Four pills gave 18
            ADMIT and 2 QUARANTINE the same width, so the shape of the fleet had
            to be reconstructed by reading four numbers instead of being seen.
            At hero scale here because it is the page's opening statement. */}
        <BandRuler bands={board.bands} hero />

        <div className="front-columns">
          <FleetMap records={records} />
          <DocketLog transitions={transitions} cap={transitionCap} />
        </div>
      </Section>

      <Section
        title="The record"
        aside={
          <Denominator
            shown={board.shownTotal}
            total={board.fleetTotal}
            noun="publishers"
            shortfall="filtered"
          />
        }
      >
        <FleetFilters
          state={state}
          onState={setState}
          version={version}
          onVersion={setVersion}
          versions={versions}
          search={search}
          onSearch={setSearch}
        />

        {/* Footnotes only when there ARE rows. Both notes below are claims
            about "the publishers shown", and with an empty result they asserted
            that some unshown publisher had never been polled and that "the
            rest" lacked history, directly above "No publisher matches these
            filters". A screen that describes a set it is not showing is the
            failure this product exists to catch, in its own furniture. */}
        {board.rows.length === 0 ? (
          <Empty>No publisher matches these filters. Widen them to see the fleet again.</Empty>
        ) : (
          <>
            <div className="scroll-x">
              <table className="fleet-table">
                <thead>
                  <tr>
                    <SortableHeader
                      label="Publisher"
                      column="publisher"
                      sort={sort}
                      onSort={toggle}
                    />
                    <SortableHeader label="State" column="state" sort={sort} onSort={toggle} />
                    {/* Context rather than verdict, and the three that give way
                      first on a narrow screen. What is left is who, what state
                      they are in, when we last looked and which rule is holding
                      them there, which is the sentence the board exists to
                      make. */}
                    <SortableHeader
                      label="Churn"
                      column="churn"
                      sort={sort}
                      onSort={toggle}
                      secondary
                      mark={<FootnoteMark n={1} />}
                    />
                    <SortableHeader
                      label="Version"
                      column="version"
                      sort={sort}
                      onSort={toggle}
                      secondary
                    />
                    <SortableHeader
                      label="Cadence"
                      column="cadence"
                      sort={sort}
                      onSort={toggle}
                      secondary
                    />
                    <SortableHeader
                      label="Last polled"
                      column="polled"
                      sort={sort}
                      onSort={toggle}
                      secondary
                      mark={<FootnoteMark n={2} />}
                    />
                    <SortableHeader
                      label="Latching"
                      column="latching"
                      sort={sort}
                      onSort={toggle}
                    />
                  </tr>
                </thead>
                <tbody>
                  {board.rows.map((r) => (
                    <tr key={r.publisher_key}>
                      <td>
                        <Link href={publisherHref(r.publisher_key)}>
                          <PublisherName publisherKey={r.publisher_key} />
                        </Link>
                      </td>
                      <td>
                        <StateBadge state={r.fleet_state} />
                      </td>
                      <td className="col-secondary">
                        {/* The measurement, not the word "measured". A column
                          headed Churn whose only two values were `measured` and
                          `INSUFFICIENT HISTORY` told a reader that churn had
                          been measured and never what it measured, here or on
                          the publisher page. */}
                        {r.churn_status === 'OK' ? (
                          <span className="count">{churnSummary(r)}</span>
                        ) : (
                          <span className="badge tone-unchecked">insufficient history</span>
                        )}
                      </td>
                      <td className="col-secondary">{r.declared_version ?? 'unknown'}</td>
                      <td className="col-secondary">
                        {duration(r.poll_interval_seconds)}
                        {backoffActive(r) ? <span className="count"> (backoff)</span> : null}
                        {/* One duration idiom, fleet-wide. This cell read
                          `1h declared 168h` while the publisher page rendered
                          the identical pair as `604800s, polling every 3600s`. */}
                        <span className="count">
                          {' '}
                          declared {duration(r.declared_cadence_seconds)}
                        </span>
                      </td>
                      <td className="col-secondary">
                        {/* Per row, because a board-level stamp cannot say that
                        ONE publisher stopped being polled while the rest
                        carried on. */}
                        {r.last_polled_at === null ? (
                          <span className="badge tone-unchecked">never</span>
                        ) : (
                          <Timestamp at={r.last_polled_at} now={now} />
                        )}
                      </td>
                      <td>
                        {/* Resolvable codes. `R3, R4, R5` appeared here and was
                          defined nowhere an operator could reach: the only
                          definitions in the product were inside evidence
                          packets, which most publishers do not have. */}
                        <RuleCodes ids={r.latching_rule_ids} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Footnotes>
              <Footnote n={1}>
                {/* The cause named here used to be "have not been polled often
                    enough", which is the wrong reason and points at the wrong
                    remedy. R5's first gate is the SPAN of retained history, not
                    the number of polls in it: it needs a full 24 hours before it
                    will say anything. Measured on the live fleet, the deepest
                    history was 10.9 hours and one publisher had 44 polls inside
                    it. Polling harder cannot fix that, and an operator reading
                    the old sentence would try. */}
                Churn is measured for {churnMeasured} of the {board.rows.length}{' '}
                {board.rows.length === 1 ? 'publisher' : 'publishers'} shown
                {churnMeasured === board.rows.length
                  ? '.'
                  : churnMeasured === 0
                    ? '. R5 needs 24 hours of continuous history before it can measure churn, and no publisher here has that yet.'
                    : '; the rest do not yet have the 24 hours of continuous history R5 needs.'}
              </Footnote>
              <Footnote n={2}>
                {/* The board's own freshness, stated. Everything else on this
                    screen is a claim about how current someone ELSE's data is,
                    and the screen making those claims said nothing about how
                    current it was itself. */}
                {board.oldestPoll === null
                  ? 'Some publishers shown have never been polled, so this view has no single as-of time.'
                  : `Every publisher shown was polled within the last ${since(
                      Date.parse(board.oldestPoll),
                      now,
                    ).replace(' ago', '')}.`}
              </Footnote>
              {/* Rendered only where it is true, by CSS at the same breakpoint
                  that hides the columns. Any cap or truncation this product
                  applies is stated in the output, and quietly dropping four
                  columns on a phone is a truncation like any other. Unnumbered
                  because the columns it refers to are not on screen to mark. */}
              <li className="narrow-only">
                Churn, version, cadence and last polled are hidden at this width, so what is left
                fits without a sideways drag. They are on each publisher&rsquo;s own page, and on a
                wider screen.
              </li>
            </Footnotes>
          </>
        )}
      </Section>
    </>
  )
}
