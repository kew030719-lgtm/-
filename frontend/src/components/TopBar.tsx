import HealthIndicator from './HealthIndicator'
import { api } from '../api'

interface Props {
  chatOpen: boolean
  onToggleChat: () => void
}

export default function TopBar({ chatOpen, onToggleChat }: Props) {
  return (
    <header className="topbar">
      <a className="brand" href="/app/" aria-label="CareerRadar 首页">
        <span className="brand-mark" aria-hidden="true">CR</span>
        <span>CareerRadar</span>
      </a>
      <HealthIndicator />
      <div className="local-controls">
        <button type="button" onClick={() => window.dispatchEvent(new Event('career-radar-open-setup'))}>模型设置</button>
        <a href="/api/local-data/export" download>导出本地数据</a>
        <button type="button" onClick={() => {
          if (!window.confirm('确定删除全部本地数据、导出文件、日志和模型密钥吗？此操作不可撤销。')) return
          void api('/api/local-data?confirmation=DELETE_ALL_LOCAL_DATA', { method: 'DELETE' })
            .then(() => window.location.reload())
            .catch((error) => window.alert((error as Error).message))
        }}>删除本地数据</button>
      </div>
      <button
        className="chat-top-button"
        id="chat-toggle"
        type="button"
        aria-controls="chat-drawer"
        aria-expanded={chatOpen}
        onClick={onToggleChat}
      >
        <span>✦</span> 求职 Agent
      </button>
    </header>
  )
}
