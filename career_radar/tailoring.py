from __future__ import annotations

import hashlib
import html
import json
import re
import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from .agent import AgentService, _extract_json
from .database import Database, now_iso
from .resume import (
    EMAIL_RE, PHONE_RE, PROJECT_LINK_LABEL_RE, SKILLS, URL_RE, ResumeError,
    ResumeGenerationError, _resume_entries,
    extract_candidate_contact,
    sanitize_candidate_contact,
    skill_is_grounded,
)
from .resume_qa import inspect_export
from .schemas import (
    CandidateContact, CandidateProfile, Evidence, JobSnapshot, ResumeBullet,
    ResumeDraftVersion, ResumeEntry, ResumeExport, ResumeSection, ResumeSourceEntry, ResumeTailoring,
    ProjectUploadAnalysis, ResumeQualityCheck, SupplementalEvidence, TailoringQuestion, TargetJob,
)
from .sites.base import is_benefit_label


SYNTHETIC_ENTRY_HEADINGS = {"用户确认的补充经历", "用户补充项目经历"}


def _display_bullet_text(text: str, links: list[str] | None = None) -> str:
    """Keep repository URLs in the dedicated project-link row only."""
    value = text
    for link in links or []:
        value = re.sub(
            rf"(?:项目代码仓库|项目地址|项目链接|仓库地址|代码仓库|仓库链接)?\s*{re.escape(link)}",
            "", value,
        )
    value = re.sub(r"(?:项目代码仓库|项目地址|项目链接|仓库地址|代码仓库|仓库链接)\s*$", "", value)
    return value.strip(" ，,；;")


def _personal_facts(profile: CandidateProfile) -> list[str]:
    """Return exact non-contact personal facts from the uploaded resume."""
    labels = ("性别", "民族", "年龄", "政治面貌", "身高", "体重")
    facts: list[str] = []
    for item in profile.evidence:
        value = item.quote.strip()
        if value.startswith(labels) and value not in facts:
            facts.append(value)
    return facts


def supplement_entry_metadata(answer: str) -> tuple[str, str]:
    """Classify a confirmed answer without inventing a project identity.

    A repository/program description is a project experience, but the title
    must be a phrase copied from the user's answer. If no safe phrase can be
    extracted, keep a synthetic title and let the evidence remain the source
    of truth.
    """
    text = re.sub(r"\s+", " ", answer).strip()
    project_signal = re.search(
        r"(?:项目|仓库|代码|程序|系统|平台|爬虫|开发|实现|编写|搭建|构建)", text,
    )
    if not project_signal:
        return "other", "补充经历"
    heading_match = re.search(
        r"(?:编写|开发|实现|完成|搭建|构建)\s*([^，。；;\n]{2,40}"
        r"(?:项目|程序|系统|平台|爬虫))",
        text,
    )
    if not heading_match:
        heading_match = re.search(
            r"(?:写过|做过|负责)\s*([^，。；;\n]{2,40}(?:项目|程序|系统|平台|爬虫))",
            text,
        )
    if not heading_match:
        heading_match = re.search(r"([^，。；;\n]{2,30}(?:项目|程序|系统|平台|爬虫))", text)
    heading = heading_match.group(1).strip() if heading_match else "补充项目经历"
    return "project", heading


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _job_blocks(target_id: str, values: list[str]) -> list[Evidence]:
    blocks = []
    for index, value in enumerate(values, start=1):
        value = value.strip()
        if value:
            blocks.append(Evidence(
                source_type="job", source_id=target_id,
                block_id=f"target-{index:02d}-{hashlib.sha256(value.encode()).hexdigest()[:10]}",
                quote=value, section="目标岗位", provenance="job_snapshot",
            ))
    return blocks


def target_from_snapshot(profile_id: str, snapshot: JobSnapshot,
                         source_type: str = "snapshot", target_id: str | None = None) -> TargetJob:
    target_id = target_id or f"target_{uuid4().hex[:12]}"
    required_skills = [item for item in snapshot.required_skills if not is_benefit_label(item)]
    values = [snapshot.title, snapshot.company, *snapshot.responsibilities, *required_skills]
    return TargetJob(
        target_job_id=target_id, profile_id=profile_id, source_type=source_type,
        # A target derived from a stored snapshot inherits that snapshot's site, so
        # a 智联 posting is never later described as coming from BOSS.
        site=snapshot.site or "boss",
        snapshot_id=snapshot.snapshot_id, company=snapshot.company, title=snapshot.title,
        url=snapshot.canonical_url, cleaned_text=snapshot.cleaned_text,
        responsibilities=snapshot.responsibilities, required_skills=required_skills,
        blocks=_job_blocks(target_id, values), created_at=now_iso(),
    )


def target_from_pasted(profile_id: str, company: str, title: str, content: str,
                       url: str = "") -> TargetJob:
    cleaned = re.sub(r"\s+", " ", content).strip()
    if len(cleaned) < 30:
        raise ResumeError("岗位描述过短，请粘贴职责、技能和任职要求")
    target_id = f"target_{uuid4().hex[:12]}"
    lowered = cleaned.lower()
    required = sorted({display for token, display in SKILLS.items() if token in lowered})
    lines = [line.strip() for line in re.split(r"[\n；;。]", content) if line.strip()]
    responsibilities = lines[:30]
    return TargetJob(
        target_job_id=target_id, profile_id=profile_id, source_type="pasted",
        company=company.strip(), title=title.strip(), url=url.strip(), cleaned_text=cleaned[:20_000],
        responsibilities=responsibilities, required_skills=required,
        blocks=_job_blocks(target_id, [title, company, *lines]), created_at=now_iso(),
    )


