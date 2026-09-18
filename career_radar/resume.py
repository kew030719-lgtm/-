from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from docx import Document
from pypdf import PdfReader

from .schemas import CandidateContact, CandidateProfile, Evidence, ResumeSourceEntry


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_CITIES = ["北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "南京", "西安", "苏州"]
SKILLS = {
    "python": "Python", "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
    "sql": "SQL", "mysql": "MySQL", "postgresql": "PostgreSQL", "redis": "Redis",
    "docker": "Docker", "kubernetes": "Kubernetes", "linux": "Linux", "git": "Git",
    "playwright": "Playwright", "selenium": "Selenium", "scrapy": "Scrapy",
    "requests": "Requests", "httpx": "HTTPX", "爬虫": "爬虫", "数据采集": "数据采集",
    "llm": "LLM", "大模型": "大模型", "rag": "RAG", "agent": "Agent",
    "langchain": "LangChain", "pandas": "Pandas", "numpy": "NumPy",
    "javascript": "JavaScript", "typescript": "TypeScript", "vue": "Vue", "react": "React",
}
# Some Chinese skill labels are natural-language variants of the same verified
# capability.  These aliases are only used when checking evidence; they do not
# add a new skill to a candidate profile or allow unrelated evidence through.
SKILL_EVIDENCE_ALIASES = {
    "数据采集": ("数据采集", "数据抓取", "采集数据", "爬虫", "抓取"),
    "爬虫": ("爬虫", "数据采集", "数据抓取", "采集数据", "抓取"),
}


def skill_is_grounded(token: str, evidence_text: str) -> bool:
    normalized = evidence_text.lower()
    aliases = SKILL_EVIDENCE_ALIASES.get(token, (token,))
    return any(alias.lower() in normalized for alias in aliases)


PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GENERIC_NAME_LABELS = {
    "基本信息", "个人信息", "个人简历", "求职简历", "简历", "联系方式",
    "personal information", "resume", "curriculum vitae",
}


class ResumeError(ValueError):
    pass


class ResumeGenerationError(ResumeError):
    pass


class ScannedResumeError(ResumeError):
    """The PDF is valid but has no usable text layer and needs visual OCR."""


def _is_docx(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def extract_resume_text(data: bytes, filename: str, declared_type: str) -> str:
    if not data:
        raise ResumeError("文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ResumeError("文件超过 10 MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if declared_type != "application/pdf" or not data.startswith(b"%PDF"):
            raise ResumeError("文件声明类型与实际 PDF 格式不一致")
        try:
            text = "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)
        except Exception as exc:
            raise ResumeError("PDF 已损坏或无法解析") from exc
        if len(text.strip()) < 30:
            raise ScannedResumeError("PDF 没有可用文字层，需要视觉模型识别")
        return text
    if suffix == ".docx":
        valid_types = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        if declared_type not in valid_types or not _is_docx(data):
            raise ResumeError("文件声明类型与实际 DOCX 格式不一致")
        try:
            doc = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as exc:
            raise ResumeError("DOCX 已损坏或无法解析") from exc
        if not text.strip():
            raise ResumeError("DOCX 中没有可读取的简历文本")
        return text
    raise ResumeError("只支持 PDF 和 DOCX 文件")


def normalize_resume_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) < 30:
        raise ResumeError("简历内容过短，请提供教育、技能、项目或经历信息")
    return cleaned[:80_000]


def _section_for(line: str, current: str) -> str:
    value = line.lower().strip(" ：:")
    labels = {
        "教育": ("教育", "教育经历", "education"),
        "技能": ("技能", "专业技能", "技术栈", "skills"),
        "项目": ("项目", "项目经历", "projects"),
        "经历": ("经历", "工作经历", "实习经历", "experience", "work experience"),
    }
    for section, aliases in labels.items():
        if any(value == alias or value.startswith(alias + "：") for alias in aliases):
            return section
    return current


SECTION_LABELS = {
    "教育", "教育经历", "education", "技能", "专业技能", "技术栈", "skills",
    "项目", "项目经历", "projects", "经历", "工作经历", "实习经历",
    "experience", "work experience",
}
DATE_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?\s*(?:[-—至~～]|到)\s*"
    r"(?:(?:19|20)\d{2}(?:[./年-]\d{1,2}月?)?|至今|现在)"
)


def _looks_entry_heading(text: str, section: str) -> bool:
    value = text.strip(" ：:")
    if value.lower() in SECTION_LABELS or len(value) > 80:
        return False
    if DATE_RANGE_RE.search(value):
        return True
    if section == "教育" and any(token in value for token in ("大学", "学院", "学校", "本科", "硕士", "博士", "大专")):
        return True
    if section == "经历" and any(token in value for token in ("公司", "研究院", "中心", "工程师", "实习生", "负责人")):
        return True
    if section == "项目" and ("项目" in value or value.endswith(("系统", "平台", "应用", "服务"))):
        return True
    return False


def _resume_entries(evidence: list[Evidence]) -> list[ResumeSourceEntry]:
    by_section: dict[str, list[Evidence]] = {}
    for item in evidence:
        if item.quote.strip(" ：:").lower() not in SECTION_LABELS:
            by_section.setdefault(item.section or "概览", []).append(item)
    kinds = {"经历": "experience", "项目": "project", "教育": "education", "技能": "skills"}
    entries: list[ResumeSourceEntry] = []
    for section, items in by_section.items():
        kind = kinds.get(section, "other")
        groups: list[list[Evidence]] = []
        for item in items:
            if not groups or (kind in {"experience", "project", "education"} and _looks_entry_heading(item.quote, section)):
                groups.append([item])
            else:
                groups[-1].append(item)
        for group in groups:
            first = group[0].quote.strip()
            # Headings are copied into the final resume and therefore must be an
            # exact, citable fragment rather than a synthesized section label.
            heading = first
            date = DATE_RANGE_RE.search(first)
            organization = ""
            if kind in {"experience", "education"}:
                organization = next((part.strip() for part in re.split(r"[|｜·]", first)
                                     if any(suffix in part for suffix in ("公司", "大学", "学院", "研究院", "学校"))), "")
            role = next((token for token in ("后端工程师", "开发工程师", "算法工程师", "数据工程师", "实习生", "负责人")
                         if token in first), "")
            digest = hashlib.sha256("\0".join(item.block_id for item in group).encode()).hexdigest()[:12]
            entries.append(ResumeSourceEntry(
                entry_id=f"source-entry-{digest}", kind=kind, heading=heading,
                organization=organization, role=role,
                date_range=date.group(0) if date else "",
                evidence_ids=[item.block_id for item in group],
                original_bullets=[item.quote for item in group],
            ))
    return entries


def build_profile(text: str, profile_id: str | None = None) -> CandidateProfile:
    cleaned = normalize_resume_text(text)
    profile_id = profile_id or f"profile_{uuid4().hex[:12]}"
    evidence: list[Evidence] = []
    section = "概览"
    for index, line in enumerate(cleaned.splitlines(), start=1):
        section = _section_for(line, section)
        stable = hashlib.sha256(f"{section}\0{line}".encode()).hexdigest()[:12]
        evidence.append(Evidence(
            source_type="resume", source_id=profile_id,
            block_id=f"resume-{section}-{stable}", quote=line, section=section,
            provenance="uploaded_resume",
        ))

    lowered = cleaned.lower()
    skills = sorted({display for token, display in SKILLS.items() if token in lowered})
    experience_years = None
    year_matches = re.findall(r"(?:工作经验|经验|从业)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*年", cleaned)
    year_matches += re.findall(r"(\d+(?:\.\d+)?)\s*年[^\n。；]{0,30}(?:工作|开发|从业)?经验", cleaned)
    if year_matches:
        experience_years = max(float(value) for value in year_matches)
    education = next((degree for degree in ("博士", "硕士", "本科", "大专") if degree in cleaned), None)
    return CandidateProfile(
        profile_id=profile_id, skills=skills, experience_years=experience_years,
        education=education, evidence=evidence, resume_entries=_resume_entries(evidence),
    )


def extract_candidate_contact(profile: CandidateProfile) -> CandidateContact:
    """Split contact data from model-visible evidence while retaining useful text."""
    contact = CandidateContact(profile_id=profile.profile_id)
    public: list[Evidence] = []
    remapped_ids: dict[str, str | None] = {}
    for index, item in enumerate(profile.evidence):
        quote = item.quote
        phone = PHONE_RE.search(quote)
        email = EMAIL_RE.search(quote)
        if phone and not contact.phone:
            contact.phone = phone.group(0)
        if email and not contact.email:
            contact.email = email.group(0)
        location = re.search(r"(?:现居|所在地|地址|城市)\s*[:：]?\s*([^|｜，,；;]{2,20})", quote)
        if not contact.location and location:
            contact.location = location.group(1).strip()
        normalized_name = quote.strip(" ：:").lower()
        is_name_candidate = (
            not contact.name and index < 3 and item.section == "概览" and
            not phone and not email and not re.search(r"\d", quote) and
            2 <= len(quote.strip()) <= 30 and
            normalized_name not in GENERIC_NAME_LABELS and
            not any(word in quote.lower() for word in ("简历", "求职", "技能", "教育", "经历", "项目", "resume"))
        )
        if is_name_candidate:
            contact.name = quote.strip()
            remapped_ids[item.block_id] = None
            continue
        sanitized = EMAIL_RE.sub("", PHONE_RE.sub("", quote))
        if location:
            sanitized = sanitized.replace(location.group(0), "")
        sanitized = re.sub(r"(?:电话|手机|邮箱|电子邮箱|现居|所在地|地址|城市)\s*[:：]?", "", sanitized)
        sanitized = re.sub(r"\s*[|｜·]\s*[|｜·]*\s*", " | ", sanitized).strip(" |｜·，,；;")
        if sanitized:
            stable = hashlib.sha256(f"{item.section}\0{sanitized}".encode()).hexdigest()[:12]
            new_id = f"resume-{item.section}-{stable}"
            remapped_ids[item.block_id] = new_id
            public.append(item.model_copy(update={
                "quote": sanitized,
                "block_id": new_id,
                "provenance": item.provenance or "uploaded_resume",
            }))
        else:
            remapped_ids[item.block_id] = None
    profile.evidence = public
    available = {item.block_id: item.quote for item in public}
    entries = []
    for entry in profile.resume_entries:
        ids = [remapped_ids.get(item, item) for item in entry.evidence_ids]
        entry.evidence_ids = [item for item in ids if item in available]
        entry.original_bullets = [available[item] for item in entry.evidence_ids]
        if entry.evidence_ids:
            entries.append(entry)
    profile.resume_entries = entries
    return contact


def sanitize_candidate_contact(contact: CandidateContact) -> CandidateContact:
    """Remove labels that were historically mistaken for a candidate name."""
    value = contact.model_copy(deep=True)
    if value.name.strip(" ：:").lower() in GENERIC_NAME_LABELS:
        value.name = ""
    return value


def validate_citations(citations: list[Evidence], sources: list[Evidence]) -> None:
    index = {(item.source_type, item.source_id, item.block_id): item.quote for item in sources}
    for citation in citations:
        key = (citation.source_type, citation.source_id, citation.block_id)
        original = index.get(key)
        if original is None or citation.quote not in original:
            raise ResumeError(f"引用校验失败：{citation.block_id}")
