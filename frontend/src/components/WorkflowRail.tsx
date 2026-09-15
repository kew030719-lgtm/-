import { WORKFLOW_STEPS, stepBlockedReason } from '../lib/format'

interface Props {
  step: number
  reachable: Set<number>
  onNavigate: (step: number) => void
  onBlocked: (message: string) => void
}

export default function WorkflowRail({ step, reachable, onNavigate, onBlocked }: Props) {
  return (
    <aside className="rail" aria-label="工作流程">
      <p className="eyebrow">WORKFLOW</p>
      <ol className="steps" id="steps">
        {WORKFLOW_STEPS.map((item) => {
          const available = reachable.has(item.n)
          const className = [item.n === step ? 'active' : '', item.n < step ? 'done' : '', available ? 'reachable' : 'locked']
            .filter(Boolean)
            .join(' ')
          return (
            <li key={item.n} data-step={item.n} className={className}>
              {/* A real button so the rail is keyboard-navigable; the step is a
                  navigation target now, not just a progress indicator.
                  Deliberately NOT aria-disabled: a locked step still responds, by
                  explaining what is missing, and aria-disabled would tell assistive
                  tech (and Playwright's actionability check) that it does nothing. */}
              <button
                type="button"
                className="step-button"
                aria-current={item.n === step ? 'step' : undefined}
                title={available ? undefined : stepBlockedReason(item.n)}
                onClick={() => (available ? onNavigate(item.n) : onBlocked(stepBlockedReason(item.n)))}
              >
                <span>{String(item.n).padStart(2, '0')}</span>
                <div>
                  <b>{item.title}</b>
                  <small>{item.hint}</small>
                </div>
              </button>
            </li>
          )
        })}
      </ol>
      <div className="privacy-note">
        <b>隐私边界</b>
        <p>确认画像后不保存简历完整原文，只保留结构化资料和证据片段。</p>
      </div>
    </aside>
  )
}