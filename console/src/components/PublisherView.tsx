'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback } from 'react'

import {
  type Handlers,
  type WebConfig,
  watchPublisherDaily,
  watchRecentObservations,
  watchTransitions,
} from '@/lib/firestore'
import { publisherName } from '@/lib/format'
import type { Observation, PublisherDaily, PublisherRecord, TrustTransition } from '@/lib/types'
import { AuthGate } from './AuthGate'
import { PageShell } from './PageShell'
import { PublisherDetail } from './PublisherDetail'
import { Empty } from './primitives'
import { combined, useCollection } from './useCollection'
import { useFleet } from './useFleet'
import { useNow } from './useNow'

/**
 * Raw observations are capped and paginated. At the five minute floor a
 * publisher accumulates roughly 288 a day against 90 days of retention, so an
 * uncapped read here would be tens of thousands of documents per page view.
 * Charts read the daily rollup instead.
 */
const OBSERVATION_CAP = 200
const DAILY_DAYS = 90

function PublisherViewInner({
  config,
  publisherKey,
}: {
  config: WebConfig | null
  publisherKey: string
}): ReactNode {
  // The organization's name, not its document id. This line read "Reliability
  // history and trust decisions for Hawaii DOT|hidot" on the one screen whose
  // whole subject is a named organization.
  const action = `Reliability history and trust decisions for ${publisherName(publisherKey).org}`
  const fleet = useFleet(config)
  const observations = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<Observation>) =>
        watchRecentObservations(store, { publisherKey, cap: OBSERVATION_CAP }, handlers),
      [publisherKey],
    ),
  )
  const dailies = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<PublisherDaily>) =>
        watchPublisherDaily(store, { publisherKey, days: DAILY_DAYS }, handlers),
      [publisherKey],
    ),
  )
  const transitions = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<TrustTransition>) =>
        watchTransitions(store, publisherKey, handlers),
      [publisherKey],
    ),
  )

  const record: PublisherRecord | undefined = fleet.records.find(
    (r) => r.publisher_key === publisherKey,
  )
  // All four, not just the fleet listener. This screen is as current as its
  // stalest collection, and reporting the freshest would let three healthy
  // listeners hide a dead one.
  const status = combined([fleet, observations, dailies, transitions])
  const now = useNow()

  return (
    <PageShell
      subscription={{ ...status, loading: fleet.loading }}
      action={action}
      hasData={record !== undefined}
    >
      {record === undefined ? (
        <Empty>No publisher {publisherKey} in the fleet.</Empty>
      ) : (
        <PublisherDetail
          record={record}
          observations={observations.rows}
          dailies={dailies.rows}
          transitions={transitions.rows}
          observationCap={OBSERVATION_CAP}
          now={now ?? 0}
        />
      )}
    </PageShell>
  )
}

export function PublisherView(props: Parameters<typeof PublisherViewInner>[0]): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  return (
    <AuthGate
      config={props.config}
      action={`Reliability history and trust decisions for ${publisherName(props.publisherKey).org}`}
    >
      {() => <PublisherViewInner {...props} />}
    </AuthGate>
  )
}
