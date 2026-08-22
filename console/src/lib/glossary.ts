/**
 * Every code and term this console prints, with what it means. Spec 6.4, 6.6, 6.9.
 *
 * The console used to print `R2`, `clean streak`, `Tier 1 declared upstream` and
 * `symmetric coverage` on screens an operator acts from, and defined none of
 * them anywhere. The definitions existed, but only inside an evidence packet, so
 * the publishers with no packet, which is nearly all of them, showed rule codes a
 * reader had no way to resolve.
 *
 * One table, read by the fleet board, the publisher page, the queue, the
 * reconciliation screen and `/glossary`. A term defined in one place cannot
 * drift into two meanings, and a code with no entry here fails the test rather
 * than reaching a screen undefined.
 *
 * `asserts` mirrors `RULE_SUMMARIES` in `src/features/evidence/renderings.py`,
 * which is the text that goes into an outbound notice. Asserted in both suites.
 */

/**
 * The ruleset currently in force, mirroring `RULESET_VERSION` in
 * `src/features/trust_scorer/rules.py`. Asserted equal in `tests/test_console_api.py`.
 *
 * The console needs it to tell an approver that a draft notice was written under
 * rules no longer in force. A packet drafted under v1 asserted R6 against a feed
 * whose timestamp was two seconds ahead of the poll; v2 admits that, so the
 * notice was still in the queue making a claim the system had stopped making.
 */
export const RULESET_VERSION = 'v2'

/**
 * Every ruleset version in order, and which rules changed VERDICT in each.
 * Mirrors `RULESET_HISTORY` and `RULESET_CHANGES` in the same Python module.
 */
export const RULESET_HISTORY = ['v1', 'v2'] as const
// A Map rather than an object literal, so a version string coming off a stored
// packet cannot reach Object.prototype.
const RULESET_CHANGES: ReadonlyMap<string, readonly string[]> = new Map([['v2', ['R6']]])

/**
 * Whether a packet still asserts a finding this system makes.
 *
 * Per rule, not per version. Flagging every packet opened under an older ruleset
 * is too blunt: bumping to v2 for a change to R6 marked a Hawaii DOT R2
 * quarantine superseded, and R2 reaches the same verdict on the same evidence
 * under both versions. Blocking a still-true finding is its own wrong answer.
 *
 * A version not in the history returns true whenever the packet cites any rule
 * that ever changed: it cannot be placed in the order, so a human should look.
 */
export function supersededRules(version: string): ReadonlySet<string> {
  const index = (RULESET_HISTORY as readonly string[]).indexOf(version)
  const after: readonly string[] =
    index === -1 ? [...RULESET_CHANGES.keys()] : RULESET_HISTORY.slice(index + 1)
  return new Set(after.flatMap((v) => RULESET_CHANGES.get(v) ?? []))
}

export function isSuperseded(version: string, ruleIds: readonly string[]): boolean {
  const changed = supersededRules(version)
  return ruleIds.some((id) => changed.has(id))
}

export interface RuleEntry {
  /** The sentence a notice makes about a publisher when this rule fires. */
  asserts: string
  /** What the rule measures, and against what, for a reader who must judge it. */
  measures: string
}

