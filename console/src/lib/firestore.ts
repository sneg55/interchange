/**
 * Client-side Firestore reads. Spec 6.9.
 *
 * Reads go straight to Firestore via `onSnapshot` under read-only security
 * rules. A fleet board watching 40 PublisherRecord documents is exactly what a
 * realtime listener is for, and building a polling layer to reproduce it would
 * be waste.
 *
 * What does NOT come from here is anything the console writes. There is one
 * write in the whole app and it goes through the authenticated API route, so
 * this module exports no mutation of any kind.
 *
 * Connecting lives in `firebase-app.ts`; this file is only queries.
 */

'use client'

import {
  collection,
  type Firestore,
  limit,
  onSnapshot,
  orderBy,
  type QueryConstraint,
  query,
  where,
} from 'firebase/firestore'

import type {
  CanonicalZone,
  EvidencePacket,
  Observation,
  OutputArtifact,
  PublisherDaily,
  PublisherRecord,
  ReconciliationSnapshot,
  RegistryEventDoc,
  TrustTransition,
} from './types'

export { db, firebase, idToken, type WebConfig } from './firebase-app'

/**
 * What a snapshot was served from.
 *
 * `onError` is not enough on its own, and assuming it was is how the console
 * spent two and a half minutes showing forty publishers' trust states after the
 * backend had been killed outright. The Firestore SDK reports permission and
 * query errors through `onError`; losing the transport is not one of those. It
 * retries in the background, logs to the browser console, and keeps serving its
 * local cache, so the callback the design leaned on never fires for the failure
 * it was written to catch.
 *
 * `fromCache` is the signal that does fire. False means the server confirmed
 * this snapshot; true means the SDK answered from memory, which after the first
 * successful load means the connection is gone.
 */
export interface SnapshotMeta {
  fromCache: boolean
}

/**
 * Both callbacks together, and `onError` is REQUIRED rather than optional.
 *
 * A dropped listener that silently stops updating leaves an operator looking at
 * a stale fleet board believing it is live, which is the same class of failure
 * this product exists to catch. Making the error handler part of the type means
 * a caller cannot forget it, and `onData` carries the cache flag for the same
 * reason: there is no way to receive rows without also being told where they
 * came from.
 */
export interface Handlers<T> {
  onData: (rows: T[], meta: SnapshotMeta) => void
  onError: (error: Error) => void
}

function subscribe<T>(
  store: Firestore,
  path: string,
  constraints: QueryConstraint[],
  handlers: Handlers<T>,
): () => void {
  return onSnapshot(
    query(collection(store, path), ...constraints),
    // Metadata changes are the whole point of this listener. Without the option
    // the SDK suppresses the callback when only `fromCache` flipped, which is
    // exactly the event that says the view has stopped being live.
    { includeMetadataChanges: true },
    (snapshot) => {
      handlers.onData(
        snapshot.docs.map((doc) => doc.data() as T),
        {
          fromCache: snapshot.metadata.fromCache,
        },
      )
    },
    handlers.onError,
  )
}

export function watchFleet(store: Firestore, handlers: Handlers<PublisherRecord>): () => void {
  return subscribe(store, 'publishers', [orderBy('publisher_key')], handlers)
}

export function watchPublisherDaily(
  store: Firestore,
  options: { publisherKey: string; days: number },
  handlers: Handlers<PublisherDaily>,
): () => void {
  return subscribe(
    store,
    'publisher_daily',
    [
      where('publisher_key', '==', options.publisherKey),
      orderBy('day', 'desc'),
      limit(options.days),
    ],
    handlers,
  )
}

/**
 * The most recent raw observations for one publisher.
 *
 * Capped and paginated on purpose. At the five minute floor a publisher
 * accumulates roughly 288 observations a day against 90 days of retention, so
 * an uncapped listener here would be tens of thousands of document reads per
 * page view. Charts read `publisher_daily` instead.
 */
export function watchRecentObservations(
  store: Firestore,
  options: { publisherKey: string; cap: number },
  handlers: Handlers<Observation>,
): () => void {
  return subscribe(
    store,
    'observations',
    [
      where('publisher_key', '==', options.publisherKey),
      orderBy('polled_at', 'desc'),
      limit(options.cap),
    ],
    handlers,
  )
}

export function watchTransitions(
  store: Firestore,
  publisherKey: string,
  handlers: Handlers<TrustTransition>,
): () => void {
  return subscribe(
    store,
    'trust_transitions',
    [where('publisher_key', '==', publisherKey), orderBy('at', 'desc')],
    handlers,
  )
}

