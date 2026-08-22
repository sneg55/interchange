/**
 * The resolver must discriminate: every spelling the live registry actually
 * uses resolves, and every junk value the registry actually contains returns
 * null rather than landing somewhere plausible. Values below are the real
 * distinct spellings from the captured registry fixture, not invented cases.
 *
 * Run with: npm test
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { resolveState } from './usmap'

// `void test(...)` throughout: node:test returns a promise and the lint rule
// forbids leaving one unhandled. The runner awaits them itself.

void test('resolves the lowercase full names the live registry uses', () => {
  assert.equal(resolveState('colorado')?.id, 'CO')
  assert.equal(resolveState('new jersey')?.id, 'NJ')
  assert.equal(resolveState('hawaii')?.id, 'HI')
})

void test('resolves the one capitalised spelling in the capture', () => {
  assert.equal(resolveState('Illinois')?.id, 'IL')
})

void test('resolves USPS codes regardless of case', () => {
  assert.equal(resolveState('UT')?.id, 'UT')
  assert.equal(resolveState('ut')?.id, 'UT')
})

void test('returns null for the junk the registry actually contains', () => {
  assert.equal(resolveState('n/a'), null)
  assert.equal(resolveState('nps'), null)
  assert.equal(resolveState(null), null)
  assert.equal(resolveState(''), null)
})
