/**
 * Section 6.6's negative control, rendered from the cycle's own measurements.
 *
 * Split out of `ReconciliationView.tsx` when that file outgrew its size budget.
 */

'use client'

import type { ReactNode } from 'react'

import type { RejectedPair } from '@/lib/types'

import { PublisherName, Term } from './legend'
import { Denominator, Empty, Section } from './primitives'

/**
 * The two thresholds the reconciler applies, mirrored from
 * `src/features/reconciler/geometry.py`.
 *
 * Duplicated deliberately and asserted in both suites, the same way the view
 * logic is. The alternative is a screen that prints a measured value against a
 * bar it does not name, which is what it was doing: the distance threshold was
 * stated in the prose and the coverage threshold was not, so 0.450 could have
 * been a near miss or nowhere close and a reader had no way to tell.
 */
export const DISTANCE_THRESHOLD_M = 150
export const MIN_SYMMETRIC_COVERAGE = 0.6

/**
 * Section 6.6's negative control, from the cycle's own measurements.
 *
 * The pairs used to be passed in as a hardcoded empty array, so this rendered a
 * heading naming a control over an empty list, with an empty state describing a
 * DIFFERENT pair of publishers than the heading. As shown it asserted a control
 * that had never run, which is worse than having no section: a reader takes
 * "0 candidate pairs, all rejected" as a result.
 *
 * The title is generic now. Naming Missouri DOT and St. Charles County in the
 * heading while rendering whatever the cycle measured is the same mistake in a
 * smaller font.
 */
export function NegativeControl({
  pairs,
  total,
}: {
  pairs: readonly RejectedPair[]
  /** How many were rejected in total. The sample is bounded; this is not. */
  total: number | null
}): ReactNode {
  if (total === null) {
    return (
      <Section title="Rejected by coverage">
        {/* Not "none were rejected". No cycle snapshot has been read, so the
            number is unknown, and rendering unknown as zero here would be the
            absence-as-a-pass failure this system exists to catch. */}
        <Empty>
          No reconciliation snapshot loaded, so how many pairs the coverage rule refused is not
          known on this screen.
        </Empty>
      </Section>
    )
  }
  return (
    <Section
      title="Rejected by coverage"
      aside={
        // Capped, not filtered. The prose below says the rest were not
        // retained, and this label used to claim the opposite.
        <Denominator
          shown={pairs.length}
          total={total}
          noun="rejected pairs shown"
          shortfall="capped"
        />
      }
    >
      {total === 0 ? (
        <Empty>
          No pair came within the distance threshold and was then refused. With no near-miss to
          reject, this cycle does not exercise the coverage rule.
        </Empty>
      ) : (
        <>
          <p className="empty">
            Every pair below is INSIDE the {DISTANCE_THRESHOLD_M} m distance threshold and was
            refused anyway, on <Term term="Symmetric coverage">symmetric length coverage</Term>.
            Distance alone would have merged them.{' '}
            {/* The threshold, stated. The distance rule was given precisely and
                the coverage rule was not, so a reader saw values from 0.000 to
                0.450 in the column below with no way to tell a near miss from
                nowhere close. */}
            The rule requires at least {MIN_SYMMETRIC_COVERAGE.toFixed(2)} of BOTH zones&rsquo;
            lengths to coincide; every figure below fell short of it.
            {pairs.length < total
              ? ` Showing ${String(pairs.length)} of ${String(total)}; the rest were not retained.`
              : ''}
          </p>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Pair</th>
                  <th>Distance</th>
                  {/* No Outcome column. Every one of these rows carried a badge
                      reading "rejected by coverage" under a heading that says
                      "Rejected by coverage", in the widest column of a table
                      whose Pair column was truncating identifiers to fit. A
                      column with one possible value distinguishes nothing. */}
                  <th>Symmetric coverage, against {MIN_SYMMETRIC_COVERAGE.toFixed(2)} required</th>
                </tr>
              </thead>
              <tbody>
                {pairs.map((p) => (
                  <tr
                    key={`${p.left_publisher}/${p.left_road_event_id}-${p.right_publisher}/${p.right_road_event_id}`}
                  >
                    <td>
                      <PublisherName publisherKey={p.left_publisher} />{' '}
                      <span className="count">{p.left_road_event_id}</span> &#8596;{' '}
                      <PublisherName publisherKey={p.right_publisher} />{' '}
                      <span className="count">{p.right_road_event_id}</span>
                    </td>
                    {/* Some are at zero metres, geometrically intersecting, for
                        zones that are plainly different work zones. */}
                    <td>
                      {p.distance_m === null ? (
                        <span className="badge tone-unchecked">not measured</span>
                      ) : (
                        `${p.distance_m.toFixed(1)} m`
                      )}
                    </td>
                    <td>
                      {p.coverage === null ? (
                        <span className="badge tone-unchecked">not computed</span>
                      ) : (
                        p.coverage.toFixed(3)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  )
}
