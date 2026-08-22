/**
 * The only write in the console. Spec 6.9.
 *
 * Reads go straight to Firestore from the client under read-only security
 * rules; this route exists because the approval must be audited and the client
 * cannot be trusted to say who it is.
 *
 * The packet is re-read inside the transaction rather than taken from the
 * request. Two approvers hitting the queue at the same moment would otherwise
 * both see DRAFT, both pass the check, and the second write would silently
 * overwrite who approved and when.
 */

import { getFirestore } from 'firebase-admin/firestore'
import { type NextRequest, NextResponse } from 'next/server'

import { ApprovalError, approvalWrite, parseDecision } from '@/lib/approval'
import { AuthError, callerFrom, requireApprover } from '@/lib/auth'
import type { EvidencePacket } from '@/lib/types'

export const runtime = 'nodejs'

interface Body {
  decision?: unknown
  note?: unknown
  /** Hash of the rendering the operator read. Not an identity; a freshness check. */
  rendering_sha256?: unknown
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ packetId: string }> },
): Promise<NextResponse> {
  const { packetId } = await context.params
  try {
    const caller = requireApprover(await callerFrom(request))
    const body = (await request.json()) as Body
    const decision = parseDecision(body.decision)
    const note = typeof body.note === 'string' ? body.note : undefined
    const seen = typeof body.rendering_sha256 === 'string' ? body.rendering_sha256 : ''
    if (seen === '') {
      throw new ApprovalError(
        400,
        'E_EVID_002',
        'rendering_sha256 is required: an approval must name the text it approved',
      )
    }
    const at = new Date().toISOString()

    const db = getFirestore()
    const ref = db.collection('evidence_packets').doc(packetId)

    const write = await db.runTransaction(async (tx) => {
      const snapshot = await tx.get(ref)
      if (!snapshot.exists) {
        throw new ApprovalError(404, 'E_EVID_001', `no packet ${packetId}`)
      }
      const packet = snapshot.data() as EvidencePacket
      const update = approvalWrite(
        packet,
        note === undefined
          ? { packetId, decision, renderingSha256: seen }
          : { packetId, decision, note, renderingSha256: seen },
        // The verified identity, never the body. This is the line the whole
        // route exists to protect.
        caller.identity,
        at,
      )
      tx.update(ref, { ...update })
      return update
    })

    return NextResponse.json({
      packet_id: packetId,
      ...write,
      // "ready to send", never "sent". Section 3 makes autonomous filing a
      // non-goal and nothing here dispatches anything.
      terminal_state: write.approval_state === 'APPROVED' ? 'READY_TO_SEND' : 'WITHHELD',
    })
  } catch (error) {
    if (error instanceof AuthError) {
      return NextResponse.json({ error: error.message }, { status: error.status })
    }
    if (error instanceof ApprovalError) {
      return NextResponse.json(
        { error: error.message, error_id: error.errorId },
        { status: error.status },
      )
    }
    // Logged with the packet id so an operator can find it, and reported as a
    // failure rather than swallowed. An approval that silently did nothing is
    // worse than one that errored.
    console.error(`[E_EVID_001] approval failed for ${packetId}`, error)
    return NextResponse.json({ error: 'approval failed' }, { status: 500 })
  }
}
