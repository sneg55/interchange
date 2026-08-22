/**
 * The 404. Spec 6.9.
 *
 * Next's built-in 404 renders its own markup with its own inline colours, which
 * on this paper ground came out near-white on near-white under a stray black
 * bar: a page a mistyped URL reaches, in the one design language this console
 * does not speak, effectively unreadable, and with no route back.
 *
 * It is deliberately a record like every other screen: a preamble saying what
 * the page is, and cross-references out. A dead end is the one thing a console
 * for tracing a finding to its evidence must not have.
 */

import type { Metadata } from 'next'
import Link from 'next/link'
import type { ReactNode } from 'react'
import { CrossRefs, Preamble } from '@/components/apparatus'
import { Empty, Section } from '@/components/primitives'

export const metadata: Metadata = { title: 'No such record · Interchange' }

export default function NotFound(): ReactNode {
  return (
    <>
      <Preamble
        rows={[
          { label: 'ACTION', value: 'No record exists at this address' },
          {
            label: 'AS OF',
            // Not a liveness line. Nothing was fetched, so claiming any
            // as-of time would be asserting a read that never happened.
            value: 'Nothing was looked up, so there is nothing to be current about.',
          },
        ]}
      />
      <Section title="Not found">
        <Empty>
          This address does not correspond to a publisher, a packet or a screen. It may have been
          mistyped, or it may name a record this deployment has never held. Interchange does not
          delete records, so a publisher or packet that existed still exists.
        </Empty>
        <CrossRefs>
          <Link href="/">Fleet board</Link>
          <Link href="/queue">Notice queue</Link>
          <Link href="/reconciliation">Reconciliation</Link>
          <Link href="/output">Output health</Link>
          <Link href="/glossary">Glossary</Link>
        </CrossRefs>
      </Section>
    </>
  )
}