/**
 * The newest transitions across the WHOLE fleet, for the front page's docket.
 *
 * Capped, and the cap is the caller's to state on screen: a log that shows the
 * latest N must say it is the latest N. Ordered by `at` alone, which Firestore
 * indexes automatically, so this adds no composite index.
 */
export function watchRecentTransitions(
  store: Firestore,
  cap: number,
  handlers: Handlers<TrustTransition>,
): () => void {
  return subscribe(store, 'trust_transitions', [orderBy('at', 'desc'), limit(cap)], handlers)
}

export function watchRegistryEvents(
  store: Firestore,
  handlers: Handlers<RegistryEventDoc>,
): () => void {
  return subscribe(store, 'registry_events', [orderBy('at')], handlers)
}

export function watchDraftPackets(
  store: Firestore,
  handlers: Handlers<EvidencePacket>,
): () => void {
  return subscribe(
    store,
    'evidence_packets',
    [where('approval_state', '==', 'DRAFT'), orderBy('created_at')],
    handlers,
  )
}

/**
 * Packets that have been decided, newest decision first.
 *
 * Separate from the draft queue rather than a filter over one listener, because
 * the two answer different questions and want opposite orderings: the queue is
 * oldest-first so nothing sits in it unnoticed, and this is newest-first because
 * it is a log.
 *
 * It exists at all because deciding a packet removed it from the only list that
 * showed packets. The approval binds an identity and a hash of the exact text
 * approved, which makes it the auditable act in this product, and there was
 * nowhere to see that it had happened.
 */
export function watchDecidedPackets(
  store: Firestore,
  cap: number,
  handlers: Handlers<EvidencePacket>,
): () => void {
  return subscribe(
    store,
    'evidence_packets',
    // `!=` rather than two equality listeners, so a state added later shows up
    // here instead of silently belonging to neither list.
    [
      where('approval_state', '!=', 'DRAFT'),
      orderBy('approval_state'),
      orderBy('approved_at', 'desc'),
      limit(cap + 1),
    ],
    handlers,
  )
}

export function watchLatestOutput(
  store: Firestore,
  handlers: Handlers<OutputArtifact>,
): () => void {
  return subscribe(store, 'output_artifacts', [orderBy('at', 'desc'), limit(1)], handlers)
}

/**
 * Zones with more than one source: the ones the merge actually merged.
 *
 * Filtered in the QUERY, on the `source_count` field the reconciler writes.
 * Firestore cannot filter on an array's length, so the screen used to read a
 * UUID-ordered window of canonical zones and count the merged ones inside it,
 * which meant reading 2,001 documents to render a hundred rows and reporting
 * the window's total as though it were the fleet's.
 *
 * cap + 1, so the caller can tell "exactly cap" from "at least cap". Querying
 * exactly `cap` and capping client-side always reports "showing all", which is
 * silent truncation wearing the label that was meant to prevent it.
 */
export function watchMergedZones(
  store: Firestore,
  cap: number,
  handlers: Handlers<CanonicalZone>,
): () => void {
  return subscribe(
    store,
    'canonical_zones',
    [
      where('source_count', '>', 1),
      orderBy('source_count', 'desc'),
      orderBy('canonical_id'),
      limit(cap + 1),
    ],
    handlers,
  )
}

/**
 * The most recent reconciliation snapshot.
 *
 * The cycle's own account of what it merged. The screen used to derive that from
 * whatever slice of `canonical_zones` the listener had loaded, which produced a
 * complete-looking count of an arbitrary UUID-ordered window: it said "105 of
 * 105 merged zones" when the cycle had merged 391. The snapshot carries the
 * measured totals and the rejected-pair sample, so the screen can state the
 * cycle's figures and separately say how much of the cycle it is displaying.
 */
export function watchLatestReconciliation(
  store: Firestore,
  handlers: Handlers<ReconciliationSnapshot>,
): () => void {
  return subscribe(store, 'reconciliation_snapshots', [orderBy('at', 'desc'), limit(1)], handlers)
}

export function watchPacket(
  store: Firestore,
  packetId: string,
  handlers: Handlers<EvidencePacket>,
): () => void {
  // By id, NOT by the draft queue. Subscribing to drafts means approving a
  // packet removes it from its own detail screen, and no historical link to an
  // approved, withheld or resolved finding can ever resolve.
  return subscribe(
    store,
    'evidence_packets',
    [where('packet_id', '==', packetId), limit(1)],
    handlers,
  )
}
