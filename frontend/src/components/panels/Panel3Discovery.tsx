import { useState } from 'react'

import type { JobSnapshot, SiteOption, Task } from '../../types'

interface Props {
  active: boolean
  runId: string | null
  task: Task | null
  jobs: JobSnapshot[]
  /** The boards this run actually crawls, so links point at the right places. */
  sites: SiteOption[]
  onRetry: () => Promise<void>
  onCancel: () => Promise<void>
  onFinish: () => Promise<void>
  onRefreshCount: () => Promise<void>
  onManual: (url: string, content: string) => Promise<void>
  onToast: (message: string) => void
}

export default function Panel3Discovery({
  active,
  runId,
  task,
  jobs,
  sites,
  onRetry,
  onCancel,
  onFinish,
  onRefreshCount,
  onManual,
  onToast,
}: Props) {
  const [manualUrl, setManualUrl] = useState('')
  const [manualContent, setManualContent] = useState('')
  const [finishing, setFinishing] = useState(false)

  const progress = task?.progress ?? 0
  const message = task?.message ?? '任务排队中'
  const meta = runId ? `任务 ${runId} · ${task?.status ?? 'QUEUED'}` : '正在准备检索组合…'
  const failed = task != null && !['QUEUED', 'RUNNING', 'NEEDS_MANUAL_INPUT', 'SUCCEEDED'].includes(task.status)
  const isBrowserMode = task?.payload?.mode === 'browser' || task?.status === 'NEEDS_MANUAL_INPUT'

  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-3"
      aria-labelledby="crawl-title"
    >
      <div className="panel-heading">
        <span>03</span>
        <div>
          <h2 id="crawl-title">发现真实岗位</h2>
          <p>公开页面自动抓取；站点要求登录时由普通浏览器助手接续采集。</p>
        </div>
      </div>
      <div className="radar-wrap">
        <div className="radar" aria-hidden="true"><i /><b /></div>
        <div className="progress-copy">
          <strong id="progress-value">{progress}%</strong>
          <h3 id="progress-message">{message}</h3>
          <p id="progress-meta">{meta}</p>
        </div>
      </div>
      <div className="progress-track"><i id="progress-bar" style={{ width: `${progress}%` }} /></div>
      <button
        className={failed ? 'secondary retry-button' : 'secondary hidden retry-button'}
        id="retry-task"
        type="button"
        onClick={() => void onRetry()}
      >
        重试当前任务
      </button>
      <form
        id="manual-form"
        className={isBrowserMode ? 'manual-box' : 'manual-box hidden'}
        onSubmit={(event) => {
          event.preventDefault()
          void onManual(manualUrl, manualContent)
        }}
      >
        <h3>自动采集助手</h3>
        <p id="manual-reason">{task?.message ?? ''}</p>
        <div className="login-box">
          <div>
            <b>安装并启用一次，之后自动搜索岗位</b>
            <small>助手在专用标签页搜索、翻页和读取详情。需要登录或验证时会暂停，完成后继续原任务。</small>
          </div>
          <div className="login-actions">
            <a className="secondary button-link" href="/browser-helper.zip">下载浏览器助手</a>
            {/* One link per crawled board — this used to be a hardcoded BOSS link
                regardless of which sites the run actually searched. */}
            {sites.map((site) => (
              <a
                key={site.key}
                className="primary compact button-link"
                href={site.home}
                target="_blank"
                rel="noopener noreferrer"
              >
                在普通浏览器打开 {site.label}
              </a>
            ))}
          </div>
          <ol className="helper-steps">
            <li>在 Chrome 扩展程序页面加载解压的扩展；已安装旧版的用户请点击扩展的刷新按钮。</li>
            <li>保持本页面为当前标签页，在扩展中点击“使用当前 CareerRadar 页面地址”，看到“已连接”后再启用自动接单。</li>
            <li>之后无需逐个打开岗位，最多 30 秒自动领取任务；需要登录时，在专用标签页登录，再点击扩展中的“继续任务”。</li>
            <li>
              当前任务：<code id="capture-task-id">{runId ?? 'task_…'}</code>{' '}
              <button
                className="text-button"
                id="copy-task-id"
                type="button"
                onClick={() => {
                  if (!runId) return
                  navigator.clipboard
                    .writeText(runId)
                    .then(() => onToast('任务编号已复制'))
                    .catch(() => onToast(`任务编号：${runId}`))
                }}
              >
                复制
              </button>
            </li>
          </ol>
          <div className="login-actions">
            <button className="secondary" id="refresh-capture-count" type="button" onClick={() => void onRefreshCount()}>
              刷新采集数量
            </button>
            <button
              className="primary compact"
              id="finish-browser-capture"
              type="button"
              disabled={finishing}
              onClick={() => {
                setFinishing(true)
                void onFinish().finally(() => setFinishing(false))
              }}
            >
              提前结束并分析已有岗位
            </button>
            <button className="secondary" id="cancel-browser-task" type="button" onClick={() => void onCancel()}>
              取消任务
            </button>
          </div>
          <p id="login-status" className={jobs.length > 0 ? 'login-status connected' : 'login-status'}>
            已采集 {jobs.length} 个岗位
          </p>
        </div>
        <div className="manual-divider"><span>仍无法访问时，粘贴岗位描述</span></div>
        <input
          id="manual-url"
          type="url"
          placeholder="https://…（岗位详情页链接）"
          required
          value={manualUrl}
          onChange={(event) => setManualUrl(event.target.value)}
        />
        <textarea
          id="manual-content"
          rows={7}
          placeholder="粘贴职位名称、公司、薪资、职责和技能要求…"
          required
          value={manualContent}
          onChange={(event) => setManualContent(event.target.value)}
        />
        <button className="secondary" type="submit">保存岗位描述并继续</button>
      </form>
    </section>
  )
}