/**
 * The front page's record of change: the newest trust transitions fleet-wide.
 *
 * The board says what every publisher IS; nothing on it said what had
 * HAPPENED. An operator opening the console after a weekend had to visit forty
 * publisher pages to learn whether anything moved. This is the register's own
 * answer, entries in reverse date order, each one the same sentence a
 * transition is everywhere else: who, from what state to what state, on which
 * rules, with the evidence packet when one was opened.
 *
 * Capped by the query and the cap is stated, following the observation table's
 * precedent: it is a query limit, not a denominator, and "12 of 12" would claim
 * a total the query never counted.
 */

import Link from 'next/link'
import type { ReactNode } from 'react'

import type { TrustTransition } from '@/lib/types'

import { PublisherLink, RuleCodes, When } from './legend'
import { Empty, Section, StateBadge } from './primitives'

export function DocketLog({
  transitions,
  cap,
}: {
  transitions: readonly TrustTransition[]
  /** The query's own limit, stated on screen. */
  cap: number
}): ReactNode {
  return (
    <Section
      title="Trust transitions"
      aside={
        <span className="count">
          {transitions.length < cap
            ? `all ${transitions.length} recorded, fleet-wide`
            : `latest ${transitions.length}, fleet-wide`}
        </span>
      }
    >
      {transitions.length === 0 ? (
        <Empty>
          No transition recorded anywhere in the fleet yet: no publisher has changed trust state
          since evaluation began.
        </Empty>
      ) : (
        <>
          <ol className="docket">
            {transitions.map((t) => (
              <li key={`${t.publisher_key}-${t.at}`}>
                <span className="docket-when">
                  <When at={t.at} />
                </span>
                <span className="docket-entry">
                  <PublisherLink publisherKey={t.publisher_key} />
                  <span className="docket-move">
                    <StateBadge state={t.from_state} />
                    <span aria-hidden="true" className="docket-arrow">
                      &rarr;
                    </span>
                    <span className="sr-only">to</span>
                    <StateBadge state={t.to_state} />
                  </span>
                  <span className="count">
                    {/* No "on none". A de-escalation records no firing rule,
                        and naming an absent rule list reads as a broken row
                        rather than as a recovery. */}
                    {t.rule_ids.length === 0 ? null : (
                      <>
                        on <RuleCodes ids={t.rule_ids} />
                      </>
                    )}
                    {t.evidence_packet_id === null ? null : (
                      <>
                        {t.rule_ids.length === 0 ? '' : ' · '}
                        <Link href={`/packets/${encodeURIComponent(t.evidence_packet_id)}`}>
                          evidence packet
                        </Link>
                      </>
                    )}
                  </span>
                </span>
              </li>
            ))}
          </ol>
          {transitions.length < cap ? null : (
            <p className="empty">
              Older transitions are on each publisher&rsquo;s own page, under Transitions.
            </p>
          )}
        </>
      )}
    </Section>
  )
}
