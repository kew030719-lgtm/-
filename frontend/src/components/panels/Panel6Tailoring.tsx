import { useEffect, useState } from 'react'

import { provenanceLabel, targetSourceLabel } from '../../lib/format'
import type {
  ResumeBullet,
  ResumeDraftBundle,
  ResumeDraftVersion,
  ResumeExport,
  ResumeTailoring,
  ResumeTemplate,
  TargetJob,
} from '../../types'

interface Props {
  active: boolean
  targetJob: TargetJob | null
  tailoring: ResumeTailoring | null
  tailoringBusy: boolean
  tailorNotice: { title: string; body: string } | null
  draftBundle: ResumeDraftBundle | null
  draft: ResumeDraftVersion | null
  resumeTemplate: ResumeTemplate
  exportLinks: ResumeExport[] | null
  exportError: string | null
  resumeBusy: boolean
  onToast: (message: string) => void
  onSubmitTarget: (fields: { company: string; title: string; content: string; url: string }) => Promise<void>
  onCaptureTarget: (url: string) => Promise<void>
  onSubmitAnswers: (answers: Record<string, string | null>) => Promise<void>
  onSelectVersion: (versionId: string) => void
  onSetTemplate: (template: ResumeTemplate) => void
  onSaveVersion: (draft: ResumeDraftVersion) => Promise<void>
  onExport: () => Promise<void>
}

