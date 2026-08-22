/**
 * Connecting to Firebase, and nothing else. Spec 6.9.
 *
 * Split from `firestore.ts` so the queries next door are only queries. This file
 * is the one place that decides which backend the browser talks to, including
 * whether that backend is a local emulator, and that decision is made from
 * config passed in rather than from an environment read buried beside
 * `initializeApp`.
 */

'use client'

import { type FirebaseApp, getApps, initializeApp } from 'firebase/app'
import { connectAuthEmulator, getAuth } from 'firebase/auth'
import { connectFirestoreEmulator, type Firestore, getFirestore } from 'firebase/firestore'

export interface WebConfig {
  apiKey: string
  authDomain: string
  projectId: string
  /** Local emulator host, or null. See `env.ts`. */
  emulatorHost: string | null
}

const FIRESTORE_EMULATOR_PORT = 8080
const AUTH_EMULATOR_PORT = 9099

let app: FirebaseApp | undefined
let store: Firestore | undefined

export function firebase(config: WebConfig): FirebaseApp {
  if (app) return app
  const existing = getApps()
  app = existing.length > 0 && existing[0] ? existing[0] : initializeApp(config)
  if (config.emulatorHost !== null) {
    connectAuthEmulator(getAuth(app), `http://${config.emulatorHost}:${AUTH_EMULATOR_PORT}`, {
      disableWarnings: true,
    })
  }
  return app
}

export function db(config: WebConfig): Firestore {
  if (store) return store
  // Cached, because `connectFirestoreEmulator` must run before the first
  // request and throws once the instance has been used. `getFirestore` returns
  // the same instance every time, so guarding on a local flag rather than
  // re-deriving it is what keeps the second caller from re-connecting.
  store = getFirestore(firebase(config))
  if (config.emulatorHost !== null) {
    connectFirestoreEmulator(store, config.emulatorHost, FIRESTORE_EMULATOR_PORT)
  }
  return store
}

/** The caller's ID token, for the one authenticated write. */
export async function idToken(config: WebConfig): Promise<string | null> {
  const user = getAuth(firebase(config)).currentUser
  return user ? await user.getIdToken() : null
}
