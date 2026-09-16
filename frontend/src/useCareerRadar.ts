import { useCallback, useEffect, useRef, useState } from 'react'

import { ACTIVE_TASK_STATUSES, api, patchJson, post, postJson, putJson } from './api'
import { chatContextLabel } from './lib/format'
import {
  CHAT_OPEN_KEY,
  CONVERSATION_KEY,
  readTailoringId,
  readTaskId,
  tailoringSearch,
  taskSearch,
} from './lib/urlState'
import type {
  Application,
  ApplicationStatus,
  Comparison,
  Conversation,
  InterviewPrep,
  JobSnapshot,
  Profile,
  ResumeDraftBundle,
  ResumeDraftVersion,
  ResumeExport,
  ResumeTailoring,
  ResumeTemplate,
  TargetJob,
  Task,
} from './types'

export interface AppState {
  step: number
  profile: Profile | null
  runId: string | null
  runTask: Task | null
  /** Boards this run crawls, known immediately at start and refreshed on poll. */
  sites: string[]
  jobs: JobSnapshot[]
  comparison: Comparison | null
  conversation: Conversation | null
  conversationId: string | null
  chatBusy: boolean
  interviewPrep: InterviewPrep | null
  interviewBusy: boolean
  /** Assisted applications, keyed by the snapshot they target. */
  applications: Application[]
  applyBusy: string | null
  targetJob: TargetJob | null
  tailoring: ResumeTailoring | null
  tailoringBusy: boolean
  tailorNotice: { title: string; body: string } | null
  draftBundle: ResumeDraftBundle | null
  draft: ResumeDraftVersion | null
  resumeTemplate: ResumeTemplate
  exportLinks: ResumeExport[] | null
  exportError: string | null
  toast: string | null
  resumeBusy: boolean
}

const INITIAL: AppState = {
  step: 1,
  profile: null,
  runId: null,
  runTask: null,
  sites: [],
  jobs: [],
  comparison: null,
  conversation: null,
  conversationId: null,
  chatBusy: false,
  interviewPrep: null,
  interviewBusy: false,
  applications: [],
  applyBusy: null,
  targetJob: null,
  tailoring: null,
  tailoringBusy: false,
  tailorNotice: null,
  draftBundle: null,
  draft: null,
  resumeTemplate: 'technical',
  exportLinks: null,
  exportError: null,
  toast: null,
  resumeBusy: false,
}

