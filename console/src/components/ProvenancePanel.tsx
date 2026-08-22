/**
 * The provenance panel behind a selected merged zone. Spec 6.6, 6.9.
 *
 * The point of screen 3. It shows both New York DOT and NJIT declaring
 * `TRANSCOM` as their `data_source_id`, which is the strongest fact in the
 * system, and it shows it rather than having a presenter assert it.
 *
 * Split out of `ReconciliationView.tsx` when that file outgrew its size budget.
 */

'use client'

import type { ReactNode } from 'react'

import { termMeans } from '@/lib/glossary'
import type { CanonicalZone, SourceRef } from '@/lib/types'

import { PublisherName } from './legend'
import { Section } from './primitives'

export function TierBadge({ tier }: { tier: SourceRef['merge_tier'] }): ReactNode {
  if (tier === 'TIER_1_DETERMINISTIC') {
    return (
      <span className="badge tone-pass" title={termMeans('Tier 1, declared upstream') ?? undefined}>
        same upstream source
      </span>
    )
  }
  if (tier === 'TIER_2_ADJUDICATED') {
    return (
      <span className="badge tone-warn" title={termMeans('Tier 2, adjudicated') ?? undefined}>
        adjudicated
      </span>
    )
  }
  return (
    <span className="badge tone-unknown" title={termMeans('Single source') ?? undefined}>
      single source
    </span>
  )
}

/**
 * How this cycle's merges were decided, including the tier that did not fire.
 *
 * The Tier column read `TIER 1 DECLARED UPSTREAM` in all 504 cells on the
 * screen, so a column existed to distinguish outcomes and had one value; and the
 * one place a model participates in this pipeline, adjudicating an ambiguous
 * pair, was invisible in the product because no row had reached it. A tier with
 * a zero count is stated rather than omitted: "no pair needed adjudication this
 * cycle" and "adjudication is not implemented" look identical when the tier is
 * simply absent, and only one of them is true.
 */
export function TierSummary({
  tierCounts,
  adjudicationCounts,
}: {
  tierCounts: Record<string, number>
  adjudicationCounts: Record<string, number>
}): ReactNode {
  const tiers = new Map(Object.entries(tierCounts))
  const verdicts = new Map(Object.entries(adjudicationCounts))
  const tier1 = tiers.get('TIER_1_DETERMINISTIC') ?? 0
  const tier2 = tiers.get('TIER_2_ADJUDICATED') ?? 0
  const notRun = verdicts.get('NOT_RUN') ?? 0
  const decided = [...verdicts]
    .filter(([outcome, n]) => outcome !== 'NOT_RUN' && n > 0)
    .map(([outcome, n]) => `${String(n)} ${outcome.toLowerCase()}`)
    .join(', ')
  return (
    <p className="empty">
      {/* Source claims, not zones. `tier_counts` counts the publisher records
          behind the merges, so phrasing it as "N of these" against a table of
          zones would be a third count on this screen that reconciles with
          neither of the other two. */}
      {tier1} source claims were merged because both publishers declare the same upstream data
      source, which needs no judgement of any kind.{' '}
      {notRun > 0 ? (
        // NOT "were put to the adjudicator". `NOT_RUN` means no adjudicator was
        // configured, so nothing was consulted; rendering it as an adjudication
        // outcome would have the console claiming a model had weighed in on 86
        // pairs it never saw.
        <>
          {notRun} more matched on geometry with identifiers too ambiguous to decide
          deterministically. No adjudicator is configured in this deployment, so none of them was
          put to one and none was merged on that basis.
        </>
      ) : tier2 === 0 ? (
        <>
          No pair reached Tier 2: nothing matched on geometry with identifiers ambiguous enough to
          need adjudicating, so no model was consulted about any zone in this table.
        </>
      ) : (
        <>
          {tier2} matched on geometry with ambiguous identifiers and were adjudicated
          {decided === '' ? '' : ` (${decided})`}.
        </>
      )}{' '}
      The adjudicator answers only whether two zones are the same work zone. It has no part in any
      trust decision.
    </p>
  )
}

export function ProvenancePanel({ zone }: { zone: CanonicalZone }): ReactNode {
  return (
    <Section
      title={`Canonical zone ${zone.canonical_id}`}
      aside={<span className="count">{zone.sources.length} sources</span>}
    >
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Publisher</th>
              <th>Road event</th>
              {/* The field name as the value's label, not as the column
                  heading. `DATA_SOURCE_ID` in apparatus caps over a table an
                  operator reads was the storage schema showing through. */}
              <th>Declared upstream source</th>
              <th>Distance</th>
              <th>Symmetric coverage</th>
              <th>Merged on</th>
            </tr>
          </thead>
          <tbody>
            {zone.sources.map((s) => (
              <tr key={`${s.publisher_key}-${s.road_event_id}`}>
                <td>
                  <PublisherName publisherKey={s.publisher_key} />
                </td>
                <td>{s.road_event_id}</td>
                {/* Both publishers declaring the same upstream source is the
                  strongest evidence anywhere in this system. Shown, not told. */}
                <td>
                  {s.data_source_id ?? <span className="badge tone-unchecked">none declared</span>}
                </td>
                <td>
                  {s.distance_m === null ? (
                    <span className="badge tone-unchecked">not measured</span>
                  ) : (
                    `${s.distance_m.toFixed(1)} m`
                  )}
                </td>
                <td>
                  {/* Not a dash. Coverage is not computed for a pair merged on a
                      declared upstream source, because geometry was never what
                      decided it, and an unlabelled dash read as a failed
                      measurement rather than as one that was never needed. */}
                  {s.coverage === null ? (
                    <span className="count">not needed at this tier</span>
                  ) : (
                    s.coverage.toFixed(2)
                  )}
                </td>
                <td>
                  <TierBadge tier={s.merge_tier} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {zone.conflicts.length === 0 ? null : (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Conflict</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {zone.conflicts.map((c, i) => (
                <tr key={`${c.type}-${String(i)}`}>
                  <td>{c.type.replace(/_/g, ' ')}</td>
                  <td>
                    {c.dropped_edge === null ? (
                      // Disagreement preserved rather than silently resolved: a
                      // consumer needs to know two organizations disagree.
                      `${c.field ?? 'field'}: emitted ${String(c.emitted_value)}, ${String(c.values.length)} other value(s) recorded`
                    ) : (
                      <>
                        a third claim on this zone was set aside:{' '}
                        <PublisherName publisherKey={c.dropped_edge.other_publisher_key} /> /{' '}
                        {c.dropped_edge.other_road_event_id} at{' '}
                        {c.dropped_edge.distance_m === null
                          ? 'unknown distance'
                          : `${c.dropped_edge.distance_m.toFixed(1)} m`}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
