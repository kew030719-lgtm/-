import { actionFieldLabel, actionLabel, actionStatus, actionValue } from '../../lib/format'
import type { ChatAction } from '../../types'

interface Props {
  action: ChatAction
  onConfirm: (actionId: string) => void
  onReject: (actionId: string) => void
}

export default function ChatActionCard({ action, onConfirm, onReject }: Props) {
  const preview = action.preview ?? {}
  const before = preview.before ?? {}
  const after = preview.after ?? {}
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  const jobCount = Number(preview.current_job_count ?? 0)
  const active = ['QUEUED', 'RUNNING', 'NEEDS_MANUAL_INPUT'].includes(preview.current_run_status ?? '')

  let changes
  if (action.kind === 'START_RESUME_TAILORING') {
    changes = (
      <div className="action-change">
        <div><small>目标公司</small><b>{preview.company || ''}</b></div>
        <span>→</span>
        <div><small>目标岗位</small><b>{preview.title || ''}</b></div>
      </div>
    )
  } else if (keys.length) {
    changes = keys.map((key) => (
      <div className="action-change" key={key}>
        <div><small>当前{actionFieldLabel(key)}</small><b>{actionValue(before[key])}</b></div>
        <span>→</span>
        <div><small>修改后</small><b>{actionValue(after[key])}</b></div>
      </div>
    ))
  } else {
    changes = (
      <div className="action-change">
        <div><small>当前任务</small><b>{preview.current_run_id || '无'}</b></div>
        <span>→</span>
        <div>
          <small>确认后</small>
          <b>{action.kind === 'START_COMPARISON' ? '生成岗位排名' : '创建新任务'}</b>
        </div>
      </div>
    )
  }

  const taskInfo = preview.current_run_id
    ? `当前任务 ${preview.current_run_id} · ${preview.current_run_status || '未知'} · ${jobCount} 个岗位`
    : ''

  let impact = ''
  if (action.kind === 'RESTART_DISCOVERY') {
    impact = active
      ? '确认后将停止当前任务并创建新的浏览器采集任务；旧岗位和报告继续保留。'
      : '确认后将创建新的浏览器采集任务；旧岗位和报告继续保留。'
  } else if (action.kind === 'START_COMPARISON') {
    impact = active
      ? `确认后将停止当前采集，并使用已有 ${jobCount} 个岗位生成排名。`
      : `确认后将使用已有 ${jobCount} 个岗位生成排名。`
  } else if (action.kind === 'START_RESUME_TAILORING') {
    impact = '确认后会对照岗位要求提出最多 5 个事实问题，不会立即改写原简历。'
  }

  const result =
    action.status === 'FAILED' ? (
      <p>{action.result?.error || '操作未执行'}</p>
    ) : action.status === 'EXECUTED' ? (
      <p>{action.result?.message || '操作已执行'}</p>
    ) : null

  return (
    <article className={`chat-action ${action.status.toLowerCase()}`}>
      <h4>{actionLabel(action.kind)} · {actionStatus(action.status)}</h4>
      <p>{preview.user_intent || ''}</p>
      {changes}
      {taskInfo ? <p>{taskInfo}</p> : null}
      {impact ? <p>{impact}</p> : null}
      {result}
      {action.status === 'PENDING' ? (
        <div className="action-buttons">
          <button className="action-confirm" onClick={() => onConfirm(action.action_id)}>确认执行</button>
          <button className="action-reject" onClick={() => onReject(action.action_id)}>取消</button>
        </div>
      ) : null}
    </article>
  )
}