def _evidence_score(evidence: Evidence, target: TargetJob) -> int:
    text = evidence.quote.lower()
    terms = [*target.required_skills, *re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", target.cleaned_text)]
    return sum(12 for term in terms if term.lower() in text) + min(len(text), 80) // 20


def _is_resume_noise(evidence: Evidence) -> bool:
    text = evidence.quote.strip(" ：:").lower()
    exact = {
        "基本信息", "个人信息", "个人简历", "求职简历", "简历", "联系方式",
        "教育", "教育经历", "技能", "专业技能", "技术栈", "项目", "项目经历",
        "经历", "工作经历", "实习经历",
    }
    noisy_phrases = ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")
    return text in exact or any(phrase in text for phrase in noisy_phrases)


def _questions(profile: CandidateProfile, target: TargetJob,
               supplements: list[SupplementalEvidence]) -> tuple[list[TailoringQuestion], list[str]]:
    known = " ".join([*(item.quote for item in profile.evidence), *(item.quote for item in supplements)]).lower()
    required_skills = [skill for skill in target.required_skills if not is_benefit_label(skill)]
    missing = [skill for skill in required_skills if skill.lower() not in known]
    questions = [TailoringQuestion(
        question_id=_stable_id("question", f"{target.target_job_id}:{skill}"),
        question=f"岗位要求 {skill}。你是否在真实项目中使用过？如果有，请说明使用场景、你的职责和结果；没有可以跳过。",
        requirement=skill,
    ) for skill in missing[:5]]
    if len(questions) < 5:
        signals = ("年以上", "至少", "经验", "具备", "熟悉", "能够", "负责", "设计", "建立", "推动", "分析", "评测")
        boilerplate = ("在这里", "真诚地邀请", "技术氛围", "职业生涯", "平台是", "一起驱动", "共同探索", "站在")
        concepts = (
            "产品管理", "项目管理", "用户研究", "数据分析", "问题归因", "评测体系",
            "评测数据集", "评分标准", "回归测试", "自动化评测", "人工评测", "A/B 实验",
            "大模型", "工具调用", "跨团队协作", "产品路线图", "模型选型",
        )
        candidates = []
        for index, raw in enumerate(target.responsibilities):
            requirement = re.sub(r"^\s*\d+[、.．)）]\s*", "", raw).strip()
            if not 12 <= len(requirement) <= 220 or any(value in requirement for value in boilerplate):
                continue
            score = sum(3 if value in {"年以上", "至少", "经验", "具备", "熟悉", "能够"} else 1
                        for value in signals if value in requirement)
            if score == 0:
                continue
            lowered = requirement.lower()
            terms = {display.lower() for token, display in SKILLS.items() if token in lowered}
            terms.update(token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#./-]{1,24}", requirement))
            terms.update(value.lower() for value in concepts if value.lower() in lowered)
            covered = sum(1 for term in terms if term in known)
            if terms and covered >= max(2, round(len(terms) * 0.6)):
                continue
            candidates.append((-score, index, requirement))
        for _score, _index, requirement in sorted(candidates):
            if len(questions) >= 5:
                break
            if any(skill.lower() in requirement.lower() for skill in missing):
                continue
            questions.append(TailoringQuestion(
                question_id=_stable_id("question", f"{target.target_job_id}:{requirement}"),
                question=(
                    f"岗位强调“{requirement[:90]}”。你的真实经历中是否有能证明这一点的案例？"
                    "如有请说明使用场景、个人贡献、交付结果和可证实数据；没有可以跳过。"
                ),
                requirement=requirement,
            ))
    return questions, missing


def _as_bullet(evidence: Evidence, target: TargetJob, index: int,
               provenance: str | None = None) -> ResumeBullet:
    return ResumeBullet(
        bullet_id=f"bullet_{uuid4().hex[:10]}", text=evidence.quote,
        evidence_ids=[evidence.block_id],
        provenance=provenance or evidence.provenance or "uploaded_resume",
        priority=min(100, 40 + _evidence_score(evidence, target) + max(0, 12 - index)),
    )


def _fallback_draft(profile: CandidateProfile, contact: CandidateContact, target: TargetJob,
                    tailoring: ResumeTailoring, draft_id: str) -> ResumeDraftVersion:
    usable = [item for item in profile.evidence if not _is_resume_noise(item)]
    ordered = sorted(usable, key=lambda item: _evidence_score(item, target), reverse=True)
    by_section: dict[str, list[Evidence]] = {}
    for item in ordered:
        by_section.setdefault(item.section or "其他", []).append(item)

    summary_pool = [item for item in ordered if item.section in {"经历", "项目", "用户补充", "概览"}]
    summary_sources = summary_pool[:2] or ordered[:2]
    summary = [_as_bullet(item, target, index) for index, item in enumerate(summary_sources)]
    skills = []
    for index, skill in enumerate(profile.skills):
        source = next((item for item in profile.evidence if skill.lower() in item.quote.lower()), None)
        if source:
            skills.append(ResumeBullet(
                bullet_id=f"skill_{uuid4().hex[:10]}", text=skill,
                evidence_ids=[source.block_id], provenance=source.provenance or "uploaded_resume",
                priority=90 if skill in target.required_skills else 55 - min(index, 20),
            ))
    skills = sorted(skills, key=lambda item: item.priority, reverse=True)[:8]
    sections = []
    labels = (
        ("经历", "工作经历", 5), ("项目", "项目经历", 4),
        ("用户补充", "补充经历", 3), ("教育", "教育经历", 2),
        ("技能", "补充技能", 2), ("概览", "其他信息", 1),
    )
    used = {item.block_id for item in summary_sources}
    for key, title, limit in labels:
        items = [item for item in by_section.get(key, []) if item.block_id not in used]
        if not items:
            continue
        bullets = [_as_bullet(item, target, index) for index, item in enumerate(items[:limit])]
        sections.append(ResumeSection(section_id=f"section_{key}", title=title, bullets=bullets))
    return ResumeDraftVersion(
        version_id=f"version_{uuid4().hex[:12]}", draft_id=draft_id, version=1,
        tailoring_id=tailoring.tailoring_id, profile_id=profile.profile_id,
        target_job_id=target.target_job_id, headline=target.title,
        summary=summary, skills=skills,
        sections=sections, contact=contact, source="fallback", created_at=now_iso(),
        change_log=[{"kind": "generated", "target": f"{target.company} · {target.title}"}],
    )


def _all_bullets(version: ResumeDraftVersion) -> list[ResumeBullet]:
    values = [*version.summary, *version.skills]
    for section in version.sections:
        values.extend(section.bullets)
        for entry in section.entries:
            values.extend(entry.bullets)
    return values


def _public_profile(profile: CandidateProfile, contact: CandidateContact) -> CandidateProfile:
    value = profile.model_copy(deep=True)
    private = {item for item in (contact.name, contact.phone, contact.email, contact.location) if item}
    value.evidence = [item for item in value.evidence if not _is_resume_noise(item) and not (
        PHONE_RE.search(item.quote) or EMAIL_RE.search(item.quote) or
        any(secret in item.quote for secret in private)
    )]
    safe = {item.block_id: item.quote for item in value.evidence}
    # Rebuild source entries from the ordered evidence. Older OCR runs stored
    # a project title, date and repository link as three separate entries; the
    # parser now groups them, and this migration repairs those profiles when a
    # new tailored resume is generated.
    original_entries = [entry for entry in _resume_entries(value.evidence) if not all(
        next((e.section for e in value.evidence if e.block_id == evidence_id), "") == "用户补充"
        for evidence_id in entry.evidence_ids
    )]
    supplemental_entries = []
    for entry in value.resume_entries:
        entry.evidence_ids = [item for item in entry.evidence_ids if item in safe]
        entry.original_bullets = [safe[item] for item in entry.evidence_ids]
        if entry.evidence_ids and any(
            next((e.section for e in value.evidence if e.block_id == evidence_id), "") == "用户补充"
            for evidence_id in entry.evidence_ids
        ):
            supplemental_entries.append(entry)
    entries = []
    supplemental_by_key: dict[str, ResumeSourceEntry] = {}
    for entry in [*original_entries, *supplemental_entries]:
        entry.evidence_ids = [item for item in entry.evidence_ids if item in safe]
        entry.original_bullets = [safe[item] for item in entry.evidence_ids]
        if entry.evidence_ids:
            if entry.kind == "other" and entry.heading in SYNTHETIC_ENTRY_HEADINGS:
                entry.kind, entry.heading = supplement_entry_metadata(
                    " ".join(entry.original_bullets),
                )
            if any(
                next((e.section for e in value.evidence if e.block_id == evidence_id), "") == "用户补充"
                for evidence_id in entry.evidence_ids
            ):
                urls = re.findall(r"https?://[^\s，。；;）)]+", " ".join(entry.original_bullets), re.I)
                key = (urls[0].lower() if urls else entry.heading.lower())
                previous = supplemental_by_key.get(key)
                if previous:
                    previous.evidence_ids.extend(
                        item for item in entry.evidence_ids if item not in previous.evidence_ids
                    )
                    previous.original_bullets.extend(
                        item for item in entry.original_bullets if item not in previous.original_bullets
                    )
                    continue
                supplemental_by_key[key] = entry
            is_project_link = (
                entry.kind == "project"
                and (
                    re.search(r"https?://|www\.", entry.heading, re.I)
                    or re.search(r"(?:项目地址|项目链接|仓库地址|代码仓库|源码地址|仓库链接)\s*[:：]",
                                 entry.heading, re.I)
                )
            )
            if is_project_link and entries and entries[-1].kind == "project":
                previous = entries[-1]
                previous.evidence_ids.extend(
                    item for item in entry.evidence_ids if item not in previous.evidence_ids
                )
                previous.original_bullets.extend(
                    item for item in entry.original_bullets if item not in previous.original_bullets
                )
                continue
            entries.append(entry)
    value.resume_entries = entries
    return value


def _repair_draft_source_metadata(version: ResumeDraftVersion,
                                  profile: CandidateProfile) -> bool:
    """Repair metadata from older drafts without rewriting their bullet text.

    Before structured source entries were introduced, OCR/model output could
    use the date as an entry heading and omit the project name or repository.
    The bullets are still kept as-is and remain evidence-validated; only the
    citable heading, date, subtitle and links are restored from the uploaded
    resume.  This makes re-exporting an existing draft safe and consistent
    with newly generated drafts.
    """
    # Reuse the same migration used for a fresh generation. This repairs old
    # OCR rows and collapses duplicate answers that point to the same repo.
    public = _public_profile(profile, CandidateContact(profile_id=profile.profile_id))
    sources = {item.block_id: item for item in public.evidence}
    source_entries = public.resume_entries
    by_kind: dict[str, list[ResumeSourceEntry]] = {}
    for entry in source_entries:
        by_kind.setdefault(entry.kind, []).append(entry)
    used: set[str] = set()
    changed = False

    def links_for(entry: ResumeSourceEntry) -> list[str]:
        found: list[str] = []
        for evidence_id in entry.evidence_ids:
            quote = sources.get(evidence_id)
            if not quote:
                continue
            for link in re.findall(r"https?://[^\s，。；;）)]+", quote.quote, re.I):
                value = link.rstrip(".,，。；;")
                if value not in found:
                    found.append(value)
        return found

    def subtitle_for(entry: ResumeSourceEntry) -> str:
        excluded = {entry.heading.strip(), entry.date_range.strip()}
        for evidence_id in entry.evidence_ids:
            quote = sources.get(evidence_id)
            if not quote:
                continue
            value = quote.quote.strip()
            if value in excluded or URL_RE.search(value) or PROJECT_LINK_LABEL_RE.search(value):
                continue
            if entry.kind == "education" and ("本科" in value or "硕士" in value or "专业" in value):
                return value
        return ""

    for section in version.sections:
        kind = {"项目经历": "project", "工作经历": "experience", "教育经历": "education",
                "补充经历": "project", "其他信息": "other"}.get(section.title)
        if not kind:
            continue
        candidates = by_kind.get(kind, [])
        if section.title in {"补充经历", "其他信息"}:
            # Historical drafts put confirmed project answers in an "other"
            # section. Match cited IDs against project entries first, then use
            # the generic entries only when no project evidence is present.
            candidates = [*by_kind.get("project", []), *candidates]
        for draft_entry in section.entries:
            overlap = set(draft_entry.evidence_ids)
            match = next((entry for entry in candidates
                          if entry.entry_id not in used and overlap.intersection(entry.evidence_ids)), None)
            if match is None:
                # A very old row may have lost its citations. Keep the original
                # order as a conservative fallback, but never merge entries.
                match = next((entry for entry in candidates if entry.entry_id not in used), None)
            if match is None:
                continue
            used.add(match.entry_id)
            new_heading = match.heading
            new_date = match.date_range
            new_subheading = draft_entry.subheading or subtitle_for(match)
            new_links = links_for(match)
            if (draft_entry.heading, draft_entry.date_range, draft_entry.subheading,
                    draft_entry.links) != (new_heading, new_date, new_subheading, new_links):
                draft_entry.heading = new_heading
                draft_entry.date_range = new_date
                draft_entry.subheading = new_subheading
                draft_entry.links = new_links
                draft_entry.evidence_ids = list(dict.fromkeys(match.evidence_ids))
                changed = True
            if section.title in {"补充经历", "其他信息"} and match.kind == "project":
                section.title = "项目经历"
                changed = True
    # Merge the migrated project answer into the existing project section so it
    # is displayed as a real project, never as an internal "other" block.
    merged: list[ResumeSection] = []
    for section in version.sections:
        previous = next((item for item in merged if item.title == section.title), None)
        if previous is None:
            merged.append(section)
            continue
        previous.entries.extend(section.entries)
        previous.bullets.extend(section.bullets)
        changed = True
    version.sections = merged
    return changed


def validate_draft(version: ResumeDraftVersion, profile: CandidateProfile, *,
                   check_skills: bool = True) -> None:
    sources = {item.block_id: item for item in profile.evidence}

    def validate_text(text: str, evidence_ids: list[str], label: str) -> None:
        if not evidence_ids or any(evidence_id not in sources for evidence_id in evidence_ids):
            raise ResumeError(f"简历内容引用了无效证据：{label}")
        quotes = " ".join(sources[item].quote for item in evidence_ids)
        for number in re.findall(r"\d+(?:\.\d+)?%?", text):
            if number not in quotes:
                raise ResumeError(f"简历内容出现未经证实的数字：{number}")
        for entity in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,30}(?:有限责任公司|有限公司|大学|学院|研究院)", text):
            if entity not in quotes:
                raise ResumeError(f"简历内容出现未经证实的机构：{entity}")
        if check_skills:
            for token, display in SKILLS.items():
                if token in text.lower() and not skill_is_grounded(token, quotes):
                    raise ResumeError(f"简历内容出现未经证实的技能：{display}")

    for bullet in _all_bullets(version):
        validate_text(bullet.text, bullet.evidence_ids, bullet.bullet_id)
        if any(phrase in bullet.text.lower() for phrase in ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")):
            raise ResumeError("简历内容包含模板提示语")
    for section in version.sections:
        for entry in section.entries:
            validate_text(" ".join(filter(None, [entry.heading, entry.subheading, entry.date_range])),
                          entry.evidence_ids, entry.entry_id)
            entry_quotes = " ".join(sources[item].quote for item in entry.evidence_ids)
            for metadata in (entry.heading, entry.subheading, entry.date_range):
                if metadata and metadata not in SYNTHETIC_ENTRY_HEADINGS and metadata not in {"补充经历", "补充项目经历"} and metadata not in entry_quotes:
                    raise ResumeError(f"简历条目出现未经证实的标题或日期：{metadata}")
            allowed = set(entry.evidence_ids)
            if any(not set(bullet.evidence_ids).issubset(allowed) for bullet in entry.bullets):
                raise ResumeError(f"简历条目混入了其他经历的事实：{entry.heading}")


def assess_draft_quality(version: ResumeDraftVersion, target: TargetJob) -> None:
    bullets = _all_bullets(version)
    normalized = [re.sub(r"\W+", "", item.text).lower() for item in bullets]
    duplicates = len(normalized) - len(set(normalized))
    text = " ".join(item.text for item in bullets)
    characters = len(text) + sum(len(entry.heading + entry.subheading + entry.date_range)
                                 for section in version.sections for entry in section.entries)
    report = version.quality_report
    model_passed = report.passed
    report.evidence_coverage = 100 if bullets and all(item.evidence_ids for item in bullets) else 0
    report.duplicate_count = duplicates
    report.estimated_pages = round(max(0.5, characters / 1600), 1)
    report.uncovered_requirements = [skill for skill in target.required_skills if skill.lower() not in text.lower()]
    checks: list[ResumeQualityCheck] = []
    checks.append(ResumeQualityCheck(
        id="fact_accuracy", status="PASS",
        message="数字、机构、日期和经历归属已通过后端证据校验",
    ))
    checks.append(ResumeQualityCheck(
        id="contact_consistency", status="PASS",
        message="联系方式由本地联系人字段合并，不作为模型事实生成",
    ))
    template_phrases = ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")
    has_template_text = any(phrase in text for phrase in template_phrases)
    checks.append(ResumeQualityCheck(
        id="no_template_text", status="FAIL" if has_template_text else "PASS",
        message="简历包含模板提示语" if has_template_text else "未发现模板提示语或模型内部文字",
        suggestion="删除模板提示语后重新生成" if has_template_text else "",
    ))
    checks.append(ResumeQualityCheck(
        id="evidence_coverage", status="PASS" if report.evidence_coverage == 100 else "FAIL",
        message="所有简历内容均有证据引用" if report.evidence_coverage == 100 else "存在没有证据引用的简历内容",
        suggestion="补充证据后重新生成" if report.evidence_coverage != 100 else "",
    ))
    checks.append(ResumeQualityCheck(
        id="duplicate_content", status="FAIL" if duplicates else "PASS",
        message=f"存在 {duplicates} 条重复内容" if duplicates else "没有发现重复要点",
        suggestion="合并重复要点并保留更具体的一条" if duplicates else "",
    ))
    too_many = [entry.heading for section in version.sections for entry in section.entries if len(entry.bullets) > 4]
    checks.append(ResumeQualityCheck(
        id="entry_bullet_limit", status="FAIL" if too_many else "PASS",
        message="以下经历超过 4 个要点：" + "、".join(too_many[:5]) if too_many else "每条经历不超过 4 个要点",
        suggestion="精简每条经历的要点" if too_many else "",
    ))
    placeholders = [entry.heading for section in version.sections for entry in section.entries
                    if entry.heading in SYNTHETIC_ENTRY_HEADINGS]
    checks.append(ResumeQualityCheck(
        id="no_internal_placeholders", status="FAIL" if placeholders else "PASS",
        message="存在内部占位标题：" + "、".join(placeholders[:5]) if placeholders else "未发现内部占位标题",
        suggestion="把补充事实归入真实项目或经历标题后重新生成" if placeholders else "",
    ))
    if report.estimated_pages > 2:
        page_status, page_message = "FAIL", "内容预计超过两页上限"
    elif report.estimated_pages > 1.2:
        page_status, page_message = "WARN", "内容超过一页优先篇幅，但仍在两页上限内"
    else:
        page_status, page_message = "PASS", "内容符合一页优先策略"
    checks.append(ResumeQualityCheck(
        id="estimated_pages", status=page_status, message=page_message,
        suggestion="精简低相关内容后重新生成" if page_status != "PASS" else "",
    ))
    checks.append(ResumeQualityCheck(
        id="section_structure", status="PASS" if version.sections and bullets else "FAIL",
        message="经历章节结构完整" if version.sections and bullets else "简历缺少可用的经历内容",
        suggestion="保留至少一段有证据的经历或项目" if not version.sections or not bullets else "",
    ))
    if report.uncovered_requirements:
        checks.append(ResumeQualityCheck(
            id="uncovered_requirements", status="WARN",
            message="未找到候选人证据覆盖：" + "、".join(report.uncovered_requirements[:8]),
            suggestion="保持为真实缺口，不要为了匹配岗位虚构技能。",
        ))
    else:
        checks.append(ResumeQualityCheck(
            id="uncovered_requirements", status="PASS", message="岗位要求均有候选人证据或已覆盖",
        ))
    issues = list(dict.fromkeys(report.issues))
    if duplicates:
        issues.append(f"存在 {duplicates} 条重复内容")
    if report.estimated_pages > 1.2:
        issues.append("内容超过默认一页篇幅")
    if not version.sections or not bullets:
        issues.append("简历缺少可用的经历内容")
    report.issues = list(dict.fromkeys(issues))
    report.checks = checks
    report.status = "FAIL" if any(item.status == "FAIL" for item in checks) else (
        "WARN" if any(item.status == "WARN" for item in checks) else "PASS"
    )
    report.passed = bool(
        model_passed and report.status != "FAIL"
        and min(report.relevance_score, report.specificity_score,
                report.structure_score, report.conciseness_score) >= 70
    )
    if min(report.relevance_score, report.specificity_score,
           report.structure_score, report.conciseness_score) < 70:
        report.status = "FAIL"
        report.checks.append(ResumeQualityCheck(
            id="model_quality_scores", status="FAIL", message="模型质量评分未达到 70 分",
            suggestion="补充事实或重新生成定向简历。",
        ))
    else:
        report.checks.append(ResumeQualityCheck(
            id="model_quality_scores", status="PASS", message="相关性、具体性、结构和简洁度均达到 70 分",
        ))
        if not model_passed:
            report.status = "FAIL"
            report.checks.append(ResumeQualityCheck(
                id="model_quality_review", status="FAIL", message="模型质量评审未通过",
                suggestion="根据质量评审意见修改后重新生成。",
            ))
    if not report.passed:
        raise ResumeError("定向简历未通过质量检查：" + "；".join(report.issues[:5] or ["模型评分未达到 70 分"]))


class TailoringService:
    def __init__(self, database: Database, agent: AgentService, data_dir: Path):
        self.database = database
        self.agent = agent
        self.export_dir = data_dir / "exports"

    def create_tailoring(self, profile_id: str, target_job_id: str,
                         conversation_id: str | None = None) -> ResumeTailoring:
        profile = self.database.get_profile(profile_id)
        target = self.database.get_target_job(target_job_id)
        if not profile or not target or target.profile_id != profile_id:
            raise ResumeError("目标岗位与候选人画像不匹配")
        questions, missing = _questions(profile, target, self.database.list_supplemental_evidence(profile_id))
        stamp = now_iso()
        tailoring = ResumeTailoring(
            tailoring_id=f"tailor_{uuid4().hex[:12]}", conversation_id=conversation_id,
            profile_id=profile_id, target_job_id=target_job_id,
            status="COLLECTING" if questions else "READY", questions=questions,
            missing_requirements=missing, created_at=stamp, updated_at=stamp,
        )
        return self.database.save_tailoring(tailoring)

    def restore_missing_questions(self, tailoring: ResumeTailoring) -> ResumeTailoring:
        """Backfill questions for tasks created before JD requirement fallback existed."""
        has_benefit_questions = any(is_benefit_label(item.requirement) for item in tailoring.questions)
        if (tailoring.questions and not has_benefit_questions) or tailoring.status == "SUCCEEDED":
            return tailoring
        profile = self.database.get_profile(tailoring.profile_id)
        target = self.database.get_target_job(tailoring.target_job_id)
        if not profile or not target:
            return tailoring
        questions, missing = _questions(
            profile, target, self.database.list_supplemental_evidence(profile.profile_id),
        )
        if not questions and not has_benefit_questions:
            return tailoring
        previous = {item.question_id: item for item in tailoring.questions}
        for question in questions:
            if question.question_id in previous:
                question.status = previous[question.question_id].status
                question.answer = previous[question.question_id].answer
        tailoring.questions = questions
        tailoring.missing_requirements = missing
        tailoring.status = "READY" if all(item.status != "PENDING" for item in questions) else "COLLECTING"
        tailoring.task_id = None
        tailoring.error = None
        return self.database.save_tailoring(tailoring)

    def save_answers(self, tailoring_id: str, answers: dict[str, str | None]) -> ResumeTailoring:
        tailoring = self.database.get_tailoring(tailoring_id)
        if not tailoring:
            raise ResumeError("定向简历任务不存在")
        if tailoring.status not in {"COLLECTING", "READY", "FAILED", "FAILED_VALIDATION"}:
            raise ResumeError("当前状态不能修改追问答案")
        if tailoring.status in {"FAILED", "FAILED_VALIDATION"}:
            tailoring.task_id = None
            tailoring.error = None
        valid_ids = {question.question_id for question in tailoring.questions}
        if set(answers) - valid_ids:
            raise ResumeError("包含不属于当前任务的追问")
        for question in tailoring.questions:
            if question.question_id not in answers:
                continue
            answer = (answers[question.question_id] or "").strip()
            if len(answer) > 1000:
                raise ResumeError("单条补充说明不能超过 1000 字")
            question.answer = answer or None
            question.status = "ANSWERED" if answer else "SKIPPED"
        tailoring.status = "READY" if all(item.status != "PENDING" for item in tailoring.questions) else "COLLECTING"
        return self.database.save_tailoring(tailoring)

    def confirm_facts(self, tailoring: ResumeTailoring) -> CandidateProfile:
        profile = self.database.get_profile(tailoring.profile_id)
        if not profile:
            raise ResumeError("候选人画像不存在")
        stamp = now_iso()
        existing = {item.block_id for item in profile.evidence}
        for question in tailoring.questions:
            if question.status != "ANSWERED" or not question.answer:
                continue
            evidence_id = _stable_id("resume-supplement", f"{profile.profile_id}:{question.answer}")
            stored = SupplementalEvidence(
                evidence_id=evidence_id, profile_id=profile.profile_id,
                tailoring_id=tailoring.tailoring_id, question_id=question.question_id,
                quote=question.answer, created_at=stamp,
            )
            self.database.save_supplemental_evidence(stored)
            if evidence_id not in existing:
                profile.evidence.append(Evidence(
                    source_type="resume", source_id=profile.profile_id, block_id=evidence_id,
                    quote=question.answer, section="用户补充", provenance="user_confirmed",
                ))
                entry_kind, entry_heading = supplement_entry_metadata(question.answer)
                profile.resume_entries.append(ResumeSourceEntry(
                    entry_id=f"source-entry-{evidence_id}", kind=entry_kind,
                    heading=entry_heading, evidence_ids=[evidence_id],
                    original_bullets=[question.answer],
                ))
                existing.add(evidence_id)
        self.database.save_profile(profile)
        return profile

    def confirm_project_upload(self, upload_id: str) -> ProjectUploadAnalysis:
        analysis = self.database.get_project_upload(upload_id)
        if not analysis:
            raise ResumeError("项目分析不存在或已过期")
        tailoring = self.database.get_tailoring(analysis.tailoring_id)
        profile = self.database.get_profile(analysis.profile_id)
        if not tailoring or not profile or tailoring.profile_id != analysis.profile_id:
            raise ResumeError("项目分析与当前候选人画像不匹配")
        if analysis.status == "CONFIRMED":
            return analysis
        evidence_id = _stable_id("project-upload", f"{analysis.upload_id}:{analysis.evidence_quote}")
        if not any(item.block_id == evidence_id for item in profile.evidence):
            profile.evidence.append(Evidence(
                source_type="resume", source_id=profile.profile_id, block_id=evidence_id,
                quote=analysis.evidence_quote, section="项目", provenance="user_confirmed",
            ))
            profile.resume_entries.append(ResumeSourceEntry(
                entry_id=f"source-entry-{evidence_id}", kind="project",
                heading=analysis.project_name, evidence_ids=[evidence_id],
                original_bullets=[analysis.evidence_quote],
            ))
            for technology in analysis.technologies:
                if technology not in profile.skills:
                    profile.skills.append(technology)
            profile.confirmed = True
            self.database.save_profile(profile)
        analysis.status = "CONFIRMED"
        analysis.confirmed_at = now_iso()
        self.database.save_project_upload(analysis)
        # A confirmed upload invalidates the previous draft task but preserves
        # its draft_id, so the next confirmation creates a new version.
        tailoring.task_id = None
        tailoring.error = None
        tailoring.status = "READY" if all(
            item.status != "PENDING" for item in tailoring.questions
        ) else "COLLECTING"
        self.database.save_tailoring(tailoring)
        return analysis

    async def generate(self, tailoring_id: str) -> ResumeDraftVersion:
        tailoring = self.database.get_tailoring(tailoring_id)
        if not tailoring or tailoring.status not in {"READY", "GENERATING"}:
            raise ResumeError("追问尚未完成或任务状态无效")
        profile = self.confirm_facts(tailoring)
        target = self.database.get_target_job(tailoring.target_job_id)
        if not target:
            raise ResumeError("目标岗位不存在")
        contact = sanitize_candidate_contact(self.database.get_contact(profile.profile_id))
        if not any((contact.name, contact.phone, contact.email, contact.location)):
            candidate_copy = profile.model_copy(deep=True)
            contact = sanitize_candidate_contact(extract_candidate_contact(candidate_copy))
        self.database.save_contact(contact)
        public_profile = _public_profile(profile, contact)
        draft_id = tailoring.draft_id or f"draft_{uuid4().hex[:12]}"
        version = _fallback_draft(public_profile, contact, target, tailoring, draft_id)
        latest = self.database.get_latest_draft(draft_id)
        if latest:
            version.version = latest.version + 1
        if not self.agent.settings.api_key:
            tailoring.status = "FAILED"
            tailoring.error = "尚未配置可用的大模型，已停止生成，未展示低质量整理版"
            self.database.save_tailoring(tailoring)
            raise ResumeGenerationError(tailoring.error)
        try:
            version = await self.agent.tailor_resume(
                public_profile, target, tailoring, contact, version,
                # Skill wording is checked semantically by the typed quality
                # reviewer against the cited evidence below. Hard facts and
                # evidence ownership remain deterministic here.
                validator=lambda draft: validate_draft(draft, profile, check_skills=False),
            )
        except Exception as exc:
            tailoring.status = "FAILED"
            reason = str(exc).strip() or ("请求超时" if isinstance(exc, TimeoutError) else type(exc).__name__)
            tailoring.error = f"大模型生成或质量返工失败：{reason}"
            self.database.save_tailoring(tailoring)
            raise ResumeGenerationError(tailoring.error) from exc
        try:
            validate_draft(version, profile, check_skills=False)
            assess_draft_quality(version, target)
        except ResumeError as exc:
            version.validation_status = "FAILED_VALIDATION"
            self.database.save_draft_version(version)
            tailoring.status = "FAILED_VALIDATION"
            tailoring.error = str(exc)
            self.database.save_tailoring(tailoring)
            raise
        self.database.save_draft_version(version)
        tailoring.status = "SUCCEEDED"
        tailoring.error = None
        tailoring.draft_id = draft_id
        self.database.save_tailoring(tailoring)
        return version

    def save_user_version(self, draft_id: str, value: ResumeDraftVersion) -> ResumeDraftVersion:
        previous = self.database.get_latest_draft(draft_id)
        if not previous or value.profile_id != previous.profile_id or value.target_job_id != previous.target_job_id:
            raise ResumeError("草稿版本上下文不匹配")
        profile = self.database.get_profile(previous.profile_id)
        if not profile:
            raise ResumeError("候选人画像不存在")
        previous_text = {item.bullet_id: item.text for item in _all_bullets(previous)}
        change_log = []
        if value.headline != previous.headline:
            change_log.append({"kind": "headline", "before": previous.headline, "after": value.headline})
        value.contact.profile_id = previous.profile_id
        for field in ("name", "phone", "email", "location"):
            before = getattr(previous.contact, field)
            after = getattr(value.contact, field)
            if before != after:
                change_log.append({
                    "kind": "contact", "field": field,
                    "before": "已填写" if before else "未填写",
                    "after": "已填写" if after else "未填写",
                })
        for bullet in _all_bullets(value):
            old = previous_text.get(bullet.bullet_id)
            if old == bullet.text:
                continue
            evidence_id = _stable_id("resume-edit", f"{profile.profile_id}:{bullet.text}")
            if not any(item.block_id == evidence_id for item in profile.evidence):
                profile.evidence.append(Evidence(
                    source_type="resume", source_id=profile.profile_id, block_id=evidence_id,
                    quote=bullet.text, section="用户编辑", provenance="user_edited",
                ))
            bullet.evidence_ids = [evidence_id]
            bullet.provenance = "user_edited"
            change_log.append({"bullet_id": bullet.bullet_id, "before": old or "", "after": bullet.text})
        value.version_id = f"version_{uuid4().hex[:12]}"
        value.version = previous.version + 1
        value.draft_id = draft_id
        value.tailoring_id = previous.tailoring_id
        value.source = "user_edit"
        value.change_log = change_log
        value.created_at = now_iso()
        value.contact = sanitize_candidate_contact(value.contact)
        self.database.save_contact(value.contact)
        self.database.save_profile(profile)
        validate_draft(value, profile)
        target = self.database.get_target_job(value.target_job_id)
        if not target:
            raise ResumeError("目标岗位不存在")
        assess_draft_quality(value, target)
        return self.database.save_draft_version(value)

    def render_docx(self, version: ResumeDraftVersion, target: TargetJob,
                    template: str, output: Path) -> None:
        if template in {"technical", "business"}:
            self._render_original_docx(version, target, template, output)
            return

        # Kept below for reference while older exported versions are read; all
        # new DOCX files use the source resume's visual shell above.
        document = Document()
        section = document.sections[0]
        section.top_margin = Inches(0.55)
        section.bottom_margin = Inches(0.55)
        section.left_margin = Inches(0.65)
        section.right_margin = Inches(0.65)
        styles = document.styles
        styles["Normal"].font.name = "Noto Sans CJK SC"
        styles["Normal"].font.size = Pt(9.5 if template == "business" else 10)
        styles["Normal"].paragraph_format.space_after = Pt(0)
        title = document.add_paragraph(style="Title")
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.paragraph_format.space_after = Pt(1)
        run = title.add_run(version.contact.name or "个人简历")
        run.font.name = "Noto Sans CJK SC"
        run.font.size = Pt(20)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0, 0, 0)
        contacts = "  ·  ".join(filter(None, [version.contact.phone, version.contact.email, version.contact.location]))
        if contacts:
            paragraph = document.add_paragraph(contacts)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(1)
        subtitle = document.add_paragraph(f"目标岗位：{target.company} · {version.headline}")
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle.paragraph_format.space_after = Pt(1)

        def add_heading(text: str) -> None:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(4)
            paragraph.paragraph_format.space_after = Pt(1)
            run = paragraph.add_run(text)
            run.bold = True
            run.font.size = Pt(11.5)
            run.font.color.rgb = RGBColor(0, 0, 0)

        def add_bullets(values: list[ResumeBullet]) -> None:
            for item in sorted(values, key=lambda bullet: bullet.priority, reverse=True):
                paragraph = document.add_paragraph(style="List Bullet")
                paragraph.paragraph_format.space_after = Pt(0.5)
                paragraph.paragraph_format.line_spacing = 1.0
                paragraph.add_run(item.text)

        def add_skills(values: list[ResumeBullet]) -> None:
            paragraph = document.add_paragraph(" · ".join(
                item.text for item in sorted(values, key=lambda bullet: bullet.priority, reverse=True)
            ))
            paragraph.paragraph_format.space_after = Pt(1)

        priorities = {"项目经历": 0, "工作经历": 1, "补充技能": 2, "教育经历": 3, "其他信息": 4} if template == "technical" else {"工作经历": 0, "项目经历": 1, "教育经历": 2, "补充技能": 3, "其他信息": 4}
        ordered = sorted(version.sections, key=lambda item: priorities.get(item.title, 9))
        if template == "technical":
            add_heading("专业技能")
            add_skills(version.skills)
            add_heading("个人概述")
            add_bullets(version.summary)
        else:
            add_heading("个人概述")
            add_bullets(version.summary)
        for item in ordered:
            add_heading(item.title)
            add_bullets(item.bullets)
            for entry in item.entries:
                paragraph = document.add_paragraph()
                paragraph.add_run(entry.heading).bold = True
                if entry.subheading:
                    paragraph.add_run(f"  {entry.subheading}")
                if entry.date_range:
                    paragraph.add_run(f"  {entry.date_range}")
                add_bullets(entry.bullets)
        if template == "business" and version.skills:
            add_heading("专业技能")
            add_skills(version.skills)
        document.core_properties.title = f"{target.company} {version.headline} 定向简历"
        document.core_properties.author = version.contact.name
        output.parent.mkdir(parents=True, exist_ok=True)
        document.save(output)

    @staticmethod
    def _render_original_docx(version: ResumeDraftVersion, target: TargetJob,
                              template: str, output: Path) -> None:
        """Render both variants inside the uploaded resume's visual shell."""
        document = Document()
        section = document.sections[0]
        section.page_width = Inches(8.27)
        section.page_height = Inches(17.83)
        section.top_margin = Inches(0.45)
        section.bottom_margin = Inches(0.45)
        section.left_margin = Inches(0.62)
        section.right_margin = Inches(0.62)
        styles = document.styles
        styles["Normal"].font.name = "Noto Sans CJK SC"
        styles["Normal"].font.size = Pt(10.5)
        styles["Normal"].paragraph_format.space_after = Pt(1)
        orange = RGBColor(239, 137, 72)
        gray = RGBColor(112, 112, 112)

        header = document.add_table(rows=1, cols=2)
        header.autofit = False
        header.columns[0].width = Inches(1.35)
        header.columns[1].width = Inches(5.45)
        photo_cell, info_cell = header.rows[0].cells
        photo = photo_cell.paragraphs[0]
        photo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        photo_path = Path(version.contact.photo_path) if version.contact.photo_path else None
        if photo_path and photo_path.is_file():
            photo_run = photo.add_run()
            photo_run.add_picture(str(photo_path), width=Inches(1.25))
        else:
            photo_run = photo.add_run("●")
            photo_run.font.size = Pt(58)
            photo_run.font.color.rgb = orange
        name = info_cell.paragraphs[0]
        name.paragraph_format.space_after = Pt(2)
        name_run = name.add_run(version.contact.name or "个人简历")
        name_run.bold = True
        name_run.font.size = Pt(24)
        contacts = "  ·  ".join(filter(None, [version.contact.phone, version.contact.email, version.contact.location]))
        if contacts:
            p = info_cell.add_paragraph(contacts)
            p.paragraph_format.space_after = Pt(2)
        if version.contact.personal_facts:
            p = info_cell.add_paragraph("  ·  ".join(version.contact.personal_facts))
            p.paragraph_format.space_after = Pt(2)
        target_paragraph = document.add_paragraph(f"目标岗位：{target.company} · {target.title}")
        target_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        target_paragraph.paragraph_format.space_after = Pt(2)
        target_paragraph.runs[0].font.color.rgb = orange
        rule = document.add_paragraph("─" * 105)
        rule.paragraph_format.space_after = Pt(2)
        rule.runs[0].font.color.rgb = RGBColor(225, 225, 225)

        def add_heading(text: str) -> None:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(text)
            run.bold = True
            run.font.size = Pt(14)
            accent = paragraph.add_run("  ━━━━━")
            accent.font.size = Pt(8)
            accent.font.color.rgb = orange

        def add_bullets(values: list[ResumeBullet], links: list[str] | None = None) -> None:
            for item in sorted(values, key=lambda bullet: bullet.priority, reverse=True):
                paragraph = document.add_paragraph(style="List Number")
                paragraph.paragraph_format.left_indent = Inches(0.18)
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.line_spacing = 1.05
                paragraph.add_run(_display_bullet_text(item.text, links))

        if version.summary:
            add_heading("求职意向")
            add_bullets(version.summary[:2])
        priorities = {"教育经历": 0, "项目经历": 1, "工作经历": 2, "其他信息": 3, "补充技能": 4}
        for section_value in sorted(version.sections, key=lambda item: priorities.get(item.title, 9)):
            if not section_value.entries and not section_value.bullets:
                continue
            add_heading(section_value.title)
            add_bullets(section_value.bullets)
            for entry in section_value.entries:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(2)
                paragraph.paragraph_format.space_after = Pt(1)
                run = paragraph.add_run(entry.heading if entry.heading != entry.date_range else "")
                run.bold = True
                if entry.date_range:
                    date_run = paragraph.add_run(f"  {entry.date_range}")
                    date_run.font.color.rgb = gray
                for link in entry.links:
                    link_paragraph = document.add_paragraph(f"项目地址：{link}")
                    link_paragraph.paragraph_format.space_after = Pt(1)
                    link_paragraph.runs[0].font.color.rgb = gray
                add_bullets(entry.bullets, entry.links)
        if version.skills:
            add_heading("相关技能")
            paragraph = document.add_paragraph(" · ".join(item.text for item in version.skills))
            paragraph.paragraph_format.line_spacing = 1.05
        document.core_properties.title = f"{target.company} {version.headline} 定向简历"
        document.core_properties.author = version.contact.name
        output.parent.mkdir(parents=True, exist_ok=True)
        document.save(output)

    @staticmethod
    def render_html(version: ResumeDraftVersion, target: TargetJob, template: str) -> str:
        def bullets(values: list[ResumeBullet], links: list[str] | None = None) -> str:
            items = "".join(
                f"<li>{html.escape(_display_bullet_text(item.text, links))}</li>"
                for item in sorted(values, key=lambda value: value.priority, reverse=True)
            )
            return f"<ul>{items}</ul>" if items else ""

        def section_html(title: str, value: ResumeSection) -> str:
            body = bullets(value.bullets)
            for entry in value.entries:
                title_value = "" if entry.heading == entry.date_range else entry.heading
                meta = " · ".join(filter(None, [entry.subheading, entry.date_range]))
                links = "".join(
                    f"<p class='project-link'>项目地址：{html.escape(link)}</p>"
                    for link in entry.links
                )
                body += (
                    f"<div class='entry'><div class='entry-head'><strong>{html.escape(title_value)}</strong>"
                    f"<span>{html.escape(meta)}</span></div>{links}{bullets(entry.bullets, entry.links)}</div>"
                )
            return f"<section><h2>{html.escape(title)}</h2>{body}</section>" if body else ""

        contact = " · ".join(filter(None, [version.contact.phone, version.contact.email, version.contact.location]))
        personal_facts = " · ".join(version.contact.personal_facts)
        personal_facts_markup = (
            f"<p class='contact'>{html.escape(personal_facts)}</p>" if personal_facts else ""
        )
        priorities = {"教育经历": 0, "项目经历": 1, "工作经历": 2, "其他信息": 3, "补充技能": 4}
        content: list[str] = []
        if version.summary:
            content.append(section_html(
                "求职意向", ResumeSection(section_id="summary", title="求职意向", bullets=version.summary[:2]),
            ))
        content.extend(
            section_html(value.title, value)
            for value in sorted(version.sections, key=lambda item: priorities.get(item.title, 9))
        )
        if version.skills:
            skills = html.escape(" · ".join(item.text for item in version.skills))
            content.append(f"<section><h2>相关技能</h2><p class='skills'>{skills}</p></section>")
        body = "".join(item for item in content if item)
        initials = html.escape((version.contact.name or "简历")[:1])
        photo_markup = f"<div class='photo'>{initials}</div>"
        photo_path = Path(version.contact.photo_path) if version.contact.photo_path else None
        if photo_path and photo_path.is_file():
            encoded = base64.b64encode(photo_path.read_bytes()).decode("ascii")
            photo_markup = f"<img class='photo photo-image' src='data:image/png;base64,{encoded}' alt='个人照片'>"
        return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><style>
        @page{{size:210mm 453mm;margin:0}}*{{box-sizing:border-box}}body{{font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif;color:#333;font-size:11pt;line-height:1.55;margin:0;padding:13mm 16mm 10mm;background:#fff}}header{{display:grid;grid-template-columns:36mm 1fr;column-gap:10mm;align-items:center;padding-bottom:7mm;border-bottom:1px solid #e4e4e4}}.photo{{width:31mm;height:31mm;border:2px solid #f29a5c;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#f29a5c;font-size:24pt;font-weight:700;background:#fff7f0;object-fit:cover}}.photo-image{{padding:0;background:#fff}}h1{{font-size:25pt;margin:0 0 2mm;color:#111;font-weight:800}}.contact{{font-size:10.5pt;color:#555;margin:1mm 0}}.target{{color:#f08b4b;font-weight:700;font-size:11.5pt;margin:2mm 0 0}}section{{padding:5mm 0 4mm;border-bottom:1px solid #e5e5e5;break-inside:avoid}}h2{{font-size:15pt;color:#222;margin:0 0 2.5mm;font-weight:800}}h2::after{{content:'';display:block;width:23mm;height:1.2mm;background:#f29a5c;margin-top:1.5mm}}ul{{margin:0;padding-left:6mm}}li{{margin:0 0 1.2mm;break-inside:avoid}}.entry{{margin:2mm 0 3mm;break-inside:avoid}}.entry-head{{display:flex;justify-content:space-between;gap:5mm;font-size:12pt;margin-bottom:1mm}}.entry-head span{{color:#666;font-size:10.5pt;white-space:nowrap}}.project-link{{margin:0 0 1mm;color:#666;font-size:10.5pt}}.skills{{margin:0;line-height:1.7}}section:last-child{{border-bottom:0}}
        </style></head><body><header>{photo_markup}<div><h1>{html.escape(version.contact.name or '个人简历')}</h1><p class='contact'>{html.escape(contact)}</p>{personal_facts_markup}<p class='target'>目标岗位：{html.escape(target.company)} · {html.escape(target.title)}</p></div></header>{body}</body></html>"""

    async def render_pdf(self, html_value: str, output: Path) -> None:
        from playwright.async_api import async_playwright
        output.parent.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html_value, wait_until="load")
            await page.pdf(path=str(output), print_background=True, prefer_css_page_size=True,
                           margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            await browser.close()

    async def export(self, export_id: str) -> ResumeExport:
        export = self.database.get_resume_export(export_id)
        if not export:
            raise ResumeError("导出任务不存在")
        version = self.database.get_draft_version(export.version_id)
        if not version or version.validation_status != "VALID":
            raise ResumeError("简历版本不存在或未通过校验")
        profile = self.database.get_profile(version.profile_id)
        if not profile:
            raise ResumeError("候选人画像不存在")
        version.contact = sanitize_candidate_contact(version.contact)
        version.contact.personal_facts = _personal_facts(profile)
        self.database.save_contact(version.contact)
        if _repair_draft_source_metadata(version, profile):
            # Persist the repaired metadata so the preview, subsequent exports
            # and the user-edit flow all see the same project headings/links.
            self.database.update_draft_version(version)
        target = self.database.get_target_job(version.target_job_id)
        if not target:
            raise ResumeError("目标岗位不存在")
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", f"{target.company}_{target.title}").strip("_")[:80]
        folder = self.export_dir / export.export_id
        docx_path = folder / f"{safe}_简历_v{version.version}_{export.template}.docx"
        pdf_path = folder / f"{safe}_简历_v{version.version}_{export.template}.pdf"
        self.render_docx(version, target, export.template, docx_path)
        await self.render_pdf(self.render_html(version, target, export.template), pdf_path)
        export.docx_path = str(docx_path)
        export.pdf_path = str(pdf_path)
        export.qa_report = inspect_export(version, target, export.template, docx_path, pdf_path)
        if export.qa_report.status == "FAIL":
            export.status = "FAILED"
            failed = [item.message for item in export.qa_report.checks if item.status == "FAIL"]
            export.error = "导出验收未通过：" + "；".join(failed[:4])
            self.database.save_resume_export(export)
            raise ResumeError(export.error)
        export.status = "SUCCEEDED"
        export.error = None
        return self.database.save_resume_export(export)
