/**
 * Nothing renders, and nothing subscribes, until a user is signed in. Spec 6.9.
 *
 * "An unauthenticated visitor sees nothing" is a security property in the spec,
 * and it is enforced twice on purpose: here, so a visitor is never shown data,
 * and in `infra/firestore.rules`, so the enforcement does not depend on this
 * component being used.
 *
 * Once signed in, who you are and the way out are portalled into the masthead
 * (DESIGN.md's certification line) rather than rendered above the content. They
 * are not part of the document, and giving them the top of every screen made
 * "Sign out" the most prominent control on a console whose only real action is
 * approving a notice.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'

import type { WebConfig } from '@/lib/firestore'
import { Preamble } from './apparatus'
import { Empty } from './primitives'
import { useAuth } from './useAuth'

export function AuthGate({
  config,
  action,
  children,
}: {
  config: WebConfig | null
  /**
   * What this screen is a record of.
   *
   * Rendered even when signed out. It names the route and holds no protected
   * data, and hiding it behind the gate meant a visitor sent a link to `/queue`
   * landed on an unlabelled page reading a sentence about the fleet board.
   */
  action: string
  children: (identity: string) => ReactNode
}): ReactNode {
  const { user, resolved } = useAuth(config)

  if (config === null) {
    return (
      <p className="error">
        Firebase is not configured for this deployment. Set NEXT_PUBLIC_FIREBASE_API_KEY and
        NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN.
      </p>
    )
  }
  if (!resolved) {
    return (
      <>
        <Preamble rows={[{ label: 'Action', value: action }]} />
        <Empty>Checking sign-in…</Empty>
      </>
    )
  }
  if (user === null) {
    return (
      <>
        <Preamble rows={[{ label: 'Action', value: action }]} />
        {/* The signed-out page is the volume's COVER, not an apology. It is
            most visitors' entire first impression of the product, and it used
            to be a bare button over two grey paragraphs. */}
        <div className="cover">
          {/* No sign-in button here. The masthead carries the one session
              control, on every route including the ungated glossary, and a
              second button in the body meant two "Sign in" controls on the same
              screen. This half of the gate says what the screen is and why it is
              closed; the way in is where the way out already was. */}
          {/* What this system IS, stated at the size of a title. Nothing here
              is a live figure: the whole point of the gate is that this page
              reads no data, and inventing a plausible one to look convincing
              would be exactly the failure the rest of the product exists to
              catch. "Forty" is the registry's size, not a measurement. */}
          <p className="cover-lede">
            Forty independent public agencies publish live work zone feeds to the federal WZDx
            registry. Interchange keeps the continuous record of which of them can be believed
            today.
          </p>
          {/* Broken into the four things it does, because as one paragraph of
              italic serif it was the entire first impression of the product and
              read as a preface. */}
          <ol className="gate-steps">
            <li>
              Polls every organization publishing a work zone feed to the federal WZDx registry, on
              the cadence each one declares.
            </li>
            <li>
              Scores each against a fixed ruleset. The gate is deterministic and no model takes any
              part in it; the six rules are public, in <Link href="/glossary">the definitions</Link>
              .
            </li>
            <li>
              Merges the feeds that pass into one, and validates that merged feed against the
              official WZDx schema before publishing it. A feed that would quarantine its own
              publisher is not published.
            </li>
            <li>
              Opens an evidence packet whenever a publisher&rsquo;s trust state falls, and files
              nothing without a human decision recorded against a named identity.
            </li>
          </ol>
          {/* Route-neutral. This gate renders on all six screens, and naming
              the fleet board's verdicts on the notice queue described a
              different screen than the one being asked for. */}
          <p className="empty">
            The records behind this gate are assertions about named public agencies, which is why
            they are not anonymous-readable; sign in from the top of this page. The vocabulary they
            are written in is not gated: <Link href="/glossary">the definitions</Link> are open to
            anyone, and every rule code the console prints links into them.
          </p>
        </div>
      </>
    )
  }
  // Identity and the way out are the masthead's, on every route including the
  // ungated glossary. This component used to portal them here, which meant the
  // one screen without a gate also had no session control at all.
  return <>{children(user.email ?? user.uid)}</>
}