export default function Panel6Tailoring(props: Props) {
  const {
    active, targetJob, tailoring, tailoringBusy, tailorNotice, draftBundle, draft,
    resumeTemplate, exportLinks, exportError, resumeBusy,
    onToast, onSubmitTarget, onCaptureTarget, onSubmitAnswers,
    onSelectVersion, onSetTemplate, onSaveVersion, onExport,
  } = props

  const [company, setCompany] = useState('')
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [content, setContent] = useState('')
  const [skipped, setSkipped] = useState<Record<string, boolean>>({})
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [edited, setEdited] = useState<ResumeDraftVersion | null>(null)

  useEffect(() => {
    setEdited(draft ? (JSON.parse(JSON.stringify(draft)) as ResumeDraftVersion) : null)
  }, [draft])

  useEffect(() => {
    const next: Record<string, boolean> = {}
    const nextAnswers: Record<string, string> = {}
    for (const question of tailoring?.questions ?? []) {
      next[question.question_id] = question.status === 'SKIPPED'
      nextAnswers[question.question_id] = question.answer ?? ''
    }
    setSkipped(next)
    setAnswers(nextAnswers)
  }, [tailoring])

  const updateBullet = (bulletId: string, text: string) => {
    setEdited((current) => {
      if (!current) return current
      const mapBullets = (items: ResumeBullet[]) =>
        items.map((item) => (item.bullet_id === bulletId ? { ...item, text } : item))
      return {
        ...current,
        summary: mapBullets(current.summary ?? []),
        skills: mapBullets(current.skills ?? []),
        sections: (current.sections ?? []).map((section) => ({
          ...section,
          bullets: mapBullets(section.bullets ?? []),
          entries: (section.entries ?? []).map((entry) => ({
            ...entry,
            bullets: mapBullets(entry.bullets ?? []),
          })),
        })),
      }
    })
  }

  const bulletEditor = (item: ResumeBullet) => (
    <div className="resume-bullet-editor" key={item.bullet_id}>
      <textarea
        maxLength={500}
        data-bullet-id={item.bullet_id}
        value={item.text}
        onChange={(event) => updateBullet(item.bullet_id, event.target.value)}
      />
      <small>{provenanceLabel(item.provenance)}</small>
    </div>
  )

  const latestVersionId = draftBundle?.current.version_id
  const isLatest = edited != null && latestVersionId === edited.version_id
  const questions = tailoring?.questions ?? []
  const showQuestions = tailoring != null && tailoring.status !== 'SUCCEEDED' && !tailoringBusy

  const notice = tailorNotice ?? (targetJob
    ? null
    : { title: '尚未选择目标岗位', body: '从岗位排名选择，或在下方粘贴 JD、填写公开职位链接。' })

  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-6"
      aria-labelledby="tailor-title"
    >
      <div className="panel-heading">
        <span>06</span>
        <div>
          <h2 id="tailor-title">目标公司定向简历</h2>
          <p>选择具体岗位，确认真实补充信息，再生成可编辑的两套简历。</p>
        </div>
      </div>

      <div className="tailor-target" id="tailor-target">
        {notice ? (
          <><b>{notice.title}</b><p>{notice.body}</p></>
        ) : targetJob ? (
          <>
            <b>{targetJob.company} · {targetJob.title}</b>
            <p>
              {targetSourceLabel(targetJob.source_type)}
              {' · '}
              {targetJob.required_skills?.length
                ? `要求 ${targetJob.required_skills.join('、')}`
                : '技能要求待从原文判断'}
            </p>
          </>
        ) : null}
      </div>

      <form
        id="target-job-form"
        className="target-job-form"
        onSubmit={(event) => {
          event.preventDefault()
          void onSubmitTarget({ company, title, content, url })
        }}
      >
        <label>
          <span>目标公司</span>
          <input maxLength={200} placeholder="例如 星图科技" value={company} onChange={(e) => setCompany(e.target.value)} />
        </label>
        <label>
          <span>岗位名称</span>
          <input maxLength={200} placeholder="例如 Python 后端工程师" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="wide">
          <span>公开职位链接</span>
          <input type="url" placeholder="https://…（岗位详情页链接）" value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label className="wide">
          <span>岗位描述 JD</span>
          <textarea rows={6} maxLength={80000} placeholder="粘贴岗位职责、必需技能和任职要求……" value={content} onChange={(e) => setContent(e.target.value)} />
        </label>
        <div className="tailor-actions wide">
          <button className="secondary" id="capture-target" type="button" onClick={() => void onCaptureTarget(url)}>
            读取职位链接
          </button>
          <button className="primary compact" type="submit">使用这份 JD</button>
        </div>
      </form>

      <form
        id="tailoring-questions"
        className={showQuestions ? 'tailoring-questions' : 'tailoring-questions hidden'}
        onSubmit={(event) => {
          event.preventDefault()
          const payload: Record<string, string | null> = {}
          for (const question of questions) {
            const id = question.question_id
            payload[id] = skipped[id] ? null : (answers[id] ?? '').trim() || null
          }
          void onSubmitAnswers(payload)
        }}
      >
        <div className="section-intro">
          <h3>确认缺失事实</h3>
          <p>只填写你真实做过的内容；没有相关经历时点击跳过。</p>
        </div>
        <div id="question-list">
          {questions.length === 0 ? (
            <p>现有简历证据已经覆盖主要岗位要求，可以直接生成。</p>
          ) : (
            questions.map((item) => (
              <div className="tailor-question" key={item.question_id} data-question-id={item.question_id}>
                <b>{item.question}</b>
                <textarea
                  maxLength={1000}
                  placeholder="只填写真实经历；建议包含场景、职责和结果"
                  disabled={skipped[item.question_id]}
                  value={skipped[item.question_id] ? '' : (answers[item.question_id] ?? '')}
                  onChange={(event) =>
                    setAnswers((current) => ({ ...current, [item.question_id]: event.target.value }))
                  }
                />
                <label className="skip-line">
                  <input
                    type="checkbox"
                    checked={Boolean(skipped[item.question_id])}
                    onChange={(event) => {
                      const checked = event.target.checked
                      setSkipped((current) => ({ ...current, [item.question_id]: checked }))
                      if (checked) setAnswers((current) => ({ ...current, [item.question_id]: '' }))
                    }}
                  />
                  没有相关经历，跳过
                </label>
              </div>
            ))
          )}
        </div>
        <button className="primary compact" type="submit">确认事实并生成简历</button>
      </form>

      <div id="tailoring-progress" className={tailoringBusy ? 'tailoring-progress' : 'tailoring-progress hidden'}>
        <span className="pulse" /><b>Agent 正在生成定向简历…</b>
      </div>

      <div id="resume-workbench" className={edited ? 'resume-workbench' : 'resume-workbench hidden'}>
        <div className="resume-toolbar">
          <div className="template-switch">
            <button
              className={resumeTemplate === 'technical' ? 'active' : undefined}
              data-template="technical"
              type="button"
              onClick={() => onSetTemplate('technical')}
            >
              简洁技术版
            </button>
            <button
              className={resumeTemplate === 'business' ? 'active' : undefined}
              data-template="business"
              type="button"
              onClick={() => onSetTemplate('business')}
            >
              紧凑商务版
            </button>
          </div>
          <label>
            历史版本{' '}
            <select
              id="resume-version"
              value={edited?.version_id ?? ''}
              onChange={(event) => {
                onSelectVersion(event.target.value)
              }}
            >
              {(draftBundle?.versions ?? []).map((item) => (
                <option key={item.version_id} value={item.version_id}>
                  版本 {item.version} · {item.source}
                </option>
              ))}
            </select>
          </label>
        </div>

        {edited?.quality_report ? (
          <div className="export-card">
            <b>{edited.quality_report.passed ? '质量检查已通过' : '质量检查未通过'}</b>
            <p>
              岗位相关性 {edited.quality_report.relevance_score} ·
              具体性 {edited.quality_report.specificity_score} ·
              结构 {edited.quality_report.structure_score} ·
              简洁度 {edited.quality_report.conciseness_score} ·
              证据覆盖 {edited.quality_report.evidence_coverage}% ·
              预计 {edited.quality_report.estimated_pages} 页
            </p>
            {edited.quality_report.uncovered_requirements.length ? (
              <p>尚无证据覆盖：{edited.quality_report.uncovered_requirements.join('、')}</p>
            ) : null}
          </div>
        ) : null}

        {edited ? (
          <div className={`resume-preview ${resumeTemplate}`} id="resume-preview">
            <div className="resume-contact">
              <input
                className="resume-name"
                value={edited.contact.name}
                placeholder="姓名"
                onChange={(e) => setEdited({ ...edited, contact: { ...edited.contact, name: e.target.value } })}
              />
              <input
                value={edited.contact.phone}
                placeholder="电话"
                onChange={(e) => setEdited({ ...edited, contact: { ...edited.contact, phone: e.target.value } })}
              />
              <input
                value={edited.contact.email}
                placeholder="邮箱"
                onChange={(e) => setEdited({ ...edited, contact: { ...edited.contact, email: e.target.value } })}
              />
              <input
                value={edited.contact.location}
                placeholder="所在城市"
                onChange={(e) => setEdited({ ...edited, contact: { ...edited.contact, location: e.target.value } })}
              />
            </div>
            <input
              className="resume-headline"
              value={edited.headline}
              onChange={(e) => setEdited({ ...edited, headline: e.target.value })}
            />

            {(() => {
              const summary = <><h2>个人概述</h2>{edited.summary.map(bulletEditor)}</>
              const skills = <><h2>专业技能</h2>{edited.skills.map(bulletEditor)}</>
              const sections = edited.sections.map((section) => (
                <div key={section.section_id}>
                  <h2>{section.title}</h2>
                  {section.bullets.map(bulletEditor)}
                  {section.entries.map((entry) => (
                    <div key={entry.entry_id}>
                      <h3>
                        {entry.heading}{' '}
                        <small>{entry.subheading} {entry.date_range}</small>
                      </h3>
                      {entry.bullets.map(bulletEditor)}
                    </div>
                  ))}
                </div>
              ))
              return resumeTemplate === 'technical' ? <>{skills}{summary}{sections}</> : <>{summary}{sections}{skills}</>
            })()}
          </div>
        ) : null}

        <div className="resume-diff" id="resume-diff">
          {edited?.change_log?.length ? (
            <details>
              <summary>查看本版本修改记录（{edited.change_log.length}）</summary>
              {edited.change_log.map((item, index) =>
                item.kind === 'generated' ? (
                  <p key={index}>根据 {item.target || '目标岗位'} 生成</p>
                ) : (
                  <p key={index}>
                    <del>{item.before || '新增内容'}</del>
                    <br />
                    <ins>{item.after || ''}</ins>
                  </p>
                ),
              )}
            </details>
          ) : null}
        </div>

        <div className="tailor-actions">
          <button
            className="secondary"
            id="save-resume-version"
            type="button"
            disabled={resumeBusy}
            onClick={() => {
              if (edited) void onSaveVersion(edited)
            }}
          >
            {isLatest ? '保存为新版本' : '将此版本恢复为新版本'}
          </button>
          <button className="primary compact" id="export-resume" type="button" disabled={resumeBusy} onClick={() => void onExport()}>
            生成两套 DOCX 和 PDF
          </button>
        </div>

        <div className="export-links" id="export-links">
          {exportLinks?.map((item) => (
            <div className="export-card" key={item.export_id}>
              <b>{item.template === 'technical' ? '简洁技术版' : '紧凑商务版'}</b>
              <br />
              <a href={`/api/resume-exports/${item.export_id}/download?format=docx`}>下载 DOCX</a>
              <a href={`/api/resume-exports/${item.export_id}/download?format=pdf`}>下载 PDF</a>
            </div>
          ))}
          {resumeBusy && !exportLinks?.length ? (
            <div className="export-card">正在生成两套 DOCX 和 PDF…</div>
          ) : null}
          {exportError ? <div className="export-card">{exportError}</div> : null}
        </div>
      </div>

      {tailoring && tailoring.status === 'FAILED_VALIDATION' && tailoring.error ? (
        <p className="login-status" onClick={() => onToast(tailoring.error ?? '')}>{tailoring.error}</p>
      ) : null}
      {tailoring && tailoring.status === 'FAILED' && tailoring.error ? (
        <p className="login-status" onClick={() => onToast(tailoring.error ?? '')}>
          {tailoring.error}。可修改上方补充事实后再次生成。
        </p>
      ) : null}
    </section>
  )
}
