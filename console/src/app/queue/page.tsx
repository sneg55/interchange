/** Screen 5. Spec 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { QueueView } from '@/components/QueueView'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

// Named per route. Six screens sharing one title left an operator with the
// fleet, a publisher and a packet open in three tabs unable to tell them apart.
export const metadata: Metadata = { title: 'Notice queue · Interchange' }

export default function Page(): ReactNode {
  return <QueueView config={env().firebaseWebConfig} />
}
