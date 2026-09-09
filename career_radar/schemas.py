from __future__ import annotations

from enum import StrEnum
from typing import Literal

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
    recommendations: list[RoleRecommendation] = Field(default_factory=list)
    selected_roles: list[RoleRecommendation] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    salary_preference: str | None = None
    work_type_preference: str | None = None
    confirmed: bool = False
    analysis_source: Literal["hermes", "fallback"] = "fallback"

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
    title: str
    company: str
    city: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    bonus_skills: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    work_type: str = ""
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
    source: Literal["hermes", "fallback"]
    created_at: str
