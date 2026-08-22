/**
 * How this console says a time, a duration, an identity and a poll outcome.
 *
 * One module because the same fact was being formatted three ways on three
 * screens. The fleet board rendered a poll as "5d ago" and the publisher page
 * rendered the identical field as `2026-08-08T19:24:23.683660+00:00`; the board
 * said `1h declared 168h` where the publisher page said `604800s, polling every
 * 3600s`; the fleet called a publisher `Hawaii DOT / hidot` where the queue,
 * output health and every notice called it `Hawaii DOT|hidot`.
 *
 * None of those are storage problems. The stored value is the ISO string and the
 * pipe-joined key, and both are correct; what was missing was one place deciding
 * how they are read aloud. That is this file.
 *
 * Nothing here loses information. A relative stamp carries the absolute one in
 * its tooltip, and the absolute one is the readable form rather than the wire
 * form: an operator hovering a cell should get "8 Aug 2026, 19:24 UTC", not six
 * digits of fractional second.
 */

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const

/**
 * An instant, to the minute, in UTC.
 *
 * UTC and said so. The fleet spans Quebec City to Hawaii DOT and the records are
 * stored in UTC; rendering an operator's local zone without naming it would make
 * two people reading the same row disagree about when it happened.
 *
 * Returns the input unchanged when it cannot be parsed. A timestamp this
 * function cannot read is still evidence, and swallowing it to 'unknown' would
 * be this system's own cardinal error: presenting "we could not read it" as if
 * nothing had been recorded.
 */
