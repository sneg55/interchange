/**
 * What each screen means, in one testable place. Spec 6.9.
 *
 * Mirrors `src/features/console_api/views.py`. The duplication is deliberate:
 * the Python side serves the replay path and the API, and this side renders
 * live Firestore snapshots without a round trip. What must not diverge is the
 * *rule*, and the rule is the same in both files and asserted in both suites.
 *
 * The recurring one: a view that shows a subset always states what it is a
 * subset of. Silent truncation is the failure this product exists to catch, so
 * a count that could be mistaken for the whole fleet never appears without its
 * denominator.
 */

import type {
  EvidencePacket,
  FleetState,
  OutputArtifact,
  PublisherRecord,
  Reason,
  RegistryEventDoc,
  RuleResult,
  TrustTransition,
  Verdict,
} from './types'

// Re-exported so every caller can keep importing view logic from one place;
// the split below the surface is a file-size concern, not an API change.
export {
  BANDS,
  type BandCount,
  backoffActive,
  churnSummary,
  type FleetBoard,
  type FleetFilters,
  fleetBoard,
  type SortKey,
} from './fleet'

/** Reasons meaning "we did not check". Spec 6.4. */
const UNEVALUATED: ReadonlySet<Reason> = new Set<Reason>([
  'NO_BODY',
  'MISSING_INPUT',
  'SCHEMA_UNKNOWN',
  'SUPPRESSED',
  'INSUFFICIENT_HISTORY',
])

/**
 * How a rule outcome should read on screen.
 *
 * `NOT_APPLICABLE` is deliberately NOT rendered as a pass. "We did not check"
 * and "we checked and it passed" look identical to a viewer shown only a green
 * tick, and that confusion is the exact failure the product exists to catch in
 * other people's feeds.
 */
export function verdictLabel(result: RuleResult): {
  tone: 'pass' | 'measured' | 'unchecked' | 'warn' | 'fail'
  text: string
} {
  if (result.verdict === 'QUARANTINE') return { tone: 'fail', text: 'Quarantine' }
  if (result.verdict === 'WATCH') return { tone: 'warn', text: 'Watch' }
  if (result.verdict === 'ADMIT') return { tone: 'pass', text: 'Pass' }
  if (result.reason === 'MEASURED_INAPPLICABLE') {
    // Its OWN tone, not the pass tone. The recorded verdict really is
    // NOT_APPLICABLE: the rule ran over a real body and did not apply. That
    // counts toward recovery, which is why it is not the "unchecked" tone
    // either, but painting it identically to an ADMIT would claim the rule
    // passed when it abstained.
    return { tone: 'measured', text: 'Measured, does not apply' }
  }
  return { tone: 'unchecked', text: `Not checked (${result.reason})` }
}

export function wasEvaluated(result: RuleResult): boolean {
  return !UNEVALUATED.has(result.reason)
}

/**
 * NOT_APPLICABLE sits BELOW ADMIT so it can never raise a maximum.
 *
 * A naive ordering that ranked it alongside ADMIT would let a publisher be
 * admitted on the strength of rules that never ran.
 */
const SEVERITY = new Map<Verdict, number>([
  ['NOT_APPLICABLE', -1],
  ['ADMIT', 0],
  ['WATCH', 1],
  ['QUARANTINE', 2],
])

export function severityOf(verdict: Verdict): number {
  return SEVERITY.get(verdict) ?? -1
}

/** One exclusion reason, its count, and the zones it named. */
export interface Exclusion {
  reason: string
  count: number
  /** The zone ids recorded for this reason. Empty means none were recorded. */
  ids: string[]
}

export interface OutputHealth {
  published: boolean
  headline: string
  excluded: Exclusion[]
  /** Per publisher, zones never offered to the merge because it is quarantined. */
  withheld: [string, number][]
  /**
   * Total withheld, or null when the artifact predates the field.
   *
   * Null rather than zero. An artifact that never recorded what it withheld has
   * not told us that it withheld nothing, and collapsing the two would be this
   * system's cardinal error committed against its own records.
   */
  withheldTotal: number | null
}

