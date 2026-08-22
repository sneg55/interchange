/**
 * Screen 6. Republisher status. Spec 6.8, 6.9.
 *
 * If Interchange's own output fails validation, this screen says so first and
 * says it loudly. A merged feed that would quarantine its own publisher is the
 * one failure this project cannot ship, so the failure state is the headline
 * rather than a field somewhere in a table.
 */

'use client'

import type { ReactNode } from 'react'

import { fieldLabel } from '@/lib/format'
import type { OutputArtifact } from '@/lib/types'
import { outputHealth } from '@/lib/views'

import { PublisherLink, Term, When } from './legend'
import { Denominator, Empty, Section } from './primitives'

/** How many excluded zone ids one reason will list before it says it stopped. */
const ID_CAP = 25

/**
 * WHICH required fields were missing, on the summary line.
 *
 * "missing required field: 16151 zones" named no field, and expanding it gave 25
 * bare UUIDs. Neither told an operator what to ask a publisher to fix. The
 * republisher already computes the field list per zone; it was being discarded.
 */
function namedFields(artifact: OutputArtifact): string {
  const counts = Object.entries(artifact.missing_field_counts ?? {}).sort((a, b) => b[1] - a[1])
  if (counts.length === 0) return ''
  return ` (${counts.map(([name, n]) => `${name}: ${String(n)}`).join(', ')})`
}

/** Why one publisher was held back, or that this cycle did not record it. */
function WithheldReason({ reason }: { reason: string | undefined }): ReactNode {
  if (reason === 'NOT_POLLABLE') {
    return <span className="badge tone-unknown">no access, never polled</span>
  }
  if (reason === 'QUARANTINE')
    return <span className="badge tone-fail">quarantined this cycle</span>
  if (reason === 'NO_RETAINED_BODY') {
    // Trusted, and contributed nothing: the poll failed, or it answered 304 with
    // nothing held to answer it with. The count beside this is what its last
    // measured poll counted, not a measurement of this cycle, so the label says
    // "last known" rather than letting the number read as current.
    return <span className="badge tone-unknown">no body this cycle, count is last known</span>
  }
  // Not "quarantined". An artifact that did not record the reason has not told
  // us it was quarantine, and guessing the common case here would be this
  // system's cardinal error committed against its own records.
  return <span className="badge tone-unchecked">not recorded by this cycle</span>
}

