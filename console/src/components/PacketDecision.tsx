/**
 * The decision controls for an evidence packet, and every reason one of them
 * might be off. Spec 6.7.
 *
 * Split from `EvidencePacketView.tsx` on the file size limit. A disabled button
 * with no stated reason is a dead control: an approver could not tell whether
 * they lacked permission, lacked a drafted notice, had not opened the tab whose
 * text the approval binds, or were looking at a finding made under rules that
 * have since changed.
 */

'use client'

import type { ReactNode } from 'react'

import { isSuperseded, RULESET_VERSION } from '@/lib/glossary'
import type { EvidencePacket } from '@/lib/types'

import { When } from './legend'

export function Decision({
  packet,
  canApprove,
  busy,
  readRegistry,
  onDecide,
}: {
  packet: EvidencePacket
  canApprove: boolean
  busy: boolean
  /** Whether the registry notice has been on screen. See `EvidencePacketView`. */
  readRegistry: boolean
  onDecide: (decision: 'APPROVED' | 'WITHHELD') => void
}): ReactNode {
  if (packet.approval_state !== 'DRAFT') {
    return (
      <div className="controls">
        <span className="count">
          {packet.approval_state === 'APPROVED' ? 'Approved' : 'Withheld'} by{' '}
          {packet.approved_by ?? 'unknown'} on <When at={packet.approved_at} />.{' '}
          {/* What happens next, on the packet itself and not only in the toast
              that appears once. "Ready to send" left an approver unable to tell
              whether a step was outstanding or the workflow had ended;
              autonomous filing is a non-goal, so it has ended.

              The withheld half of this line and the confirmation toast under it
              both ended "Nothing will be sent.", so the one sentence a reader
              needed appeared twice in adjacent lines and read as an error being
              repeated for emphasis. This states the consequence; the toast
              states what was recorded. */}
          {packet.approval_state === 'APPROVED'
            ? 'Interchange does not send notices: filing this with the registry owner is a manual step outside the product.'
            : 'This notice will not be filed with the registry owner.'}
        </span>
      </div>
    )
  }
  const undrafted = packet.registry_rendering === null
  // Drafted under rules no longer in force, so the finding it asserts may not be
  // one this system still makes. Approving it would record a human decision
  // against a named organization for a rule that has since changed its mind.
  const superseded = isSuperseded(packet.ruleset_version, packet.rule_ids)
  return (
    <div className="controls">
      <button
        type="button"
        className="primary"
        disabled={!canApprove || busy || undrafted || !readRegistry || superseded}
        onClick={() => {
          onDecide('APPROVED')
        }}
      >
        Approve for sending
      </button>
      <button
        type="button"
        className="danger"
        disabled={!canApprove || busy}
        onClick={() => {
          onDecide('WITHHELD')
        }}
      >
        Withhold
      </button>
      <BlockedReason
        canApprove={canApprove}
        undrafted={undrafted}
        superseded={superseded}
        supersededVersion={packet.ruleset_version}
        readRegistry={readRegistry}
      />
    </div>
  )
}

/**
 * Why the approve button is off, in one sentence, or nothing when it is on.
 *
 * A disabled control with no stated reason is a dead control. Extracted from
 * `Decision` because each new reason added another branch to a component that
 * was already at the complexity ceiling, and the reasons are ordered: the most
 * fundamental obstacle is the one worth naming.
 */
function BlockedReason({
  canApprove,
  undrafted,
  superseded,
  supersededVersion,
  readRegistry,
}: {
  canApprove: boolean
  undrafted: boolean
  superseded: boolean
  supersededVersion: string
  readRegistry: boolean
}): ReactNode {
  if (!canApprove) {
    return <span className="count">You have viewer access. Only an approver can decide this.</span>
  }
  const reason = undrafted
    ? 'There is no registry notice to approve yet, so there is nothing for an approval to bind to.'
    : superseded
      ? `This notice was drafted under ruleset ${supersededVersion} and ${RULESET_VERSION} is in force. The rules it cites have changed since, so it may assert a finding Interchange no longer makes. Withhold it, and let the current ruleset open a fresh one if the publisher still fails.`
      : readRegistry
        ? null
        : 'Open the Registry notice tab first. Your approval records a hash of that text, not of the decision record.'
  return reason === null ? null : <span className="count count-partial">{reason}</span>
}
