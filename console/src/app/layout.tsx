/**
 * Console shell. Spec 6.9.
 *
 * Six screens, and the notice queue is in the primary navigation rather than
 * buried under a publisher. Section 3 makes autonomous filing a non-goal, and
 * the queue is what turns "a human approves this" from an assertion into a
 * place someone has to visit.
 *
 * Set as a government rulemaking document (DESIGN.md): an agency block closed by
 * a double rule, one sheet of paper, and the signed-in operator carried up into
 * the masthead rather than sitting on top of the content it is not part of.
 */

import type { Metadata } from 'next'
import localFont from 'next/font/local'
import Link from 'next/link'
import type { ReactNode } from 'react'

import { IssueLine } from '@/components/IssueLine'
import { MastheadNav } from '@/components/MastheadNav'
import { SessionControl } from '@/components/SessionControl'
import { env } from '@/lib/env'

import './globals.css'

/**
 * Both typefaces are vendored under `fonts/` rather than fetched by
 * `next/font/google`. The Google loader downloads from fonts.gstatic.com at
 * BUILD time, which worked on a laptop and failed in Cloud Build every time,
 * with `Failed to fetch 'Source Serif 4' from Google Fonts` after three retries
 * per file. A build that reaches the network can fail for reasons unrelated to
 * the code. `console/scripts/vendor-fonts.sh` records where the files came from
 * and re-downloads them.
 *
 * Variable fonts, so one file per style spans the whole weight range and the
 * `weight` below is a range rather than a list.
 */

/**
 * Franklin Gothic is the American federal print and signage grotesque, and it
 * carries every part of this console an operator scans rather than reads.
 */
const gothic = localFont({
  src: './fonts/libre-franklin-latin.woff2',
  weight: '400 700',
  variable: '--font-gothic',
  display: 'swap',
})

/** The reading voice: notice prose and the paragraphs that qualify a count. */
const text = localFont({
  src: [
    { path: './fonts/source-serif-4-latin.woff2', weight: '400 600', style: 'normal' },
    { path: './fonts/source-serif-4-latin-italic.woff2', weight: '400 600', style: 'italic' },
  ],
  variable: '--font-text',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Interchange console',
  description: 'Governed ingestion across the federal WZDx publisher fleet.',
}

export default function RootLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <html lang="en" className={`${gothic.variable} ${text.variable}`}>
      <body>
        <header className="masthead">
          <div className="agency">
            <Link href="/" className="wordmark">
              Interchange
            </Link>
            <p className="agency-line">
              Governed ingestion over the federal WZDx publisher registry
            </p>
            <IssueLine />
            {env().standingNotice !== null && (
              <p className="standing-notice">
                <span className="apparatus">Standing notice</span> {env().standingNotice}
              </p>
            )}
          </div>
          <MastheadNav config={env().firebaseWebConfig} />
          {/* On every route, including the ungated glossary. This used to be an
              empty slot that AuthGate portalled into, so the one screen with no
              gate also had no way to sign in. */}
          <div className="identity">
            <SessionControl config={env().firebaseWebConfig} />
          </div>
        </header>
        <main className="sheet">{children}</main>
        <footer className="colophon">
          <span className="apparatus">Notice</span>
          <p>
            Operator console. Reads the fleet and approves notices; it never modifies another
            organization&rsquo;s data. Every rule and term it decides in is defined where it
            appears, and in full in the{' '}
            {/* The one standing route to the glossary as a document. It left
                the primary nav on purpose: it is back matter, not a screen of
                the fleet, and every rule code on every screen already links
                its own entry. */}
            <Link href="/glossary">definitions</Link>, which are public.
          </p>
        </footer>
      </body>
    </html>
  )
}
