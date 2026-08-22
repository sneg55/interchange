/**
 * The approval decision. Spec sections 3, 6.7 and 6.9.
 *
 * Mirrors `src/features/evidence/approval.py`, and deliberately repeats its
 * checks rather than trusting the client to have made them. The Python module
 * governs the record; this governs the request.
 *
 * Three properties, each of which is the reason a specific mistake cannot
 * happen here:
 *
 * - `approved_by` is the verified identity, never the request body.
 * - A decision cannot be overwritten. The first decision is the one that was
 *   actually made, and a second would silently rewrite who approved and when.
 * - Approval binds to the hash of the rendering it approved, so it cannot
 *   survive the evidence changing underneath it.
 *
 * The terminal state is "ready to send", not "sent". Section 3 makes autonomous
 * filing a non-goal and nothing in this file dispatches anything.
 */

import { createHash } from 'node:crypto'

import type { ApprovalState, EvidencePacket } from './types'

export type Decision = Extract<ApprovalState, 'APPROVED' | 'WITHHELD'>

export interface ApprovalRequest {
  packetId: string
  decision: Decision
  /**
   * SHA-256 of the rendering the operator READ before deciding.
   *
   * Required, not optional. Without it a stale tab approves whatever the packet
   * says now: the transaction re-reads the latest revision and hashes that, so
   * an operator who reviewed Tuesday's text approves Thursday's. Comparing the
   * displayed hash inside the transaction turns that into a 409.
   */
  renderingSha256: string
  note?: string
}

export interface ApprovalWrite {
  approval_state: Decision
  approved_by: string
  approved_at: string
  approved_rendering_sha256: string
  note: string | null
}

export class ApprovalError extends Error {
  constructor(
    readonly status: 400 | 404 | 409,
    readonly errorId: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApprovalError'
  }
}

export function parseDecision(value: unknown): Decision {
  if (value === 'APPROVED' || value === 'WITHHELD') return value
  throw new ApprovalError(
    400,
    'E_EVID_002',
    `decision must be APPROVED or WITHHELD, got ${JSON.stringify(value)}`,
  )
}

export function renderingHash(rendering: string): string {
  return createHash('sha256').update(rendering, 'utf8').digest('hex')
}

/**
 * Build the write for one decision, or throw.
 *
 * `identity` comes from the caller resolved by `auth.ts`. It is a separate
 * parameter rather than a field on the request precisely so that a route
 * cannot accidentally pass the body through: there is no shape of
 * `ApprovalRequest` that carries an identity.
 */
export function approvalWrite(
  packet: EvidencePacket,
  request: ApprovalRequest,
  identity: string,
  at: string,
): ApprovalWrite {
  if (identity.trim().length === 0) {
    throw new ApprovalError(400, 'E_EVID_002', 'refusing to record an anonymous decision')
  }
  if (packet.approval_state !== 'DRAFT') {
    throw new ApprovalError(
      409,
      'E_EVID_003',
      `packet ${packet.packet_id} is already ${packet.approval_state}`,
    )
  }
  const rendering = packet.registry_rendering
  if (rendering === null || rendering.trim().length === 0) {
    // Approving a packet with nothing drafted would produce an approved notice
    // with no text, and the approval would attest to nothing.
    throw new ApprovalError(
      404,
      'E_EVID_001',
      `packet ${packet.packet_id} has no registry rendering to approve`,
    )
  }
  const current = renderingHash(rendering)
  if (current !== request.renderingSha256) {
    // The packet changed between display and decision. Extending a packet
    // already returns it to DRAFT on the Python side, but the window between
    // rendering and clicking is not covered by that, and an approval that
    // attests to text nobody read is the failure the whole gate exists to
    // prevent.
    throw new ApprovalError(
      409,
      'E_EVID_003',
      `packet ${packet.packet_id} changed since it was displayed; re-read it before deciding`,
    )
  }
  return {
    approval_state: request.decision,
    approved_by: identity,
    approved_at: at,
    approved_rendering_sha256: current,
    note: request.note ?? null,
  }
}
