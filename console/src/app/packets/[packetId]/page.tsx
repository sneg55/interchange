/** Screen 4. Spec 6.7, 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { PacketView } from '@/components/PacketView'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

// The record's own name in the tab, for the same reason the static routes
// carry theirs: an operator comparing two publishers has two tabs open.
export async function generateMetadata({
  params,
}: {
  params: Promise<{ packetId: string }>
}): Promise<Metadata> {
  const { packetId } = await params
  return { title: `Notice · ${decodeURIComponent(packetId).split('|')[0]} · Interchange` }
}

export default async function Page({
  params,
}: {
  params: Promise<{ packetId: string }>
}): Promise<ReactNode> {
  const { packetId } = await params
  return (
    <PacketView
      config={env().firebaseWebConfig}
      packetId={decodeURIComponent(packetId)}
      // The allowlist itself is never shipped to the browser. What crosses is
      // the caller's own membership, resolved server-side, so a viewer sees a
      // disabled button rather than one that 403s on click. The route re-checks
      // regardless: this is a UI honesty fix, not the authorization.
      approvers={[...env().approvers]}
    />
  )
}
