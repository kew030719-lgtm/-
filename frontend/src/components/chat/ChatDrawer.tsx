import { useEffect, useRef, useState } from 'react'

import { chatQuickPrompts } from '../../lib/format'
import type { Conversation } from '../../types'
import EvidenceCitations from '../EvidenceCitations'
import ChatActionCard from './ChatActionCard'

interface Props {
  open: boolean
  conversation: Conversation | null
  contextLabel: string
  chatBusy: boolean
  onSend: (text: string) => Promise<void>
  onConfirm: (actionId: string) => Promise<void>
  onReject: (actionId: string) => Promise<void>
  onClose: () => void
}

export default function ChatDrawer({
  open, conversation, contextLabel, chatBusy, onSend, onConfirm, onReject, onClose,
}: Props) {
  const [draft, setDraft] = useState('')
  const messagesRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const box = messagesRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [conversation, chatBusy])

  const send = (text: string) => {
    setDraft('')
    void onSend(text)
  }

  const messages = conversation?.messages ?? []
  const actions = conversation?.actions ?? []
  const empty = messages.length === 0 && actions.length === 0

  return (
    // Visibility is driven by `body.chat-open`, exactly as the previous UI did.
    <aside className="chat-drawer" id="chat-drawer" data-open={open} aria-hidden={!open} aria-label="求职 Agent 对话">
      <div className="chat-header">
        <div>
          <span className="chat-agent-mark">✦</span>
          <div>
            <b>CareerRadar Agent</b>
            <small id="chat-context-label">{contextLabel}</small>
          </div>
        </div>
        <button id="chat-close" type="button" aria-label="关闭对话" onClick={onClose}>×</button>
      </div>

      <div className="chat-quick" id="chat-quick">
        {chatQuickPrompts(conversation).map((text) => (
          <button type="button" key={text} onClick={() => send(text)}>{text}</button>
        ))}
      </div>

      <div className="chat-messages" id="chat-messages" aria-live="polite" ref={messagesRef}>
        {empty ? (
          <div className="chat-empty">
            <span>✦</span>
            <b>可以直接和我讨论求职问题</b>
            <p>提交简历后，我会结合简历证据、真实岗位和固定评分回答。</p>
          </div>
        ) : (
          <>
            {messages.map((message) => {
              const failed = !['SUCCEEDED', 'PENDING'].includes(message.status)
              const agentLabel = message.citations?.length
                ? 'PydanticAI · LangGraph · 已校验证据'
                : 'PydanticAI · LangGraph'
              return (
                <article
                  className={`chat-message ${message.role}${failed ? ' failed' : ''}`}
                  key={message.message_id}
                >
                  <div className="message-content">{message.content}</div>
                  <EvidenceCitations citations={message.citations ?? []} />
                  <small className="message-meta">
                    {message.role === 'assistant'
                      ? message.source === 'langgraph'
                        ? agentLabel
                        : message.source === 'fallback'
                          ? '本地备用分析'
                          : '系统消息'
                      : '你'}
                  </small>
                </article>
              )
            })}
            {actions.map((action) => (
              <ChatActionCard
                key={action.action_id}
                action={action}
                onConfirm={(id) => void onConfirm(id)}
                onReject={(id) => void onReject(id)}
              />
            ))}
          </>
        )}
      </div>

      <div className={chatBusy ? 'chat-typing' : 'chat-typing hidden'} id="chat-typing">
        <i /><i /><i /><span>Agent 正在读取上下文</span>
      </div>

      <form
        className="chat-form"
        id="chat-form"
        onSubmit={(event) => {
          event.preventDefault()
          if (draft.trim()) send(draft)
        }}
      >
        <textarea
          id="chat-input"
          rows={2}
          maxLength={4000}
          placeholder="问岗位排名、能力缺口，或修改搜索条件……"
          required
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (draft.trim()) send(draft)
            }
          }}
        />
        <button type="submit" aria-label="发送消息" disabled={chatBusy}>↑</button>
      </form>
      <p className="chat-footnote">修改画像或任务前会先请你确认</p>
    </aside>
  )
}