export function outputHealth(artifact: OutputArtifact): OutputHealth {
  const result = artifact.validation_result
  let headline: string
  if (artifact.published) {
    // Against the cycle's INPUT, not on its own. "32278 canonical zones from
    // 32819 source zones" read as a funnel from the second number to the first,
    // which it is not: 32,819 counts the publisher records behind the zones that
    // were published. The number a reader needs is how many zones the merge
    // produced, because the exclusion counts below subtract from that one.
    headline =
      artifact.input_zone_count === undefined
        ? `Published ${String(artifact.canonical_zone_count)} canonical zones, drawn from ${String(artifact.source_zone_count)} publisher records. This cycle did not record how many zones the merge produced, so what was excluded cannot be reconciled here.`
        : // Not "this cycle produced". `input_zone_count` counts what the
          // republisher received from the canonical zone store, which persists
          // across cycles and therefore accumulates: it grew from 47,521 to
          // 49,326 while the zones emitted each cycle stayed near 31,000.
          `Published ${String(artifact.canonical_zone_count)} of the ${String(artifact.input_zone_count)} canonical zones the merge handed the republisher, drawn from ${String(artifact.source_zone_count)} publisher records. The rest are accounted for below.`
  } else if (result.unresolvable) {
    headline = 'NOT PUBLISHED: the output schema could not be resolved, so nothing was validated.'
  } else {
    headline = `NOT PUBLISHED: output failed its own schema validation with ${String(result.error_count)} errors.`
  }
  return {
    published: artifact.published,
    headline,
    // Joined here rather than in the component, so the screen renders a list
    // and never indexes a record by a string it was handed. Through a Map for
    // the same reason: a plain record lookup keyed by a document-supplied string
    // reaches Object.prototype members, and `__proto__` would come back as an
    // object rather than as the absent list it is.
    excluded: Object.entries(artifact.excluded_counts)
      .filter(([, n]) => n > 0)
      .map(([reason, count]) => ({
        reason,
        count,
        ids: new Map(Object.entries(artifact.excluded_zone_ids)).get(reason) ?? [],
      })),
    // Withheld and excluded are different things and are reported separately.
    // Quarantined publishers are held back BEFORE the merge, so
    // `excluded_counts.quarantined_sources_only` can only ever be zero: it
    // counts among zones the republisher received. Folding the two together
    // would let a screen say "0 excluded for quarantine" about a cycle that
    // withheld 824 zones for exactly that reason.
    withheld: Object.entries(artifact.withheld_source_zones ?? {}).filter(([, n]) => n > 0),
    withheldTotal: artifact.withheld_source_zone_count ?? null,
  }
}

/**
 * Fleet membership at an instant, replayed from RegistryEvent.
 *
 * Not read off PublisherRecord, which is mutable and only ever describes now. A
 * replay of last month showing today's fleet with last month's trust states
 * attached is a more convincing lie than showing nothing.
 */
export function membershipAt(events: readonly RegistryEventDoc[], at: string): Set<string> {
  const members = new Set<string>()
  for (const event of [...events].sort((a, b) => a.at.localeCompare(b.at))) {
    if (event.at > at) break
    if (event.event === 'PROVISIONED' || event.event === 'REAPPEARED') {
      members.add(event.publisher_key)
    } else if (event.event === 'DECOMMISSIONED') {
      members.delete(event.publisher_key)
    }
  }
  return members
}

/**
 * Each publisher's trust state at an instant: its last transition at or before.
 *
 * A publisher with no transition yet is ABSENT rather than defaulting to ADMIT.
 * Defaulting would record "not checked" as "passed" at replay time, which is
 * the same error the scorer refuses at evaluation time.
 */
export function statesAt(
  transitions: readonly TrustTransition[],
  at: string,
): Map<string, FleetState> {
  const states = new Map<string, FleetState>()
  for (const t of [...transitions].sort((a, b) => a.at.localeCompare(b.at))) {
    if (t.at > at) break
    states.set(t.publisher_key, t.to_state)
  }
  return states
}

/** Screen 5. DRAFT only, oldest first. */
export function noticeQueue(packets: readonly EvidencePacket[]): EvidencePacket[] {
  return packets
    .filter((p) => p.approval_state === 'DRAFT' && p.resolved_at === null)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
}

export interface Capped<T> {
  items: T[]
  shown: number
  matched: number
  capped: boolean
  note: string
}

/** Apply a hard cap and SAY SO. Never a silent truncation. */
export function cap<T>(items: readonly T[], limit: number): Capped<T> {
  const capped = items.length > limit
  return {
    items: items.slice(0, limit),
    shown: Math.min(items.length, limit),
    matched: items.length,
    capped,
    note: capped
      ? `Showing ${String(limit)} of ${String(items.length)} in view.`
      : `Showing all ${String(items.length)} in view.`,
  }
}
