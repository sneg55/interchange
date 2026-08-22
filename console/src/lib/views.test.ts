/**
 * Console view logic. Spec 6.9.
 *
 * Run with: npm test
 *
 * These assert the same rules `tests/test_console_api.py` asserts on the Python
 * side. The duplication is deliberate and so is the duplicated test: the two
 * implementations serve different paths (replay and API versus live snapshot
 * rendering) and the thing that must not diverge is the rule.
 *
 * The recurring one: a view showing a subset always states what it is a subset
 * of, and NOT_APPLICABLE never renders as a pass.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

// `void test(...)` throughout: node:test returns a promise and the lint rule
// forbids leaving one unhandled. The runner awaits them itself.

import type {
  EvidencePacket,
  OutputArtifact,
  PublisherRecord,
  RegistryEventDoc,
  RuleResult,
  TrustTransition,
} from './types'
import {
  backoffActive,
  cap,
  churnSummary,
  fleetBoard,
  membershipAt,
  noticeQueue,
  outputHealth,
  severityOf,
  statesAt,
  verdictLabel,
  wasEvaluated,
} from './views'

function publisher(overrides: Partial<PublisherRecord> = {}): PublisherRecord {
  return {
    publisher_key: 'A|a',
    org: 'A',
    feedname: 'a',
    us_state: 'UT',
    registered_since: null,
    url: 'https://example.test/f.json',
    declared_version: '4.2',
    declared_cadence_seconds: 300,
    needs_api_key: false,
    fleet_state: 'ADMIT',
    state_before_no_access: null,
    churn_status: 'INSUFFICIENT_HISTORY',
    ruleset_version: 'v1',
    latching_rule_ids: [],
    clean_poll_streak: 0,
    clean_streak_started_at: null,
    first_seen: '2026-01-01T00:00:00Z',
    last_seen_in_registry: '2026-08-01T00:00:00Z',
    absent_pull_count: 0,
    decommissioned_at: null,
    agent_identity: null,
    poll_interval_seconds: 300,
    last_polled_at: '2026-08-07T12:00:00Z',
    ...overrides,
  }
}

const FLEET = [
  publisher(),
  publisher({ publisher_key: 'B|b', org: 'B', feedname: 'b', fleet_state: 'WATCH' }),
  publisher({ publisher_key: 'C|c', org: 'C', feedname: 'c', fleet_state: 'QUARANTINE' }),
  publisher({ publisher_key: 'D|d', org: 'D', feedname: 'd', fleet_state: 'NO_ACCESS' }),
]

void test('NO_ACCESS is its own band', () => {
  const bands = new Map(fleetBoard(FLEET).bands.map((b) => [b.band, b.total]))
  assert.equal(bands.get('NO_ACCESS'), 1)
  assert.equal(bands.get('ADMIT'), 1)
})

void test('a filtered band still reports the fleet total', () => {
  // A console saying "0 QUARANTINE" under a filter, meaning "0 of the 1 shown",
  // is the same unlabelled partial claim the product catches in feeds.
  const board = fleetBoard(FLEET, { state: 'ADMIT' })
  assert.equal(board.shownTotal, 1)
  assert.equal(board.fleetTotal, 4)
  assert.ok(board.isFiltered)
  const quarantine = board.bands.find((b) => b.band === 'QUARANTINE')
  assert.deepEqual([quarantine?.shown, quarantine?.total], [0, 1])
})

void test('an unfiltered board is not marked filtered', () => {
  assert.equal(fleetBoard(FLEET).isFiltered, false)
})

void test('a decommissioned publisher leaves the board but not the store', () => {
  const board = fleetBoard([
    ...FLEET,
    publisher({ publisher_key: 'Z|z', decommissioned_at: '2026-08-01T00:00:00Z' }),
  ])
  assert.equal(board.fleetTotal, 4)
})

void test('a churn column shows the measurement, not the word measured', () => {
  // The fleet board and the publisher page both rendered the single word
  // `measured` in a column headed Churn: that a measurement had happened, never
  // what it found. R5 carried the figures on every poll and nothing wrote them.
  const measured = publisher({
    churn_status: 'OK',
    churn_detail: { polls_in_window: 13, advances: 0, regressions: 0, span_seconds: 86400 },
  })
  assert.match(churnSummary(measured), /13 polls/)
  assert.match(churnSummary(measured), /24h/)
})

void test('a record that never wrote its churn figures says so, not zero', () => {
  // "Not recorded" read as "no churn measured" is this system's cardinal error
  // committed against its own records.
  const old = publisher({ churn_status: 'OK' })
  assert.match(churnSummary(old), /not recorded/)
})

void test('backoff compares against the clamped cadence, not the declared one', () => {
  // Otherwise every publisher declaring faster than the five minute floor looks
  // permanently backed off.
  assert.equal(
    backoffActive(publisher({ declared_cadence_seconds: 60, poll_interval_seconds: 300 })),
    false,
  )
  assert.equal(
    backoffActive(publisher({ declared_cadence_seconds: 60, poll_interval_seconds: 3600 })),
    true,
  )
})

function result(overrides: Partial<RuleResult>): RuleResult {
  return { rule_id: 'R4', verdict: 'NOT_APPLICABLE', reason: 'NO_BODY', detail: {}, ...overrides }
}

void test('NOT_APPLICABLE never renders as a pass', () => {
  // "We did not check" and "we checked and it passed" look identical to a
  // viewer shown only a green tick.
  const unchecked = verdictLabel(result({ reason: 'NO_BODY' }))
  assert.equal(unchecked.tone, 'unchecked')
  assert.match(unchecked.text, /Not checked/)
  assert.equal(verdictLabel(result({ verdict: 'ADMIT', reason: 'EVALUATED' })).tone, 'pass')
})

void test('a measured inapplicability gets its own tone, not the pass tone', () => {
  // The publisher moved every offending zone out of `active`. It complied, and
  // that counts toward recovery. But the recorded verdict is NOT_APPLICABLE,
  // not ADMIT: the rule ran and ABSTAINED. Painting it identically to a pass
  // would claim the rule passed when it did no such thing, and painting it as
  // "not checked" would deny the publisher the recovery it earned.
  const complied = verdictLabel(result({ reason: 'MEASURED_INAPPLICABLE' }))
  assert.equal(complied.tone, 'measured')
  assert.notEqual(
    complied.tone,
    verdictLabel(result({ verdict: 'ADMIT', reason: 'EVALUATED' })).tone,
  )
  assert.notEqual(complied.tone, verdictLabel(result({ reason: 'NO_BODY' })).tone)
  assert.ok(wasEvaluated(result({ reason: 'MEASURED_INAPPLICABLE' })))
  assert.equal(wasEvaluated(result({ reason: 'MISSING_INPUT' })), false)
})

void test('NOT_APPLICABLE sorts below ADMIT so it cannot raise a maximum', () => {
  assert.ok(severityOf('NOT_APPLICABLE') < severityOf('ADMIT'))
  assert.ok(severityOf('QUARANTINE') > severityOf('WATCH'))
})

void test('sorting by state puts quarantines first, not alphabetical order', () => {
  // Sorting by state is a triage action. Alphabetical order interleaves ADMIT,
  // NO_ACCESS, QUARANTINE and WATCH into something with no operational meaning.
  const board = fleetBoard(
    [
      publisher({ publisher_key: 'A|a', fleet_state: 'ADMIT' }),
      publisher({ publisher_key: 'Q|q', fleet_state: 'QUARANTINE' }),
      publisher({ publisher_key: 'W|w', fleet_state: 'WATCH' }),
    ],
    {},
    { key: 'state', descending: false },
  )
  assert.deepEqual(
    board.rows.map((r) => r.fleet_state),
    ['QUARANTINE', 'WATCH', 'ADMIT'],
  )
})

void test('sorting is stable on the publisher key when the column ties', () => {
  const rows = [
    publisher({ publisher_key: 'B|b', fleet_state: 'WATCH' }),
    publisher({ publisher_key: 'A|a', fleet_state: 'WATCH' }),
  ]
  const board = fleetBoard(rows, {}, { key: 'state', descending: false })
  assert.deepEqual(
    board.rows.map((r) => r.publisher_key),
    ['A|a', 'B|b'],
    'equal states must not depend on snapshot arrival order',
  )
})

void test('a never-polled publisher sorts to the stale end, not the fresh one', () => {
  const board = fleetBoard(
    [
      publisher({ publisher_key: 'N|n', last_polled_at: null }),
      publisher({ publisher_key: 'P|p', last_polled_at: '2026-08-07T12:00:00Z' }),
    ],
    {},
    { key: 'polled', descending: false },
  )
  assert.equal(board.rows[0]?.publisher_key, 'N|n', 'never is the least current')
})

void test('the board reports its OLDEST poll, and none at all if any is unpolled', () => {
  // The newest would let one recently polled publisher speak for thirty-nine
  // that had not been reached in a day.
  const fresh = fleetBoard([
    publisher({ publisher_key: 'A|a', last_polled_at: '2026-08-07T12:00:00Z' }),
    publisher({ publisher_key: 'B|b', last_polled_at: '2026-08-07T09:00:00Z' }),
  ])
  assert.equal(fresh.oldestPoll, '2026-08-07T09:00:00Z')

  const mixed = fleetBoard([
    publisher({ publisher_key: 'A|a', last_polled_at: '2026-08-07T12:00:00Z' }),
    publisher({ publisher_key: 'B|b', last_polled_at: null }),
  ])
  assert.equal(mixed.oldestPoll, null, 'one never-polled row means there is no as-of time')
})

function artifact(overrides: Partial<OutputArtifact> = {}): OutputArtifact {
  return {
    cycle_id: 'c1',
    at: '2026-08-07T12:00:00Z',
    feed_uri: null,
    input_zone_count: 15,
    canonical_zone_count: 10,
    source_zone_count: 12,
    validation_result: {
      schema_version: '4.2',
      error_count: 0,
      unresolvable: false,
      errors: [],
      errors_truncated: false,
    },
    published: true,
    excluded_counts: { null_geometry: 1, quarantined_sources_only: 0 },
    excluded_zone_ids: {},
    withheld_source_zones: {},
    withheld_source_zone_count: 0,
    ...overrides,
  }
}

void test('zones withheld before the merge are reported apart from exclusions', () => {
  // The republisher's own quarantine counter can only count among zones it
  // RECEIVED, and quarantined publishers never get that far. Reading its zero as
  // the account of what quarantine excluded is how the screen reported nothing
  // withheld for a cycle that withheld 824 zones.
  const health = outputHealth(
    artifact({
      withheld_source_zones: { 'Utah DOT|udot': 744, 'Hawaii DOT|hidot': 80 },
      withheld_source_zone_count: 824,
    }),
  )
  assert.equal(health.withheldTotal, 824)
  assert.equal(health.withheld.length, 2)
  assert.equal(
    health.excluded.find((e) => e.reason === 'quarantined_sources_only'),
    undefined,
    'the structurally-zero counter is not what reports quarantine',
  )
})

void test('an artifact that withheld nothing says so rather than going blank', () => {
  const health = outputHealth(artifact())
  assert.equal(health.withheldTotal, 0)
  assert.deepEqual(health.withheld, [])
})

void test('an artifact predating the field reports unknown, never zero withheld', () => {
  // Documents written before withholding was recorded are still in the
  // collection. "Not recorded" read as "nothing was withheld" is this system's
  // cardinal error committed against its own records.
  const { withheld_source_zones, withheld_source_zone_count, ...old } = artifact()
  void withheld_source_zones
  void withheld_source_zone_count
  assert.equal(outputHealth(old).withheldTotal, null)
})

void test('each exclusion reason carries the zone ids it named', () => {
  // "1031 missing required field" is not something an operator can act on.
  const health = outputHealth(
    artifact({
      excluded_counts: { missing_required_field: 2 },
      excluded_zone_ids: { missing_required_field: ['z-1', 'z-2'] },
    }),
  )
  assert.deepEqual(health.excluded, [
    { reason: 'missing_required_field', count: 2, ids: ['z-1', 'z-2'] },
  ])
})

void test('a reason with a count but no recorded ids yields an empty list', () => {
  const health = outputHealth(
    artifact({ excluded_counts: { null_geometry: 3 }, excluded_zone_ids: {} }),
  )
  assert.deepEqual(
    health.excluded[0]?.ids,
    [],
    'and the screen says so rather than showing nothing',
  )
})

void test('the headline reconciles what was published with what the cycle produced', () => {
  // "10 canonical zones from 12 source zones" read as a funnel from the second
  // number to the first, which it is not: the second counts publisher records
  // behind the zones that were published. The number the exclusion counts below
  // subtract from appeared nowhere on the screen.
  const health = outputHealth(artifact())
  assert.match(health.headline, /10 of the 15 canonical zones/)
  assert.match(health.headline, /12 publisher records/)
})

void test('an artifact that never recorded its input says so rather than implying a total', () => {
  const { input_zone_count, ...old } = artifact()
  void input_zone_count
  const health = outputHealth(old)
  assert.match(health.headline, /did not record/)
  assert.doesNotMatch(health.headline, /of the undefined/)
})

void test('a validation failure is the headline', () => {
  const health = outputHealth(
    artifact({
      published: false,
      validation_result: {
        schema_version: '4.2',
        error_count: 7,
        unresolvable: false,
        errors: [],
        errors_truncated: false,
      },
    }),
  )
  assert.match(health.headline, /^NOT PUBLISHED/)
  assert.match(health.headline, /7 errors/)
})

void test('an unresolvable schema reads as not validated, never as a pass', () => {
  const health = outputHealth(
    artifact({
      published: false,
      validation_result: {
        schema_version: '4.2',
        error_count: null,
        unresolvable: true,
        errors: [],
        errors_truncated: false,
      },
    }),
  )
  assert.match(health.headline, /nothing was validated/)
})

void test('zero-count exclusions are not listed as if they happened', () => {
  assert.deepEqual(outputHealth(artifact()).excluded, [
    { reason: 'null_geometry', count: 1, ids: [] },
  ])
})

const EVENTS: RegistryEventDoc[] = [
  {
    publisher_key: 'A|a',
    at: '2026-01-01T00:00:00Z',
    event: 'PROVISIONED',
    from_value: null,
    to_value: null,
  },
  {
    publisher_key: 'B|b',
    at: '2026-03-01T00:00:00Z',
    event: 'PROVISIONED',
    from_value: null,
    to_value: null,
  },
  {
    publisher_key: 'A|a',
    at: '2026-06-01T00:00:00Z',
    event: 'DECOMMISSIONED',
    from_value: null,
    to_value: null,
  },
]

void test('membership is replayed from RegistryEvent, not read off the record', () => {
  // A replay showing today's fleet with last month's states attached is a more
  // convincing lie than showing nothing.
  assert.deepEqual([...membershipAt(EVENTS, '2026-02-01T00:00:00Z')], ['A|a'])
  assert.deepEqual([...membershipAt(EVENTS, '2026-08-01T00:00:00Z')], ['B|b'])
})

function transition(at: string, to: TrustTransition['to_state']): TrustTransition {
  return {
    publisher_key: 'B|b',
    at,
    from_state: 'WATCH',
    to_state: to,
    rule_ids: ['R2'],
    primary_rule_id: 'R2',
    direction: 'ESCALATION',
    ruleset_version: 'v1',
    observation_ids: [],
    evidence_packet_id: null,
  }
}

void test('a publisher with no transition yet is absent, not admitted', () => {
  const states = statesAt([transition('2026-04-01T00:00:00Z', 'ADMIT')], '2026-03-01T00:00:00Z')
  assert.equal(states.has('B|b'), false)
})

void test('state is the last transition at or before the instant', () => {
  const transitions = [
    transition('2026-04-01T00:00:00Z', 'ADMIT'),
    transition('2026-07-01T00:00:00Z', 'QUARANTINE'),
  ]
  assert.equal(statesAt(transitions, '2026-05-01T00:00:00Z').get('B|b'), 'ADMIT')
  assert.equal(statesAt(transitions, '2026-08-01T00:00:00Z').get('B|b'), 'QUARANTINE')
})

function packet(overrides: Partial<EvidencePacket>): EvidencePacket {
  return {
    packet_id: 'p1',
    publisher_keys: ['A|a'],
    finding_type: 'TRUST_TRANSITION',
    created_at: '2026-08-01T00:00:00Z',
    rule_ids: ['R2'],
    ruleset_version: 'v1',
    observation_window: { start: '', end: '', count: 0 },
    observations: [],
    observations_truncated: false,
    total_observations: 0,
    consumer_rendering: null,
    registry_rendering: 'text',
    approval_state: 'DRAFT',
    approved_by: null,
    approved_at: null,
    approved_rendering_sha256: null,
    resolved_at: null,
    ...overrides,
  }
}

void test('the queue holds drafts only, oldest first', () => {
  // A decided packet left in the queue would invite a second decision.
  const queue = noticeQueue([
    packet({ packet_id: 'late', created_at: '2026-08-03T00:00:00Z' }),
    packet({ packet_id: 'early', created_at: '2026-08-01T00:00:00Z' }),
    packet({ packet_id: 'done', approval_state: 'APPROVED' }),
  ])
  assert.deepEqual(
    queue.map((p) => p.packet_id),
    ['early', 'late'],
  )
})

void test('a cap is always stated', () => {
  // Silent truncation is the same failure this product exists to catch.
  const capped = cap([1, 2, 3, 4, 5], 2)
  assert.equal(capped.capped, true)
  assert.equal(capped.note, 'Showing 2 of 5 in view.')
  assert.equal(cap([1, 2], 10).note, 'Showing all 2 in view.')
})

/**
 * The product critic's F-10. Sorting existed on five of the fleet board's seven
 * columns, so an operator who learned it on State found it missing on Churn.
 */
