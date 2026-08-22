/**
 * The fleet board's own view logic. Spec 6.9.
 *
 * Split out of `views.ts` when that file outgrew its size budget. What lives
 * here is everything the board on screen 1 needs and nothing else: the four
 * bands, the filters, the sort, and the backoff read. `views.ts` re-exports it,
 * so callers import from either without caring which.
 */

import type { FleetState, PublisherRecord } from './types'

export const BANDS: readonly FleetState[] = ['ADMIT', 'WATCH', 'QUARANTINE', 'NO_ACCESS']

export interface BandCount {
  band: FleetState
  shown: number
  total: number
}

export interface FleetFilters {
  state?: FleetState
  schemaVersion?: string
  usState?: string
  search?: string
}

export interface FleetBoard {
  rows: PublisherRecord[]
  bands: BandCount[]
  fleetTotal: number
  shownTotal: number
  isFiltered: boolean
  /** The oldest poll across the shown rows, or null if none has been polled. */
  oldestPoll: string | null
}

/** Columns the fleet table can be ordered by. Every column, not most of them. */
export type SortKey =
  | 'publisher'
  | 'state'
  | 'version'
  | 'cadence'
  | 'polled'
  | 'churn'
  | 'latching'

/**
 * Trust states sort by severity, not alphabetically.
 *
 * Sorting by state is a triage action: an operator asking for it wants the
 * quarantines at one end. Alphabetical order interleaves ADMIT, NO_ACCESS,
 * QUARANTINE and WATCH into something with no operational meaning.
 */
const STATE_ORDER = new Map<FleetState, number>([
  ['QUARANTINE', 0],
  ['WATCH', 1],
  ['NO_ACCESS', 2],
  ['ADMIT', 3],
])

function compare(a: PublisherRecord, b: PublisherRecord, key: SortKey): number {
  if (key === 'state') {
    return (STATE_ORDER.get(a.fleet_state) ?? 9) - (STATE_ORDER.get(b.fleet_state) ?? 9)
  }
  if (key === 'version') {
    return (a.declared_version ?? '').localeCompare(b.declared_version ?? '')
  }
  if (key === 'cadence') return a.poll_interval_seconds - b.poll_interval_seconds
  if (key === 'polled') {
    // Never polled sorts to the STALE end rather than to the fresh one. A
    // publisher with no contact at all is the least current thing on the board,
    // and an empty string would sort it as the most recent.
    return (a.last_polled_at ?? '').localeCompare(b.last_polled_at ?? '')
  }
  if (key === 'churn') {
    // Unmeasured first, for the same reason quarantines sort first: an operator
    // sorting this column is asking which publishers the system cannot yet
    // judge, not which ones it can.
    return Number(a.churn_status === 'OK') - Number(b.churn_status === 'OK')
  }
  if (key === 'latching') {
    // Most latched rules first, then by which rules, so R2 and R3 group. A
    // publisher with nothing latched is the least interesting row here and
    // sorts last.
    return (
      b.latching_rule_ids.length - a.latching_rule_ids.length ||
      a.latching_rule_ids.join(',').localeCompare(b.latching_rule_ids.join(','))
    )
  }
  return 0
}

export function fleetBoard(
  records: readonly PublisherRecord[],
  filters: FleetFilters = {},
  sort: { key: SortKey; descending: boolean } = { key: 'publisher', descending: false },
): FleetBoard {
  const live = records.filter((r) => r.decommissioned_at === null)
  const needle = filters.search?.toLowerCase() ?? ''
  const rows = live
    .filter((r) => filters.state === undefined || r.fleet_state === filters.state)
    .filter(
      (r) =>
        filters.schemaVersion === undefined || (r.declared_version ?? '') === filters.schemaVersion,
    )
    .filter((r) => filters.usState === undefined || (r.us_state ?? '') === filters.usState)
    .filter(
      (r) =>
        needle.length === 0 ||
        r.org.toLowerCase().includes(needle) ||
        r.feedname.toLowerCase().includes(needle),
    )
    // Publisher key is the tie-break under every sort, so equal states or equal
    // cadences keep a stable, reproducible order instead of whatever order the
    // snapshot happened to arrive in.
    .sort(
      (a, b) =>
        (compare(a, b, sort.key) || a.publisher_key.localeCompare(b.publisher_key)) *
        (sort.descending ? -1 : 1),
    )

  const polled = rows.map((r) => r.last_polled_at).filter((v): v is string => v !== null)

  return {
    rows,
    // Every band carries its total, so a filtered view can never be mistaken
    // for the whole fleet.
    bands: BANDS.map((band) => ({
      band,
      shown: rows.filter((r) => r.fleet_state === band).length,
      total: live.filter((r) => r.fleet_state === band).length,
    })),
    fleetTotal: live.length,
    shownTotal: rows.length,
    isFiltered: rows.length !== live.length,
    // The oldest, not the newest. The board is only as fresh as its stalest row,
    // and reporting the newest would let one recently polled publisher speak for
    // thirty-nine that had not been reached in a day.
    oldestPoll:
      polled.length === rows.length && polled.length > 0 ? (polled.sort()[0] ?? null) : null,
  }
}

/**
 * What R5 actually measured on this publisher's last scored poll.
 *
 * The fleet board's Churn column and the publisher page's Churn row both
 * rendered the single word `measured`, which tells a reader that a measurement
 * happened and never what it found. R5's own detail carries the figures; nothing
 * was reading them.
 *
 * Returns null when there is no measurement to report, so a caller renders the
 * INSUFFICIENT HISTORY marker rather than an empty parenthesis. Absence stays
 * distinguishable from a measured zero, here as everywhere.
 */
export function churnSummary(record: PublisherRecord): string {
  const detail = record.churn_detail
  if (detail === undefined || detail === null) {
    // The record predates the field. Not "no change measured": this cycle did
    // not write what it saw, and reading that as zero churn would be the
    // absence-as-a-pass failure the whole product exists to catch.
    return 'measured, figures not recorded'
  }
  const polls = detail.polls_in_window
  const advances = detail.advances
  const span = detail.span_seconds
  const hours = span >= 3600 ? `${String(Math.round(span / 3600))}h` : `${String(span)}s`
  if (polls <= 1) return 'one poll, nothing to compare'
  return advances === 0
    ? `unchanged across ${String(polls)} polls in ${hours}`
    : `unchanged across ${String(polls)} polls in ${hours}, ${String(advances)} timestamp advances`
}

/**
 * Whether adaptive backoff is currently slowing this publisher.
 *
 * Compared against the CLAMPED declared cadence rather than the raw one, or
 * every publisher declaring faster than the five minute floor would look
 * permanently backed off.
 */
export function backoffActive(record: PublisherRecord): boolean {
  const clamped = Math.max(300, Math.min(3600, record.declared_cadence_seconds))
  return record.poll_interval_seconds > 0 && record.poll_interval_seconds !== clamped
}
