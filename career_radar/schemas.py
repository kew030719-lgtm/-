from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    NEEDS_MANUAL_INPUT = "NEEDS_MANUAL_INPUT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    FAILED_VALIDATION = "FAILED_VALIDATION"


class Evidence(BaseModel):
    source_type: Literal["resume", "job"]
    source_id: str
    block_id: str
    quote: str
    section: str = ""
    provenance: Literal["uploaded_resume", "user_confirmed", "user_edited", "job_snapshot"] | None = None


class ResumeSourceEntry(BaseModel):
    entry_id: str
    kind: Literal["experience", "project", "education", "skills", "other"]
    heading: str = ""
    organization: str = ""
    role: str = ""
    date_range: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    original_bullets: list[str] = Field(default_factory=list)


class RoleRecommendation(BaseModel):
    role: str
    keywords: list[str] = Field(min_length=1, max_length=4)
    confidence: Literal["高", "中", "低"]
    rationale: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    citations: list[Evidence] = Field(min_length=1)


class CandidateProfile(BaseModel):
    profile_id: str
    skills: list[str] = Field(default_factory=list)
    experience_years: float | None = None
    education: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    resume_entries: list[ResumeSourceEntry] = Field(default_factory=list)
    recommendations: list[RoleRecommendation] = Field(default_factory=list)
    selected_roles: list[RoleRecommendation] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    salary_preference: str | None = None
    work_type_preference: str | None = None
    expected_graduation_year: int | None = Field(default=None, ge=2000, le=2100)
    confirmed: bool = False
    analysis_source: Literal["langgraph", "fallback"] = "fallback"

    @field_validator("selected_roles")
    @classmethod
    def at_most_two_roles(cls, value):
        if len(value) > 2:
            raise ValueError("最多选择两个岗位方向")
        return value

    @field_validator("cities")
    @classmethod
    def at_most_two_cities(cls, value):
        if len(value) > 2:
            raise ValueError("最多选择两个城市")
        return value


class JobSnapshot(BaseModel):
    snapshot_id: str
    platform_job_id: str
    canonical_url: str
    # Which adapter produced this snapshot. Empty means a row stored before
    # CareerRadar supported more than one job site, all of which are BOSS.
    site: str = ""
    title: str
    company: str
    city: str = ""
    salary: str = ""
    # Normalised to yuan per month by the adapter, because sites disagree about
    # units ("20-35K", "15-25万/年", "8000-12000元/月"). Scoring prefers these
    # over re-parsing the display string.
    salary_min: int | None = None
    salary_max: int | None = None
    experience: str = ""
    education: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    bonus_skills: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    work_type: str = ""
    recruitment_type: Literal["campus", "internship", "experienced", "unknown"] = "unknown"
    graduation_years: list[int] = Field(default_factory=list)
    experience_requirement_years: float | None = None
    recruitment_batch: str | None = None
    published_date: str | None = None
    application_deadline: str | None = None
    conversion_opportunity: bool | None = None
    status: Literal["active", "offline", "unknown"] = "active"
    cleaned_text: str = ""
    content_hash: str
    fetched_at: str
    transport: Literal["http", "browser", "manual"]
    blocks: list[Evidence] = Field(default_factory=list)
    changed_fields: list[str] = Field(default_factory=list)


class JobScore(BaseModel):
    job_id: str
    total: float
    skill: float | None
    project: float | None
    experience: float | None
    education: float | None
    preference: float | None
    graduate_fit: float | None = None
    graduate_advantages: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    explanation: str = ""
    citations: list[Evidence] = Field(default_factory=list)


class Comparison(BaseModel):
    comparison_id: str
    profile_id: str
    rankings: list[JobScore]
    action_plan: list[str]
    source: Literal["langgraph", "fallback"]
    created_at: str


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    FEEDBACK = "feedback"
    FACT = "fact"
    REJECTED_JOB = "rejected_job"