export function OutputHealth({ artifact }: { artifact: OutputArtifact | null }): ReactNode {
  if (artifact === null) {
    return (
      <Section title="Output health">
        <Empty>No republish cycle has run yet.</Empty>
      </Section>
    )
  }
  const health = outputHealth(artifact)
  const result = artifact.validation_result
  const reasons = new Map(Object.entries(artifact.withheld_reasons ?? {}))
  return (
    <Section
      title="Output health"
      aside={
        // Not `cycle cycle-2026-08-08T19:24:23.683660+00:00`. The word "cycle"
        // appeared twice, once as the label and once inside the id, followed by
        // a wire timestamp, on a screen whose every other quantity is written
        // for a person. The exact id stays in the tooltip.
        <span className="count" title={artifact.cycle_id}>
          Cycle of <When at={artifact.at} />
        </span>
      }
    >
      {/* The verdict on Interchange's own output, at the size of a verdict. A
          merged feed that would quarantine its own publisher is the one failure
          this project cannot ship, so it is stated before anything else and in
          the same marker vocabulary the fleet board uses on everyone else. */}
      <p className={health.published ? 'headline' : 'error headline'}>
        <span className={`badge ${health.published ? 'tone-pass' : 'tone-fail'}`}>
          {health.published ? 'Passed its own gate' : 'Failed its own gate'}
        </span>
        {health.headline}
      </p>

      <table className="kv">
        <tbody>
          <tr>
            <th>
              <Term term="Passed its own gate">Self-validation</Term>
            </th>
            <td>
              {result.unresolvable
                ? 'schema unresolvable, NOT validated'
                : `${String(result.error_count)} errors against WZDx ${result.schema_version}`}
            </td>
          </tr>
          <tr>
            <th>Merged feed</th>
            <td>
              {artifact.feed_uri !== null ? (
                <a href={artifact.feed_uri}>download</a>
              ) : artifact.published ? (
                // NOT "not published". The headline directly above says the
                // cycle published, and this row said the opposite about the
                // same cycle: `feed_uri` is a storage location, and a cycle can
                // pass its own gate without anything having written the bytes
                // anywhere a browser can fetch them.
                // Which it is, and what would change it. "published, but no
                // stored copy to download" told an operator that the one
                // artifact this screen reports on could not be looked at and
                // left them unable to tell a failure from a configuration they
                // do not have.
                <span className="count">
                  This cycle validated and emitted its merged feed. No object store is configured in
                  this deployment, so no copy was kept and there is nothing to fetch from here. The
                  zones behind it are on the reconciliation screen; everything left out is below.
                </span>
              ) : (
                <span className="count">not published</span>
              )}
            </td>
          </tr>
        </tbody>
      </table>

      {/* Withheld FIRST, and separately from excluded. Quarantined publishers
          are held back before the merge, so `quarantined_sources_only` below is
          structurally zero and reading it as the account of what quarantine
          excluded is how this screen reported nothing withheld for a cycle that
          withheld 824 zones. */}
      <Section
        title="Withheld before the merge"
        aside={
          <span className={health.withheldTotal === null ? 'count count-partial' : 'count'}>
            {health.withheldTotal === null
              ? 'not recorded by this cycle'
              : `${health.withheldTotal} source zones from ${health.withheld.length} publishers`}
          </span>
        }
      >
        {health.withheld.length === 0 ? (
          <Empty>
            No publisher was quarantined this cycle, so nothing was held back before the merge.
          </Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Publisher</th>
                  <th>Source zones withheld</th>
                  {/* The reason, on the row. This table gave a publisher and a
                      count and nothing else, so why 824 zones were held back had
                      to be reconstructed by opening each publisher in turn. Two
                      reasons, and they are not interchangeable: quarantine is a
                      trust verdict and a key-gated feed is not one. */}
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {health.withheld.map(([publisher, count]) => (
                  <tr key={publisher}>
                    <td>
                      <PublisherLink publisherKey={publisher} />
                    </td>
                    <td>{count}</td>
                    <td>
                      {/* Through a Map, not a record lookup keyed by a
                          document-supplied string: a plain index reaches
                          Object.prototype, and `__proto__` would come back as an
                          object rather than as the absent reason it is. */}
                      <WithheldReason reason={reasons.get(publisher)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {health.excluded.length === 0 ? null : (
        <Section title="Excluded from the output">
          {/* Every exclusion is reported. Excluding a zone is never a silent
              drop, so the reasons are shown even when the cycle succeeded, and
              each one names the zones rather than only counting them: "1031
              missing required field" is not something an operator can act on. */}
          {health.excluded.map(({ reason, count, ids }) => {
            const shown = ids.slice(0, ID_CAP)
            return (
              <details key={reason}>
                <summary>
                  {fieldLabel(reason)}: {count} zones
                  {reason === 'missing_required_field' ? namedFields(artifact) : ''}
                </summary>
                {ids.length === 0 ? (
                  <Empty>
                    This cycle recorded no zone ids for that reason, so which zones they were is not
                    answerable from this record.
                  </Empty>
                ) : (
                  <>
                    {/* Against `count`, not `ids.length`. The ids are capped
                        twice over, once where the artifact is written and again
                        here, and a denominator of the list's own length would
                        report "25 of 200" for a reason that excluded 32,313
                        zones: a truncation stating the size of the truncation. */}
                    <Denominator
                      shown={shown.length}
                      total={count}
                      noun="zone ids"
                      shortfall="capped"
                    />
                    <pre>{shown.join('\n')}</pre>
                  </>
                )}
              </details>
            )
          })}
        </Section>
      )}

      {result.errors.length === 0 ? null : (
        <pre>
          {result.errors.join('\n')}
          {result.errors_truncated ? '\n… further errors omitted' : ''}
        </pre>
      )}
    </Section>
  )
}