export const RULES: ReadonlyMap<string, RuleEntry> = new Map([
  [
    'R1',
    {
      asserts: 'the feed did not respond across consecutive polls',
      measures:
        'Consecutive polls that got no usable response. Three in a row raises Watch, twelve raises Quarantine. A 304 Not Modified is a successful poll and ends the run, and a poll that failed at this end is not counted against the publisher at all.',
    },
  ],
  [
    'R2',
    {
      asserts: 'the feed reports a last-updated time older than its own declared cadence allows',
      measures:
        'How old the feed says its own data is, against both a fixed floor and the update interval the publisher declared to the registry. A publisher declaring a one-minute cadence is judged more strictly than one declaring a week.',
    },
  ],
  [
    'R3',
    {
      asserts: 'the feed does not validate against the WZDx version it declares',
      measures:
        'Schema errors against the exact WZDx version the publisher declares. If that version cannot be resolved the rule is suppressed rather than failed, so an Interchange-side gap never counts against a publisher.',
    },
  ],
  [
    'R4',
    {
      asserts: 'the feed marks work zones active whose end date has already passed',
      measures:
        'The share of zones marked active whose end date is in the past, over the dated active zones only. Zero dated active zones is not a pass; the rule abstains.',
    },
  ],
  [
    'R5',
    {
      asserts: 'the feed content has not changed while its last-updated time advanced',
      measures:
        'Whether the structure of the feed stayed byte-identical across a window of polls while the publisher kept advancing its own timestamp. This is the claim of freshness no timestamp check and no schema check can catch.',
    },
  ],
  [
    'R6',
    {
      asserts: 'the feed reports a last-updated time that is missing, unreadable or in the future',
      measures:
        'Whether the feed carries a usable timestamp at all. Never quarantines on its own, and is suppressed on a failed poll, where there is no document to carry one.',
    },
  ],
])

/** The `asserts` sentence for a rule, or the bare code if it has no entry. */
export function ruleAsserts(id: string): string {
  return RULES.get(id)?.asserts ?? id
}

/** Every rule id, joined into one sentence, for a queue or notice summary. */
export function assertsFor(ids: readonly string[]): string {
  return ids.map(ruleAsserts).join('; ')
}

export interface TermEntry {
  term: string
  /** One sentence, in the words an operator would use. */
  means: string
}

/**
 * Everything else the console prints that is not self-explanatory.
 *
 * Keyed by the exact string a screen renders, so a component can look one up by
 * what it is about to display rather than by a key someone has to remember.
 */
