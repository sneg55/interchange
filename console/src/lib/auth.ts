/**
 * Identity and roles for the write path. Spec section 6.9.
 *
 * Reads go straight to Firestore under read-only security rules. Writes come
 * here first, and there is exactly one write in the whole console: moving an
 * evidence packet out of DRAFT.
 *
 * The rule this file exists to enforce: `approved_by` comes from the VERIFIED
 * ID token and never from the request body. A packet recording whoever the
 * client claimed to be would make the audit trail worthless at exactly the
 * point it matters, which is the point section 3 makes a non-goal of
 * autonomous filing.
 */

import { type App, cert, getApps, initializeApp, type ServiceAccount } from 'firebase-admin/app'
import { type DecodedIdToken, getAuth } from 'firebase-admin/auth'

import { env } from './env'

export type Role = 'viewer' | 'approver'

export interface Caller {
  uid: string
  /** The verified subject. This is what lands in `approved_by`. */
  identity: string
  role: Role
}

export class AuthError extends Error {
  constructor(
    readonly status: 401 | 403,
    message: string,
  ) {
    super(message)
    this.name = 'AuthError'
  }
}

let app: App | undefined

function adminApp(): App {
  if (app) return app
  const existing = getApps()
  if (existing.length > 0 && existing[0]) {
    app = existing[0]
    return app
  }
  const raw = env().serviceAccountJson
  app =
    raw === null
      ? initializeApp()
      : initializeApp({ credential: cert(JSON.parse(raw) as ServiceAccount) })
  return app
}

/**
 * The approver allowlist, from configuration.
 *
 * Configuration rather than a Firestore document on purpose: a role stored in
 * the same database the console can read is one misconfigured security rule
 * away from being writable by the people it governs.
 */
export function approvers(): ReadonlySet<string> {
  return env().approvers
}

function subjectOf(token: DecodedIdToken): string {
  // Prefer the verified email where the provider asserts it, and fall back to
  // the uid. Never a display name: those are user-settable and two operators
  // could share one.
  const email = typeof token.email === 'string' ? token.email : ''
  const verified = token.email_verified === true
  return verified && email ? email.toLowerCase() : token.uid
}

/**
 * Verify a bearer token and resolve the caller's role.
 *
 * Throws rather than returning a nullable caller, so a route that forgets to
 * check cannot proceed with an anonymous one. An unauthenticated visitor sees
 * nothing.
 */
export async function callerFrom(request: Request): Promise<Caller> {
  const header = request.headers.get('authorization') ?? ''
  const bearer = header.startsWith('Bearer ') ? header.slice(7).trim() : ''
  if (!bearer) {
    throw new AuthError(401, 'missing bearer token')
  }
  let token: DecodedIdToken
  try {
    // checkRevoked: a session revoked after sign-in must not still approve
    // notices, and the approval is the one irreversible-looking action here.
    token = await getAuth(adminApp()).verifyIdToken(bearer, true)
  } catch {
    throw new AuthError(401, 'invalid or revoked token')
  }
  const identity = subjectOf(token)
  return {
    uid: token.uid,
    identity,
    role: approvers().has(identity) ? 'approver' : 'viewer',
  }
}

export function requireApprover(caller: Caller): Caller {
  if (caller.role !== 'approver') {
    throw new AuthError(403, `${caller.identity} is not an approver`)
  }
  return caller
}
