/**
 * Screen 2. One publisher's signals, cadence, timeline and transitions.
 * Spec 6.9.
 *
 * The transition table carries the rule ID and the ruleset version on every
 * row. Without the version a transition cannot be explained after a ruleset
 * change: the same rule ID may have meant something different when it fired,
 * and an operator reading history would silently apply today's definition to
 * yesterday's decision.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'

import { absolute, age, duration, pollOutcome } from '@/lib/format'
import type { Observation, PublisherDaily, PublisherRecord, TrustTransition } from '@/lib/types'
import { backoffActive, churnSummary } from '@/lib/views'

import { CrossRefs } from './apparatus'
import { RuleCodes, Term, Timestamp, When } from './legend'
import { DailyRollup, RecentObservations } from './PublisherHistory'
import { Empty, Section, StateBadge } from './primitives'
import { WhyThisState } from './WhyThisState'

/**
 * What the last poll did, in words, with the status behind it.
 *
 * This row read `2026-08-08T19:24:23.683660+00:00 HTTP 304 (304, carried
 * forward)`: a wire timestamp, the number 304 twice, and no statement of what
 * either meant. Minnesota DOT's read `HTTP 0`, which is not a status code at
 * all but the sentinel for a request that never got a response.
 */
function LatestPoll({ latest, now }: { latest: Observation | undefined; now: number }): ReactNode {
  if (latest === undefined) return <>none retained</>
  const outcome = pollOutcome(latest)
  return (
    <>
      <Timestamp at={latest.polled_at} now={now} />
      {', '}
      <span className={`badge tone-${outcome.tone}`}>{outcome.text}</span>{' '}
      {/* The summary row is the one place the whole sentence fits, so it is
          spelled out here and abbreviated in the table below. */}
      <span className="count">{outcome.detail}</span>
    </>
  )
}

function Summary({
  record,
  latest,
  now,
}: {
  record: PublisherRecord
  latest: Observation | undefined
  now: number
}): ReactNode {
  return (
    <>
      <Section
        title={`${record.org} / ${record.feedname}`}
        aside={<StateBadge state={record.fleet_state} />}
      >
        {/* Where this record sits. The page had exactly one link out and no
            route back, so arriving from a table left no way to return to it. */}
        {/* The verdict, read aloud, before the table that evidences it. The
            badge said QUARANTINE and the table held every fact behind it, and
            nothing on the page connected the two: which of these seven rows is
            R4 was left to a reader who already knew. */}
        <WhyThisState record={record} />
        <CrossRefs>
          <Link href="/">Fleet board</Link>
          <Link href="/queue">Notices awaiting a decision</Link>
          {/* This page prints rule codes, latching, clean streak and four kinds
              of absence. Until now none of them was resolvable from here. */}
          <Link href="/glossary">Glossary</Link>
        </CrossRefs>
        <div className="scroll-x">
          <table className="kv">
            <tbody>
              <tr>
                <th>Agent identity</th>
                <td>{record.agent_identity ?? 'not provisioned'}</td>
              </tr>
              <tr>
                <th>Cadence</th>
                <td>
                  {/* One duration idiom, fleet-wide. This cell said `604800s,
                      polling every 3600s` where the fleet board rendered the
                      identical pair as `1h declared 168h`, and neither said
                      which number was the publisher's promise and which was
                      ours. */}
                  Declared {duration(record.declared_cadence_seconds)} by the publisher; Interchange
                  polls every {duration(record.poll_interval_seconds)}
                  {backoffActive(record) ? (
                    <span className="count">
                      {' '}
                      (adaptive backoff is slowing this publisher below its declared rate)
                    </span>
                  ) : null}
                </td>
              </tr>
              <tr>
                <th>
                  <Term term="Churn" />
                </th>
                <td>
                  {record.churn_status === 'OK' ? (
                    churnSummary(record)
                  ) : (
                    <span className="badge tone-unchecked">insufficient history</span>
                  )}
                </td>
              </tr>
              <tr>
                <th>
                  <Term term="Latching" />
                </th>
                <td>
                  <RuleCodes ids={record.latching_rule_ids} />
                  <span className="count">
                    {' '}
                    <Term term="Clean streak">clean streak</Term> {record.clean_poll_streak}
                  </span>
                </td>
              </tr>
              <tr>
                <th>Latest poll</th>
                <td>
                  <LatestPoll latest={latest} now={now} />
                </td>
              </tr>
              <tr>
                <th>Feed’s own last-updated time</th>
                <td>
                  {/* The label is English and the field name is beside the
                      value, where a reader who needs to quote it to a publisher
                      can find it. `FEED UPDATE_DATE` as a row header was the
                      storage schema showing through the page. */}
                  {latest?.update_date === undefined || latest.update_date === null ? (
                    <span className="badge tone-unchecked">not recorded</span>
                  ) : (
                    <>
                      {/* Readable, with the publisher's own string verbatim in
                          the tooltip. The value is their assertion and belongs
                          in evidence exactly as they wrote it; it does not have
                          to be how an operator reads it. */}
                      <time title={latest.update_date}>{absolute(latest.update_date)}</time>{' '}
                      <span className="count">
                        (<code>update_date</code>, {age(latest.update_age_seconds)} old)
                      </span>
                    </>
                  )}
                </td>
              </tr>
              <tr>
                <th>Contradictory zones</th>
                <td>
                  {latest?.active_count === null || latest === undefined ? (
                    <span className="badge tone-unchecked">not measured</span>
                  ) : (
                    <>
                      {latest.active_with_past_end_date ?? 0} of {latest.active_count} zones marked
                      active have an end date in the past{' '}
                      <span className="count">
                        (<code>end_date</code>)
                      </span>
                    </>
                  )}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Section>
    </>
  )
}