class AgentMemory(BaseModel):
    """A durable preference, constraint or feedback signal.

    Facts are deliberately absent: they belong to ``CandidateProfile.evidence``,
    which carries provenance and goes through confirmation. A memory is a soft
    signal the agent may use, never a statement about what the candidate can do.
    """

    memory_id: str
    profile_id: str
    kind: MemoryKind
    text: str
    source_type: str = ""
    source_id: str = ""
    confidence: float = 1.0
    superseded_by: str | None = None
    created_at: str


class ChatActionKind(StrEnum):
    UPDATE_PROFILE = "UPDATE_PROFILE"
    RESTART_DISCOVERY = "RESTART_DISCOVERY"
    START_COMPARISON = "START_COMPARISON"
    START_RESUME_TAILORING = "START_RESUME_TAILORING"


class ChatCitation(BaseModel):
    source_type: Literal["resume", "job"]
    source_id: str
    block_id: str
    quote: str = ""
    section: str = ""


class ChatMessage(BaseModel):
    message_id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    status: Literal["PENDING", "SUCCEEDED", "FAILED", "FAILED_VALIDATION"] = "SUCCEEDED"
    citations: list[ChatCitation] = Field(default_factory=list)
    task_id: str | None = None
    source: Literal["user", "langgraph", "fallback", "system"] = "user"
    created_at: str


class ChatAction(BaseModel):
    action_id: str
    conversation_id: str
    source_message_id: str | None = None
    kind: ChatActionKind
    arguments: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    status: Literal["PENDING", "EXECUTING", "EXECUTED", "REJECTED", "FAILED"] = "PENDING"
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class Conversation(BaseModel):
    conversation_id: str
    profile_id: str | None = None
    run_id: str | None = None
    comparison_id: str | None = None
    title: str = "通用职业咨询"
    created_at: str
    updated_at: str
    messages: list[ChatMessage] = Field(default_factory=list)
    actions: list[ChatAction] = Field(default_factory=list)
    # Compacted history for turns that fell out of the live window. Empty means
    # nothing was folded yet, which is the case for every conversation when
    # summarization is off.
    summary: str = ""
    summary_upto_message_id: str | None = None


class ChatTurn(BaseModel):
    content: str
    citations: list[ChatCitation] = Field(default_factory=list)
    source: Literal["langgraph", "fallback"] = "fallback"


class CandidateContact(BaseModel):
    profile_id: str
    name: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""


class TargetJob(BaseModel):
    target_job_id: str
    profile_id: str
    source_type: Literal["snapshot", "pasted", "external_url"]
    # Which site `url` belongs to; only meaningful for external_url.
    site: str = ""
    snapshot_id: str | None = None
    company: str
    title: str
    url: str = ""
    cleaned_text: str
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    blocks: list[Evidence] = Field(default_factory=list)
    created_at: str


class SupplementalEvidence(BaseModel):
    evidence_id: str
    profile_id: str
    tailoring_id: str
    question_id: str
    quote: str
    created_at: str


class TailoringQuestion(BaseModel):
    question_id: str
    question: str
    requirement: str
    status: Literal["PENDING", "ANSWERED", "SKIPPED"] = "PENDING"
    answer: str | None = None


class ResumeTailoring(BaseModel):
    tailoring_id: str
    conversation_id: str | None = None
    profile_id: str
    target_job_id: str
    status: Literal["COLLECTING", "READY", "GENERATING", "SUCCEEDED", "FAILED", "FAILED_VALIDATION"]
    questions: list[TailoringQuestion] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    draft_id: str | None = None
    task_id: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class InterviewDay(BaseModel):
    day: int = Field(ge=1, le=14)
    focus: str
    deliverable: str
    evidence_ids: list[str] = Field(default_factory=list)
    citations: list[ChatCitation] = Field(default_factory=list)


class InterviewQuestion(BaseModel):
    question_id: str
    question: str = Field(min_length=1, max_length=300)
    category: Literal["技术深挖", "项目经历", "能力缺口", "行为面", "反问"]
    why_asked: str = ""
    answer_hint: str = ""
    evidence_ids: list[str] = Field(min_length=1)
    # Filled by the backend from the cited blocks; the model never authors quotes.
    citations: list[ChatCitation] = Field(default_factory=list)


