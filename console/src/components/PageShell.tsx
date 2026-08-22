/**
 * The subscribe-and-render wrapper every screen shares. Spec 6.9.
 *
 * Exists so no screen can forget to surface a dropped listener. A view that
 * silently stops updating shows an operator a stale board they believe is live,
 * which is the same unlabelled-staleness failure the product exists to catch.
 *
 * It takes the whole `Subscription` rather than two of its fields, because the
 * previous signature accepted `loading` and `error` alone, and a screen could
 * therefore be complete and correct while having no way to say it was no longer
 * live. Passing the subscription means liveness cannot be left out by a caller
 * that simply did not think of it.
 *
 * Liveness renders inside the document preamble rather than as a line floating
 * above the content, so a reader is told what this screen is a record of and
 * how current it is in the same breath, before any row of it.
 */

'use client'

import type { ReactNode } from 'react'
import { useState } from 'react'

import { Preamble } from './apparatus'
import { ListenerError, Liveness } from './primitives'
import type { Subscription } from './useCollection'
import { useNow } from './useNow'

export function PageShell<T>({
  subscription,
  action,
  hasData = false,
  children,
}: {
  subscription: Pick<Subscription<T>, 'loading' | 'error' | 'live' | 'confirmedAt'>
  /**
   * Whether rows are actually painted underneath the banner.
   *
   * The connecting banner said "Nothing on this screen has been confirmed by the
   * server yet" over a fully populated forty-row table served from cache. The
   * sentence was true about the listener and false about the screen, and a
   * banner that reads as wrong is the one an operator learns to skip past. Only
   * the caller knows whether it rendered anything, so only the caller can say.
   */
  hasData?: boolean
  /**
   * What this screen is a record of, in one line.
   *
   * Required rather than optional: a screen with no statement of its own scope
   * is the thing the preamble exists to prevent, and an optional prop would let
   * one be added without ever supplying it.
   */
  action: string
  children: ReactNode
}): ReactNode {
  const { loading, error, live, confirmedAt } = subscription
  const now = useNow()

  return (
    <>
      <Preamble
        rows={[
          { label: 'Action', value: action },
          {
            label: 'As of',
            value:
              // Never an empty AS OF row. `useNow` returns null until the
              // client has a clock, which is one render on arrival and again on
              // every navigation, and the preamble dropped the row entirely for
              // that window: a labelled line with no value, on the one line of
              // the screen that says how current the screen is.
              now === null ? (
                <p className="notice" role="status">
                  Connecting to Interchange. Nothing on screen has been confirmed yet.
                </p>
              ) : (
                <Liveness live={live} confirmedAt={confirmedAt} now={now} hasData={hasData} />
              ),
          },
          { label: 'Alert', value: error === null ? null : <ListenerError error={error} /> },
        ]}
      />
      {loading ? <Loading action={action} /> : children}
    </>
  )
}

/**
 * What is being waited for, and how long it has been.
 *
 * The word "Loading…" on an otherwise empty sheet was the whole reconciliation
 * screen for about a minute: no skeleton, no count, no progress, no way to tell
 * a slow first query from a dead one. The latency itself is environmental, a
 * development build reading a 48,000-document store, and it is not the defect.
 * A screen that gives a reader nothing to judge the wait against is.
 *
 * The elapsed seconds are the honest part. There is no progress to report,
 * because a Firestore snapshot arrives whole and reports none, so this counts
 * rather than pretending to a percentage it does not have. Past the threshold it
 * says the wait is now longer than these queries usually take, which is the
 * point at which an operator should consider reloading rather than keep waiting.
 */
const SLOW_AFTER_SECONDS = 12

function Loading({ action }: { action: string }): ReactNode {
  const now = useNow()
  const [since] = useState(() => Date.now())
  const seconds = now === null ? 0 : Math.max(0, Math.floor((now - since) / 1000))
  return (
    <p className="empty" role="status" aria-live="polite">
      {/* The action verbatim, after a colon. Folded into the sentence it read
          "Reading zones claimed by more than one publisher, collapsed with
          provenance retained from the server", which attaches the last three
          words to the wrong clause. */}
      Reading from the server: {action.charAt(0).toLowerCase() + action.slice(1)}. Waiting {seconds}
      s.{' '}
      {seconds < SLOW_AFTER_SECONDS
        ? 'Nothing is shown until the first snapshot arrives, because a partial view of a fleet is the thing this console exists not to render.'
        : 'This is longer than a first snapshot usually takes. The listener is still open and will paint as soon as it answers; if it never does, reloading starts a fresh one.'}
    </p>
  )
}
