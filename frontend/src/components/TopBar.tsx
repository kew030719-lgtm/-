import HealthIndicator from './HealthIndicator'

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