/**
 * How the console says a time, a duration, an identity and a poll outcome.
 *
 * Run with: npm test
 *
 * One test per thing a review pass found being said two ways, or said
 * in the storage layer's words. The pattern behind all of them: the console had
 * a house idiom, applied it on some screens, and printed the wire value on the
 * others.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

// `void test(...)` throughout: node:test returns a promise and the lint rule
// forbids leaving one unhandled. The runner awaits them itself.

import {
  absolute,
  duration,
  latency,
  pollOutcome,
  publisherName,
  stamp,
  stateLabel,
} from './format'
import { RULES, termMeans } from './glossary'

const WIRE = '2026-08-08T19:24:23.683660+00:00'

void test('a stored instant never reaches a screen in its wire form', () => {
  // Thirty of these appeared as visible text across the six screens, sixteen of
  // them on one page, while the fleet board rendered the same field as "5d ago".
  const now = Date.parse(WIRE) + 5 * 86400 * 1000
  const { text, title } = stamp(WIRE, now)
  assert.equal(text, '5d ago')
  assert.doesNotMatch(title, /\d{6}/, 'no microseconds in anything a person reads')
  assert.doesNotMatch(title, /T\d{2}:/, 'not the wire form in the tooltip either')
  assert.match(title, /8 Aug 2026, 19:24 UTC/)
})

void test('an unparseable timestamp is shown, not swallowed', () => {
  // "We could not read it" presented as "nothing was recorded" is this system's
  // cardinal error committed against its own records.
  assert.equal(absolute('not-a-date'), 'not-a-date')
  assert.equal(absolute(null), 'not recorded')
})

void test('a never-polled publisher reads as never', () => {
  assert.equal(stamp(null, 0).text, 'never')
})

void test('one duration idiom, so two screens cannot describe one cadence differently', () => {
  // The fleet board said `1h declared 168h` where the publisher page rendered
  // the identical pair as `604800s, polling every 3600s`.
  assert.equal(duration(604800), '7d')
  assert.equal(duration(3600), '1h')
  assert.equal(duration(300), '5m')
  assert.equal(duration(90), '90s', 'not rounded into a claim the publisher did not make')
  assert.equal(duration(0), 'not polled')
})

void test('a publisher key is read as an organization and a feed', () => {
  // The key stayed the display form on the queue, on output health, in the
  // packet heading and inside notices addressed to the organization itself.
  assert.deepEqual(publisherName('Hawaii DOT|hidot'), { org: 'Hawaii DOT', feed: 'hidot' })
  assert.deepEqual(publisherName('unkeyed'), { org: 'unkeyed', feed: '' })
})

void test('a trust state reads the same everywhere it appears', () => {
  // The fleet filter was the one place in the app that spelled it `NO_ACCESS`.
  assert.equal(stateLabel('NO_ACCESS'), 'NO ACCESS')
})

void test('a 304 is explained rather than stated twice', () => {
  // The row read `HTTP 304 (304, carried forward)`: the number twice, the
  // meaning never.
  const out = pollOutcome({
    http_status: 304,
    not_modified: true,
    carried_forward: true,
    error: null,
  })
  assert.equal(out.text, 'not modified')
  assert.match(out.detail, /previous body was reused/)
  assert.equal(out.tone, 'pass')
})

void test('status 0 is not printed as an HTTP status, because it is not one', () => {
  const out = pollOutcome({
    http_status: 0,
    not_modified: false,
    carried_forward: false,
    error: 'x',
  })
  assert.equal(out.text, 'no response')
  assert.doesNotMatch(out.text, /HTTP 0/)
})

void test('a response that arrived and was unusable reads differently from one that never came', () => {
  const out = pollOutcome({
    http_status: 503,
    not_modified: false,
    carried_forward: false,
    error: 'HTTPStatus: 503',
  })
  assert.equal(out.text, 'refused')
  assert.equal(out.code, 'HTTP 503', 'the status stays on the row, behind the outcome')
})

void test('a poll Interchange never attempted is not reported as the publisher not answering', () => {
  // R1 asserts the PUBLISHER did not answer. A missing capture at this end read
  // as `NoFixture: nothing captured for <url>` on thirteen rows, and a notice
  // went to the registry owner on the strength of it.
  const ours = pollOutcome({
    http_status: 0,
    not_modified: false,
    carried_forward: false,
    error: 'Interchange has no captured response for https://example/feed in this offline run',
  })
  assert.equal(ours.text, 'not attempted')
  const theirs = pollOutcome({
    http_status: 0,
    not_modified: false,
    carried_forward: false,
    error: 'ConnectionError',
  })
  assert.equal(theirs.text, 'no response')
})

void test('a latency that was never taken is not a measured zero', () => {
  // `0ms` is the BEST possible latency and it was what every failed poll
  // rendered, beside four columns showing an absence marker for the same poll.
  assert.equal(latency(null), 'not measured')
  assert.equal(latency(0), '0ms', 'a genuine sub-millisecond poll still reads as measured')
})

void test('every rule the console can print has a definition', () => {
  // `R2` appeared on the fleet board, on the publisher summary and in every
  // transition row, and was defined only inside an evidence packet, which most
  // publishers do not have.
  for (const id of ['R1', 'R2', 'R3', 'R4', 'R5', 'R6']) {
    const entry = RULES.get(id)
    assert.ok(entry, `${id} has no definition`)
    assert.ok(entry.asserts.length > 0 && entry.measures.length > 0)
  }
})

void test('no rule assertion addresses a stranger in this system field names', () => {
  // These sentences go to someone at a named organization who has never seen
  // this system's schema.
  for (const [id, entry] of RULES) {
    assert.doesNotMatch(entry.asserts, /update_date|end_date|_/, `${id} leaks a field name`)
  }
})

void test('every trust state the badge can render is defined', () => {
  for (const state of ['ADMIT', 'WATCH', 'QUARANTINE', 'NO_ACCESS']) {
    assert.notEqual(termMeans(stateLabel(state)), null, `${state} has no glossary entry`)
  }
})
