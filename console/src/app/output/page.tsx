/** Screen 6. Spec 6.8, 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { OutputView } from '@/components/OutputView'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

// Named per route. Six screens sharing one title left an operator with the
// fleet, a publisher and a packet open in three tabs unable to tell them apart.
export const metadata: Metadata = { title: 'Output health · Interchange' }

export default function Page(): ReactNode {
  return <OutputView config={env().firebaseWebConfig} />
}
