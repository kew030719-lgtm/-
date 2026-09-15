import { describe, expect, it } from 'vitest'

import {
  CONVERSATION_KEY,
  TASK_PARAM,
  readTailoringId,
  readTaskId,
  tailoringSearch,
  taskSearch,
} from './urlState'

describe('?task= URL state', () => {
  // The Chrome extension's preferredTaskId() reads this exact parameter to
  // decide which CareerRadar instance auto-claims a run. Losing it is silent:
  // the task stays QUEUED and nothing reports an error.
  it('writes the parameter the extension looks for', () => {
    expect(taskSearch('task_abc123')).toBe('?task=task_abc123')
    expect(TASK_PARAM).toBe('task')
  })

  it('round-trips a run id', () => {
    expect(readTaskId(taskSearch('task_abc123'))).toBe('task_abc123')
  })

  it('reads the id out of a query string carrying other params', () => {
    expect(readTaskId('?foo=1&task=task_xyz')).toBe('task_xyz')
  })

  it('escapes ids so the query string cannot be broken', () => {
    const awkward = 'task_a b&c=d'
    expect(taskSearch(awkward)).not.toContain('&c=d')
    expect(readTaskId(taskSearch(awkward))).toBe(awkward)
  })

  it('returns null when the parameter is absent', () => {
    expect(readTaskId('')).toBeNull()
    expect(readTaskId('?other=1')).toBeNull()
    expect(readTailoringId('?task=x')).toBeNull()
  })
})

describe('?tailoring= URL state', () => {
  it('round-trips a tailoring id', () => {
    expect(readTailoringId(tailoringSearch('tailoring_9'))).toBe('tailoring_9')
  })
})

describe('localStorage keys', () => {
  it('keeps the names the previous UI used, so saved sessions survive', () => {
    expect(CONVERSATION_KEY).toBe('careerRadarConversation')
  })
})