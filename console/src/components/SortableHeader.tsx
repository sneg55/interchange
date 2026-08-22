/**
 * The fleet board's sortable column header.
 *
 * Split out of `FleetBoard.tsx` on the file size limit. It is a real button
 * rather than a `<th>` with a click handler: the table had inert headers and no
 * other way to ask "which publishers are furthest behind", and a div-with-onclick
 * answers the mouse while leaving the keyboard out.
 */

'use client'

import type { ReactNode } from 'react'

import type { SortKey } from '@/lib/views'

import { Term } from './legend'

export function SortableHeader({
  label,
  column,
  sort,
  onSort,
  secondary = false,
  mark,
}: {
  label: string
  column: SortKey
  sort: { key: SortKey; descending: boolean }
  onSort: (key: SortKey) => void
  /**
   * Whether this column gives way on a narrow screen.
   *
   * At 390px the board needed 828px of table inside a 358px box, so four of
   * seven columns lived behind a horizontal drag with nothing indicating they
   * were there. Hiding the three that are context rather than verdict is the
   * honest version of the same trade, and the board states that it has done it.
   */
  secondary?: boolean
  /**
   * A footnote mark, rendered BESIDE the sort button rather than inside it.
   * Inside would nest an anchor in a button, which is invalid and hands the
   * keyboard two controls fused into one target.
   */
  mark?: ReactNode
}): ReactNode {
  const active = sort.key === column
  return (
    <th
      className={
        [secondary ? 'col-secondary' : '', mark === undefined ? '' : 'th-marked']
          .filter(Boolean)
          .join(' ') || undefined
      }
      aria-sort={active ? (sort.descending ? 'descending' : 'ascending') : 'none'}
    >
      <button
        type="button"
        className="sort"
        // Named explicitly rather than left to name-from-content. The two
        // headers carrying a glossary definition render their label inside a
        // <span title>, and those were the only two of seven whose accessible
        // name came back empty. Whatever the cause, a sort control should say
        // that it sorts, and this settles it either way.
        aria-label={`Sort by ${label}`}
        onClick={() => {
          onSort(column)
        }}
      >
        {/* The column name carries its own definition where one exists. Churn
            and Latching are terms of art in this product and were printed as
            bare headers over values only their author could read. */}
        <Term term={label} focusable={false}>
          {label}
        </Term>
        <span className="count">
          {' '}
          {active ? (sort.descending ? '\u2193' : '\u2191') : '\u2195'}
        </span>
      </button>
      {mark}
    </th>
  )
}
