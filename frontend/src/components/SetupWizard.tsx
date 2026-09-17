import { useEffect, useState } from 'react'

import { api, post, putJson } from '../api'

interface SettingsView {
  model_base_url: string
  model_name: string
  vision_model_name: string
  api_key_configured: boolean
  data_dir: string
}

interface BrowserStatus {
  connected: boolean
  help: string | null
}

export default function SetupWizard() {
  const [settings, setSettings] = useState<SettingsView | null>(null)
  const [browser, setBrowser] = useState<BrowserStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [message, setMessage] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const reopen = () => setOpen(true)
    window.addEventListener('career-radar-open-setup', reopen)
    Promise.all([
      api<SettingsView>('/api/settings'),
      api<BrowserStatus>('/api/browser-helper/status'),
    ]).then(([nextSettings, nextBrowser]) => {
      setSettings(nextSettings)
      setBrowser(nextBrowser)
      setOpen(!nextSettings.api_key_configured && localStorage.getItem('career-radar-setup-dismissed') !== '1')
    }).catch(() => undefined)
    return () => window.removeEventListener('career-radar-open-setup', reopen)
  }, [])

  if (!open || !settings) return null

  const saveAndTest = async () => {
    setMessage('正在保存并测试连接…')
    try {
      const saved = await putJson<SettingsView>('/api/settings/model', {
        model_base_url: settings.model_base_url,
        model_name: settings.model_name,
        vision_model_name: settings.vision_model_name,
        api_key: apiKey.trim() || undefined,
      })
      await post<{ ok: boolean }>('/api/settings/model/test')
      setSettings(saved)
      setApiKey('')
      setMessage('连接成功，密钥已交给系统安全存储。')
      window.setTimeout(() => setOpen(false), 800)
    } catch (error) {
      setMessage((error as Error).message)
    }
  }

  return (
    <div className="setup-backdrop" role="dialog" aria-modal="true" aria-labelledby="setup-title">
      <div className="setup-card">
        <p className="eyebrow">本地配置</p>
        <h2 id="setup-title">连接你的模型服务</h2>
        <p>CareerRadar 本地运行；岗位与简历只会按功能需要发送给你配置的模型服务。</p>
        <label>服务地址<input value={settings.model_base_url} onChange={(e) => setSettings({...settings, model_base_url: e.target.value})} /></label>
        <label>模型名称<input value={settings.model_name} onChange={(e) => setSettings({...settings, model_name: e.target.value})} /></label>
        <label>视觉模型名称（扫描版 PDF）<input placeholder="例如支持图片输入的视觉模型" value={settings.vision_model_name} onChange={(e) => setSettings({...settings, vision_model_name: e.target.value})} /></label>
        <label>模型密钥<input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
        <p className={browser?.connected ? 'setup-ok' : 'setup-warning'}>
          Chrome 助手：{browser?.connected ? '已连接' : browser?.help || '未检测到'}
        </p>
        {message ? <p className="setup-message">{message}</p> : null}
        <div className="setup-actions">
          <button className="primary" type="button" disabled={!settings.api_key_configured && !apiKey.trim()} onClick={() => void saveAndTest()}>保存并测试</button>
          <button className="ghost" type="button" onClick={() => {
            localStorage.setItem('career-radar-setup-dismissed', '1')
            setOpen(false)
          }}>暂时使用本地降级功能</button>
        </div>
        <small>本地数据目录：{settings.data_dir}</small>
      </div>
    </div>
  )
}
