import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import Panel6Tailoring from './Panel6Tailoring'
import type { JobSnapshot, ResumeTailoring, TargetJob } from '../../types'

const job: JobSnapshot = {
  snapshot_id: 'snapshot_1',
  title: 'Python 后端工程师',
  company: '星图科技',
  city: '广州',
  salary: '',
  experience: '',
  education: '',
  required_skills: ['Python'],
  status: 'active',
  canonical_url: 'https://example.com/job/1',
  recruitment_type: 'campus',
  graduation_years: [],
  recruitment_batch: null,
  published_date: null,
  application_deadline: null,
  conversion_opportunity: null,
}

const baseProps = {
  active: true,
  jobs: [job],
  targetJob: null,
  tailoring: null,
  tailoringBusy: false,
  tailorNotice: null,
  draftBundle: null,
  draft: null,
  resumeTemplate: 'technical' as const,
  exportLinks: null,
  exportError: null,
  resumeBusy: false,
  onToast: () => undefined,
  onSelectStoredJob: async () => undefined,
  onSubmitTarget: async () => undefined,
  onCaptureTarget: async () => undefined,
  onSubmitAnswers: async () => undefined,
  onSelectVersion: () => undefined,
  onSetTemplate: () => undefined,
  onSaveVersion: async () => undefined,
  onExport: async () => undefined,
}

describe('Panel6Tailoring target selection', () => {
  it('offers jobs already stored by the current collection task', () => {
    const html = renderToStaticMarkup(<Panel6Tailoring {...baseProps} />)

    expect(html).toContain('从已采集岗位中选择')
    expect(html).toContain('星图科技 · Python 后端工程师 · 广州')
    expect(html).toContain('无需复制链接')
    expect(html).toContain('仅当目标岗位未被 CareerRadar 采集时')
  })

  it('labels a genuinely empty question list as ready to generate', () => {
    const html = renderToStaticMarkup(<Panel6Tailoring {...baseProps} />)

    expect(html).toContain('无需补充事实')
    expect(html).toContain('直接生成定向简历')
    expect(html).not.toContain('确认事实并生成简历')
  })

  it('confirms that a snapshot target is using database details', () => {
    const targetJob: TargetJob = {
      target_job_id: 'target_1',
      profile_id: 'profile_1',
      source_type: 'snapshot',
      company: job.company,
      title: job.title,
      url: job.canonical_url,
      required_skills: job.required_skills,
    }
    const html = renderToStaticMarkup(
      <Panel6Tailoring {...baseProps} targetJob={targetJob} />,
    )

    expect(html).toContain('已读取数据库中的岗位详情，无需复制岗位链接')
    expect(html).toContain('改用数据库之外的岗位')
    expect(html).not.toContain('从已采集岗位中选择')
  })

  it('offers project upload analysis after a target is selected', () => {
    const tailoring: ResumeTailoring = {
      tailoring_id: 'tailor_1', profile_id: 'profile_1', target_job_id: 'target_1',
      status: 'COLLECTING', questions: [], draft_id: null, error: null,
    }
    const html = renderToStaticMarkup(
      <Panel6Tailoring
        {...baseProps}
        tailoring={tailoring}
        onUploadProject={async () => {
          throw new Error('not called during static render')
        }}
        onConfirmProjectUpload={async () => undefined}
      />,
    )

    expect(html).toContain('用项目材料补充经历')
    expect(html).toContain('确认后才会把它归入“项目经历”')
    expect(html).toContain('分析项目')
  })
})
