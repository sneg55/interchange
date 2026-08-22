/**
 * What has already been decided, and by whom. Spec 6.9.
 *
 * The queue shows drafts only, so deciding a packet removed it from the one list
 * that showed packets and the only remaining route to it was Fleet, publisher,
 * transitions, packet. The approval binds the approver's verified identity and a
 * SHA-256 of the exact text they approved: that is the auditable act in this
 * product, and it had nowhere to be read.
 *
 * Newest first, unlike the queue. The queue is oldest-first so nothing sits in
 * it unnoticed; this is a log, and a log is read from the top.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'

import type { EvidencePacket } from '@/lib/types'
import { cap } from '@/lib/views'

import { Stamp } from './apparatus'
import { PublisherLink, When } from './legend'
import { Denominator, Empty, Section } from './primitives'

/** How many decisions the screen holds. Stated on screen when it bites. */
export const DECIDED_CAP = 50

export function DecidedNotices({
  packets,
  queryTruncated,
}: {
  packets: readonly EvidencePacket[]
  /**
   * Whether the QUERY hit its limit, before this component saw anything.
   *
   * Without it the denominator is computed over an already-truncated array and
   * always reads "showing all", which is silent truncation wearing the label
   * that was meant to prevent it.
   */
  queryTruncated: boolean
}): ReactNode {
  const rendered = cap([...packets], DECIDED_CAP)
  return (
    <Section
      title="Decided"
      aside={
        <Denominator
          shown={rendered.shown}
          total={rendered.matched}
          noun="decisions shown"
          shortfall="capped"
        />
      }
    >
      {rendered.items.length === 0 ? (
        <Empty>No notice has been decided yet.</Empty>
      ) : (
        <>
          <p className="empty">
            {/* Not `rendered.note` as well. The section header directly above
                already renders "N of M decisions shown", and printing "Showing
                all 1 in view." underneath it stated the same fact twice in two
                different phrasings. */}
            {rendered.capped ? rendered.note : ''}
            {queryTruncated
              ? ' The query also hit its limit, so earlier decisions exist that were not loaded.'
              : ''}
          </p>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Decided</th>
                  <th>Publisher</th>
                  <th>Decision</th>
                  <th>By</th>
                  {/* Not "Text approved". A withheld notice approved nothing,
                      and this column printed a hash under that header on every
                      WITHHELD row. What the hash identifies is the text the
                      decision was made against, whichever way it went. */}
                  <th>Text decided on</th>
                  <th>Notice</th>
                </tr>
              </thead>
              <tbody>
                {rendered.items.map((p) => (
                  <tr key={p.packet_id}>
                    <td>
                      <When at={p.approved_at} />
                    </td>
                    {/* To the publisher, matching every other Publisher column
                        in the app. The notice itself has its own column. */}
                    <td>
                      {p.publisher_keys.map((key) => (
                        <PublisherLink key={key} publisherKey={key} />
                      ))}
                    </td>
                    <td>
                      <Stamp state={p.approval_state} />
                    </td>
                    {/* The verified identity, from the token rather than the
                        request body. An approval naming whoever the client
                        claimed to be would be worthless here. */}
                    <td>{p.approved_by ?? <span className="count">not recorded</span>}</td>
                    <td>
                      {p.approved_rendering_sha256 === null ? (
                        <span className="badge tone-unchecked">no hash recorded</span>
                      ) : (
                        // The hash of what was READ, not of what the packet says
                        // now. The packet stays mutable while the finding
                        // persists, so this is what makes the decision mean a
                        // specific piece of text.
                        <code
                          title={`SHA-256 of the notice text this decision was made against: ${p.approved_rendering_sha256}`}
                        >
                          {p.approved_rendering_sha256.slice(0, 12)}
                        </code>
                      )}
                    </td>
                    <td>
                      <Link href={`/packets/${encodeURIComponent(p.packet_id)}`}>open notice</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  )
}
