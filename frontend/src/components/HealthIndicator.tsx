import { useEffect, useState } from 'react'

import { api } from '../api'

interface Health {
  status: string
  model: string
  api_key_configured: boolean
}

/**
 * Replaces the htmx health fragment. The previous UI polled an HTML fragment;
 * this reads the JSON endpoint and renders client-side, which is what drops
 * htmx from the frontend entirely.
 */
export default function HealthIndicator() {
  const [health, setHealth] = useState<Health | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        const value = await api<Health>('/health')
        if (alive) {
          setHealth(value)
          setFailed(false)
        }
      } catch {
        if (alive) setFailed(true)
      }
    }
    void check()
    const id = window.setInterval(() => void check(), 30_000)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [])

  if (failed) return <div className="system-state"><span className="pulse" />本地服务未响应</div>

  const modelState = health ? (health.api_key_configured ? health.model : '未配置模型密钥') : '正在检查本地服务…'
  const runtime = health ? 'PydanticAI + LangGraph' : ''

  return (
    <div className="system-state">
      <span className="pulse" />
      {health ? `本地服务正常 · ${runtime} · ${modelState}` : modelState}
    </div>
  )
}