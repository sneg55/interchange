/** Screen 7. What the other six screens are written in. Spec 6.9. */

import type { Metadata } from 'next'
import type { ReactNode } from 'react'

import { GlossaryView } from '@/components/GlossaryView'

export const metadata: Metadata = { title: 'Glossary · Interchange' }

// Dynamic despite having no live data of its own, because the masthead's session
// control reads the Firebase config from the runtime environment. Prerendered at
// build time this page baked in a null config and rendered no sign-in control at
// all, which is the same build-time-versus-runtime trap that once deployed a
// console reporting no Firebase config with every value set.
export const dynamic = 'force-dynamic'

// No auth gate and no listener. This document contains no assertion about any
// named organization, which is the thing the gate exists to protect: it is the
// definitions the assertions are written in, and a reader who cannot see it
// cannot check the rest.
export default function Page(): ReactNode {
  return <GlossaryView />
}
