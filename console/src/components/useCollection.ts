/**
 * One live Firestore collection, with its failure state AND its liveness.
 * Spec 6.9.
 *
 * `error` is returned rather than logged, for the same reason `Handlers`
 * requires an `onError`: a listener that drops and stops updating must be
 * visible on screen, not only in a console nobody has open.
 *
 * `live` exists because `error` turned out not to cover the case it was written
 * for. Killing the backend does not raise: the SDK retries quietly and serves
 * its cache, so every screen kept rendering a full board with no error and no
 * change at all. `live` is false whenever the most recent snapshot came from
 * cache, and `confirmedAt` is when the server last agreed with what is on
 * screen.
 */

'use client'

import type { Firestore } from 'firebase/firestore'
import { useEffect, useState } from 'react'
import { db, type Handlers, type WebConfig } from '@/lib/firestore'

export interface Subscription<T> {
  rows: T[]
  error: Error | null
  loading: boolean
  /** False once the SDK starts answering from cache: the view is not current. */
  live: boolean
  /** Epoch ms of the last server-confirmed snapshot, or null if never. */
  confirmedAt: number | null
}

export function useCollection<T>(
  config: WebConfig | null,
  subscribe: (store: Firestore, handlers: Handlers<T>) => () => void,
): Subscription<T> {
  const [rows, setRows] = useState<T[]>([])
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [live, setLive] = useState(true)
  const [confirmedAt, setConfirmedAt] = useState<number | null>(null)

  useEffect(() => {
    if (config === null) {
      setError(new Error('Firebase is not configured for this deployment'))
      setLoading(false)
      return
    }
    return subscribe(db(config), {
      onData: (next, meta) => {
        setRows(next)
        setError(null)
        setLoading(false)
        setLive(!meta.fromCache)
        // Stamped only on a server-confirmed snapshot. Stamping on every
        // callback would make a cached re-delivery look like fresh contact,
        // which is the reading this field exists to prevent.
        if (!meta.fromCache) setConfirmedAt(Date.now())
      },
      onError: (err) => {
        setError(err)
        setLoading(false)
        setLive(false)
      },
    })
  }, [config, subscribe])

  return { rows, error, loading, live, confirmedAt }
}

/**
 * Combine several subscriptions into one status for a screen that reads more
 * than one collection.
 *
 * Deliberately pessimistic on both axes: the screen is live only if EVERY
 * listener is live, and the confirmation time is the OLDEST of them. A screen
 * built from four collections is exactly as current as its stalest part, and
 * reporting the freshest would let three healthy listeners hide a dead one.
 */
export function combined(
  parts: readonly Pick<Subscription<unknown>, 'loading' | 'error' | 'live' | 'confirmedAt'>[],
): Pick<Subscription<unknown>, 'loading' | 'error' | 'live' | 'confirmedAt'> {
  const stamps = parts.map((p) => p.confirmedAt).filter((v): v is number => v !== null)
  return {
    loading: parts.some((p) => p.loading),
    error: parts.find((p) => p.error !== null)?.error ?? null,
    live: parts.every((p) => p.live),
    // Null if ANY part has never been confirmed, not just if all of them
    // haven't: a screen with one never-confirmed listener has no honest
    // "everything here is at most N old" to offer.
    confirmedAt: stamps.length === parts.length && stamps.length > 0 ? Math.min(...stamps) : null,
  }
}
