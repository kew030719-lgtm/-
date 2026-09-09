from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from docx import Document
from pypdf import PdfReader

from .schemas import CandidateProfile, Evidence


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


class ResumeError(ValueError):
    pass


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
            raise ResumeError("未提取到足够文字，扫描版 PDF 暂不支持 OCR，请粘贴简历文本")
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
        education=education, evidence=evidence,
    )


def validate_citations(citations: list[Evidence], sources: list[Evidence]) -> None:
    index = {(item.source_type, item.source_id, item.block_id): item.quote for item in sources}
    for citation in citations:
        key = (citation.source_type, citation.source_id, citation.block_id)
        original = index.get(key)
        if original is None or citation.quote not in original:
            raise ResumeError(f"引用校验失败：{citation.block_id}")
