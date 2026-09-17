// Mirrors career_radar/schemas.py. Only the fields the UI actually reads are
// declared; the backend remains the source of truth.

export interface Evidence {
  source_type: 'resume' | 'job'
  source_id: string
  block_id: string
  quote: string
  section?: string
  provenance?: string
}

export interface RoleRecommendation {
  role: string
  keywords: string[]
  confidence: '高' | '中' | '低'
  rationale: string
  strengths: string[]
  gaps: string[]
  citations: Evidence[]
}

export interface Profile {
  profile_id: string
  skills: string[]
  experience_years: number | null
  education: string | null
  cities: string[]
  salary_preference: string | null
  work_type_preference: string | null
  expected_graduation_year?: number | null
  selected_roles: RoleRecommendation[]
  recommendations: RoleRecommendation[]
  confirmed: boolean
}

export interface JobSnapshot {
  snapshot_id: string
  title: string
  company: string
  city: string
  salary: string
  experience: string
  education: string
  required_skills: string[]
  status: string
  canonical_url: string
  recruitment_type: 'campus' | 'internship' | 'experienced' | 'unknown'
  graduation_years: number[]
  recruitment_batch: string | null
  published_date: string | null
  application_deadline: string | null
  conversion_opportunity: boolean | null
}

export interface JobScore {
  job_id: string
  total: number
  explanation: string
  matched_skills: string[]
  missing_skills: string[]
  risks: string[]
  graduate_fit: number | null
  graduate_advantages: string[]
  citations: Evidence[]
}

export interface Comparison {
  comparison_id: string
  profile_id: string
  rankings: JobScore[]
  action_plan: string[]
}

export type TaskStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'NEEDS_MANUAL_INPUT'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'FAILED_VALIDATION'

export interface Task {
  id: string
  kind: string
  status: TaskStatus
  progress: number
  message: string
  error: string | null
  payload: Record<string, unknown>
}

export interface ChatCitation {
  source_type: 'resume' | 'job'
  source_id: string
  block_id: string
  quote: string
}

export type ChatActionKind =
  | 'UPDATE_PROFILE'
  | 'RESTART_DISCOVERY'
  | 'START_COMPARISON'
  | 'START_RESUME_TAILORING'

export interface ChatAction {
  action_id: string
  kind: ChatActionKind
  status: 'PENDING' | 'EXECUTING' | 'EXECUTED' | 'REJECTED' | 'FAILED'
  preview: {
    user_intent?: string
    before?: Record<string, unknown>
    after?: Record<string, unknown>
    company?: string
    title?: string
    current_run_id?: string | null
    current_run_status?: string | null
    current_job_count?: number
  }
  result: { error?: string; message?: string; [key: string]: unknown }
}

export interface ChatMessage {
  message_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  status: string
  citations: ChatCitation[]
  source: string
}

export interface Conversation {
  conversation_id: string
  profile_id: string | null
  run_id: string | null
  comparison_id: string | null
  messages: ChatMessage[]
  actions: ChatAction[]
}

export interface InterviewDay {
  day: number
  focus: string
  deliverable: string
  evidence_ids: string[]
  citations: ChatCitation[]
}

export type InterviewCategory = '技术深挖' | '项目经历' | '能力缺口' | '行为面' | '反问'

export interface InterviewQuestion {
  question_id: string
  question: string
  category: InterviewCategory
  why_asked: string
  answer_hint: string
  evidence_ids: string[]
  citations: ChatCitation[]
}

export interface InterviewPrep {
  prep_id: string
  profile_id: string
  snapshot_id: string
  company: string
  title: string
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'FAILED_VALIDATION'
  days: InterviewDay[]
  questions: InterviewQuestion[]
  missing_skills: string[]
  source: string
  note: string
  error: string | null
  task_id: string | null
}

export type ApplicationStatus =
  | 'DRAFT' | 'READY' | 'OPENED' | 'SUBMITTED' | 'ASSESSMENT'
  | 'INTERVIEW' | 'OFFER' | 'REJECTED' | 'REPLIED' | 'SKIPPED'

export interface Application {
  application_id: string
  profile_id: string
  snapshot_id: string
  company: string
  title: string
  url: string
  status: ApplicationStatus
  greeting: string
  greeting_evidence_ids: string[]
  greeting_citations: ChatCitation[]
  resume_export_id: string | null
  note: string
  source: string
  error: string | null
  task_id: string | null
  application_deadline: string | null
  reminder_at: string | null
}

export interface TargetJob {
  target_job_id: string
  profile_id: string
  source_type: 'snapshot' | 'pasted' | 'boss_url'
  company: string
  title: string
  url: string
  required_skills: string[]
}

export interface TailoringQuestion {
  question_id: string
  question: string
  requirement: string
  status: 'PENDING' | 'ANSWERED' | 'SKIPPED'
  answer: string | null
}

export interface ResumeTailoring {
  tailoring_id: string
  profile_id: string
  target_job_id: string
  status: 'COLLECTING' | 'READY' | 'GENERATING' | 'SUCCEEDED' | 'FAILED' | 'FAILED_VALIDATION'
  questions: TailoringQuestion[]
  draft_id: string | null
  error: string | null
}

export interface ResumeBullet {
  bullet_id: string
  text: string
  evidence_ids: string[]
  provenance: 'uploaded_resume' | 'user_confirmed' | 'user_edited'
  priority: number
}

export interface ResumeEntry {
  entry_id: string
  heading: string
  subheading: string
  date_range: string
  evidence_ids: string[]
  bullets: ResumeBullet[]
}

export interface ResumeQualityReport {
  relevance_score: number
  specificity_score: number
  structure_score: number
  conciseness_score: number
  evidence_coverage: number
  duplicate_count: number
  estimated_pages: number
  issues: string[]
  uncovered_requirements: string[]
  passed: boolean
}

export interface ResumeSection {
  section_id: string
  title: string
  entries: ResumeEntry[]
  bullets: ResumeBullet[]
}

export interface CandidateContact {
  name: string
  phone: string
  email: string
  location: string
}

export interface ResumeDraftVersion {
  version_id: string
  draft_id: string
  version: number
  tailoring_id: string
  profile_id: string
  target_job_id: string
  headline: string
  summary: ResumeBullet[]
  skills: ResumeBullet[]
  sections: ResumeSection[]
  contact: CandidateContact
  source: string
  change_log: Array<{ kind?: string; target?: string; before?: string; after?: string }>
  quality_report: ResumeQualityReport
  validation_status: 'VALID' | 'FAILED_VALIDATION'
}

export interface ResumeDraftBundle {
  draft_id: string
  current: ResumeDraftVersion
  versions: ResumeDraftVersion[]
}

export interface ResumeExport {
  export_id: string
  template: 'technical' | 'business'
  status: string
}

export interface SiteOption {
  key: string
  label: string
  /** The site's own home page, for "open in your browser" links. */
  home: string
  cities: string[]
  enabled: boolean
}

export type ResumeTemplate = 'technical' | 'business'
