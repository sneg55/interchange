/** Screen 3. Spec 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { ReconciliationPage } from '@/components/ReconciliationPage'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

// Named per route. Six screens sharing one title left an operator with the
// fleet, a publisher and a packet open in three tabs unable to tell them apart.
export const metadata: Metadata = { title: 'Reconciliation · Interchange' }

export default function Page(): ReactNode {
  return <ReconciliationPage config={env().firebaseWebConfig} />
}
