/**
 * A definition where the term is, instead of a page the term points at.
 *
 * The previous device was a `title` attribute: invisible on touch, invisible
 * to the keyboard, styled by the browser as a grey slip that reads as debug
 * output. Real products do not send a reader to a glossary page to learn a
 * word; the definition appears at the word. This renders it as a printed
 * footnote card in the register's own materials.
 *
 * `position: fixed`, because most triggers live inside `.scroll-x` containers
 * whose `overflow: auto` would clip an absolutely-positioned card. Fixed
 * positioning escapes the clip without a portal; nothing above these tables
 * carries a transform that would re-capture it.
 */

'use client'

import {
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react'

/** Where the open card sits, in viewport coordinates. */
interface Anchor {
  top: number
  left: number
  above: boolean
}

const CARD_WIDTH = 320
const CARD_CLEARANCE = 170

export function Defined({
  headword,
  detail,
  body,
  href,
  className,
  focusable = true,
  children,
}: {
  /** The term, as the card's own heading. */
  headword: string
  /** An optional line under the headword: what a rule asserts. */
  detail?: string
  body: string
  /** When set, the trigger is a real link (a rule code into /glossary). */
  href?: string
  className?: string
  /**
   * False when the trigger sits inside another control (a sortable header's
   * button): a second tab stop nested in a button is two controls fused into
   * one target, so there the card is hover-only and the button stays the
   * control.
   */
  focusable?: boolean
  children: ReactNode
}): ReactNode {
  const id = useId()
  const ref = useRef<HTMLElement | null>(null)
  const [anchor, setAnchor] = useState<Anchor | null>(null)

  const open = useCallback(() => {
    const el = ref.current
    if (el === null) return
    const rect = el.getBoundingClientRect()
    const above = rect.bottom > window.innerHeight - CARD_CLEARANCE
    setAnchor({
      top: above ? rect.top : rect.bottom,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - CARD_WIDTH - 8)),
      above,
    })
  }, [])
  const close = useCallback(() => {
    setAnchor(null)
  }, [])
  const onKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === 'Escape') setAnchor(null)
  }, [])

  // Scrolling moves the trigger but not a fixed-position card, and it does not
  // blur a focused trigger either, so a card left open drifted over unrelated
  // content. Capture phase, so scrolls inside `.scroll-x` close it too.
  useEffect(() => {
    if (anchor === null) return
    const onScroll = (): void => {
      setAnchor(null)
    }
    window.addEventListener('scroll', onScroll, { passive: true, capture: true })
    return () => {
      window.removeEventListener('scroll', onScroll, { capture: true })
    }
  }, [anchor])

  const card =
    anchor === null ? null : (
      <span
        className="defcard"
        role="tooltip"
        id={id}
        style={
          anchor.above
            ? { left: anchor.left, bottom: window.innerHeight - anchor.top + 6 }
            : { left: anchor.left, top: anchor.top + 6 }
        }
      >
        <span className="defcard-term apparatus">{headword}</span>
        {detail === undefined ? null : <span className="defcard-detail">{detail}</span>}
        <span className="defcard-body">{body}</span>
      </span>
    )

  // A callback ref, because the trigger is an anchor on one branch and a span
  // on the other and one typed object ref cannot be both.
  const attach = (el: HTMLElement | null): void => {
    ref.current = el
  }
  const shared = {
    'aria-describedby': anchor === null ? undefined : id,
    onMouseEnter: open,
    onMouseLeave: close,
    onFocus: open,
    onBlur: close,
    onKeyDown,
  }

  if (href !== undefined) {
    return (
      // A plain anchor rather than next/link: every href here is an in-app
      // fragment into /glossary, and the ref plus pointer handlers are the
      // point of this component.
      <a href={href} className={className} ref={attach} {...shared}>
        {children}
        {card}
      </a>
    )
  }
  return (
    <span className={className} ref={attach} tabIndex={focusable ? 0 : undefined} {...shared}>
      {children}
      {card}
    </span>
  )
}
