/**
 * Figure 1 on the front page: the fleet drawn on the country it covers.
 *
 * WZDx is geographic data about named states' roads, and until this figure the
 * console never drew geography: coverage could only be read as forty rows. The
 * map states it in one look, in the register's own idiom: a numbered figure
 * with a caption, Census geometry, ink on paper, and colour only where trust
 * state already owns it.
 *
 * Every mark repeats the state-marker grammar (solid ADMIT, half-height WATCH,
 * notched QUARANTINE, hatched NO_ACCESS), so shape carries the difference as
 * well as hue, and each is a link to the publisher's own page. A publisher
 * whose registry entry declares no usable state is LISTED under the figure,
 * never silently dropped: an unlabelled partial map is the same failure as an
 * unlabelled partial count.
 */

'use client'

import { useRouter } from 'next/navigation'
import { Fragment, type ReactNode, useMemo } from 'react'

import { publisherHref, publisherName } from '@/lib/format'
import type { FleetState, PublisherRecord } from '@/lib/types'
import { US_MAP_VIEWBOX, US_STATES } from '@/lib/us-map-data'
import { resolveState } from '@/lib/usmap'

import { PublisherLink } from './legend'

/** Mark geometry in the map's 975x610 frame. */
const MARK_W = 6
const MARK_H = 18
const MARK_GAP = 10

export function FleetMap({ records }: { records: readonly PublisherRecord[] }): ReactNode {
  const router = useRouter()

  const { placed, unplaced } = useMemo(() => {
    const byState = new Map<string, PublisherRecord[]>()
    const unresolvable: PublisherRecord[] = []
    for (const record of records) {
      const state = resolveState(record.us_state)
      if (state === null) {
        unresolvable.push(record)
        continue
      }
      const list = byState.get(state.id)
      if (list === undefined) byState.set(state.id, [record])
      else list.push(record)
    }
    return { placed: byState, unplaced: unresolvable }
  }, [records])

  if (records.length === 0) return null
  const drawn = records.length - unplaced.length

  return (
    <figure className="fig">
      <svg
        className="fig-map"
        viewBox={US_MAP_VIEWBOX}
        role="img"
        aria-label={`Map of the United States. ${drawn} of ${records.length} publishers are drawn at the state their registry entry declares.`}
      >
        <defs>
          {/* The register's own mark for "reserved / not applicable". The same
              45-degree hatch the NO_ACCESS state marker carries everywhere
              else, so the map does not invent a second vocabulary. */}
          <pattern
            id="fig-hatch"
            patternUnits="userSpaceOnUse"
            width="5"
            height="5"
            patternTransform="rotate(45)"
          >
            <rect width="5" height="5" fill="var(--sheet)" />
            <line x1="1" y1="0" x2="1" y2="5" stroke="var(--unknown-mark)" strokeWidth="1.5" />
          </pattern>
        </defs>
        {US_STATES.map((s) => (
          <path
            key={s.id}
            d={s.d}
            className="fig-state"
            data-hosts={placed.has(s.id) ? '' : undefined}
          />
        ))}
        {/* Marks after every shape, so no border paints over one. */}
        {US_STATES.flatMap((s) => {
          const list = placed.get(s.id)
          if (list === undefined) return []
          const x0 = s.cx - ((list.length - 1) * MARK_GAP) / 2
          return list.map((record, i) => (
            <MapMark
              key={record.publisher_key}
              record={record}
              x={x0 + i * MARK_GAP}
              y={s.cy}
              stateName={s.name}
              onOpen={(href) => router.push(href)}
            />
          ))
        })}
      </svg>
      <figcaption className="fig-caption">
        <span className="apparatus">Fig. 1</span>{' '}
        <span>
          Where the fleet publishes. Each mark is one publisher at the state its registry entry
          declares, in its current trust state; {drawn} of {records.length} publishers declare one.
          Marks open the publisher&rsquo;s record.
        </span>
      </figcaption>
      {unplaced.length === 0 ? null : (
        <p className="empty fig-unplaced">
          The remaining{' '}
          {unplaced.length === 1 ? 'publisher declares' : `${unplaced.length} declare`} no usable
          state and {unplaced.length === 1 ? 'is' : 'are'} not drawn:{' '}
          {unplaced.map((record, i) => (
            <Fragment key={record.publisher_key}>
              {i === 0 ? null : ', '}
              <PublisherLink publisherKey={record.publisher_key} />
            </Fragment>
          ))}
          .
        </p>
      )}
    </figure>
  )
}

function MapMark({
  record,
  x,
  y,
  stateName,
  onOpen,
}: {
  record: PublisherRecord
  /** Mark centre in the map frame. */
  x: number
  y: number
  stateName: string
  onOpen: (href: string) => void
}): ReactNode {
  const href = publisherHref(record.publisher_key)
  const { org, feed } = publisherName(record.publisher_key)
  const label = `${org}${feed === '' ? '' : ` / ${feed}`} — ${record.fleet_state.replace('_', ' ')}, ${stateName}`
  return (
    <a
      href={href}
      className="fig-mark"
      aria-label={label}
      onClick={(event) => {
        event.preventDefault()
        onOpen(href)
      }}
    >
      <title>{label}</title>
      {/* The visible bar is 6 map units; the thing an operator aims at is not. */}
      <rect className="fig-hit" x={x - 9} y={y - MARK_H / 2 - 5} width={18} height={MARK_H + 10} />
      <MarkShape state={record.fleet_state} x={x - MARK_W / 2} y={y - MARK_H / 2} />
    </a>
  )
}

/** The state-marker grammar, drawn in map units. Shape differs, not only hue. */
function MarkShape({ state, x, y }: { state: FleetState; x: number; y: number }): ReactNode {
  switch (state) {
    case 'ADMIT':
      return <rect x={x} y={y} width={MARK_W} height={MARK_H} fill="var(--pass-mark)" />
    case 'WATCH':
      // Half height, bottom-aligned, exactly as the inline marker renders it.
      return (
        <rect x={x} y={y + MARK_H / 2} width={MARK_W} height={MARK_H / 2} fill="var(--warn-mark)" />
      )
    case 'QUARANTINE':
      return (
        <>
          <rect x={x} y={y} width={MARK_W} height={MARK_H * 0.39} fill="var(--fail-mark)" />
          <rect
            x={x}
            y={y + MARK_H * 0.61}
            width={MARK_W}
            height={MARK_H * 0.39}
            fill="var(--fail-mark)"
          />
        </>
      )
    case 'NO_ACCESS':
      return (
        <rect
          x={x}
          y={y}
          width={MARK_W}
          height={MARK_H}
          fill="url(#fig-hatch)"
          stroke="var(--unknown-mark)"
          strokeWidth="1"
        />
      )
  }
}
