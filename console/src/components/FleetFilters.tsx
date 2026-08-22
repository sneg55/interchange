/**
 * The three ways to narrow the fleet board, and the one way back.
 *
 * Split out of `FleetBoard.tsx` on the file size limit. The board had state,
 * version and search and no reset: clearing meant selecting the search text,
 * deleting it, and walking each dropdown back to its first option by hand, on a
 * screen where narrowing is the normal way to work.
 */

'use client'

import type { ReactNode } from 'react'

import { stateLabel } from '@/lib/format'
import type { FleetState } from '@/lib/types'
import { BANDS } from '@/lib/views'

export function FleetFilters({
  state,
  onState,
  version,
  onVersion,
  versions,
  search,
  onSearch,
}: {
  state: FleetState | ''
  onState: (value: FleetState | '') => void
  version: string
  onVersion: (value: string) => void
  versions: readonly string[]
  search: string
  onSearch: (value: string) => void
}): ReactNode {
  const narrowed = state !== '' || version !== '' || search !== ''
  return (
    <div className="controls">
      <select
        value={state}
        onChange={(e) => {
          onState(e.target.value as FleetState | '')
        }}
        aria-label="Filter by trust state"
      >
        <option value="">All states</option>
        {BANDS.map((band) => (
          <option key={band} value={band}>
            {/* The word, not the enum. This dropdown was the one place in the
                app that spelled it `NO_ACCESS`; the chip above it, the table
                cell beside it and every transition row said `NO ACCESS`. */}
            {stateLabel(band)}
          </option>
        ))}
      </select>
      <select
        value={version}
        onChange={(e) => {
          onVersion(e.target.value)
        }}
        aria-label="Filter by declared schema version"
      >
        <option value="">All versions</option>
        {versions.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
      <input
        value={search}
        onChange={(e) => {
          onSearch(e.target.value)
        }}
        placeholder="Search organization or feed"
        aria-label="Search"
      />
      {/* Rendered only when there is something to clear, so the row does not
          carry a permanently inert control. */}
      {narrowed ? (
        <button
          type="button"
          className="quiet"
          onClick={() => {
            onState('')
            onVersion('')
            onSearch('')
          }}
        >
          Clear filters
        </button>
      ) : null}
    </div>
  )
}
