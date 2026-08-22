/**
 * How a merged zone reads, and what the screening gate did to it.
 *
 * Run with: npm test
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

// `void test(...)` throughout: node:test returns a promise and the lint rule
// forbids leaving one unhandled. The runner awaits them itself.

import type { CanonicalZone } from './types'
import { decodeGeometry, mergedOn, REDACTION_PLACEHOLDER, roadOf } from './zones'

function zone(details: Record<string, unknown>, tiers: string[] = []): CanonicalZone {
  return {
    canonical_id: 'c1',
    geometry: null,
    core_details: details,
    start_date: null,
    end_date: null,
    sources: tiers.map((merge_tier, i) => ({
      publisher_key: `Org ${String(i)}|feed${String(i)}`,
      road_event_id: `e${String(i)}`,
      data_source_id: null,
      distance_m: null,
      coverage: null,
      merge_tier,
    })) as CanonicalZone['sources'],
    conflicts: [],
    supersedes: [],
    bbox: null,
  }
}

void test('two sources agreeing on the road name say it once', () => {
  // `Tices Ln, Tices Ln` read as a duplication bug on a screen whose whole
  // subject is two publishers claiming the same zone.
  assert.equal(roadOf(zone({ road_names: ['Tices Ln', 'Tices Ln'] })).text, 'Tices Ln')
})

void test('agreement is matched on case and surrounding space, not on bytes', () => {
  assert.equal(roadOf(zone({ road_names: ['Tices Ln', ' TICES LN '] })).text, 'Tices Ln')
})

void test('two sources disagreeing keep both names', () => {
  // The disagreement is the finding. Collapsing it would hide the thing the
  // reconciliation screen exists to show.
  const road = roadOf(zone({ road_names: ['North of NJ 33', 'South of Dey Rd'] }))
  assert.equal(road.text, 'North of NJ 33 / South of Dey Rd')
  assert.equal(road.alternatives, 1)
})

void test('a redacted name is counted, never printed as a road name', () => {
  const road = roadOf(zone({ road_names: [REDACTION_PLACEHOLDER, 'Tices Ln'] }))
  assert.equal(road.text, 'Tices Ln')
  assert.equal(road.redacted, 1)
})

void test('a zone whose only name was redacted has no road text at all', () => {
  const road = roadOf(zone({ road_names: [REDACTION_PLACEHOLDER] }))
  assert.equal(road.text, '')
  assert.equal(road.redacted, 1)
})

void test('a zone with no names falls back to direction, but never to the word unknown', () => {
  assert.equal(roadOf(zone({ direction: 'northbound' })).text, 'northbound')
  assert.equal(roadOf(zone({ direction: 'unknown' })).text, 'unnamed')
  assert.equal(roadOf(zone({})).text, 'unnamed')
})

void test('the tier filter matches any source, not only the first', () => {
  // The table badges `sources[0]`. A filter that did the same would hide the
  // adjudicated merges from the view opened to find them.
  const z = zone({ road_names: ['A'] }, ['TIER_1_DETERMINISTIC', 'TIER_2_ADJUDICATED'])
  assert.equal(mergedOn(z, 'TIER_2_ADJUDICATED'), true)
  assert.equal(mergedOn(z, 'TIER_1_DETERMINISTIC'), true)
  assert.equal(mergedOn(z, 'SINGLETON'), false)
})

void test('geometry decodes through the storage contract, and junk stays undrawable', () => {
  // Spec 7: Firestore cannot hold nested arrays, so a LineString's coordinates
  // are stored as a JSON string. A flat shape passes through untouched.
  const line = decodeGeometry({ type: 'LineString', coordinates: '[[-74.1,40.7],[-74.2,40.8]]' })
  assert.deepEqual(line, {
    type: 'LineString',
    coordinates: [
      [-74.1, 40.7],
      [-74.2, 40.8],
    ],
  })
  const point = decodeGeometry({ type: 'Point', coordinates: [-74.1, 40.7] })
  assert.deepEqual(point, { type: 'Point', coordinates: [-74.1, 40.7] })
  assert.equal(decodeGeometry(null), null)
  // A string that does not parse is undrawable, never a half-decoded shape.
  assert.equal(decodeGeometry({ type: 'LineString', coordinates: 'not json' }), null)
})
