/**
 * The single place this app reads its environment. Spec 6.9.
 *
 * Mirrors `src/utils/env.py` on the Python side and exists for the same reason:
 * a `process.env` read buried in a module fails at request time, in production,
 * on the one code path nobody exercised locally. Validated once here, the
 * failure happens at boot with the name of the missing variable.
 *
 * The approver allowlist is the reason this matters most. It is configuration
 * rather than a Firestore document because a role stored in the database the
 * console reads is one misconfigured security rule away from being writable by
 * the people it governs.
 */

export interface Env {
  /** Firestore/Firebase project. */
  projectId: string
  /** Optional service-account JSON; absent means Application Default Credentials. */
  serviceAccountJson: string | null
  /** Verified identities permitted to move a packet out of DRAFT. */
  approvers: ReadonlySet<string>
  /**
   * Operator-declared standing notice, printed in the masthead on every route.
   * Set while scheduled polling is suspended so the sheet states its own
   * staleness instead of leaving readers to infer it from old timestamps.
   */
  standingNotice: string | null
  /** Client-side Firebase config, safe to ship to the browser. */
  firebaseWebConfig: {
    apiKey: string
    authDomain: string
    projectId: string
    /**
     * Local emulator host, e.g. `localhost`. Null in every real deployment.
     *
     * Carried on the config rather than read where the SDK is initialised, so
     * the decision to talk to an emulator is made once, at the boundary, where
     * it is visible. A `process.env` check buried beside `initializeApp` is how
     * a build ends up pointed at localhost in production.
     */
    emulatorHost: string | null
  } | null
}

export class EnvError extends Error {
  constructor(name: string, why: string) {
    super(`environment variable ${name} ${why}`)
    this.name = 'EnvError'
  }
}

function read(name: string): string {
  // eslint-disable-next-line no-restricted-properties, security/detect-object-injection -- this module IS the env boundary and `name` is a literal from this file
  const value = process.env[name]
  return typeof value === 'string' ? value.trim() : ''
}

function required(name: string): string {
  const value = read(name)
  if (!value) {
    throw new EnvError(name, 'is required but was empty or unset')
  }
  return value
}

let cached: Env | undefined

export function env(): Env {
  if (cached) return cached
  const apiKey = read('NEXT_PUBLIC_FIREBASE_API_KEY')
  const authDomain = read('NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN')
  const projectId = required('GOOGLE_CLOUD_PROJECT')
  const list = read('INTERCHANGE_APPROVERS')
    .split(',')
    .map((entry) => entry.trim().toLowerCase())
    .filter((entry) => entry.length > 0)

  cached = {
    projectId,
    serviceAccountJson: read('FIREBASE_SERVICE_ACCOUNT') || null,
    // An empty allowlist is legal and means nobody can approve anything. That
    // is the safe direction: a misconfigured deployment should stall the notice
    // queue, not open it.
    approvers: new Set(list),
    standingNotice: read('INTERCHANGE_STANDING_NOTICE') || null,
    firebaseWebConfig:
      apiKey && authDomain
        ? {
            apiKey,
            authDomain,
            projectId,
            emulatorHost: read('NEXT_PUBLIC_FIREBASE_EMULATOR_HOST') || null,
          }
        : null,
  }
  return cached
}

/** Test seam. Never called in production code. */
export function resetEnvForTest(): void {
  cached = undefined
}
