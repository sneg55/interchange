/**
 * How many notices are waiting, on every screen. Spec 6.9.
 *
 * The queue is the only place in this product where a human has to do
 * something, and its count appeared in exactly one place: the queue itself. An
 * operator on the fleet board, on a publisher's history or on output health had
 * no way to know a notice was sitting undecided without going to look, and
 * "nothing sits in it unnoticed" is the queue's whole reason for being ordered
 * oldest-first.
 *
 * Signed-in only, and silent until it knows. An unauthenticated visitor must not
 * subscribe to anything, and a badge that rendered `0` before its first snapshot
 * would be saying "nothing is waiting" about a queue it had not read yet.
 */

'use client'

import type { ReactNode } from 'react'

import { type WebConfig, watchDraftPackets } from '@/lib/firestore'
import type { EvidencePacket } from '@/lib/types'
import { noticeQueue } from '@/lib/views'

import { useAuth } from './useAuth'
import { useCollection } from './useCollection'

export function QueueCount({ config }: { config: WebConfig | null }): ReactNode {
  const { user } = useAuth(config)
  // Gated on the user, not only on config. Subscribing before sign-in trips the
  // Firestore rules and would surface a permission error on every screen.
  const packets = useCollection<EvidencePacket>(user === null ? null : config, watchDraftPackets)
  if (user === null || packets.loading || packets.error !== null) return null
  const waiting = noticeQueue(packets.rows).length
  if (waiting === 0) return null
  return (
    <>
      <span className="navcount" aria-hidden="true">
        {waiting}
      </span>
      {/* The number in words, because a bare numeral appended to a link name
          reads as "Notice queue 2" and says nothing about what two is. */}
      <span className="sr-only">, {waiting} awaiting a decision</span>
    </>
  )
}