export function useCareerRadar() {
  const [state, setState] = useState<AppState>(INITIAL)
  const [chatOpen, setChatOpen] = useState(false)

  const patch = useCallback((next: Partial<AppState>) => {
    setState((current) => ({ ...current, ...next }))
  }, [])

  // ----- timers -----------------------------------------------------------
  const timers = useRef(new Set<number>())
  const schedule = useCallback((fn: () => void, ms: number) => {
    const id = window.setTimeout(() => {
      timers.current.delete(id)
      fn()
    }, ms)
    timers.current.add(id)
  }, [])
  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((id) => window.clearTimeout(id))
  }, [])

  // Pollers capture the run they belong to and bail if a newer run supersedes
  // them, so a retry or restart cannot be overwritten by a stale loop.
  const generation = useRef(0)

  const toast = useCallback(
    (message: string) => {
      patch({ toast: message })
      schedule(() => patch({ toast: null }), 3200)
    },
    [patch, schedule],
  )

  const goToStep = useCallback(
    (number: number) => {
      patch({ step: number })
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    [patch],
  )

  // ----- chat drawer ------------------------------------------------------
  const applyChatOpen = useCallback((open: boolean) => {
    setChatOpen(open)
    localStorage.setItem(CHAT_OPEN_KEY, open ? '1' : '0')
  }, [])

  useEffect(() => {
    const saved = localStorage.getItem(CHAT_OPEN_KEY)
    setChatOpen(saved === null ? window.innerWidth > 900 : saved === '1')
  }, [])

  useEffect(() => {
    document.body.classList.toggle('chat-open', chatOpen)
  }, [chatOpen])

  // ----- conversations ----------------------------------------------------
  const renderConversation = useCallback(
    (conversation: Conversation) => {
      patch({ conversation, conversationId: conversation.conversation_id })
    },
    [patch],
  )

  const createConversation = useCallback(
    async (context: Record<string, unknown> = {}) => {
      const conversation = await postJson<Conversation>('/api/conversations', context)
      localStorage.setItem(CONVERSATION_KEY, conversation.conversation_id)
      renderConversation(conversation)
      return conversation
    },
    [renderConversation],
  )

  const refreshConversation = useCallback(
    async (conversationId: string | null) => {
      if (!conversationId) return null
      const conversation = await api<Conversation>(`/api/conversations/${conversationId}`)
      renderConversation(conversation)
      return conversation
    },
    [renderConversation],
  )

  const ensureConversation = useCallback(async () => {
    const saved = localStorage.getItem(CONVERSATION_KEY)
    if (saved) {
      try {
        const conversation = await api<Conversation>(`/api/conversations/${saved}`)
        renderConversation(conversation)
        return conversation
      } catch {
        // fall through to a fresh conversation
      }
    }
    return createConversation()
  }, [createConversation, renderConversation])

  const bindConversationContext = useCallback(
    async (conversationId: string, update: Record<string, unknown>) => {
      const conversation = await patchJson<Conversation>(
        `/api/conversations/${conversationId}/context`,
        update,
      )
      renderConversation(conversation)
      return conversation
    },
    [renderConversation],
  )

  const conversationForProfile = useCallback(
    async (profileId: string, runId: string | null = null) => {
      const saved = localStorage.getItem(CONVERSATION_KEY)
      if (saved) {
        try {
          const current = await api<Conversation>(`/api/conversations/${saved}`)
          if (current.profile_id === profileId) {
            const update: Record<string, unknown> = { profile_id: profileId }
            if (runId) update.run_id = runId
            return await bindConversationContext(current.conversation_id, update)
          }
        } catch {
          // fall through
        }
      }
      return createConversation({ profile_id: profileId, run_id: runId })
    },
    [bindConversationContext, createConversation],
  )

  // ----- resume upload ----------------------------------------------------
  const uploadResume = useCallback(
    async (form: FormData) => {
      try {
        const profile = await api<Profile>('/api/resumes', { method: 'POST', body: form })
        patch({
          profile,
          runId: null,
          runTask: null,
          comparison: null,
          jobs: [],
          targetJob: null,
          tailoring: null,
          interviewPrep: null,
          draftBundle: null,
          draft: null,
          exportLinks: null,
          exportError: null,
        })
        history.replaceState(null, '', location.pathname)
        await createConversation({ profile_id: profile.profile_id })
        goToStep(2)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [createConversation, goToStep, patch, toast],
  )

  // ----- discovery --------------------------------------------------------
  // Composition order matters: pollComparison <- startComparisonFor <- pollDiscovery.
  const pollComparison = useCallback(
    async (taskId: string, gen: number) => {
      if (gen !== generation.current) return
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (gen !== generation.current) return
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollComparison(taskId, gen), 2000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        const comparison = await api<Comparison>(`/api/tasks/${taskId}/comparison`)
        if (gen !== generation.current) return
        patch({ comparison })
        const conversationId = localStorage.getItem(CONVERSATION_KEY)
        if (conversationId) {
          await bindConversationContext(conversationId, { comparison_id: comparison.comparison_id })
        }
        goToStep(4)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [bindConversationContext, goToStep, patch, schedule, toast],
  )

  const startComparisonFor = useCallback(
    async (runId: string, gen: number, profileId: string) => {
      try {
        const result = await postJson<{ task_id: string }>('/api/comparisons', {
          profile_id: profileId,
          run_id: runId,
        })
        await pollComparison(result.task_id, gen)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [pollComparison, toast],
  )

  const pollDiscovery = useCallback(
    async (runId: string, gen: number, profileId: string) => {
      if (gen !== generation.current) return
      try {
        const task = await api<Task>(`/api/tasks/${runId}`)
        if (gen !== generation.current) return
        const jobs = await api<JobSnapshot[]>(`/api/jobs?run_id=${runId}`)
        if (gen !== generation.current) return
        const next: Partial<AppState> = { runTask: task, jobs, runId }
        // Adopt the run's own site list when it reports one; a restored session
        // has no in-memory list of its own.
        const payloadSites = (task.payload as { sites?: string[] })?.sites
        if (payloadSites?.length) next.sites = payloadSites
        patch(next)
        if (['QUEUED', 'RUNNING', 'NEEDS_MANUAL_INPUT'].includes(task.status)) {
          schedule(() => void pollDiscovery(runId, gen, profileId), 2000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        await startComparisonFor(runId, gen, profileId)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [patch, schedule, startComparisonFor, toast],
  )

  const startDiscovery = useCallback(
    async (payload: Record<string, unknown>, sites: string[]) => {
      const profile = state.profile
      if (!profile) return
      try {
        const saved = await putJson<Profile>(`/api/profiles/${profile.profile_id}`, payload)
        const run = await postJson<{ task_id: string }>('/api/discovery-runs', {
          profile_id: saved.profile_id,
          sites,
        })
        generation.current += 1
        const gen = generation.current
        history.replaceState(null, '', taskSearch(run.task_id))
        patch({ profile: saved, runId: run.task_id, runTask: null, sites, jobs: [], comparison: null })
        const conversationId = localStorage.getItem(CONVERSATION_KEY)
        if (conversationId) {
          await bindConversationContext(conversationId, {
            profile_id: saved.profile_id,
            run_id: run.task_id,
            comparison_id: null,
          })
        }
        goToStep(3)
        void pollDiscovery(run.task_id, gen, saved.profile_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [bindConversationContext, goToStep, patch, pollDiscovery, state.profile, toast],
  )

  const retryTask = useCallback(async () => {
    const profile = state.profile
    if (!state.runId || !profile) return
    try {
      const retry = await post<{ task_id: string }>(`/api/tasks/${state.runId}/retry`)
      generation.current += 1
      const gen = generation.current
      history.replaceState(null, '', taskSearch(retry.task_id))
      patch({ runId: retry.task_id, runTask: null })
      const conversationId = localStorage.getItem(CONVERSATION_KEY)
      if (conversationId) {
        await bindConversationContext(conversationId, { run_id: retry.task_id, comparison_id: null })
      }
      void pollDiscovery(retry.task_id, gen, profile.profile_id)
    } catch (error) {
      toast((error as Error).message)
    }
  }, [bindConversationContext, patch, pollDiscovery, state.profile, state.runId, toast])

  const cancelBrowserTask = useCallback(async () => {
    if (!state.runId) return
    try {
      await api(`/api/tasks/${state.runId}`, { method: 'DELETE' })
      toast('已请求取消任务')
    } catch (error) {
      toast((error as Error).message)
    }
  }, [state.runId, toast])

  const refreshCaptureCount = useCallback(async () => {
    if (!state.runId) return
    try {
      const jobs = await api<JobSnapshot[]>(`/api/jobs?run_id=${state.runId}`)
      patch({ jobs })
      await refreshConversation(localStorage.getItem(CONVERSATION_KEY))
    } catch (error) {
      toast((error as Error).message)
    }
  }, [patch, refreshConversation, state.runId, toast])

  const finishBrowserCapture = useCallback(async () => {
    if (!state.runId) return
    try {
      await post(`/api/browser-captures/${state.runId}/finish`)
      toast('已提交采集到的岗位，正在生成排名')
    } catch (error) {
      toast((error as Error).message)
    }
  }, [state.runId, toast])

  const submitManualJob = useCallback(
    async (url: string, content: string) => {
      const profile = state.profile
      if (!state.runId || !profile) return
      try {
        await postJson(`/api/jobs/${state.runId}/manual-content`, {
          url,
          content,
          city: profile.cities[0],
        })
        const jobs = await api<JobSnapshot[]>(`/api/jobs?run_id=${state.runId}`)
        patch({ jobs })
        await startComparisonFor(state.runId, generation.current, profile.profile_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [patch, startComparisonFor, state.profile, state.runId, toast],
  )

  const restart = useCallback(() => {
    generation.current += 1
    setState({ ...INITIAL, toast: null })
    history.replaceState(null, '', '/app/')
  }, [])

  // ----- tailoring --------------------------------------------------------
  const pollTargetJob = useCallback(
    async (taskId: string, targetJobId: string) => {
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollTargetJob(taskId, targetJobId), 1000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        await beginTailoringFor(targetJobId)
      } catch (error) {
        toast((error as Error).message)
        patch({ tailorNotice: { title: '职位链接暂时无法读取', body: '请在下方粘贴这份岗位描述继续。' } })
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [patch, schedule, toast],
  )

  const loadDraft = useCallback(
    async (draftId: string) => {
      const bundle = await api<ResumeDraftBundle>(`/api/resume-drafts/${draftId}`)
      patch({ draftBundle: bundle, draft: bundle.current, exportLinks: null, exportError: null })
    },
    [patch],
  )

  const loadTailoring = useCallback(
    async (tailoringId: string, updateHistory = true) => {
      const result = await api<{ tailoring: ResumeTailoring; target_job: TargetJob }>(
        `/api/resume-tailorings/${tailoringId}`,
      )
      patch({ tailoring: result.tailoring, targetJob: result.target_job, tailorNotice: null })
      if (result.tailoring.draft_id) await loadDraft(result.tailoring.draft_id)
      if (updateHistory) {
        history.replaceState(null, '', tailoringSearch(tailoringId))
      }
    },
    [loadDraft, patch],
  )

  const beginTailoringFor = useCallback(
    async (targetJobId: string) => {
      const profile = state.profile
      if (!profile) return
      const tailoring = await postJson<ResumeTailoring>('/api/resume-tailorings', {
        profile_id: profile.profile_id,
        target_job_id: targetJobId,
        conversation_id: localStorage.getItem(CONVERSATION_KEY),
      })
      await loadTailoring(tailoring.tailoring_id)
      goToStep(6)
    },
    [goToStep, loadTailoring, state.profile],
  )

  const beginTailoring = useCallback(
    async (snapshotId: string) => {
      const profile = state.profile
      if (!profile) return toast('请先提交简历')
      try {
        const result = await postJson<{ target_job_id: string }>('/api/target-jobs', {
          profile_id: profile.profile_id,
          source_type: 'snapshot',
          snapshot_id: snapshotId,
        })
        await beginTailoringFor(result.target_job_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [beginTailoringFor, state.profile, toast],
  )

  const submitTargetForm = useCallback(
    async (fields: { company: string; title: string; content: string; url: string }) => {
      const profile = state.profile
      if (!profile) return toast('请先提交简历')
      try {
        const result = await postJson<{ target_job_id: string }>('/api/target-jobs', {
          profile_id: profile.profile_id,
          source_type: 'pasted',
          ...fields,
        })
        await beginTailoringFor(result.target_job_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [beginTailoringFor, state.profile, toast],
  )

  const captureTargetUrl = useCallback(
    async (url: string) => {
      const profile = state.profile
      if (!profile) return toast('请先提交简历')
      if (!url.trim()) return toast('请填写公开职位链接')
      try {
        const result = await postJson<{ task_id: string; target_job_id: string }>(
          '/api/target-jobs',
          { profile_id: profile.profile_id, source_type: 'boss_url', url },
        )
        patch({
          tailorNotice: {
            title: '正在读取目标岗位',
            body: '如果页面要求登录或验证，可以改为粘贴岗位描述。',
          },
        })
        goToStep(6)
        void pollTargetJob(result.task_id, result.target_job_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [goToStep, patch, pollTargetJob, state.profile, toast],
  )

  const pollTailoring = useCallback(
    async (taskId: string, tailoringId: string) => {
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollTailoring(taskId, tailoringId), 1000)
          return
        }
        patch({ tailoringBusy: false })
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        await loadTailoring(tailoringId)
        toast('定向简历已生成')
      } catch (error) {
        patch({ tailoringBusy: false })
        toast((error as Error).message)
      }
    },
    [loadTailoring, patch, schedule, toast],
  )

  const submitTailoringAnswers = useCallback(
    async (answers: Record<string, string | null>) => {
      const tailoring = state.tailoring
      if (!tailoring) return
      try {
        const saved = await postJson<ResumeTailoring>(
          `/api/resume-tailorings/${tailoring.tailoring_id}/answers`,
          { answers },
        )
        patch({ tailoring: saved })
        if (saved.status !== 'READY') return toast('请回答或跳过全部问题')
        const queued = await post<{ task_id: string }>(
          `/api/resume-tailorings/${saved.tailoring_id}/confirm`,
        )
        patch({ tailoringBusy: true })
        void pollTailoring(queued.task_id, saved.tailoring_id)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [patch, pollTailoring, state.tailoring, toast],
  )

  const selectVersion = useCallback(
    (versionId: string) => {
      const bundle = state.draftBundle
      if (!bundle) return
      const draft = bundle.versions.find((item) => item.version_id === versionId)
      if (draft) patch({ draft })
    },
    [patch, state.draftBundle],
  )

  const saveResumeVersion = useCallback(
    async (edited: ResumeDraftVersion) => {
      try {
        const saved = await postJson<{ draft_id: string; version: number }>(
          `/api/resume-drafts/${edited.draft_id}/versions`,
          edited,
        )
        await loadDraft(saved.draft_id)
        toast(`已保存版本 ${saved.version}`)
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [loadDraft, toast],
  )

  const pollExports = useCallback(
    async (taskId: string, exportIds: string[]) => {
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollExports(taskId, exportIds), 1000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        const exports = await Promise.all(
          exportIds.map((id) => api<ResumeExport>(`/api/resume-exports/${id}`)),
        )
        patch({ exportLinks: exports, exportError: null, resumeBusy: false })
        toast('两套简历文件已生成')
      } catch (error) {
        patch({ exportError: (error as Error).message, resumeBusy: false })
        toast((error as Error).message)
      }
    },
    [patch, schedule, toast],
  )

  const exportResume = useCallback(async () => {
    const draft = state.draft
    if (!draft) return
    try {
      const result = await postJson<{ task_id: string; exports: ResumeExport[] }>(
        `/api/resume-drafts/${draft.draft_id}/exports`,
        { templates: ['technical', 'business'] },
      )
      patch({ resumeBusy: true, exportLinks: null, exportError: null })
      void pollExports(
        result.task_id,
        result.exports.map((item) => item.export_id),
      )
    } catch (error) {
      toast((error as Error).message)
    }
  }, [patch, pollExports, state.draft, toast])

  // ----- interview preparation -------------------------------------------
  const pollInterviewPrep = useCallback(
    async (taskId: string, prepId: string) => {
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollInterviewPrep(taskId, prepId), 1000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        const prep = await api<InterviewPrep>(`/api/interview-preps/${prepId}`)
        patch({ interviewPrep: prep, interviewBusy: false })
        toast(`面试准备已生成：${prep.questions.length} 道题`)
      } catch (error) {
        patch({ interviewBusy: false })
        toast((error as Error).message)
      }
    },
    [patch, schedule, toast],
  )

  const startInterviewPrep = useCallback(
    async (snapshotId: string) => {
      const profile = state.profile
      if (!profile) return toast('请先提交简历')
      try {
        const queued = await postJson<{ prep_id: string; task_id: string }>('/api/interview-preps', {
          profile_id: profile.profile_id,
          snapshot_id: snapshotId,
        })
        patch({ interviewPrep: null, interviewBusy: true })
        goToStep(5)
        void pollInterviewPrep(queued.task_id, queued.prep_id)
      } catch (error) {
        patch({ interviewBusy: false })
        toast((error as Error).message)
      }
    },
    [goToStep, patch, pollInterviewPrep, state.profile, toast],
  )

  const clearInterviewPrep = useCallback(() => patch({ interviewPrep: null }), [patch])

  // ----- assisted applications --------------------------------------------
  const loadApplications = useCallback(
    async (profileId: string | null | undefined) => {
      if (!profileId) return
      try {
        patch({ applications: await api<Application[]>(`/api/applications?profile_id=${profileId}`) })
      } catch {
        // A list that will not load must not take the ranking panel down with it.
      }
    },
    [patch],
  )

  const pollApplication = useCallback(
    async (taskId: string, applicationId: string) => {
      try {
        const task = await api<Task>(`/api/tasks/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollApplication(taskId, applicationId), 1000)
          return
        }
        if (task.status !== 'SUCCEEDED') throw new Error(task.error || task.message)
        const application = await api<Application>(`/api/applications/${applicationId}`)
        patch({ applyBusy: null })
        await loadApplications(state.profile?.profile_id ?? application.profile_id)
        toast('打招呼语已生成，可在岗位卡片中复制；发送由你本人完成')
      } catch (error) {
        patch({ applyBusy: null })
        toast((error as Error).message)
      }
    },
    [loadApplications, patch, schedule, state.profile, toast],
  )

  const startApplication = useCallback(
    async (snapshotId: string) => {
      const profile = state.profile
      if (!profile) return toast('请先提交简历')
      const existing = state.applications.find((item) => item.snapshot_id === snapshotId)
      if (existing?.greeting) {
        toast('这条投递已经准备好了，复制打招呼语即可')
        return
      }
      try {
        const queued = await postJson<{ application_id: string; task_id?: string; status: string }>(
          '/api/applications',
          { profile_id: profile.profile_id, snapshot_id: snapshotId },
        )
        if (!queued.task_id) {
          await loadApplications(profile.profile_id)
          toast('这条投递已经准备好了')
          return
        }
        patch({ applyBusy: queued.application_id })
        await loadApplications(profile.profile_id)
        void pollApplication(queued.task_id, queued.application_id)
      } catch (error) {
        patch({ applyBusy: null })
        toast((error as Error).message)
      }
    },
    [loadApplications, patch, pollApplication, state.applications, state.profile, toast],
  )

  const setApplicationStatus = useCallback(
    async (applicationId: string, next: 'opened' | 'submitted' | 'skip') => {
      try {
        await post<Application>(`/api/applications/${applicationId}/${next}`)
        await loadApplications(state.profile?.profile_id)
        toast(
          next === 'submitted' ? '已记录这份投递' : next === 'skip' ? '已跳过' : '已标记为已打开',
        )
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [loadApplications, state.profile, toast],
  )

  const setApplicationBoardStatus = useCallback(
    async (
      applicationId: string,
      status: ApplicationStatus,
      fields: { application_deadline?: string; reminder_at?: string } = {},
    ) => {
      try {
        await patchJson<Application>(`/api/applications/${applicationId}`, { status, ...fields })
        await loadApplications(state.profile?.profile_id)
        toast('投递进度已更新')
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [loadApplications, state.profile, toast],
  )

  // Load whatever exists as soon as a profile is in hand, so a reload shows the
  // applications that were already prepared.
  useEffect(() => {
    const profileId = state.profile?.profile_id
    if (!profileId) return
    void loadApplications(profileId)
  }, [state.profile?.profile_id, loadApplications])

  const remindedApplications = useRef(new Set<string>())
  useEffect(() => {
    const now = Date.now()
    const threeDays = now + 3 * 24 * 60 * 60 * 1000
    const terminal = new Set<ApplicationStatus>(['OFFER', 'REJECTED', 'SKIPPED'])
    for (const application of state.applications) {
      if (terminal.has(application.status) || remindedApplications.current.has(application.application_id)) continue
      const deadline = application.application_deadline
        ? new Date(`${application.application_deadline}T23:59:59`).getTime()
        : Number.POSITIVE_INFINITY
      const reminder = application.reminder_at
        ? new Date(application.reminder_at).getTime()
        : Number.POSITIVE_INFINITY
      if (reminder <= now || deadline <= threeDays) {
        remindedApplications.current.add(application.application_id)
        toast(`${application.company} · ${application.title} 的投递截止时间临近`)
      }
    }
  }, [state.applications, toast])

  const copyGreeting = useCallback(
    async (greeting: string) => {
      try {
        await navigator.clipboard.writeText(greeting)
        toast('打招呼语已复制')
      } catch {
        toast(greeting)
      }
    },
    [toast],
  )

  // ----- chat turns -------------------------------------------------------
  const pollChatTurn = useCallback(
    async (taskId: string) => {
      const conversationId = localStorage.getItem(CONVERSATION_KEY)
      try {
        const result = await api<{ task: Task }>(`/api/chat-turns/${taskId}`)
        if (ACTIVE_TASK_STATUSES.includes(result.task.status as (typeof ACTIVE_TASK_STATUSES)[number])) {
          schedule(() => void pollChatTurn(taskId), 1000)
          return
        }
        patch({ chatBusy: false })
        await refreshConversation(conversationId)
        if (!['SUCCEEDED', 'FAILED_VALIDATION'].includes(result.task.status)) {
          toast(result.task.error || result.task.message)
        }
      } catch (error) {
        patch({ chatBusy: false })
        toast((error as Error).message)
      }
    },
    [patch, refreshConversation, schedule, toast],
  )

  const sendChat = useCallback(
    async (text: string) => {
      const message = String(text ?? '').trim()
      if (!message || state.chatBusy) return
      try {
        const conversation = await ensureConversation()
        patch({ chatBusy: true })
        applyChatOpen(true)
        const result = await postJson<{ task_id: string }>(
          `/api/conversations/${conversation.conversation_id}/messages`,
          { message },
        )
        await refreshConversation(conversation.conversation_id)
        void pollChatTurn(result.task_id)
      } catch (error) {
        patch({ chatBusy: false })
        toast((error as Error).message)
      }
    },
    [applyChatOpen, ensureConversation, patch, pollChatTurn, refreshConversation, state.chatBusy, toast],
  )

  const confirmAction = useCallback(
    async (actionId: string) => {
      try {
        const action = await post<{
          kind: string
          result: Record<string, string>
        }>(`/api/chat-actions/${actionId}/confirm`)
        const result = action.result || {}
        if (result.profile_id) {
          const profile = await api<Profile>(`/api/resumes/${result.profile_id}/profile`)
          const next: Partial<AppState> = { profile }
          if (result.run_id) {
            generation.current += 1
            const gen = generation.current
            next.runId = result.run_id
            next.runTask = null
            next.comparison = null
            next.jobs = []
            history.replaceState(null, '', taskSearch(result.run_id))
            patch(next)
            goToStep(3)
            void pollDiscovery(result.run_id, gen, result.profile_id)
            await refreshConversation(localStorage.getItem(CONVERSATION_KEY))
            toast('操作已确认并执行')
            return
          }
          patch(next)
        }
        if (action.kind === 'UPDATE_PROFILE') goToStep(2)
        if (action.kind === 'START_COMPARISON' && result.task_id) {
          generation.current += 1
          void pollComparison(result.task_id, generation.current)
        }
        if (action.kind === 'START_RESUME_TAILORING' && result.tailoring_id) {
          await loadTailoring(result.tailoring_id)
          goToStep(6)
        }
        await refreshConversation(localStorage.getItem(CONVERSATION_KEY))
        toast('操作已确认并执行')
      } catch (error) {
        await refreshConversation(localStorage.getItem(CONVERSATION_KEY)).catch(() => {})
        toast((error as Error).message)
      }
    },
    [goToStep, loadTailoring, patch, pollComparison, pollDiscovery, refreshConversation, toast],
  )

  const rejectAction = useCallback(
    async (actionId: string) => {
      try {
        await post(`/api/chat-actions/${actionId}/reject`)
        await refreshConversation(localStorage.getItem(CONVERSATION_KEY))
        toast('已取消这项操作')
      } catch (error) {
        toast((error as Error).message)
      }
    },
    [refreshConversation, toast],
  )

  // ----- bootstrap --------------------------------------------------------
  const bootstrapped = useRef(false)
  useEffect(() => {
    if (bootstrapped.current) return
    bootstrapped.current = true
    void (async () => {
      const tailoringId = readTailoringId(location.search)
      let taskId = readTaskId(location.search)
      try {
        if (tailoringId) {
          const result = await api<{ tailoring: ResumeTailoring }>(
            `/api/resume-tailorings/${tailoringId}`,
          )
          const profile = await api<Profile>(`/api/resumes/${result.tailoring.profile_id}/profile`)
          patch({ profile })
          await conversationForProfile(profile.profile_id)
          await loadTailoring(tailoringId, false)
          goToStep(6)
          return
        }
        const conversation = await ensureConversation()
        if (!taskId && conversation.run_id) taskId = conversation.run_id
        if (taskId) {
          const task = await api<Task>(`/api/tasks/${taskId}`)
          const profile = await api<Profile>(
            `/api/resumes/${(task.payload as { profile_id: string }).profile_id}/profile`,
          )
          const jobs = await api<JobSnapshot[]>(`/api/jobs?run_id=${taskId}`)
          patch({ profile, runId: taskId, jobs, runTask: task })
          let bound = conversation
          if (
            bound.profile_id !== profile.profile_id ||
            bound.run_id !== taskId
          ) {
            bound = await conversationForProfile(profile.profile_id, taskId)
          }
          history.replaceState(null, '', `?task=${taskId}`)
          if (bound.comparison_id) {
            const comparison = await api<Comparison>(`/api/comparisons/${bound.comparison_id}`)
            patch({ comparison })
            goToStep(4)
            return
          }
          goToStep(3)
          generation.current += 1
          void pollDiscovery(taskId, generation.current, profile.profile_id)
          return
        }
        if (conversation.profile_id) {
          const profile = await api<Profile>(`/api/resumes/${conversation.profile_id}/profile`)
          patch({ profile })
          goToStep(2)
        }
      } catch (error) {
        toast(`无法恢复任务：${(error as Error).message}`)
        if (taskId) history.replaceState(null, '', location.pathname)
      }
    })()
  }, [
    conversationForProfile,
    ensureConversation,
    goToStep,
    loadTailoring,
    patch,
    pollDiscovery,
    toast,
  ])

  return {
    state,
    chatOpen,
    chatContextLabel: chatContextLabel(state.conversation, state.jobs.length),
    actions: {
      patch,
      toast,
      goToStep,
      applyChatOpen,
      uploadResume,
      startDiscovery,
      retryTask,
      cancelBrowserTask,
      refreshCaptureCount,
      finishBrowserCapture,
      submitManualJob,
      restart,
      sendChat,
      confirmAction,
      rejectAction,
      beginTailoring,
      startInterviewPrep,
      clearInterviewPrep,
      startApplication,
      setApplicationStatus,
      setApplicationBoardStatus,
      copyGreeting,
      loadApplications,
      submitTargetForm,
      captureTargetUrl,
      submitTailoringAnswers,
      selectVersion,
      saveResumeVersion,
      exportResume,
      setResumeTemplate: (template: ResumeTemplate) => patch({ resumeTemplate: template }),
    },
  }
}

export type CareerRadar = ReturnType<typeof useCareerRadar>
