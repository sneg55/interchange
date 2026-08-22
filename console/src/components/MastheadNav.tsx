/**
 * Primary navigation, with the current screen marked.
 *
 * A client component only because it needs the pathname. Four links with no
 * indication of which one you are on left an operator reading a table of
 * publishers with no way to confirm they were on the fleet board rather than a
 * filtered view of something else, and the four screens share a layout closely
 * enough that the content does not settle it.
 *
 * `aria-current="page"` carries it to assistive technology; the rule under the
 * label carries it visually. Both, because neither alone is the whole audience.
 */

'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'

import type { WebConfig } from '@/lib/firestore'

import { QueueCount } from './QueueCount'

const NAV = [
  { href: '/', label: 'Fleet' },
  { href: '/reconciliation', label: 'Reconciliation' },
  { href: '/queue', label: 'Notice queue' },
  { href: '/output', label: 'Output health' },
  // No Glossary here. The four screens above are records of the fleet; the
  // glossary is back matter. Definitions now appear AT every term and rule
  // code, and the full document keeps its stable URL from the colophon and
  // from each definition card, so nothing an operator can see is further than
  // one hover from what it means.
] as const

export function MastheadNav({ config }: { config: WebConfig | null }): ReactNode {
  const pathname = usePathname()
  return (
    <nav aria-label="Console sections">
      {NAV.map((item) => {
        // Publisher and packet pages hang off the fleet board and the queue
        // respectively, so they mark their parent rather than nothing at all.
        const current =
          item.href === '/'
            ? pathname === '/' || pathname.startsWith('/publishers')
            : pathname.startsWith(item.href) ||
              (item.href === '/queue' && pathname.startsWith('/packets'))
        return (
          <Link key={item.href} href={item.href} aria-current={current ? 'page' : undefined}>
            {item.label}
            {/* The one screen with outstanding work says so from every other
                screen. Only here: a count on Fleet or Output health would be a
                second reading of data those screens already render in full. */}
            {item.href === '/queue' ? <QueueCount config={config} /> : null}
          </Link>
        )
      })}
    </nav>
  )
}
