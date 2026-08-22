'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback } from 'react'

import {
  type Handlers,
  type WebConfig,
  watchDecidedPackets,
  watchDraftPackets,
} from '@/lib/firestore'
import type { EvidencePacket } from '@/lib/types'
import { AuthGate } from './AuthGate'
import { DECIDED_CAP, DecidedNotices } from './DecidedNotices'
import { NoticeQueue } from './NoticeQueue'
import { PageShell } from './PageShell'
import { combined, useCollection } from './useCollection'

/** What this screen is a record of. Named once: the gate shows it to a
 * signed-out visitor and the preamble shows it to a signed-in one. */
const ACTION = 'Findings awaiting a human decision, and the decisions already recorded'

function QueueViewInner({ config }: { config: WebConfig | null }): ReactNode {
  const drafts = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<EvidencePacket>) => watchDraftPackets(store, handlers),
      [],
    ),
  )
  // The decided log alongside the queue, on the same screen. Approving a packet
  // removed it from the only list that showed packets, so the one auditable act
  // in this product left nothing an operator could browse.
  const decided = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<EvidencePacket>) =>
        watchDecidedPackets(store, DECIDED_CAP, handlers),
      [],
    ),
  )
  return (
    <PageShell
      subscription={combined([drafts, decided])}
      action={ACTION}
      hasData={drafts.rows.length + decided.rows.length > 0}
    >
      <NoticeQueue packets={drafts.rows} />
      {/* cap + 1 was queried, so "more exist than are shown" is knowable rather
          than assumed. */}
      <DecidedNotices packets={decided.rows} queryTruncated={decided.rows.length > DECIDED_CAP} />
    </PageShell>
  )
}

export function QueueView(props: Parameters<typeof QueueViewInner>[0]): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  return (
    <AuthGate config={props.config} action={ACTION}>
      {() => <QueueViewInner {...props} />}
    </AuthGate>
  )
}
