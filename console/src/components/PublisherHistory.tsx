/**
 * The two long history tables on screen 2: the daily rollup and the raw
 * observation window. Spec 6.9.
 *
 * Split out of `PublisherDetail.tsx` when that file outgrew its size budget.
 * These two are the bulk of it and they share one concern: rendering retained
 * history at whatever depth the query actually returned, and saying which.
 */

'use client'

import type { ReactNode } from 'react'

import { age, latency, pollOutcome } from '@/lib/format'
import type { Observation, PublisherDaily } from '@/lib/types'

import { Term, Timestamp } from './legend'
import { Empty, Section, StateBadge } from './primitives'

export function DailyRollup({ dailies }: { dailies: readonly PublisherDaily[] }): ReactNode {
  return (
    <>
      <Section
        title="Daily rollup"
        aside={<span className="count">{dailies.length} days retained</span>}
      >
        {dailies.length === 0 ? (
          <Empty>No rollup yet.</Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Polls</th>
                  {/* "Failed polls", not "No response". The rollup does not
                      split a publisher that did not answer from a poll
                      Interchange never made, and the observation table below is
                      where that distinction is drawn. A header claiming the
                      finer reading would be asserting something this column
                      does not measure. */}
                  <th>Failed polls</th>
                  {/* Not `304s`. An HTTP status code, pluralised, as a column
                      header over a table an operator reads to judge whether a
                      publisher is behaving. */}
                  <th>
                    <Term term="Not modified, carried forward">Not modified</Term>
                  </th>
                  <th>Latency p50 / p95</th>
                  <th>Data age at worst</th>
                  <th>Schema errors</th>
                  <th>Content changes</th>
                  <th>End state</th>
                </tr>
              </thead>
              <tbody>
                {dailies.map((d) => (
                  <tr key={d.day}>
                    <td>{d.day}</td>
                    <td>{d.poll_count}</td>
                    <td>{d.failure_count}</td>
                    <td>{d.not_modified_count}</td>
                    <td>
                      {/* One phrase for "never taken", matching the observation
                          table below it. This cell said the latency was not
                          measured while every row underneath printed `0ms` for
                          the same polls. */}
                      {d.latency_p50_ms === null && d.latency_p95_ms === null ? (
                        <span className="badge tone-unchecked">not measured</span>
                      ) : (
                        `${latency(d.latency_p50_ms)} / ${latency(d.latency_p95_ms)}`
                      )}
                    </td>
                    <td>{age(d.max_update_age_seconds)}</td>
                    <td>
                      {/* null is "no poll carried a body to validate", which is
                        not the same as zero errors. */}
                      {d.schema_error_count ?? (
                        <span className="badge tone-unchecked">not checked</span>
                      )}
                    </td>
                    <td>{d.content_hash_changes}</td>
                    <td>
                      <StateBadge state={d.end_of_day_state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  )
}

export function RecentObservations({
  observations,
  observationCap,
  now,
}: {
  observations: readonly Observation[]
  observationCap: number
  /** Clock passed in rather than read here, so the render is deterministic. */
  now: number
}): ReactNode {
  return (
    <>
      <Section
        title="Recent observations"
        aside={
          // NOT a Denominator. The cap is a query limit, not a total: with more
          // records retained it would read "200 of 200" and with fewer it would
          // read "8 of 200" as though 192 were missing. Claiming a denominator
          // the query never counted is exactly the unlabelled-partial failure
          // Denominator exists to prevent, so this states the limit instead.
          <span className="count">
            most recent {observations.length}, capped at {observationCap} per page
          </span>
        }
      >
        {observations.length === 0 ? (
          <Empty>No observation retained for this publisher.</Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Polled at</th>
                  <th>Status</th>
                  <th>Latency</th>
                  <th>Features</th>
                  <th>Active / past end</th>
                  <th>Schema</th>
                  <th>Trace</th>
                </tr>
              </thead>
              <tbody>
                {observations.map((o) => {
                  const outcome = pollOutcome(o)
                  return (
                    <tr key={o.polled_at}>
                      <td>
                        <Timestamp at={o.polled_at} now={now} />
                      </td>
                      <td>
                        {/* The outcome, then the status code, with the full
                            explanation in the tooltip. This column printed the
                            raw exception text it was handed, so
                            `NoFixture: nothing captured for <url>` appeared
                            thirteen times as though it were something the
                            publisher had done. Repeating the whole sentence on
                            every row is the same noise in politer words, so the
                            row carries the short form and the sentence is one
                            hover away. */}
                        <span className={`badge tone-${outcome.tone}`} title={outcome.detail}>
                          {outcome.text}
                        </span>
                        {outcome.code === '' ? null : (
                          <span className="count"> {outcome.code}</span>
                        )}
                      </td>
                      {/* Null is "the poll never completed, so nothing was
                          timed". It rendered as `0ms`, the best possible
                          latency, on every failed poll. */}
                      <td>
                        {o.latency_ms === null ? (
                          <span className="badge tone-unchecked">not measured</span>
                        ) : (
                          latency(o.latency_ms)
                        )}
                      </td>
                      <td>
                        {o.feature_count ?? <span className="badge tone-unchecked">not read</span>}
                      </td>
                      <td>
                        {o.active_count === null ? (
                          <span className="badge tone-unchecked">not read</span>
                        ) : (
                          `${String(o.active_count)} / ${String(o.active_with_past_end_date ?? 0)}`
                        )}
                      </td>
                      <td>
                        {o.schema_version_used === 'SCHEMA_UNKNOWN' ? (
                          <span className="badge tone-unchecked">version unresolved</span>
                        ) : o.schema_version_used === null ? (
                          <span className="badge tone-unchecked">not checked</span>
                        ) : (
                          `v${o.schema_version_used}, ${String(o.schema_error_count ?? 0)} errors`
                        )}
                      </td>
                      <td>
                        {/* Section 6.9 promises the trace for any poll is
                          reachable from the observation it produced. */}
                        {o.trace_id ?? <span className="count">none</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  )
}
