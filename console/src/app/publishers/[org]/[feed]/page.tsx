/** Screen 2. Spec 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { PublisherView } from '@/components/PublisherView'
import { env } from '@/lib/env'
import { publisherKeyFrom } from '@/lib/format'

export const dynamic = 'force-dynamic'

/**
 * The key's two halves as two path segments.
 *
 * One parameter holding the whole `org|feedname` key put `%7C` in the middle of
 * every publisher URL an operator might paste into a message. The key still has
 * to arrive exactly, because it is the Firestore document id; it does not have
 * to arrive as one string, and neither half can contain the separator, so two
 * segments rejoin without ambiguity.
 */
interface Params {
  params: Promise<{ org: string; feed: string }>
}

// The record's own name in the tab, for the same reason the static routes carry
// theirs: an operator comparing two publishers has two tabs open. The
// organization, not the key: a tab strip is read at a glance.
export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { org } = await params
  return { title: `${decodeURIComponent(org)} · Interchange` }
}

export default async function Page({ params }: Params): Promise<ReactNode> {
  const { org, feed } = await params
  return (
    <PublisherView
      config={env().firebaseWebConfig}
      publisherKey={publisherKeyFrom(decodeURIComponent(org), decodeURIComponent(feed))}
    />
  )
}
