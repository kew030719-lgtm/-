import { useState } from 'react'

import ChatDrawer from './components/chat/ChatDrawer'
import SetupWizard from './components/SetupWizard'
import Panel1Resume from './components/panels/Panel1Resume'
import Panel2Confirm from './components/panels/Panel2Confirm'
import Panel3Discovery from './components/panels/Panel3Discovery'
import Panel4Ranking from './components/panels/Panel4Ranking'
import Panel5Plan from './components/panels/Panel5Plan'
import Panel6Tailoring from './components/panels/Panel6Tailoring'
import Toast from './components/Toast'
import TopBar from './components/TopBar'
import WorkflowRail from './components/WorkflowRail'
import { reachableSteps } from './lib/format'
import { useCareerRadar } from './useCareerRadar'
import { useSites } from './useSites'

export default function App() {
  const { state, chatOpen, chatContextLabel, actions } = useCareerRadar()
  const { sites, cities } = useSites()
  const [uploading, setUploading] = useState(false)

  const reachable = reachableSteps({
    profile: state.profile,
    runId: state.runId,
    comparison: state.comparison,
    interviewPrep: state.interviewPrep,
    tailoring: state.tailoring,
    targetJob: state.targetJob,
    draftBundle: state.draftBundle,
  })

  return (
    <>
      <SetupWizard />
      <TopBar
        chatOpen={chatOpen}
        onToggleChat={() => actions.applyChatOpen(!chatOpen)}
      />

      <main className="shell">
        <WorkflowRail
          step={state.step}
          reachable={reachable}
          onNavigate={actions.goToStep}
          onBlocked={actions.toast}
        />

        <section className="workspace">
          <div className="hero-copy">
            <p className="eyebrow">RESUME → ROLE → EVIDENCE</p>
            <h1>让每一次岗位判断<br /><em>都有原文依据。</em></h1>
            <p>提交简历后，Agent 推荐方向；你确认目标，再由自研采集器发现职位并生成可解释排名。</p>
          </div>

          <Panel1Resume
            active={state.step === 1}
            busy={uploading}
            onUpload={async (form) => {
              setUploading(true)
              try {
                await actions.uploadResume(form)
              } finally {
                setUploading(false)
              }
            }}
          />

          <Panel2Confirm
            active={state.step === 2}
            profile={state.profile}
            cities={cities}
            sites={sites}
            onSubmit={actions.startDiscovery}
            onToast={actions.toast}
          />

          <Panel3Discovery
            active={state.step === 3}
            runId={state.runId}
            task={state.runTask}
            jobs={state.jobs}
            sites={sites.filter((site) => state.sites.includes(site.key))}
            onRetry={actions.retryTask}
            onCancel={actions.cancelBrowserTask}
            onFinish={actions.finishBrowserCapture}
            onRefreshCount={actions.refreshCaptureCount}
            onManual={actions.submitManualJob}
            onToast={actions.toast}
          />

          <Panel4Ranking
            active={state.step === 4}
            comparison={state.comparison}
            jobs={state.jobs}
            applications={state.applications}
            applyBusy={state.applyBusy}
            onApply={actions.startApplication}
            onCopyGreeting={actions.copyGreeting}
            onApplicationStatus={actions.setApplicationStatus}
            onApplicationBoardStatus={actions.setApplicationBoardStatus}
            onTailor={actions.beginTailoring}
            onInterview={actions.startInterviewPrep}
            onViewPlan={() => actions.goToStep(5)}
            onToast={actions.toast}
          />

          <Panel5Plan
            active={state.step === 5}
            comparison={state.comparison}
            prep={state.interviewPrep}
            busy={state.interviewBusy}
            onRestart={actions.restart}
            onClearPrep={actions.clearInterviewPrep}
          />

          <Panel6Tailoring
            active={state.step === 6}
            targetJob={state.targetJob}
            tailoring={state.tailoring}
            tailoringBusy={state.tailoringBusy}
            tailorNotice={state.tailorNotice}
            draftBundle={state.draftBundle}
            draft={state.draft}
            resumeTemplate={state.resumeTemplate}
            exportLinks={state.exportLinks}
            exportError={state.exportError}
            resumeBusy={state.resumeBusy}
            onToast={actions.toast}
            onSubmitTarget={actions.submitTargetForm}
            onCaptureTarget={actions.captureTargetUrl}
            onSubmitAnswers={actions.submitTailoringAnswers}
            onSelectVersion={actions.selectVersion}
            onSetTemplate={actions.setResumeTemplate}
            onSaveVersion={actions.saveResumeVersion}
            onExport={actions.exportResume}
          />

          <Toast message={state.toast} />
        </section>
      </main>

      <ChatDrawer
        open={chatOpen}
        conversation={state.conversation}
        contextLabel={chatContextLabel}
        chatBusy={state.chatBusy}
        onSend={actions.sendChat}
        onConfirm={actions.confirmAction}
        onReject={actions.rejectAction}
        onClose={() => actions.applyChatOpen(false)}
      />
    </>
  )
}
