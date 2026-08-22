/**
 * The components that NAME a thing and the components that EXPLAIN one.
 *
 * Split from `primitives.tsx` when that file outgrew its size budget. What is
 * here shares one concern: a value that is stored one way and must be read
 * another way. A publisher key is `org|feedname` in Firestore and "Hawaii DOT /
 * hidot" on screen. An instant is an ISO-8601 string with microseconds in the
 * record and "5d ago" on the fleet board. `R2` is a code in the transition and a
 * sentence to the operator deciding on it.
 *
 * Every one of these existed in the product already, applied on some screens and
 * not on others, which is how one publisher came to have two names and one
 * timestamp field came to have two renderings.
 */

import Link from 'next/link'
import { Fragment, type ReactNode } from 'react'

import { absolute, publisherHref, publisherName, stamp } from '@/lib/format'
import { RULES, termMeans } from '@/lib/glossary'

import { Defined } from './Defined'

/**
 * A publisher, as a reader reads one.
 *
 * The stored key stayed the display form on the queue, on output health, in the
 * packet heading and inside notices addressed to the organization itself, while
 * the fleet board and publisher page rendered the same publisher as an org and a
 * feed name. One publisher, two names, in one product.
 */
export function PublisherName({ publisherKey }: { publisherKey: string }): ReactNode {
  const { org, feed } = publisherName(publisherKey)
  return (
    <>
      {org}
      {feed === '' ? null : <span className="count"> / {feed}</span>}
    </>
  )
}

/** The same, as the link every Publisher column in the app renders. */
export function PublisherLink({ publisherKey }: { publisherKey: string }): ReactNode {
  return (
    <Link href={publisherHref(publisherKey)}>
      <PublisherName publisherKey={publisherKey} />
    </Link>
  )
}

/**
 * A rule code, carrying what it asserts.
 *
 * `R2` appeared in the Latching column of the fleet board, in the Rules column
 * of every transition, and on the publisher summary, and was defined on none of
 * them. The definition existed only inside an evidence packet, which most
 * publishers do not have.
 */
export function RuleCode({ id }: { id: string }): ReactNode {
  const entry = RULES.get(id)
  if (entry === undefined) return <>{id}</>
  return (
    <Defined
      className="rulecode"
      href={`/glossary#${id}`}
      headword={id}
      detail={entry.asserts}
      body={entry.measures}
    >
      {id}
    </Defined>
  )
}

/**
 * A comma-joined list of rule codes, each one resolvable.
 *
 * One wrapper with `white-space: nowrap`, not a span per code. Table cells carry
 * `overflow-wrap: anywhere` so a long publisher key cannot force the table
 * wider, and with each code in its own inline box that broke `R3, R4, R5` onto
 * three lines with the commas orphaned at the start of each.
 */
export function RuleCodes({ ids }: { ids: readonly string[] }): ReactNode {
  if (ids.length === 0) return <span className="count">none</span>
  return (
    <span className="rulelist">
      {ids.map((id, i) => (
        <Fragment key={id}>
          {i === 0 ? null : ', '}
          <RuleCode id={id} />
        </Fragment>
      ))}
    </span>
  )
}

/**
 * A stored instant, said in the console's own idiom with the exact time behind it.
 *
 * Never the wire form. A raw microsecond ISO-8601 stamp appeared 30 times across
 * six screens, sixteen of them on one page, while the fleet board rendered the
 * same field as "5d ago". The formatter existed; those screens did not use it.
 */
export function Timestamp({ at, now }: { at: string | null; now: number }): ReactNode {
  const { text, title } = stamp(at, now)
  return <time title={title}>{text}</time>
}

/**
 * A stored instant where the DATE is what matters, not how long ago it was.
 *
 * A decision record and a transition are read as "when did this happen", not as
 * "how stale is this"; a relative stamp on an audit row makes two readers a week
 * apart describe the same decision differently. The exact stored value stays in
 * the tooltip, because this is the one place a reader may need it verbatim.
 */
export function When({ at }: { at: string | null }): ReactNode {
  if (at === null || at === '') return <span className="count">not recorded</span>
  return <time title={at}>{absolute(at)}</time>
}

/**
 * A term with its definition attached, at the term.
 *
 * Rendered as the word with the definition card on hover and focus, instead of
 * the browser `title` slip this used to be: a definition a keyboard or a touch
 * screen could never reach was a definition most operators never had. A term
 * with no entry renders plainly rather than silently claiming to be defined.
 */
export function Term({
  term,
  children,
  focusable = true,
}: {
  term: string
  children?: ReactNode
  /** False inside another control, where a nested tab stop would be invalid. */
  focusable?: boolean
}): ReactNode {
  const means = termMeans(term)
  const shown = children ?? term
  if (means === null) return <>{shown}</>
  return (
    <Defined className="term" headword={term} body={means} focusable={focusable}>
      {shown}
    </Defined>
  )
}