export const TERMS: readonly TermEntry[] = [
  {
    term: 'Admit',
    means:
      'The publisher passed every rule that could be evaluated on its most recent polls. Its zones go into the merged feed.',
  },
  {
    term: 'Watch',
    means:
      'At least one rule raised a reservation. The publisher still contributes to the merged feed; the reservation is recorded against it.',
  },
  {
    term: 'Quarantine',
    means:
      'A rule failed severely enough that this publisher contributes nothing to the merged feed until it recovers. Every quarantine opens a notice for a human to decide on.',
  },
  {
    term: 'No access',
    means:
      'The feed is behind an API key Interchange does not hold, so it has never been polled. This is not a trust verdict: the publisher has passed nothing and failed nothing, and it is excluded from coverage denominators rather than counted either way.',
  },
  {
    term: 'Latching',
    means:
      'Which rules are currently holding this publisher out of Admit. A rule latches when it fails and stays latched until the publisher produces a run of clean polls, so one good poll cannot clear a standing failure.',
  },
  {
    term: 'Clean streak',
    means:
      'Consecutive polls on which no latched rule fired and the rule could actually be evaluated. A poll that could not be evaluated does not extend the streak, because "we did not check" is not a clean poll.',
  },
  {
    term: 'Ruleset',
    means:
      'The version of the rule definitions in force when a decision was made. It travels with every transition, so a decision made last month is read against the rules that were in force then rather than against today’s.',
  },
  {
    term: 'Churn',
    means:
      'How much the feed’s structure actually changes between polls. R5 needs a window of polls carrying real bodies before it can measure this; until then the publisher reads as insufficient history rather than as passing.',
  },
  {
    term: 'Not modified, carried forward',
    means:
      'The publisher answered 304 Not Modified, so no new body arrived and the previous body was reused for the rules that need one. The poll counts as successful; the measurements come from the last body actually fetched.',
  },
  {
    term: 'No response',
    means:
      'The poll produced no HTTP response at all: a timeout, a refused connection, a DNS failure, or an Interchange-side error before the request completed. Distinct from a response carrying an error status.',
  },
  // The screening gate had no entry here and no mark anywhere on the console,
  // so the one part of the pipeline that touches a publisher's free text was
  // invisible in the product that runs it. A redacted road name rendered as its
  // placeholder string among ordinary road names, which is how 98.8% of the
  // live store came to be placeholders without the screen saying so once.
  {
    term: 'Screened text',
    means:
      'Every piece of free text a publisher sends, road names included, is put through a screening gate before it reaches a model, a notice or the republished feed. Structural fields are never screened; they cannot carry an instruction.',
  },
  {
    term: 'Redacted',
    means:
      'The screening gate did not return a pass for this text, so the text was replaced rather than carried. Interchange never passes unscreened free text onward, so a gate that cannot answer redacts exactly as one that refuses does.',
  },
  {
    term: 'Screening unavailable',
    means:
      'The screening service could not be reached, which is recorded as its own outcome and not as a refusal. The text is redacted either way, because the alternative is passing text nothing has looked at; what differs is that nothing is being asserted about the text or about the publisher who sent it.',
  },
  {
    term: 'Cadence',
    means:
      'Two intervals, not one: the update interval the publisher declared to the registry, and how often Interchange actually polls it. They differ when adaptive backoff slows a publisher that keeps answering unchanged.',
  },
  {
    term: 'Version',
    means:
      'The WZDx specification version the publisher declares for its own feed. R3 validates against that exact version rather than against the newest one, so a publisher is never failed for not having upgraded.',
  },
  {
    term: 'Last polled',
    means:
      'When Interchange last completed a poll of this feed. A publisher behind an API key Interchange does not hold has never been polled at all, which is not the same as a poll that failed.',
  },
  {
    term: 'Tier 1, declared upstream',
    means:
      'Two publishers were merged because they declare the same upstream data source. This is the strongest evidence in the system and needs no model: it is the publishers’ own declaration.',
  },
  {
    term: 'Tier 2, adjudicated',
    means:
      'Two zones matched on geometry but the identifiers were ambiguous, so the pair was put to a model for a yes or no. The model never touches a trust decision; it only answers whether two zones are the same work zone.',
  },
  {
    term: 'Single source',
    means: 'Only one publisher claims this zone, so there was nothing to reconcile.',
  },
  {
    term: 'Symmetric coverage',
    means:
      'The fraction of BOTH zones’ lengths that lie within the distance threshold of the other. Distance alone matches a 4.8 km ramp closure to a 33 km pavement project that runs through it; requiring that a substantial part of each coincide does not.',
  },
  {
    term: 'Withheld before the merge',
    means:
      'Zones a quarantined publisher never contributed. They are held back upstream of the merge, which is why they never appear in the exclusion counts below: those can only count zones the republisher actually received.',
  },
  {
    term: 'Passed its own gate',
    means:
      'The merged feed Interchange publishes was validated against the official WZDx schema before it was emitted. A feed that would quarantine its own publisher is not published.',
  },
]

/** The `means` sentence for a term, matched case-insensitively. */
export function termMeans(term: string): string | null {
  const needle = term.trim().toLowerCase()
  return TERMS.find((t) => t.term.toLowerCase() === needle)?.means ?? null
}

/**
 * Where `/glossary` renders this term, or null if it renders nowhere.
 *
 * The rule codes have been links into the glossary since it existed; the four
 * words those rules produce have not. `QUARANTINE` is the most consequential
 * string this console prints, it drives whether a publisher's zones reach the
 * merged feed at all, and it carried a hover tooltip and no route. A tooltip is
 * not a route: it does not exist on a touch screen and cannot be linked to.
 *
 * Must stay in step with the `id` the glossary screen puts on each entry.
 */
export function termAnchor(term: string): string | null {
  const needle = term.trim().toLowerCase()
  const found = TERMS.find((t) => t.term.toLowerCase() === needle)
  return found === undefined ? null : `/glossary#${found.term.replace(/\s+/g, '-').toLowerCase()}`
}
