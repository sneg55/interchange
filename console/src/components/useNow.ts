/**
 * A ticking clock, safe to render.
 *
 * Null until the first effect runs, deliberately. Reading `Date.now()` during
 * render makes the server HTML and the first client render disagree, which React
 * reports as a hydration mismatch and which shows the user a flash of a
 * different time. A component that needs the clock renders nothing time-shaped
 * until this returns a number.
 *
 * Shared rather than duplicated per screen, because every "N ago" on the console
 * should advance on the same beat: two clocks a few seconds apart make the same
 * timestamp read differently in two places on one page.
 */

'use client'

import { useEffect, useState } from 'react'

/** How often relative times advance. Coarse: nothing here is sub-minute. */
export const TICK_MS = 5000

export function useNow(): number | null {
  const [now, setNow] = useState<number | null>(null)

  useEffect(() => {
    setNow(Date.now())
    const timer = setInterval(() => {
      setNow(Date.now())
    }, TICK_MS)
    return () => {
      clearInterval(timer)
    }
  }, [])

  return now
}
