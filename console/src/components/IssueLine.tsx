/**
 * The masthead's date line: the register's device for "this is today's issue".
 *
 * The real clock, client-side through `useNow` so the server HTML and the
 * first client render cannot disagree, and nothing time-shaped renders until
 * the clock exists. Deliberately NOT a volume-and-number line: an issue number
 * would have to be computed from an invented epoch, and this masthead does not
 * print numbers nothing measured.
 */

'use client'

import type { ReactNode } from 'react'

import { useNow } from './useNow'

export function IssueLine(): ReactNode {
  const now = useNow()
  if (now === null) return null
  const date = new Date(now).toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
  return <p className="issue-line apparatus">{date}</p>
}
