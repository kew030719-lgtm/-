// URL state for the SPA.
//
// `?task=<run_id>` is load-bearing beyond the UI: the Chrome extension's
// preferredTaskId() scans open tabs for `url.origin === endpoint` and reads the
// `task` query parameter to decide which CareerRadar instance to serve. If the
// SPA stops writing it, the extension silently never auto-claims a run: the
// task sits QUEUED forever and nothing anywhere reports an error.

export const TASK_PARAM = 'task'
export const TAILORING_PARAM = 'tailoring'

export function taskSearch(runId: string): string {
  return `?${TASK_PARAM}=${encodeURIComponent(runId)}`
}

export function tailoringSearch(tailoringId: string): string {
  return `?${TAILORING_PARAM}=${encodeURIComponent(tailoringId)}`
}

export function readParam(search: string, name: string): string | null {
  return new URLSearchParams(search).get(name)
}

export function readTaskId(search: string): string | null {
  return readParam(search, TASK_PARAM)
}

export function readTailoringId(search: string): string | null {
  return readParam(search, TAILORING_PARAM)
}

export const CHAT_OPEN_KEY = 'careerRadarChatOpen'
export const CONVERSATION_KEY = 'careerRadarConversation'