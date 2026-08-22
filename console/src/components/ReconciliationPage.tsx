'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback } from 'react'

import {
  type Handlers,
  type WebConfig,
  watchLatestReconciliation,
  watchMergedZones,
} from '@/lib/firestore'
import type { CanonicalZone, ReconciliationSnapshot } from '@/lib/types'

import { AuthGate } from './AuthGate'
import { PageShell } from './PageShell'
import { RENDER_CAP, ReconciliationView } from './ReconciliationView'
import { combined, useCollection } from './useCollection'

/** What this screen is a record of. Named once: the gate shows it to a
 * signed-out visitor and the preamble shows it to a signed-in one. */
const ACTION = 'Zones claimed by more than one publisher, collapsed with provenance retained'

function ReconciliationPageInner({ config }: { config: WebConfig | null }): ReactNode {
  const zones = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<CanonicalZone>) =>
        watchMergedZones(store, RENDER_CAP, handlers),
      [],
    ),
  )
  // The cycle's own account, alongside the zones. Without it the screen could
  // only count merged zones among the ones it had loaded, and a UUID-ordered
  // window of 2,000 out of 8,839 gave "105 of 105 merged zones" for a cycle that
  // merged 391: a complete-looking count of an arbitrary subset.
  const snapshot = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<ReconciliationSnapshot>) =>
        watchLatestReconciliation(store, handlers),
      [],
    ),
  )
  return (
    <PageShell
      subscription={combined([zones, snapshot])}
      action={ACTION}
      hasData={zones.rows.length > 0}
    >
      {/* The negative control is measured by the reconciler and written to the
          snapshot; the console renders whatever the cycle recorded rather than
          hardcoding the pairs, or the control would stop being a control. */}
      <ReconciliationView
        zones={zones.rows.slice(0, RENDER_CAP)}
        snapshot={snapshot.rows[0] ?? null}
        // The query asks for cap + 1 precisely so this comparison is possible.
        queryTruncated={zones.rows.length > RENDER_CAP}
      />
    </PageShell>
  )
}

export function ReconciliationPage(
  props: Parameters<typeof ReconciliationPageInner>[0],
): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  return (
    <AuthGate config={props.config} action={ACTION}>
      {() => <ReconciliationPageInner {...props} />}
    </AuthGate>
  )
}
