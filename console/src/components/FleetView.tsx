/** Client half of screen 1: subscribe, then render. Spec 6.9. */

'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback } from 'react'

import { type Handlers, type WebConfig, watchRecentTransitions } from '@/lib/firestore'
import type { TrustTransition } from '@/lib/types'
import { AuthGate } from './AuthGate'
import { FleetBoard } from './FleetBoard'
import { PageShell } from './PageShell'
import { combined, useCollection } from './useCollection'
import { useFleet } from './useFleet'
import { useNow } from './useNow'

/** What this screen is a record of. Named once: the gate shows it to a
 * signed-out visitor and the preamble shows it to a signed-in one. */
const ACTION = 'Continuous trust evaluation of every organization in the federal WZDx registry'

/** How many fleet-wide transitions the front page's log holds. Stated on it. */
const DOCKET_CAP = 12

function FleetViewInner({ config }: { config: WebConfig | null }): ReactNode {
  // Through PageShell rather than rendering the error itself. Hand-rolling the
  // two lines here is how this screen missed the liveness banner: it was the one
  // view that did not go through the wrapper whose whole job is to carry it.
  const fleet = useFleet(config)
  const transitions = useCollection<TrustTransition>(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<TrustTransition>) =>
        watchRecentTransitions(store, DOCKET_CAP, handlers),
      [],
    ),
  )
  const now = useNow()
  // Pessimistic on both axes: the page is as live as its stalest listener.
  const status = combined([fleet, transitions])
  return (
    <PageShell subscription={status} action={ACTION} hasData={fleet.records.length > 0}>
      {now === null ? null : (
        <FleetBoard
          records={fleet.records}
          transitions={transitions.rows}
          transitionCap={DOCKET_CAP}
          now={now}
        />
      )}
    </PageShell>
  )
}

export function FleetView(props: Parameters<typeof FleetViewInner>[0]): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  return (
    <AuthGate config={props.config} action={ACTION}>
      {() => <FleetViewInner {...props} />}
    </AuthGate>
  )
}
