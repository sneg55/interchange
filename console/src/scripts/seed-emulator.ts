/**
 * Load a `scripts/dump_console_data.py` dump into the Firestore emulator.
 *
 *   npx tsx src/scripts/seed-emulator.ts <seed.json>
 *
 * Local development only. Refuses to run unless FIRESTORE_EMULATOR_HOST is set,
 * because the Admin SDK bypasses security rules and a mistyped project id would
 * otherwise write pipeline output straight into a real database.
 */

import { readFileSync } from 'node:fs'
import { initializeApp } from 'firebase-admin/app'
import { getFirestore } from 'firebase-admin/firestore'

interface Row {
  id: string
  doc: Record<string, unknown>
}

function fail(message: string): never {
  process.stderr.write(`${message}\n`)
  process.exit(1)
}

// eslint-disable-next-line no-restricted-properties -- a local dev seeder, not the app
const host = process.env.FIRESTORE_EMULATOR_HOST
if (host === undefined || host === '') {
  fail('refusing to seed: FIRESTORE_EMULATOR_HOST is not set')
}

const path = process.argv[2]
if (path === undefined) {
  fail('usage: npx tsx src/scripts/seed-emulator.ts <seed.json>')
}

// eslint-disable-next-line no-restricted-properties -- a local dev seeder, not the app
const projectId = process.env.GOOGLE_CLOUD_PROJECT ?? 'interchange-local'
// No credential: with FIRESTORE_EMULATOR_HOST set the Admin SDK talks to the
// emulator without authenticating, which is also the guard above doing its job.
// A missing host would send these writes at a real project instead.
initializeApp({ projectId })
const db = getFirestore()

const parsed = JSON.parse(readFileSync(path, 'utf8')) as {
  collections: Record<string, Row[]>
}

const BATCH = 400

/**
 * Firestore cannot store an array whose elements are arrays, and GeoJSON
 * `coordinates` is exactly that. `CanonicalZone` is declared a Firestore
 * document in spec section 7 with `geometry` as a field, so the record as
 * specified cannot be written: the emulator rejects it with "Property geometry
 * contains an invalid nested entity".
 *
 * This encodes any nested array as a JSON string so the console can be driven
 * locally. It is a seeding workaround, not the fix. The fix is a decision about
 * how geometry is stored, and it belongs in the spec.
 */
function flatten(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.some((entry) => Array.isArray(entry)) ? JSON.stringify(value) : value.map(flatten)
  }
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, flatten(v)]),
    )
  }
  return value
}

/**
 * Empty a collection before writing the new dump into it.
 *
 * `set()` overwrites a document that shares an id and leaves every other one
 * alone, so seeding a second dump on top of a first left the union of both. A
 * cycle that stopped publishing still had its old `output_artifacts` row, and a
 * publisher dropped from the snapshot kept its observations, which is a console
 * asserting facts no run produced. Deleting first makes the emulator show one
 * dump rather than a sediment of every dump ever loaded.
 */
async function clear(name: string): Promise<number> {
  let removed = 0
  for (;;) {
    const snapshot = await db.collection(name).limit(BATCH).get()
    if (snapshot.empty) {
      return removed
    }
    const batch = db.batch()
    for (const doc of snapshot.docs) {
      batch.delete(doc.ref)
    }
    await batch.commit()
    removed += snapshot.size
  }
}

async function seed(): Promise<void> {
  for (const [name, rows] of Object.entries(parsed.collections)) {
    const removed = await clear(name)
    if (removed > 0) {
      process.stdout.write(`${name}: cleared ${removed}\n`)
    }
    for (let i = 0; i < rows.length; i += BATCH) {
      const batch = db.batch()
      for (const row of rows.slice(i, i + BATCH)) {
        batch.set(db.collection(name).doc(row.id), flatten(row.doc) as Row['doc'])
      }
      await batch.commit()
    }
    process.stdout.write(`${name}: ${rows.length}\n`)
  }
  process.stdout.write(`seeded ${projectId} at ${host}\n`)
}

seed().catch((error: unknown) => {
  fail(`seed failed: ${String(error)}`)
})
