/**
 * Screen 3. Map plus provenance panel, with the negative control alongside.
 * Spec 6.9.
 *
 * The provenance panel is the point. It shows both New York DOT and NJIT
 * declaring `TRANSCOM` as their `data_source_id`, which is the strongest fact
 * in the system, and it shows it rather than having a presenter assert it.
 *
 * The negative control renders in the SAME view rather than as a separate
 * slide. Missouri DOT and St. Charles County produce four candidate pairs
 * inside the distance threshold, three of them geometrically intersecting at
 * zero metres, and all four are rejected by symmetric coverage. A viewer sees
 * the rule doing work on screen, which is a different claim from being told it
 * works.
 */

'use client'

import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { CanonicalZone, ReconciliationSnapshot } from '@/lib/types'
import { cap } from '@/lib/views'
import { mergedOn, roadText } from '@/lib/zones'

import { Footnote, FootnoteMark, Footnotes } from './apparatus'
import { When } from './legend'
import { MergedZoneControls, MergedZoneTable } from './MergedZoneTable'
import { NegativeControl } from './NegativeControl'
import { ProvenancePanel, TierSummary } from './ProvenancePanel'
import { Section } from './primitives'
import { ZoneMap } from './ZoneMap'

/**
 * How many merged zones the screen holds.
 *
 * Far smaller than it was, because the query now filters to merged zones on the
 * server. It used to read 2,001 canonical zones to render a hundred rows, since
 * "has more than one source" could only be answered client-side.
 */
export const RENDER_CAP = 250

/** Does a zone match the operator's search, on road, publisher or canonical id? */
function matches(zone: CanonicalZone, needle: string): boolean {
  if (needle === '') return true
  return (
    roadText(zone).toLowerCase().includes(needle) ||
    zone.canonical_id.toLowerCase().includes(needle) ||
    zone.sources.some((s) => s.publisher_key.toLowerCase().includes(needle))
  )
}

export function ReconciliationView({
  zones,
  snapshot,
  queryTruncated,
}: {
  zones: readonly CanonicalZone[]
  /** The cycle's own account of the merge, or null if none was loaded. */
  snapshot: ReconciliationSnapshot | null
  /**
   * Whether the QUERY itself hit its limit, before this component saw anything.
   *
   * Without it the denominator is computed over an already-truncated array and
   * always reads "showing all", which is silent truncation wearing the label
   * that was meant to prevent it.
   */
  queryTruncated: boolean
}): ReactNode {
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  // Why a zone was merged, as a filter and not only as a badge in the last
  // column. Tier 2 is the single place a model participates in this pipeline,
  // and finding it meant reading 250 rows sorted by canonical id, all of which
  // said `same upstream source`. A column existed to distinguish outcomes and
  // there was no way to ask it for one.
  const [tier, setTier] = useState('')
  const panel = useRef<HTMLDivElement>(null)
  // The fleet board teaches an operator that a long list has a search box. This
  // one rendered 250 rows with no controls at all, so finding a road or a
  // publisher meant scrolling. Search narrows what is DISPLAYED; the counts
  // below still report against the store, and say which is which.
  const needle = search.trim().toLowerCase()
  const narrowed = needle !== '' || tier !== ''
  const matched = zones.filter((z) => matches(z, needle) && (tier === '' || mergedOn(z, tier)))
  const rendered = cap(matched, RENDER_CAP)
  const zone = zones.find((z) => z.canonical_id === selected) ?? null

  useEffect(() => {
    // Without this, selecting a zone rendered its provenance nearly six thousand
    // pixels below the fold with the page still at the top, so from the
    // operator's seat clicking a zone did nothing at all.
    if (selected !== null) panel.current?.scrollIntoView({ block: 'start' })
  }, [selected])

  return (
    <>
      <Section
        title="Merged zones"
        aside={
          // Two separate facts, not a ratio. The rows come from the canonical
          // zone STORE, which persists across cycles; the count comes from the
          // latest CYCLE. Presented as "N of M" this rendered "250 of 0" the
          // first time a cycle merged nothing while the store still held the
          // previous cycle's zones, and a ratio whose numerator can exceed its
          // denominator was never measuring one thing.
          <span className="count">
            {rendered.shown} merged zones shown
            {snapshot === null
              ? ' · latest cycle unknown'
              : ` · latest cycle merged ${String(snapshot.merged_zone_count)}`}
            <FootnoteMark n={1} />
          </span>
        }
      >
        {/* The cap is stated, never applied silently. Silent truncation would be
            the same failure this product exists to catch. */}
        <p className="empty">
          {snapshot === null ? (
            'No reconciliation snapshot loaded, so what the latest cycle merged is not known here.'
          ) : (
            <>
              {/* What the two numbers ARE. "merged 541 zones from 48439 groups"
                  sat on this screen while output health said the same cycle
                  published 32,278 canonical zones, and nothing anywhere said
                  that 541 counts only the groups more than one publisher
                  claimed while 48,439 is every canonical zone the cycle
                  produced. Two true numbers that read as a contradiction. */}
              The latest cycle produced {snapshot.group_count} canonical zones, of which{' '}
              {snapshot.merged_zone_count} were claimed by more than one publisher and collapsed.
              Those {snapshot.merged_zone_count} are what this table lists; the rest had a single
              source and nothing to reconcile. The cycle ran <When at={snapshot.at} />.
            </>
          )}
        </p>
        {snapshot === null ? null : (
          <TierSummary
            tierCounts={snapshot.tier_counts}
            adjudicationCounts={snapshot.adjudication_counts}
          />
        )}
        <MergedZoneControls
          search={search}
          onSearch={setSearch}
          tier={tier}
          onTier={setTier}
          narrowed={narrowed}
          shown={rendered.shown}
          total={zones.length}
        />

        {/* After the controls, because it draws exactly what they leave: the
            figure and the table are two renderings of one filtered set, and
            they share the selection state, so neither can contradict the
            other. */}
        <ZoneMap zones={rendered.items} selected={selected} onSelect={setSelected} />

        <MergedZoneTable
          rows={rendered.items}
          selected={selected}
          onSelect={setSelected}
          narrowed={narrowed}
        />
        <Footnotes>
          <Footnote n={1}>
            Rows come from the canonical zone store, which persists across cycles, so they are not
            necessarily this cycle&rsquo;s.{' '}
            {queryTruncated
              ? `Showing the first ${String(RENDER_CAP)}; more merged zones exist than were loaded.`
              : // Against `zones.length`, not `rendered.shown`. Once search could
                // narrow the rows, "all N merged zones in the store are shown"
                // computed from the narrowed set claimed the store held exactly
                // as many zones as the search happened to match.
                `All ${String(zones.length)} merged zones loaded here are shown.`}
          </Footnote>
          {needle === '' ? null : (
            <Footnote n={2}>
              The search above narrows these loaded rows only; it does not query the store.
            </Footnote>
          )}
        </Footnotes>
      </Section>

      <div ref={panel}>{zone === null ? null : <ProvenancePanel zone={zone} />}</div>
      <NegativeControl
        pairs={snapshot?.rejected_pairs ?? []}
        total={snapshot?.rejected_pair_total ?? null}
      />
    </>
  )
}
