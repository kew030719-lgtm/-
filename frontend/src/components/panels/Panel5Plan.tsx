import { stripDayPrefix } from '../../lib/format'
import type { Comparison, InterviewPrep } from '../../types'
import EvidenceCitations from '../EvidenceCitations'

interface Props {
  active: boolean
  comparison: Comparison | null
  prep: InterviewPrep | null
  busy: boolean
  onRestart: () => void
  onClearPrep: () => void
}

export default function Panel5Plan({ active, comparison, prep, busy, onRestart, onClearPrep }: Props) {
  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-5"
      aria-labelledby="plan-title"
    >
      <div className="panel-heading">
        <span>05</span>
        <div>
          <h2 id="plan-title">{prep ? '面试准备' : '7 天准备计划'}</h2>
          <p>
            {prep
              ? '针对单个岗位的逐日安排与有证据支撑的面试题。'
              : '围绕排名前三岗位的共同缺口安排，每天都有清晰交付物。'}
          </p>
        </div>
      </div>

      {busy ? (
        <div className="tailoring-progress" id="plan-progress">
          <span className="pulse" /><b>Agent 正在生成面试准备…</b>
        </div>
      ) : null}

      {prep ? (
        <div className="interview-prep" id="interview-prep">
          <div className="interview-target">
            <b>{prep.company} · {prep.title}</b>
            {prep.missing_skills.length ? (
              <p>岗位要求中尚无简历证据的能力：{prep.missing_skills.join('、')}</p>
            ) : null}
          </div>

          <h3 className="interview-section">逐日计划</h3>
          <ol className="plan-list" id="interview-days">
            {prep.days.map((day) => (
              <li key={day.day}>
                <b>第 {day.day} 天 · {day.focus}</b>
                {day.deliverable ? <span>交付物：{day.deliverable}</span> : null}
                <EvidenceCitations citations={day.citations ?? []} summaryPrefix="" />
              </li>
            ))}
          </ol>

          <h3 className="interview-section">面试题（{prep.questions.length}）</h3>
          <div className="interview-questions" id="interview-questions">
            {prep.questions.map((question) => (
              <article className="interview-question" key={question.question_id}>
                <span className="question-category">{question.category}</span>
                <b>{question.question}</b>
                {question.why_asked ? <p>为什么问：{question.why_asked}</p> : null}
                {question.answer_hint ? <p>回答提示：{question.answer_hint}</p> : null}
                <EvidenceCitations citations={question.citations ?? []} summaryPrefix="" />
              </article>
            ))}
          </div>

          <p className="interview-source">
            生成方式：{prep.source === 'fallback' ? '本地确定性降级' : 'PydanticAI · LangGraph'}
          </p>
          {prep.note ? <p className="apply-note">{prep.note}</p> : null}
          <button className="ghost" type="button" onClick={onClearPrep}>返回总体计划</button>
        </div>
      ) : (
        <>
          <ol id="plan-list" className="plan-list">
            {(comparison?.action_plan ?? []).map((item, index) => (
              <li key={index}>{stripDayPrefix(item)}</li>
            ))}
          </ol>
          <p className="interview-hint">在「岗位排名」里点选某个岗位的“面试准备与面试题”，即可得到针对该岗位的逐日计划与面试题。</p>
          <button className="ghost" id="restart" type="button" onClick={onRestart}>
            分析另一份简历
          </button>
        </>
      )}
    </section>
  )
}