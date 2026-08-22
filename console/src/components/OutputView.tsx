'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback } from 'react'

import { type Handlers, type WebConfig, watchLatestOutput } from '@/lib/firestore'
import type { OutputArtifact } from '@/lib/types'
import { AuthGate } from './AuthGate'
import { OutputHealth } from './OutputHealth'
import { PageShell } from './PageShell'
import { useCollection } from './useCollection'

/** What this screen is a record of. Named once: the gate shows it to a
 * signed-out visitor and the preamble shows it to a signed-in one. */
const ACTION = 'The merged feed Interchange republishes, and its result against the official schema'

function OutputViewInner({ config }: { config: WebConfig | null }): ReactNode {
  const subscribe = useCallback(
    (store: Firestore, handlers: Handlers<OutputArtifact>) => watchLatestOutput(store, handlers),
    [],
  )
  const output = useCollection(config, subscribe)
  return (
    <PageShell subscription={output} action={ACTION} hasData={output.rows.length > 0}>
      <OutputHealth artifact={output.rows[0] ?? null} />
    </PageShell>
  )
}

export function OutputView(props: Parameters<typeof OutputViewInner>[0]): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  return (
    <AuthGate config={props.config} action={ACTION}>
      {() => <OutputViewInner {...props} />}
    </AuthGate>
  )
}