class InterviewPrep(BaseModel):
    prep_id: str
    profile_id: str
    snapshot_id: str
    company: str = ""
    title: str = ""
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "FAILED_VALIDATION"] = "QUEUED"
    # Per posting, unlike Comparison.action_plan which is one plan for the top 3.
    days: list[InterviewDay] = Field(default_factory=list)
    questions: list[InterviewQuestion] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    source: Literal["langgraph", "fallback"] = "fallback"
    # Why a deterministic plan was used instead of the model's, when that happened.
    note: str = ""
    error: str | None = None
    task_id: str | None = None
    created_at: str
    updated_at: str


class Application(BaseModel):
    """An assisted application: we prepare it, the user submits it.

    CareerRadar never clicks submit. It composes a greeting the user can paste
    and opens the posting; the send action stays with the person, which is both
    the platform's terms and the only way to keep an account safe.
    """

    application_id: str
    profile_id: str
    snapshot_id: str
    company: str = ""
    title: str = ""
    url: str = ""
    status: Literal[
        "DRAFT", "READY", "OPENED", "SUBMITTED", "ASSESSMENT",
        "INTERVIEW", "OFFER", "REJECTED", "REPLIED", "SKIPPED",
    ] = "DRAFT"
    greeting: str = ""
    greeting_evidence_ids: list[str] = Field(default_factory=list)
    # Filled by the backend from the cited blocks; the model never authors quotes.
    greeting_citations: list[ChatCitation] = Field(default_factory=list)
    # The tailored resume for this posting, when one has been exported.
    resume_export_id: str | None = None
    note: str = ""
    source: Literal["langgraph", "fallback"] = "fallback"
    error: str | None = None
    task_id: str | None = None
    created_at: str
    updated_at: str
    submitted_at: str | None = None
    application_deadline: str | None = None
    reminder_at: str | None = None


class ResumeBullet(BaseModel):
    bullet_id: str
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1)
    provenance: Literal["uploaded_resume", "user_confirmed", "user_edited"]
    priority: int = Field(default=50, ge=0, le=100)


class ResumeEntry(BaseModel):
    entry_id: str
    heading: str
    subheading: str = ""
    date_range: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ResumeSection(BaseModel):
    section_id: str
    title: str
    entries: list[ResumeEntry] = Field(default_factory=list)
    bullets: list[ResumeBullet] = Field(default_factory=list)


class ResumeQualityReport(BaseModel):
    relevance_score: int = Field(default=0, ge=0, le=100)
    specificity_score: int = Field(default=0, ge=0, le=100)
    structure_score: int = Field(default=0, ge=0, le=100)
    conciseness_score: int = Field(default=0, ge=0, le=100)
    evidence_coverage: int = Field(default=0, ge=0, le=100)
    duplicate_count: int = Field(default=0, ge=0)
    estimated_pages: float = Field(default=1.0, ge=0.1)
    issues: list[str] = Field(default_factory=list)
    uncovered_requirements: list[str] = Field(default_factory=list)
    passed: bool = False


class ResumeDraftVersion(BaseModel):
    version_id: str
    draft_id: str
    version: int
    tailoring_id: str
    profile_id: str
    target_job_id: str
    headline: str
    summary: list[ResumeBullet] = Field(default_factory=list)
    skills: list[ResumeBullet] = Field(default_factory=list)
    sections: list[ResumeSection] = Field(default_factory=list)
    contact: CandidateContact
    source: Literal["langgraph", "fallback", "user_edit"] = "fallback"
    change_log: list[dict[str, Any]] = Field(default_factory=list)
    quality_report: ResumeQualityReport = Field(default_factory=ResumeQualityReport)
    validation_status: Literal["VALID", "FAILED_VALIDATION"] = "VALID"
    created_at: str


class ResumeExport(BaseModel):
    export_id: str
    version_id: str
    template: Literal["technical", "business"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    docx_path: str = ""
    pdf_path: str = ""
    error: str | None = None
    created_at: str
    updated_at: str