void test('every fleet column can be sorted, including churn and latching', () => {
  const records = [
    publisher({ publisher_key: 'a', churn_status: 'OK', latching_rule_ids: [] }),
    publisher({
      publisher_key: 'b',
      churn_status: 'INSUFFICIENT_HISTORY',
      latching_rule_ids: ['R2', 'R3'],
    }),
    publisher({
      publisher_key: 'c',
      churn_status: 'INSUFFICIENT_HISTORY',
      latching_rule_ids: ['R3'],
    }),
  ]
  // Unmeasured first: sorting this column asks which publishers cannot be judged.
  const byChurn = fleetBoard(records, {}, { key: 'churn', descending: false })
  assert.deepEqual(
    byChurn.rows.map((r) => r.publisher_key),
    ['b', 'c', 'a'],
  )
  // Most latched rules first; nothing latched sorts last.
  const byLatching = fleetBoard(records, {}, { key: 'latching', descending: false })
  assert.deepEqual(
    byLatching.rows.map((r) => r.publisher_key),
    ['b', 'c', 'a'],
  )
})

/**
 * The product critic's F-02. With no rows matching, the board printed two
 * sentences about "the publishers shown" and "the rest", directly above "No
 * publisher matches these filters". Both described an empty set.
 */
void test('an empty filter result has nothing to say about publishers shown', () => {
  const board = fleetBoard([publisher({ publisher_key: 'a' })], { search: 'no-such-org' })
  assert.equal(board.rows.length, 0)
  // The board still reports the fleet it is a view OF, so the count on screen
  // can never read as "the fleet is empty".
  assert.equal(board.fleetTotal, 1)
  assert.equal(board.shownTotal, 0)
  // And every band still carries its real total rather than collapsing to zero.
  assert.equal(
    board.bands.reduce((sum, b) => sum + b.total, 0),
    1,
  )
})
