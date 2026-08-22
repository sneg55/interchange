/**
 * The live fleet subscription. Spec 6.9.
 *
 * A thin naming layer over `useCollection` rather than its own listener. It was
 * a second copy of the same logic, and the copy is how the fleet board ended up
 * without the liveness signal: two implementations of "subscribe and report what
 * went wrong" means one of them is always the one nobody remembered to update.
 */

'use client'

import type { Firestore } from 'firebase/firestore'
import { useCallback } from 'react'
import { type Handlers, type WebConfig, watchFleet } from '@/lib/firestore'
import type { PublisherRecord } from '@/lib/types'

import { type Subscription, useCollection } from './useCollection'

export type FleetSubscription = Subscription<PublisherRecord> & {
  records: PublisherRecord[]
}

export function useFleet(config: WebConfig | null): FleetSubscription {
  const subscribe = useCallback(
    (store: Firestore, handlers: Handlers<PublisherRecord>) => watchFleet(store, handlers),
    [],
  )
  const subscription = useCollection(config, subscribe)
  return { ...subscription, records: subscription.rows }
}
