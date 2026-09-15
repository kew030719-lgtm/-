// Pure presentation helpers, ported verbatim from the previous app.js.
// React escapes interpolated values, so the old escapeHtml/escapeAttr helpers
// have no equivalent here.

import type {
  ChatActionKind,
  Comparison,
  Conversation,
  InterviewPrep,
  Profile,
  ResumeDraftBundle,
  ResumeDraftVersion,
  ResumeTailoring,
  TargetJob,
} from '../types'

export const WORKFLOW_STEPS = [
  { n: 1, title: '提交简历', hint: 'PDF · DOCX · 文本' },
  { n: 2, title: '确认方向', hint: '画像 · 城市 · 薪资' },
  { n: 3, title: '发现岗位', hint: '所选站点公开页面' },
  { n: 4, title: '岗位排名', hint: '固定权重评分' },
  { n: 5, title: '准备计划', hint: '能力缺口 · 7 天' },
  { n: 6, title: '定向简历', hint: '目标公司 · DOCX · PDF' },
] as const

export function actionValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未设置'
  if (Array.isArray(value)) {
    const joined = value
      .map((item) =>
        typeof item === 'object' && item !== null
          ? ((item as { role?: string }).role ?? JSON.stringify(item))
          : String(item),
      )
      .join('、')
    return joined || '未设置'
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const ACTION_LABELS: Record<string, string> = {
  UPDATE_PROFILE: '修改候选人画像',
  RESTART_DISCOVERY: '调整条件并重新搜索',
  START_COMPARISON: '分析当前岗位',
  START_RESUME_TAILORING: '为目标岗位修改简历',
}

export function actionLabel(kind: ChatActionKind | string): string {
  return ACTION_LABELS[kind] ?? String(kind)
}

const FIELD_LABELS: Record<string, string> = {
  selected_roles: '岗位方向',
  cities: '目标城市',
  salary_preference: '期望薪资',
  experience_years: '工作年限',
  work_type_preference: '工作类型',
}

export function actionFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field
}

const STATUS_LABELS: Record<string, string> = {
  PENDING: '等待确认',
  EXECUTING: '正在执行',
  EXECUTED: '已执行',
  REJECTED: '已取消',
  FAILED: '执行失败',
}

export function actionStatus(status: string): string {
  return STATUS_LABELS[status] ?? status
}

export function chatQuickPrompts(conversation: Conversation | null): string[] {
  if (!conversation?.profile_id) return ['我适合什么岗位？', '如何准备项目经历？']
  if (!conversation.run_id) return ['我的优势和缺口是什么？', '把目标城市改到杭州']
  if (!conversation.comparison_id) return ['当前抓到了多少岗位？', '开始分析已有岗位']
  return ['为什么第一名得分最高？', '我需要补哪些能力？', '为第一名岗位修改简历', '改到杭州并重新搜索']
}

export function chatContextLabel(conversation: Conversation | null, jobCount: number): string {
  if (!conversation?.profile_id) return '通用咨询 · 未使用个人简历'
  return conversation.run_id ? `已关联简历 · ${jobCount} 个岗位` : '已关联简历 · 尚未搜索'
}

export function draftBullets(draft: ResumeDraftVersion) {
  const values = [...(draft.summary ?? []), ...(draft.skills ?? [])]
  for (const section of draft.sections ?? []) {
    values.push(...(section.bullets ?? []))
    for (const entry of section.entries ?? []) values.push(...(entry.bullets ?? []))
  }
  return values
}

const PROVENANCE_LABELS: Record<string, string> = {
  uploaded_resume: '原简历证据',
  user_confirmed: '已确认补充',
  user_edited: '用户编辑',
}

export function provenanceLabel(provenance: string): string {
  return PROVENANCE_LABELS[provenance] ?? provenance
}

const TARGET_SOURCE_LABELS: Record<string, string> = {
  snapshot: '来自当前岗位快照',
  external_url: '来自公开职位链接',
  // Kept for targets stored before multi-site support renamed this value.
  boss_url: '来自公开职位链接',
  pasted: '来自粘贴的岗位描述',
}

export function targetSourceLabel(sourceType: string): string {
  return TARGET_SOURCE_LABELS[sourceType] ?? sourceType
}

/** The 7-day plan is stored with a leading "第 N 天：" that the list already conveys. */
export function stripDayPrefix(item: string): string {
  return item.replace(/^第\s*\d+\s*天[：:]?/, '').trim()
}

export interface StepAvailability {
  profile: Profile | null
  runId: string | null
  comparison: Comparison | null
  interviewPrep: InterviewPrep | null
  tailoring: ResumeTailoring | null
  targetJob: TargetJob | null
  draftBundle: ResumeDraftBundle | null
}

/**
 * Which workflow steps have something to show.
 *
 * The rail used to be display-only, which left no way back to an earlier step
 * once you moved on. Steps are only navigable when their panel has data: a
 * reachable-looking step that opens an empty panel is worse than one that
 * explains why it is unavailable.
 */
export function reachableSteps(state: StepAvailability): Set<number> {
  const steps = new Set<number>([1]) // the upload form is always available
  if (state.profile) steps.add(2)
  if (state.runId) steps.add(3)
  if (state.comparison) steps.add(4)
  if (state.comparison || state.interviewPrep) steps.add(5)
  if (state.tailoring || state.targetJob || state.draftBundle) steps.add(6)
  return steps
}

const BLOCKED_REASONS: Record<number, string> = {
  2: '请先提交简历',
  3: '请先确认岗位方向并开始搜索',
  4: '还没有生成岗位排名',
  5: '还没有生成准备计划',
  6: '请先从岗位排名里选择目标岗位',
}

export function stepBlockedReason(step: number): string {
  return BLOCKED_REASONS[step] ?? '这一步还没有内容'
}