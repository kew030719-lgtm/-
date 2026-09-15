import { describe, expect, it } from 'vitest'

import {
  actionFieldLabel,
  actionLabel,
  actionStatus,
  actionValue,
  chatContextLabel,
  chatQuickPrompts,
  reachableSteps,
  stepBlockedReason,
  stripDayPrefix,
} from './format'
import type { Conversation } from '../types'

function conversation(overrides: Partial<Conversation>): Conversation {
  return {
    conversation_id: 'c1',
    profile_id: null,
    run_id: null,
    comparison_id: null,
    messages: [],
    actions: [],
    ...overrides,
  }
}

describe('actionValue', () => {
  it('renders unset values explicitly rather than as blank', () => {
    expect(actionValue(null)).toBe('未设置')
    expect(actionValue(undefined)).toBe('未设置')
    expect(actionValue('')).toBe('未设置')
  })

  it('joins arrays and prefers a role label for role objects', () => {
    expect(actionValue(['北京', '杭州'])).toBe('北京、杭州')
    expect(actionValue([{ role: 'Python 后端' }])).toBe('Python 后端')
    expect(actionValue([])).toBe('未设置')
  })

  it('stringifies scalars and objects', () => {
    expect(actionValue(3)).toBe('3')
    expect(actionValue({ a: 1 })).toBe('{"a":1}')
  })
})

describe('label maps', () => {
  it('translates action kinds, fields and statuses', () => {
    expect(actionLabel('RESTART_DISCOVERY')).toBe('调整条件并重新搜索')
    expect(actionFieldLabel('salary_preference')).toBe('期望薪资')
    expect(actionStatus('EXECUTING')).toBe('正在执行')
  })

  it('falls back to the raw value for anything unknown', () => {
    expect(actionLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW')
    expect(actionFieldLabel('new_field')).toBe('new_field')
    expect(actionStatus('WEIRD')).toBe('WEIRD')
  })
})

describe('chatQuickPrompts', () => {
  it('escalates the suggestions as the conversation gains context', () => {
    expect(chatQuickPrompts(null)).toContain('我适合什么岗位？')
    expect(chatQuickPrompts(conversation({ profile_id: 'p' }))).toContain('把目标城市改到杭州')
    expect(chatQuickPrompts(conversation({ profile_id: 'p', run_id: 'r' }))).toContain('开始分析已有岗位')
    expect(
      chatQuickPrompts(conversation({ profile_id: 'p', run_id: 'r', comparison_id: 'c' })),
    ).toContain('为第一名岗位修改简历')
  })
})

describe('chatContextLabel', () => {
  it('describes the bound context', () => {
    expect(chatContextLabel(null, 0)).toBe('通用咨询 · 未使用个人简历')
    expect(chatContextLabel(conversation({ profile_id: 'p' }), 0)).toBe('已关联简历 · 尚未搜索')
    expect(chatContextLabel(conversation({ profile_id: 'p', run_id: 'r' }), 7)).toBe('已关联简历 · 7 个岗位')
  })
})

describe('reachableSteps', () => {
  const empty = {
    profile: null, runId: null, comparison: null, interviewPrep: null,
    tailoring: null, targetJob: null, draftBundle: null,
  }
  const profile = {
    profile_id: 'p', skills: [], experience_years: null, education: null, cities: [],
    salary_preference: null, work_type_preference: null, selected_roles: [],
    recommendations: [], confirmed: true,
  }

  it('offers only the upload step before a resume exists', () => {
    // Every other step would open an empty panel, so the rail must not pretend
    // they are available.
    expect([...reachableSteps(empty)]).toEqual([1])
  })

  it('matches the step to the data behind it', () => {
    expect([...reachableSteps({ ...empty, profile })].sort()).toEqual([1, 2])
    expect([...reachableSteps({ ...empty, profile, runId: 'r' })].sort()).toEqual([1, 2, 3])
  })

  it('unlocks the plan step for either the aggregate plan or a per-job prep', () => {
    const comparison = { comparison_id: 'c', profile_id: 'p', rankings: [], action_plan: [] }
    expect(reachableSteps({ ...empty, profile, runId: 'r', comparison }).has(4)).toBe(true)
    expect(reachableSteps({ ...empty, profile, runId: 'r', comparison }).has(5)).toBe(true)
    // A per-job preparation opens step 5 on its own.
    const prep = { prep_id: 'x' } as never
    expect(reachableSteps({ ...empty, profile, runId: 'r', interviewPrep: prep }).has(5)).toBe(true)
    expect(reachableSteps({ ...empty, profile, runId: 'r' }).has(5)).toBe(false)
  })

  it('unlocks the resume step once a target exists', () => {
    expect(reachableSteps({ ...empty, targetJob: { target_job_id: 't' } as never }).has(6)).toBe(true)
    expect(reachableSteps({ ...empty, tailoring: { tailoring_id: 't' } as never }).has(6)).toBe(true)
    expect(reachableSteps(empty).has(6)).toBe(false)
  })

  it('explains why a locked step is locked', () => {
    for (const step of [2, 3, 4, 5, 6]) {
      expect(stepBlockedReason(step)).not.toBe('')
    }
    expect(stepBlockedReason(2)).toContain('简历')
  })
})

describe('stripDayPrefix', () => {
  it('drops the redundant day prefix the list already conveys', () => {
    expect(stripDayPrefix('第 1 天：阅读前三岗位证据。')).toBe('阅读前三岗位证据。')
    expect(stripDayPrefix('第7天:修订简历')).toBe('修订简历')
    expect(stripDayPrefix('没有前缀')).toBe('没有前缀')
  })
})