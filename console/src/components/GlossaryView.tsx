/**
 * Screen 7. What every code and term on the other six screens means. Spec 6.9.
 *
 * The console printed `R2`, `clean streak`, `Tier 1 declared upstream`,
 * `symmetric coverage`, `carried forward`, `NOT MEASURED`, `NOT CHECKED` and
 * `INSUFFICIENT HISTORY` on screens an operator decides from, and defined none
 * of them anywhere a reader could reach. The definitions did exist, inside
 * evidence packets, which most publishers do not have.
 *
 * A static page rather than a modal, because it must be linkable: every rule
 * code in the app is an anchor into this document, so "what is R2" is one click
 * from the row that raised it rather than a search through a spec.
 *
 * No listener and no data. Nothing here is a claim about the fleet, so it needs
 * no liveness banner and cannot go stale against the server.
 */

import type { ReactNode } from 'react'

import { RULES, RULESET_VERSION, TERMS } from '@/lib/glossary'

import { Preamble } from './apparatus'
import { Section } from './primitives'

export function GlossaryView(): ReactNode {
  return (
    <>
      <Preamble
        rows={[
          { label: 'Action', value: 'The vocabulary this console decides in, defined' },
          {
            label: 'As of',
            // Deliberately not a timestamp. Every other screen's AS OF says how
            // current its data is; this document has no data, and stamping it
            // would invite a reader to check whether the definitions were live.
            value: 'Definitions, not measurements. This screen does not go stale.',
          },
        ]}
      />

      {/* The version in force, read from the same constant the packet screen
          uses to decide whether a draft notice is superseded. It was the literal
          `v1` while `RULESET_VERSION` was `v2`, so the one screen whose job is
          to be the authority on this vocabulary named the wrong ruleset. */}
      <Section
        title="Rules"
        aside={
          <span className="count">
            {RULES.size} in ruleset {RULESET_VERSION}
          </span>
        }
      >
        <p className="empty">
          Every trust decision in Interchange is one of these rules firing. The gate is
          deterministic: a rule reads measured quantities and returns a verdict, and no model has
          any part in it. A rule that cannot be evaluated returns “not applicable”, which is
          recorded distinctly from a pass.
        </p>
        <dl className="glossary">
          {[...RULES].map(([id, entry]) => (
            <div key={id} id={id}>
              <dt>
                {id}
                <span className="count"> {entry.asserts}</span>
              </dt>
              <dd>{entry.measures}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Terms" aside={<span className="count">{TERMS.length} defined</span>}>
        <dl className="glossary">
          {TERMS.map((t) => (
            <div key={t.term} id={t.term.replace(/\s+/g, '-').toLowerCase()}>
              <dt>{t.term}</dt>
              <dd>{t.means}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="How absence is written">
        {/* The distinction the whole product turns on, spelled out once. Four
            different markers appear across the six screens for four different
            kinds of "we do not know", and a reader who cannot tell them apart
            cannot tell a passing publisher from an unchecked one. */}
        <p className="empty">
          Interchange never records “we did not check” as “we checked and it passed”. Four different
          things are four different words, and none of them is a pass:
        </p>
        <dl className="glossary">
          <div>
            <dt>Not applicable</dt>
            <dd>
              The rule ran against a real body and genuinely did not apply. It counts toward
              recovery, and it is not a pass.
            </dd>
          </div>
          <div>
            <dt>Not checked</dt>
            <dd>
              No poll in this period carried a body the rule could read, so the rule did not run.
              Distinct from zero errors.
            </dd>
          </div>
          <div>
            <dt>Not measured</dt>
            <dd>
              The quantity was never taken. Distinct from a measured zero, which is the best
              possible value rather than the absence of one.
            </dd>
          </div>
          <div>
            <dt>Insufficient history</dt>
            <dd>
              The rule needs a run of polls it has not had yet. A separate axis from the trust
              state, not a fifth state.
            </dd>
          </div>
        </dl>
      </Section>
    </>
  )
}