function Transitions({ transitions }: { transitions: readonly TrustTransition[] }): ReactNode {
  return (
    <>
      <Section
        title="Transitions"
        aside={<span className="count">{transitions.length} recorded</span>}
      >
        {transitions.length === 0 ? (
          <Empty>No transition yet. This publisher has not changed state.</Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>At</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Rules</th>
                  <th>
                    <Term term="Ruleset" />
                  </th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {transitions.map((t) => (
                  <tr key={`${t.at}-${t.to_state}`}>
                    {/* The date, not how long ago. A transition is an audit row:
                        two readers a week apart must describe it identically,
                        which "5d ago" does not. The stored value stays in the
                        tooltip for anyone who needs it verbatim. */}
                    <td>
                      <When at={t.at} />
                    </td>
                    <td>
                      <StateBadge state={t.from_state} />
                    </td>
                    <td>
                      <StateBadge state={t.to_state} />
                    </td>
                    <td>
                      <RuleCodes ids={t.rule_ids} />
                    </td>
                    {/* The version travels with the row. A rule ID alone cannot
                      explain a decision made under a different ruleset. */}
                    <td>{t.ruleset_version}</td>
                    <td>
                      {t.evidence_packet_id === null ? (
                        <span className="count">none</span>
                      ) : (
                        <Link href={`/packets/${encodeURIComponent(t.evidence_packet_id)}`}>
                          packet
                        </Link>
                      )}
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

export function PublisherDetail({
  record,
  observations,
  dailies,
  transitions,
  observationCap,
  now,
}: {
  record: PublisherRecord
  observations: readonly Observation[]
  dailies: readonly PublisherDaily[]
  transitions: readonly TrustTransition[]
  observationCap: number
  /** Clock passed in rather than read here, so the render is deterministic. */
  now: number
}): ReactNode {
  return (
    <>
      <Summary record={record} latest={observations[0]} now={now} />
      <Transitions transitions={transitions} />
      <DailyRollup dailies={dailies} />
      <RecentObservations observations={observations} observationCap={observationCap} now={now} />
    </>
  )
}
