import { useState } from 'react'

interface Props {
  active: boolean
  busy: boolean
  onUpload: (form: FormData) => Promise<void>
}

export default function Panel1Resume({ active, busy, onUpload }: Props) {
  const [fileName, setFileName] = useState<string | null>(null)

  return (
    <section
      className={active ? 'panel active' : 'panel'}
      id="panel-1"
      aria-labelledby="upload-title"
    >
      <div className="panel-heading">
        <span>01</span>
        <div>
          <h2 id="upload-title">提交你的简历</h2>
          <p>支持 10 MB 以内的 PDF、DOCX，或直接粘贴文本。</p>
        </div>
      </div>
      <form
        id="resume-form"
        className="upload-grid"
        onSubmit={(event) => {
          event.preventDefault()
          void onUpload(new FormData(event.currentTarget))
        }}
      >
        <label className="dropzone" htmlFor="resume-file">
          <input
            id="resume-file"
            name="file"
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) setFileName(file.name)
            }}
          />
          <span className="upload-icon">↥</span>
          <b>{fileName ?? '选择简历文件'}</b>
          <small>点击选择 PDF 或 DOCX</small>
        </label>
        <div className="or"><span>或</span></div>
        <label className="text-box">
          <span>粘贴简历文本</span>
          <textarea
            id="resume-text"
            name="text"
            rows={11}
            placeholder="教育经历、技术栈、项目经历、工作经历……"
          />
        </label>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? 'Agent 正在分析… ' : '开始分析 '}
          {busy ? null : <span>→</span>}
        </button>
      </form>
    </section>
  )
}