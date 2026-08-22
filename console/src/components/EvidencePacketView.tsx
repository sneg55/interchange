/**
 * Screen 4. Both renderings of one packet, and the approval action. Spec 6.7, 6.9.
 *
 * Two tabs over ONE packet rather than two documents: the consumer decision
 * record and the registry notice assert the same facts to different audiences,
 * and rendering them from separate sources would let them drift.
 *
 * The approval button is the human gate section 3 requires. It is disabled for
 * a viewer, disabled once a decision exists, and its success state says "ready
 * to send" rather than "sent", because nothing in this system sends anything.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'
import { useState } from 'react'

import { publisherHref, publisherName } from '@/lib/format'
import type { EvidencePacket } from '@/lib/types'

import { CrossRefs, Stamp } from './apparatus'
import { CopyText } from './CopyText'
import { PublisherName, RuleCodes, When } from './legend'
import { Decision } from './PacketDecision'
import { Denominator, Empty, Section } from './primitives'

export interface ApprovalResult {
  ok: boolean
  message: string
}

export function EvidencePacketView({
  packet,
  canApprove,
  onDecide,
}: {
  packet: EvidencePacket
  canApprove: boolean
  onDecide: (decision: 'APPROVED' | 'WITHHELD') => Promise<ApprovalResult>
}): ReactNode {
  const [tab, setTab] = useState<'consumer' | 'registry'>('consumer')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ApprovalResult | null>(null)
  /**
   * Whether the registry notice has actually been on screen.
   *
   * The approval binds a SHA-256 of the registry rendering, and the button was
   * enabled on arrival with the consumer decision record showing, so the one
   * auditable human act in this product could attest to text the human had
   * never seen. Withholding is deliberately not gated: it sends nothing.
   */
  const [readRegistry, setReadRegistry] = useState(false)

  const body = tab === 'consumer' ? packet.consumer_rendering : packet.registry_rendering

  async function decide(decision: 'APPROVED' | 'WITHHELD'): Promise<void> {
    setBusy(true)
    try {
      setResult(await onDecide(decision))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      // The organization and what the finding is, not the document id. The
      // heading was the raw composite key: publisher, state, publisher again and
      // a microsecond timestamp, pipe-delimited, on the one screen whose whole
      // purpose is to be read by a person before they accuse a named
      // organization of something. The id is still exact in the URL, where it
      // has to be, and on the record line below.
      title={packet.publisher_keys.map((k) => publisherName(k).org).join(', ')}
      aside={
        <span className="controls" style={{ margin: 0 }}>
          <span className="count">
            {packet.publisher_keys.map((key) => (
              <PublisherName key={key} publisherKey={key} />
            ))}{' '}
            · ruleset {packet.ruleset_version} · rules <RuleCodes ids={packet.rule_ids} />
          </span>
          {/* The document's own mark. An undecided notice is dashed and unstamped;
              a decided one carries the decision on its face, so which it is does
              not depend on reading the controls at the bottom of the screen. */}
          <Stamp state={packet.approval_state} />
        </span>
      }
    >
      {/* The way out. Three screens link into a packet and it linked nowhere,
          so a finding was a dead end: an approver could not reach the publisher
          whose history the finding rests on without retyping a URL. */}
      <CrossRefs>
        {packet.publisher_keys.map((key) => (
          <Link key={key} href={publisherHref(key)}>
            {publisherName(key).org} reliability history
          </Link>
        ))}
        <Link href="/queue">Notice queue</Link>
        <Link href="/glossary">Glossary</Link>
      </CrossRefs>

      {/* Real tab roles. `aria-selected` on a bare button is not valid ARIA: the
          attribute only carries meaning on a role that defines it, so a screen
          reader was told these were two ordinary buttons and never that one of
          them was the current view. */}
      <div className="tabs" role="tablist" aria-label="Packet rendering">
        <button
          type="button"
          role="tab"
          id="tab-consumer"
          aria-controls="panel-rendering"
          aria-selected={tab === 'consumer'}
          onClick={() => {
            setTab('consumer')
          }}
        >
          Consumer decision record
        </button>
        <button
          type="button"
          role="tab"
          id="tab-registry"
          aria-controls="panel-rendering"
          aria-selected={tab === 'registry'}
          onClick={() => {
            setTab('registry')
            setReadRegistry(true)
          }}
        >
          Registry notice
        </button>
      </div>

      <div
        className="rendering"
        id="panel-rendering"
        role="tabpanel"
        aria-labelledby={tab === 'consumer' ? 'tab-consumer' : 'tab-registry'}
      >
        {/* What this text IS, once a decision exists. The notice body ends
            "This notice is a draft and requires human approval before it is
            sent", which stayed on screen under a WITHHELD stamp: the document
            said one thing and the mark on it said another. The body is not
            rewritten, because it is the exact text the decision was hashed
            against and rewriting it would break that binding. It is labelled. */}
        {packet.approval_state === 'DRAFT' ? null : (
          <p className="notice" role="status">
            This is the text as it stood when the decision was recorded, including its own draft
            wording and its own <code>Status: open</code> line. It is kept exactly as it was read,
            because the decision names a hash of it.
          </p>
        )}
        {body === null ? (
          <Empty>This rendering has not been drafted yet.</Empty>
        ) : (
          // Marked as a quotation once a decision exists, not merely captioned.
          // The frozen body still reads `Status: open` under an APPROVED stamp,
          // which is correct and unavoidable, and a reader who skipped the note
          // above met a live-looking status contradicting the mark on the
          // document. `blockquote` and a changed rule say "this is quoted text"
          // before the sentence explaining why does.
          <blockquote className={packet.approval_state === 'DRAFT' ? undefined : 'frozen'}>
            <pre>{body}</pre>
          </blockquote>
        )}
        {/* Interchange does not send notices, so the operator has to carry this
            somewhere. Until now nothing on the screen would give it to them. */}
        {body === null ? null : (
          <CopyText
            text={body}
            label={tab === 'consumer' ? 'decision record' : 'registry notice'}
          />
        )}
      </div>

      <div className="controls">
        {/* Never the embedded count alone. A packet open for years shows the 50
            observations the finding rests on, and saying so is the difference
            between "showing 50 of 8,412" and "there were 50". */}
        <Denominator
          shown={packet.observations.length}
          total={packet.total_observations}
          noun="observations embedded"
          one="observation embedded"
          shortfall="capped"
        />
        <span className="count">
          {/* Said, rather than printed as two wire timestamps. When start and
              end are the same instant the finding rests on a single poll, and a
              window rendered as "T to T" invited a reader to take it for a span
              that had been examined. */}
          {packet.observation_window.start === packet.observation_window.end ? (
            <>
              one poll, at <When at={packet.observation_window.start} />
            </>
          ) : (
            <>
              window <When at={packet.observation_window.start} /> to{' '}
              <When at={packet.observation_window.end} />
            </>
          )}
        </span>
      </div>

      <Decision
        packet={packet}
        canApprove={canApprove}
        busy={busy}
        readRegistry={readRegistry}
        onDecide={(d) => {
          void decide(d)
        }}
      />

      {result === null ? null : <p className={result.ok ? 'count' : 'error'}>{result.message}</p>}
    </Section>
  )
}
