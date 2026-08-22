/**
 * Sign-in state. Spec 6.9.
 *
 * The console had none, and that was a real gap rather than a missing nicety:
 * the Firestore rules deny every unauthenticated read, so without a sign-in
 * flow a new browser gets permission errors on every screen, and loosening the
 * rules to compensate would make the fleet's trust verdicts world-readable.
 *
 * Nothing subscribes until a user exists. Subscribing first and letting the
 * listener fail produces an error banner on a page that is simply not signed
 * in, which teaches an operator to ignore the banner that also reports a real
 * dropped listener.
 */

'use client'

import {
  GoogleAuthProvider,
  getAuth,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type User,
} from 'firebase/auth'
import { useCallback, useEffect, useState } from 'react'

import { firebase, type WebConfig } from '@/lib/firestore'

export interface AuthState {
  user: User | null
  /** False until Firebase has answered; distinct from "signed out". */
  resolved: boolean
  signIn: () => Promise<void>
  signOutNow: () => Promise<void>
}

export function useAuth(config: WebConfig | null): AuthState {
  const [user, setUser] = useState<User | null>(null)
  const [resolved, setResolved] = useState(false)

  useEffect(() => {
    if (config === null) {
      setResolved(true)
      return
    }
    return onAuthStateChanged(getAuth(firebase(config)), (next) => {
      setUser(next)
      setResolved(true)
    })
  }, [config])

  const signIn = useCallback(async () => {
    if (config === null) return
    await signInWithPopup(getAuth(firebase(config)), new GoogleAuthProvider())
  }, [config])

  const signOutNow = useCallback(async () => {
    if (config === null) return
    await signOut(getAuth(firebase(config)))
  }, [config])

  return { user, resolved, signIn, signOutNow }
}
