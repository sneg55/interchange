/**
 * Take this exact text with you. Spec 6.7.
 *
 * Interchange does not send notices: filing an approved one with the registry
 * owner is a manual step outside the product. That is a deliberate non-goal, and
 * it left the product telling an operator to go and file a document while giving
 * them no way to carry it. Once a decision was recorded the packet screen had no
 * controls at all, so the only route out was selecting a scrollable block of
 * text by hand, and the byte-exactness matters here more than anywhere else in
 * the console: the decision names a SHA-256 of this text.
 *
 * Clipboard only. A download would be a second artifact with its own name and
 * its own copy of the bytes; the clipboard hands over exactly what is on screen.
 */

'use client'

import type { ReactNode } from 'react'
import { useState } from 'react'

export function CopyText({ text, label }: { text: string; label: string }): ReactNode {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      // Said, not swallowed. The clipboard API refuses outside a secure context
      // and can be denied by policy, and a button that silently does nothing is
      // worse here than no button: an operator would believe they had the text.
      setState('failed')
    }
  }

  return (
    <span className="controls" style={{ margin: 0 }}>
      <button
        type="button"
        className="quiet"
        onClick={() => {
          void copy()
        }}
      >
        Copy {label}
      </button>
      {state === 'idle' ? null : state === 'copied' ? (
        <span className="count" role="status">
          Copied, byte for byte, including the wording the decision was hashed against.
        </span>
      ) : (
        <span className="count count-partial" role="status">
          This browser refused the clipboard. Select the text above and copy it by hand.
        </span>
      )}
    </span>
  )
}
