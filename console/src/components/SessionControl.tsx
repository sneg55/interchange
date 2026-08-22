/**
 * Who you are and the way in or out, in the masthead on every route.
 *
 * It lives in the layout rather than inside `AuthGate` because the glossary is
 * deliberately ungated and therefore never mounts a gate. That left `/glossary`
 * as the one route in the console with no sign-in control anywhere, and it is
 * precisely the route the other screens deep-link into by rule anchor: a reader
 * following R6 from a notice landed on the definitions with no way to get back
 * to the finding without clicking an unrelated nav item first.
 *
 * Signed out it offers the way in; signed in it names the operator and offers
 * the way out. It is the certification line in DESIGN.md's terms, not part of
 * the document, which is why it sits in the masthead and not above the content.
 */

'use client'

import type { ReactNode } from 'react'

import type { WebConfig } from '@/lib/firestore'

import { useAuth } from './useAuth'

export function SessionControl({ config }: { config: WebConfig | null }): ReactNode {
  const { user, resolved, signIn, signOutNow } = useAuth(config)

  // Nothing until auth has answered. A "Sign in" that flashes and is replaced by
  // an operator's name reads as having been signed out and silently signed back
  // in, and the same markup on server and first client render is what keeps this
  // out of hydration.
  if (config === null || !resolved) return null

  if (user === null) {
    return (
      <button
        type="button"
        onClick={() => {
          void signIn()
        }}
      >
        Sign in
      </button>
    )
  }
  return (
    <>
      <span>{user.email ?? user.uid}</span>
      <button
        type="button"
        onClick={() => {
          void signOutNow()
        }}
      >
        Sign out
      </button>
    </>
  )
}
