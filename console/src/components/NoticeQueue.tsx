/**
 * Screen 5. The human gate as an actual queue. Spec 6.9.
 *
 * Oldest first, because the point of a queue is that nothing sits in it
 * unnoticed. A packet that has been waiting three weeks should be the first
 * thing an approver sees, not buried under this morning's findings.
 */

'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'

import { assertsFor, isSuperseded } from '@/lib/glossary'
import type { EvidencePacket } from '@/lib/types'
import { noticeQueue } from '@/lib/views'

import { PublisherLink, RuleCodes, When } from './legend'
import { Empty, Section } from './primitives'

export function NoticeQueue({ packets }: { packets: readonly EvidencePacket[] }): ReactNode {
  const queue = noticeQueue(packets)
  return (
    <Section
      title="Notice queue"
      aside={<span className="count">{queue.length} awaiting a decision</span>}
    >
      {queue.length === 0 ? (
        <Empty>
          Nothing awaiting approval. Findings appear here as drafts; none is ever sent without a
          decision.
        </Empty>
      ) : (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Opened</th>
                <th>Publisher</th>
                <th>Asserts</th>
                <th>Rules</th>
                <th>Drafted</th>
                <th>Notice</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((p) => (
                <tr key={p.packet_id}>
                  {/* The date, not the wire form. This column printed four raw
                      microsecond ISO stamps on a screen whose every other
                      quantity is written for a person. */}
                  <td>
                    <When at={p.created_at} />
                  </td>
                  {/* The publisher, to the publisher. A link reading "Hawaii
                      DOT|hidot" under a column headed Publisher opened a PACKET
                      here while the identical link on the fleet board and on
                      output health opened the publisher, and this is the screen
                      where an approver most needs the reliability history
                      before deciding. */}
                  <td>
                    {p.publisher_keys.map((key) => (
                      <PublisherLink key={key} publisherKey={key} />
                    ))}
                  </td>
                  {/* One source for what a rule asserts, shared with the notice
                      the registry owner receives and with the glossary. Two
                      copies of this sentence drifted apart once already. */}
                  <td className="prose">{assertsFor(p.rule_ids)}</td>
                  <td>
                    <RuleCodes ids={p.rule_ids} />
                  </td>
                  <td>
                    {p.registry_rendering === null ? (
                      // Surfaced rather than hidden: a packet with no draft
                      // cannot be approved, and an approver needs to know why the
                      // button is disabled before they click it.
                      <span className="badge tone-unchecked">not drafted</span>
                    ) : isSuperseded(p.ruleset_version, p.rule_ids) ? (
                      // Drafted under rules no longer in force. Marked in the
                      // queue and not only on the packet, because the queue is
                      // where a run of them gets worked through, and a reviewer
                      // should not have to open each one to find the two that
                      // assert a finding the system has since stopped making.
                      <span className="badge tone-unchecked">
                        superseded ruleset {p.ruleset_version}
                      </span>
                    ) : (
                      <span className="count">ready for review</span>
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
      )}
    </Section>
  )
}
