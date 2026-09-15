import type { ChatCitation } from '../types'

/**
 * Verbatim quotes for an answer. The backend fills these from the cited blocks,
 * so they are evidence, not model prose.
 */
export default function EvidenceCitations({
  citations,
  className = 'chat-citations',
  summaryPrefix = '',
}: {
  citations: ChatCitation[]
  className?: string
  summaryPrefix?: string
}) {
  if (!citations.length) return null
  return (
    <div className={className}>
      {citations.map((citation, index) => {
        const source = citation.source_type === 'resume' ? '简历证据' : '岗位证据'
        return (
          <details key={`${citation.block_id}-${index}`}>
            <summary>
              {summaryPrefix}
              {source} {index + 1} · {citation.block_id}
            </summary>
            <blockquote>{citation.quote}</blockquote>
          </details>
        )
      })}
    </div>
  )
}