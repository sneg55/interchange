/** Screen 1, the fleet board. Spec 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { FleetView } from '@/components/FleetView'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

// Named per route. Six screens sharing one title left an operator with the
// fleet, a publisher and a packet open in three tabs unable to tell them apart.
export const metadata: Metadata = { title: 'Fleet · Interchange' }

export default function Page(): ReactNode {
  // Config crosses to the client explicitly rather than through a global. The
  // web config is public by design; the service account never leaves the server.
  return <FleetView config={env().firebaseWebConfig} />
}
