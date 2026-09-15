// The single HTTP seam. Mirrors the `api()` helper in the previous app.js so
// error messages keep surfacing the backend's `detail` string verbatim.

export async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, options)
  const data: unknown = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (data as { detail?: string }).detail
    throw new Error(detail || '请求失败')
  }
  return data as T
}

/** POST with no body, matching the endpoints that take none. */
export function post<T>(url: string): Promise<T> {
  return api<T>(url, { method: 'POST' })
}

export function postJson<T>(url: string, body: unknown): Promise<T> {
  return api<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function patchJson<T>(url: string, body: unknown): Promise<T> {
  return api<T>(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function putJson<T>(url: string, body: unknown): Promise<T> {
  return api<T>(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Poll a task until it leaves the queued/running states. */
export const ACTIVE_TASK_STATUSES = ['QUEUED', 'RUNNING'] as const