from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import CandidateProfile, Evidence, JobScore, JobSnapshot


SKILL_GROUPS = {
    "python后端": {"fastapi", "python web", "异步 api", "web api"},
    "关系型数据库": {"sql", "mysql", "postgresql"},
    "llm agent": {"agent", "ai agent", "llm agent"},
    "kubernetes": {"kubernetes", "k8s"},
}


def normalize_skill(skill: str) -> str:
    value = re.sub(r"[\s_./-]+", " ", skill.lower()).strip()
    for canonical, aliases in SKILL_GROUPS.items():
        if any(alias == value or alias in value for alias in aliases):
            return canonical
    return value


def _year_requirement(value: str) -> float | None:
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value)]
    return min(numbers) if numbers else None


def _education_rank(value: str | None) -> int | None:
    if not value:
        return None
    for rank, name in enumerate(("不限", "高中", "中专", "大专", "本科", "硕士", "博士")):
        if name in value:
            return rank
    return None


def _salary_range(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", value.upper().replace("K", ""))]
    if not numbers:
        return None
    return (numbers[0], numbers[1] if len(numbers) > 1 else numbers[0])


@dataclass
class Component:
    value: float | None
    risk: str | None = None


def score_job(profile: CandidateProfile, job: JobSnapshot) -> JobScore:
    candidate = {normalize_skill(item) for item in profile.skills}
    required = {normalize_skill(item) for item in job.required_skills if item.strip()}
    matched = sorted(required & candidate)
    missing = sorted(required - candidate)
    skill = None if not required else 100 * len(matched) / len(required)

    responsibility_text = " ".join(job.responsibilities).lower()
    project_blocks = [item for item in profile.evidence if item.section in {"项目", "经历"}]
    overlap = [skill_name for skill_name in candidate if skill_name in responsibility_text]
    project = None if not job.responsibilities or not project_blocks else min(100, 30 + 20 * len(overlap))

    required_years = _year_requirement(job.experience)
    experience = None
    experience_risk = None
    if required_years is not None and profile.experience_years is not None:
        experience = min(100, 100 * profile.experience_years / max(required_years, 0.5))
        if profile.experience_years < required_years:
            experience_risk = f"经验硬性要求可能不满足：岗位 {required_years:g} 年，简历 {profile.experience_years:g} 年"

    required_edu = _education_rank(job.education)
    candidate_edu = _education_rank(profile.education)
    education = None
    education_risk = None
    if required_edu is not None and candidate_edu is not None:
        education = 100 if candidate_edu >= required_edu else 0
        if candidate_edu < required_edu:
            education_risk = f"学历硬性要求可能不满足：岗位 {job.education}，简历 {profile.education}"

    preference_parts: list[float] = []
    preference_risk = None
    if profile.cities and job.city:
        city_match = any(city in job.city for city in profile.cities)
        preference_parts.append(100 if city_match else 0)
        if not city_match:
            preference_risk = f"城市不符合已确认偏好：{job.city}"
    desired_salary = _salary_range(profile.salary_preference)
    offered_salary = _salary_range(job.salary)
    if desired_salary and offered_salary:
        overlaps = max(desired_salary[0], offered_salary[0]) <= min(desired_salary[1], offered_salary[1])
        preference_parts.append(100 if overlaps else 0)
        if not overlaps and not preference_risk:
            preference_risk = f"薪资区间与偏好无重叠：岗位 {job.salary}，偏好 {profile.salary_preference}"
    if profile.work_type_preference and job.work_type:
        preference_parts.append(100 if profile.work_type_preference in job.work_type else 0)
    preference = sum(preference_parts) / len(preference_parts) if preference_parts else None

    components = ((skill, 40), (project, 25), (experience, 15), (education, 5), (preference, 15))
    known = [(value, weight) for value, weight in components if value is not None]
    total = sum(value * weight for value, weight in known) / sum(weight for _, weight in known) if known else 0
    risks = [item for item in (experience_risk, education_risk, preference_risk) if item]
    citations: list[Evidence] = []
    citations.extend(profile.evidence[:2])
    citations.extend(job.blocks[:2])
    return JobScore(
        job_id=job.snapshot_id, total=round(total, 1), skill=None if skill is None else round(skill, 1),
        project=None if project is None else round(project, 1),
        experience=None if experience is None else round(experience, 1),
        education=None if education is None else round(education, 1),
        preference=None if preference is None else round(preference, 1), risks=risks,
        matched_skills=matched, missing_skills=missing,
        explanation="已知维度按固定权重重新归一；缺失信息显示为无法判断，不按零分处理。",
        citations=citations,
    )
