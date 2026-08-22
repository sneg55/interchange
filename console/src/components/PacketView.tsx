'use client'

import type { Firestore } from 'firebase/firestore'
import type { ReactNode } from 'react'
import { useCallback, useRef, useState } from 'react'

import { type Handlers, idToken, type WebConfig, watchPacket } from '@/lib/firestore'
import type { EvidencePacket } from '@/lib/types'
import { AuthGate } from './AuthGate'
import { type ApprovalResult, EvidencePacketView } from './EvidencePacketView'
import { PageShell } from './PageShell'
import { Empty } from './primitives'
import { useCollection } from './useCollection'

/** Web Crypto: the same digest the server recomputes from its own copy. */
async function sha256(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

function PacketViewInner({
  config,
  packetId,
  approvers,
  identity,
}: {
  config: WebConfig | null
  packetId: string
  approvers: readonly string[]
  /** The signed-in subject, from the AuthGate. Never from the client payload. */
  identity: string
}): ReactNode {
  const [decided, setDecided] = useState(false)
  const reviewed = useRef<string | null>(null)
  const subscription = useCollection(
    config,
    useCallback(
      (store: Firestore, handlers: Handlers<EvidencePacket>) =>
        watchPacket(store, packetId, handlers),
      [packetId],
    ),
  )

  const packet = subscription.rows[0] ?? null
  const live = packet?.registry_rendering ?? null
  // First non-null rendering wins and is held. Everything the operator reads and
  // decides on is this copy.
  if (reviewed.current === null && live !== null) reviewed.current = live
  // The listener replaced the text underneath the reader. The controls go away
  // rather than silently deciding on the new revision.
  const changedUnderneath = reviewed.current !== null && live !== null && live !== reviewed.current

  const decide = useCallback(
    async (decision: 'APPROVED' | 'WITHHELD'): Promise<ApprovalResult> => {
      if (config === null) {
        return { ok: false, message: 'Firebase is not configured.' }
      }
      // The rendering CAPTURED when this packet was presented, not the live
      // one. Hashing at click time re-hashes whatever the listener has just
      // replaced it with, so an operator who read Tuesday's text would silently
      // approve Thursday's. The captured copy makes the mismatch a 409.
      const rendering = reviewed.current
      if (rendering === null) {
        return { ok: false, message: 'Nothing drafted to approve.' }
      }
      const token = await idToken(config)
      if (token === null) {
        return { ok: false, message: 'Sign in before deciding.' }
      }
      // The write goes through the authenticated route. `approved_by` is taken
      // from the verified token there and is deliberately not sent from here:
      // there is no field in this body that could carry an identity.
      const response = await fetch(`/api/packets/${encodeURIComponent(packetId)}/approve`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${token}`,
        },
        // The hash of the text ACTUALLY DISPLAYED. The server compares it
        // inside the transaction, so a stale tab gets a 409 rather than
        // approving a revision nobody read.
        body: JSON.stringify({
          decision,
          rendering_sha256: await sha256(rendering),
        }),
      })
      const payload = (await response.json()) as { error?: string }
      if (!response.ok) {
        return { ok: false, message: payload.error ?? 'Approval failed.' }
      }
      setDecided(true)
      return {
        ok: true,
        // Says what happens next, not only what did not happen. Autonomous
        // filing is an explicit non-goal, so nothing in Interchange sends this
        // and nothing ever will; the button read "Approve for sending" and left
        // an approver with no idea whether a step was outstanding or the
        // workflow had ended.
        message:
          decision === 'APPROVED'
            ? // Not a second copy of the "filing is manual" sentence. The decision
              // line directly above already states it, and printing the same 96
              // characters twice, adjacent, made the confirmation read as an
              // error being repeated for emphasis.
              'Approved, and recorded against your identity with a hash of the exact text you read.'
            : // Not a second copy of "nothing will be sent". The decision line
              // directly above already states the consequence, and this line
              // and that one both ended with the same sentence: the one fact an
              // approver needed, printed twice in adjacent lines. The approve
              // path had already been de-duplicated this way; this one had not.
              'Withheld, and recorded against your identity with a hash of the exact text you read.',
      }
    },
    [config, packetId],
  )

  return (
    <PageShell
      subscription={subscription}
      action="One finding, rendered for the data consumer and for the registry owner"
      hasData={packet !== null}
    >
      {packet === null ? (
        <Empty>{decided ? 'Decision recorded.' : `No packet ${packetId}.`}</Empty>
      ) : (
        <>
          {changedUnderneath ? (
            <p className="error" role="alert">
              This finding was updated while you were reading it. Reload before deciding: an
              approval names the text it approved.
            </p>
          ) : null}
          <EvidencePacketView
            packet={packet}
            canApprove={!changedUnderneath && approvers.includes(identity.toLowerCase())}
            onDecide={decide}
          />
        </>
      )}
    </PageShell>
  )
}

export function PacketView(
  props: Omit<Parameters<typeof PacketViewInner>[0], 'identity'>,
): ReactNode {
  // Gated, so nothing subscribes before a user exists. Firestore denies every
  // unauthenticated read, and a listener that fails for want of sign-in would
  // raise the same banner as a genuinely dropped one.
  // The gate hands down the verified subject, so the button reflects whether
  // THIS user may approve rather than whether anyone may. The route re-checks
  // against the token regardless; this only stops the UI lying.
  return (
    <AuthGate
      config={props.config}
      action="One finding, rendered for the data consumer and for the registry owner"
    >
      {(identity) => <PacketViewInner {...props} identity={identity} />}
    </AuthGate>
  )
}
