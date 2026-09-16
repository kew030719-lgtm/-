import type { Application, ApplicationStatus, Comparison, JobSnapshot } from '../../types'
import EvidenceCitations from '../EvidenceCitations'

interface Props {
  active: boolean
  comparison: Comparison | null
  jobs: JobSnapshot[]
  applications: Application[]
  applyBusy: string | null
  onTailor: (snapshotId: string) => Promise<void>
  onInterview: (snapshotId: string) => Promise<void>
  onApply: (snapshotId: string) => Promise<void>
  onCopyGreeting: (greeting: string) => Promise<void>
  onApplicationStatus: (applicationId: string, next: 'opened' | 'submitted' | 'skip') => Promise<void>
  onApplicationBoardStatus: (
    applicationId: string,
    next: ApplicationStatus,
    fields?: { application_deadline?: string; reminder_at?: string },
  ) => Promise<void>
  onViewPlan: () => void
  onToast: (message: string) => void
}

export default function Panel4Ranking({
  active, comparison, jobs, applications, applyBusy,
  onTailor, onInterview, onApply, onCopyGreeting, onApplicationStatus,
  onApplicationBoardStatus, onViewPlan, onToast,
}: Props) {
  const applicationFor = (snapshotId: string) =>
    applications.find((item) => item.snapshot_id === snapshotId)
  const byId = new Map(jobs.map((job) => [job.snapshot_id, job]))

  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-4"
      aria-labelledby="rank-title"
    >
      <div className="panel-heading">
        <span>04</span>
        <div>
          <h2 id="rank-title">岗位适配排名</h2>
          <p>评分由后端固定公式计算；“无法判断”不会被记为零分。</p>
        </div>
      </div>
      <div className="score-legend">
        <span>技能 40%</span><span>项目 25%</span><span>经验 15%</span><span>学历 5%</span><span>偏好 15%</span>
      </div>
      <div id="ranking-list" className="ranking-list">
        {(comparison?.rankings ?? []).map((ranking, index) => {
          const job = byId.get(ranking.job_id)
          return (
            <article className="rank-card" key={ranking.job_id}>
              <div className="rank-num">{String(index + 1).padStart(2, '0')}</div>
              <div>
                <h3>{job?.title || '岗位'}</h3>
                <p>{job?.company || ''} · {job?.city || ''} · {job?.salary || '薪资未识别'}</p>
                {job && job.recruitment_type !== 'unknown' ? (
                  <p className="graduate-fit">
                    {job.recruitment_type === 'internship' ? '实习' : job.recruitment_type === 'campus' ? '校招' : '社招'}
                    {job.graduation_years.length ? ` · ${job.graduation_years.join('/')} 届` : ''}
                    {job.recruitment_batch ? ` · ${job.recruitment_batch}` : ''}
                    {job.application_deadline ? ` · 截止 ${job.application_deadline}` : ''}
                  </p>
                ) : null}
                {ranking.graduate_fit !== null ? <p>应届生适配度：{ranking.graduate_fit}</p> : null}
                {ranking.graduate_advantages.map((item) => <p className="graduate-fit" key={item}>✓ {item}</p>)}
                <p>{ranking.explanation}</p>
                <div className="skill-row">
                  <b>已匹配 {ranking.matched_skills.join('、') || '无法判断'}</b>
                  {' · '}
                  <em>待补 {ranking.missing_skills.join('、') || '暂无明确缺口'}</em>
                </div>
                {ranking.risks.map((risk) => <p className="risk" key={risk}>⚠ {risk}</p>)}
                <button
                  type="button"
                  className="evidence-link"
                  onClick={() => onToast(ranking.citations.map((item) => `“${item.quote}”`).join(' · '))}
                >
                  查看证据引用
                </button>
                <button
                  type="button"
                  className="tailor-button"
                  onClick={() => void onTailor(ranking.job_id)}
                >
                  为这个岗位修改简历
                </button>
                <button
                  type="button"
                  className="interview-button"
                  onClick={() => void onInterview(ranking.job_id)}
                >
                  面试准备与面试题
                </button>

                {(() => {
                  const application = applicationFor(ranking.job_id)
                  if (!application) {
                    const busy = applyBusy === ranking.job_id
                    return (
                      <button
                        type="button"
                        className="apply-button"
                        disabled={busy}
                        onClick={() => void onApply(ranking.job_id)}
                      >
                        {busy ? '正在生成打招呼语…' : '帮我投递'}
                      </button>
                    )
                  }
                  if (!application.greeting) {
                    // Only "generating" while something is actually running: a
                    // preparation that failed leaves the record without a greeting,
                    // and the user needs a way to try again.
                    const generating = applyBusy === application.application_id || applyBusy === ranking.job_id
                    return generating ? (
                      <p className="apply-hint">正在生成打招呼语…</p>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="apply-button"
                          onClick={() => void onApply(ranking.job_id)}
                        >
                          重新生成打招呼语
                        </button>
                        {application.error ? <p className="apply-note">上次失败：{application.error}</p> : null}
                      </>
                    )
                  }
                  return (
                    <div className="apply-box">
                      <b>打招呼语</b>
                      <p className="apply-greeting">{application.greeting}</p>
                      <EvidenceCitations citations={application.greeting_citations ?? []} summaryPrefix="" />
                      <div className="apply-actions">
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => void onCopyGreeting(application.greeting)}
                        >
                          复制打招呼语
                        </button>
                        <a
                          className="secondary button-link"
                          href={application.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          打开岗位页
                        </a>
                        {application.status === 'SUBMITTED' ? (
                          <span className="apply-done">已记录投递</span>
                        ) : (
                          <button
                            type="button"
                            className="primary compact"
                            onClick={() => void onApplicationStatus(application.application_id, 'submitted')}
                          >
                            我已投递
                          </button>
                        )}
                        <button
                          type="button"
                          className="text-button"
                          onClick={() => void onApplicationStatus(application.application_id, 'skip')}
                        >
                          跳过
                        </button>
                      </div>
                      <p className="apply-hint">
                        CareerRadar 不会替你点击发送。请核对后在岗位页面上自行提交，
                        提交后再点「我已投递」记录结果。
                      </p>
                      <label className="application-stage">
                        投递进度
                        <select
                          value={application.status}
                          onChange={(event) => void onApplicationBoardStatus(
                            application.application_id, event.target.value as ApplicationStatus,
                          )}
                        >
                          <option value="DRAFT">待准备</option>
                          <option value="READY">已准备</option>
                          <option value="OPENED">已打开</option>
                          <option value="SUBMITTED">已投递</option>
                          <option value="ASSESSMENT">笔试</option>
                          <option value="INTERVIEW">面试</option>
                          <option value="OFFER">Offer</option>
                          <option value="REJECTED">拒绝</option>
                          <option value="SKIPPED">跳过</option>
                        </select>
                      </label>
                      <div className="application-dates">
                        <label>
                          截止日期
                          <input
                            type="date"
                            value={application.application_deadline ?? ''}
                            onChange={(event) => void onApplicationBoardStatus(
                              application.application_id, application.status,
                              { application_deadline: event.target.value },
                            )}
                          />
                        </label>
                        <label>
                          提醒时间
                          <input
                            type="datetime-local"
                            value={application.reminder_at?.slice(0, 16) ?? ''}
                            onChange={(event) => void onApplicationBoardStatus(
                              application.application_id, application.status,
                              { reminder_at: event.target.value },
                            )}
                          />
                        </label>
                      </div>
                      {application.note ? <p className="apply-note">{application.note}</p> : null}
                    </div>
                  )
                })()}
              </div>
              <div className="score">{ranking.total}<small>适配分</small></div>
            </article>
          )
        })}
      </div>
      <button className="primary next-plan" id="view-plan" type="button" onClick={onViewPlan}>
        查看 7 天准备计划 <span>→</span>
      </button>
    </section>
  )
}
