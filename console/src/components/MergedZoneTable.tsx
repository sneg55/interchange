/**
 * The merged-zone table and the controls that narrow it. Spec 6.6, 6.9.
 *
 * Split out of `ReconciliationView.tsx` on the complexity ceiling, which the
 * merge-tier filter pushed it past. The seam is real: everything here is about
 * choosing which of the store's merged zones to look at, and nothing here reads
 * the cycle snapshot or renders provenance.
 */

'use client'

import type { ReactNode } from 'react'

import type { CanonicalZone } from '@/lib/types'
import { MERGE_TIERS, TIER_WORD } from '@/lib/zones'

import { PublisherName, Term } from './legend'
import { TierBadge } from './ProvenancePanel'
import { Denominator, Empty } from './primitives'
import { RoadName } from './RoadName'

export function MergedZoneControls({
  search,
  onSearch,
  tier,
  onTier,
  narrowed,
  shown,
  total,
}: {
  search: string
  onSearch: (value: string) => void
  tier: string
  onTier: (value: string) => void
  narrowed: boolean
  shown: number
  total: number
}): ReactNode {
  return (
    <div className="controls">
      <input
        value={search}
        onChange={(e) => {
          onSearch(e.target.value)
        }}
        placeholder="Filter these rows by road, publisher or canonical id"
        aria-label="Search merged zones"
      />
      {/* Why a zone was merged, askable rather than only readable. Tier 2 is the
          only place a model participates in this pipeline, and the first screen
          of 250 rows sorted by canonical id was `same upstream source` on every
          one of them, so the interesting outcome was unreachable without
          scrolling past the ones that were not. */}
      <select
        value={tier}
        onChange={(e) => {
          onTier(e.target.value)
        }}
        aria-label="Filter by why the zone was merged"
      >
        <option value="">Merged on anything</option>
        {MERGE_TIERS.map((t) => (
          <option key={t} value={t}>
            {TIER_WORD.get(t) ?? t}
          </option>
        ))}
      </select>
      {narrowed ? (
        <>
          <Denominator
            shown={shown}
            total={total}
            noun="loaded zones match"
            one="loaded zone matches"
            shortfall="filtered"
          />
          {/* One control clears both. Two narrowing controls and no way back
              meant an operator who had filtered to a tier and typed a road had
              to undo each by hand to see the table again. */}
          <button
            type="button"
            className="quiet"
            onClick={() => {
              onSearch('')
              onTier('')
            }}
          >
            Clear filters
          </button>
        </>
      ) : null}
    </div>
  )
}

export function MergedZoneTable({
  rows,
  selected,
  onSelect,
  narrowed,
}: {
  rows: readonly CanonicalZone[]
  selected: string | null
  onSelect: (id: string | null) => void
  narrowed: boolean
}): ReactNode {
  if (rows.length === 0) {
    return (
      <Empty>
        {narrowed
          ? 'No loaded merged zone matches those filters. They narrow the zones on this page only, not the whole store.'
          : 'No zone in this view has more than one source.'}
      </Empty>
    )
  }
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Canonical zone</th>
            <th>Road</th>
            <th>Publishers</th>
            <th>
              <Term term="Tier 1, declared upstream">Merged on</Term>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((z) => (
            <tr
              key={z.canonical_id}
              // Selection is visible on the row as well as by scrolling. A panel
              // that appears elsewhere with nothing marked here leaves no way
              // back to which row you clicked.
              className={z.canonical_id === selected ? 'selected' : undefined}
            >
              <td>
                {/* Reads as the row identifier it is, not as a form control.
                    Everywhere else in the app "open this row" is a link; 250
                    bordered buttons in a table said "press me" 250 times for
                    what is a selection.

                    It toggles. `aria-pressed` announced a control that could be
                    pressed and never released: clicking the same id again did
                    nothing, so once a panel was open the only way to be rid of
                    it was to reload the screen. */}
                <button
                  type="button"
                  className="rowlink"
                  aria-pressed={z.canonical_id === selected}
                  onClick={() => {
                    onSelect(z.canonical_id === selected ? null : z.canonical_id)
                  }}
                >
                  {z.canonical_id.slice(0, 8)}
                </button>
              </td>
              {/* A human anchor. Every row rendered as an eight-character UUID
                  prefix and the same two publisher names, so 105 rows were
                  visually identical and none could be recognised. */}
              <td>
                <RoadName zone={z} />
              </td>
              <td>
                {z.sources.map((s, i) => (
                  <span key={s.publisher_key}>
                    {i === 0 ? '' : ', '}
                    <PublisherName publisherKey={s.publisher_key} />
                  </span>
                ))}
              </td>
              <td>
                <TierBadge tier={z.sources[0]?.merge_tier ?? 'SINGLETON'} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
