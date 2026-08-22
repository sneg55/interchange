/**
 * Ruleset supersession, on the console side. Spec 6.4, 6.7.
 *
 * Run with: npm test
 *
 * The same rules `tests/test_notice_queue.py` asserts in Python. The duplication
 * is deliberate: the console decides whether to disable an approve button from
 * its own copy of the ruleset history, and the thing that must not diverge is
 * the rule rather than the code.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { isSuperseded, RULESET_HISTORY, RULESET_VERSION } from './glossary'

void test('a draft citing a rule whose verdict changed is superseded', () => {
  // R6 gained a clock-skew allowance in v2, so a v1 packet citing it may assert
  // a finding this system no longer makes.
  assert.equal(isSuperseded('v1', ['R6']), true)
})

void test('but a draft citing an unchanged rule is not', () => {
  // The first cut compared versions alone and flagged every v1 packet, including
  // a Hawaii DOT R2 quarantine. R2 reaches the same verdict on the same evidence
  // under both versions; refusing a still-true finding is its own wrong answer.
  assert.equal(isSuperseded('v1', ['R2', 'R4']), false)
})

void test('a mixed packet is superseded if any cited rule changed', () => {
  assert.equal(isSuperseded('v1', ['R2', 'R6']), true)
})

void test('a packet on the current ruleset is never superseded', () => {
  assert.equal(isSuperseded(RULESET_VERSION, ['R6']), false)
})

void test('a version that cannot be placed in the history is flagged, not assumed current', () => {
  // It cannot be shown to predate or postdate anything, so a human should look.
  assert.equal(isSuperseded('v0-experimental', ['R6']), true)
  assert.equal(isSuperseded('v0-experimental', ['R2']), false)
})

void test('the current version is the last one in the history', () => {
  // A version bumped in one place and not the other would silently stop
  // flagging the packets this mechanism exists to catch.
  assert.equal(RULESET_HISTORY[RULESET_HISTORY.length - 1], RULESET_VERSION)
})