export function absolute(iso: string | null): string {
  if (iso === null || iso === '') return 'not recorded'
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return iso
  const d = new Date(at)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${String(d.getUTCDate())} ${MONTHS[d.getUTCMonth()] ?? '?'} ${String(
    d.getUTCFullYear(),
  )}, ${hh}:${mm} UTC`
}

/** Whole seconds since `at`, as the coarsest useful unit. */
export function since(at: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - at) / 1000))
  if (seconds < 60) return `${String(seconds)}s ago`
  if (seconds < 3600) return `${String(Math.floor(seconds / 60))}m ago`
  if (seconds < 86400) return `${String(Math.floor(seconds / 3600))}h ago`
  return `${String(Math.floor(seconds / 86400))}d ago`
}

/**
 * A stored instant, said the way the fleet board says it, with the absolute
 * behind it.
 *
 * Returns both halves rather than a string, so a caller can put the exact time
 * in a `title` without every caller reinventing which half goes where.
 */
export function stamp(iso: string | null, now: number): { text: string; title: string } {
  if (iso === null || iso === '') return { text: 'never', title: 'no poll recorded' }
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return { text: iso, title: iso }
  return { text: since(at, now), title: absolute(iso) }
}

/**
 * A duration in seconds, as the coarsest exact unit.
 *
 * Exact, not approximate: 604800s is 7d and 3600s is 1h, and a cadence that is
 * not a whole number of the next unit up keeps the smaller one rather than being
 * rounded into a claim the publisher did not make.
 */
export function duration(seconds: number): string {
  if (seconds <= 0) return 'not polled'
  if (seconds % 86400 === 0) return `${String(seconds / 86400)}d`
  if (seconds % 3600 === 0) return `${String(seconds / 3600)}h`
  if (seconds % 60 === 0) return `${String(seconds / 60)}m`
  return `${String(seconds)}s`
}

/** An age in seconds, coarse, for "how old is this feed's own data". */
export function age(seconds: number | null): string {
  if (seconds === null) return 'unknown'
  const days = seconds / 86400
  if (days >= 1) return `${days.toFixed(0)}d`
  return `${(seconds / 3600).toFixed(1)}h`
}

/**
 * A publisher key, split into the two things it is made of.
 *
 * The key is `org|feedname` and is the document id, so it has to stay exact in
 * URLs and in evidence. It does not have to be READ that way, and reading it
 * that way is how the same publisher appeared under two names in one product.
 */
export function publisherName(key: string): { org: string; feed: string } {
  const bar = key.indexOf('|')
  if (bar < 0) return { org: key, feed: '' }
  return { org: key.slice(0, bar), feed: key.slice(bar + 1) }
}

/**
 * Where a publisher's page lives, with the key's two halves as two segments.
 *
 * The route used to take the whole key as one parameter, so every link an
 * operator could send a colleague read
 * `/publishers/Utah%20DOT%7Cudot`: the pipe is not URL-safe, and `%7C` in the
 * middle of an address is this system's storage separator shown to a person.
 * Two segments carry the same key exactly, rejoin without ambiguity, because
 * neither half may contain a `|` by construction, and read as a path.
 *
 * A key with no separator at all still routes: the feed segment is empty and
 * `publisherKeyFrom` gives the original string back.
 */
export function publisherHref(key: string): string {
  const { org, feed } = publisherName(key)
  return `/publishers/${encodeURIComponent(org)}/${encodeURIComponent(feed)}`
}

/** The inverse, for the route that receives those two segments. */
export function publisherKeyFrom(org: string, feed: string): string {
  return feed === '' ? org : `${org}|${feed}`
}

/** A fleet state, as a human reads it rather than as the enum spells it. */
export function stateLabel(state: string): string {
  return state.replace(/_/g, ' ')
}

/**
 * What one poll actually did, in words.
 *
 * The screen used to print `HTTP 304 (304, carried forward)`, which states the
 * number twice and explains it never, and `HTTP 0`, which is not a status code
 * at all: it is the sentinel for a request that got no response. An operator
 * reading a reliability history needs the outcome, and the status number is a
 * detail behind it rather than the headline.
 */
export function pollOutcome(o: {
  http_status: number
  not_modified: boolean
  carried_forward: boolean
  error: string | null
}): {
  /** The outcome, in words. What a reader scanning the column needs. */
  text: string
  /** The machine detail, short enough to sit beside it on every row. */
  code: string
  /** The full explanation, for a tooltip and for the one-off summary row. */
  detail: string
  tone: 'pass' | 'warn' | 'fail'
} {
  if (o.error !== null) {
    // Status 0 means no response was received at all. Any other status with an
    // error means a response arrived and was unusable, which is a different
    // thing to tell a publisher about.
    const ours = o.error.startsWith('Interchange ')
    return {
      text: o.http_status === 0 ? (ours ? 'not attempted' : 'no response') : 'refused',
      code: o.http_status === 0 ? '' : `HTTP ${String(o.http_status)}`,
      detail: o.error,
      tone: 'fail',
    }
  }
  if (o.not_modified) {
    return {
      text: 'not modified',
      code: 'HTTP 304',
      detail: o.carried_forward
        ? 'HTTP 304 Not Modified. No new body arrived, so the previous body was reused for the rules that need one.'
        : 'HTTP 304 Not Modified. No new body arrived.',
      tone: 'pass',
    }
  }
  if (o.http_status >= 200 && o.http_status < 300) {
    return {
      text: 'fetched',
      code: `HTTP ${String(o.http_status)}`,
      detail: `HTTP ${String(o.http_status)}. A body arrived and was measured.`,
      tone: 'pass',
    }
  }
  return {
    text: 'refused',
    code: `HTTP ${String(o.http_status)}`,
    detail: `The publisher answered ${String(o.http_status)}.`,
    tone: 'warn',
  }
}

/**
 * A latency reading, where "the poll never completed" is not zero milliseconds.
 *
 * A failed poll used to render `0ms` beside four columns rendering an em dash
 * for the same absence, and the daily rollup on the same page reported the same
 * latency as not measured. Zero is the best possible value; printing it for a
 * measurement that was never taken is this system's cardinal error committed in
 * its own furniture.
 */
export function latency(ms: number | null): string {
  if (ms === null) return 'not measured'
  return `${ms.toFixed(0)}ms`
}

/**
 * A stored field name, as a label.
 *
 * `update_date` and `end_date` are the publisher's field names and belong in
 * evidence verbatim. As a row label on an operator's screen they were just the
 * schema leaking through the page.
 */
export function fieldLabel(name: string): string {
  return name.replace(/_/g, ' ')
}
