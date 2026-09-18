from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from uuid import uuid4

from .database import now_iso
from .schemas import ProjectUploadAnalysis


MAX_PROJECT_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PROJECT_FILES = 400
MAX_TEXT_BYTES_PER_FILE = 80_000
MAX_ANALYSIS_TEXT_BYTES = 500_000
IGNORED_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini",
    ".cfg", ".csv", ".sql", ".js", ".ts", ".tsx", ".vue", ".html", ".css",
}
PROJECT_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")
TECHNOLOGY_RULES = (
    ("Python", (r"\.py$", r"python_requires", r"python_version", r"\bpython\b")),
    ("Requests", (r"\brequests\b", r"requests\.get", r"requests\.post")),
    ("HTTPX", (r"\bhttpx\b",)),
    ("Pandas", (r"\bpandas\b", r"pd\.read_", r"pd\.DataFrame")),
    ("BeautifulSoup", (r"beautifulsoup", r"bs4", r"BeautifulSoup")),
    ("Scrapy", (r"\bscrapy\b",)),
    ("Selenium", (r"\bselenium\b",)),
    ("Playwright", (r"\bplaywright\b",)),
    ("FastAPI", (r"\bfastapi\b",)),
    ("Django", (r"\bdjango\b",)),
    ("Flask", (r"\bflask\b",)),
    ("SQL", (r"\bsql\b", r"sqlite", r"mysql", r"postgres")),
    ("Docker", (r"dockerfile$", r"docker-compose", r"docker compose")),
)


class ProjectUploadError(ValueError):
    pass


def _project_name(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1]
    for suffix in (".tar.gz", ".zip", ".py", ".md", ".txt"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    name = re.sub(r"[-_](?:main|master)$", "", name, flags=re.I).strip(" _-")
    if name.lower() in {"stock", "stocks"} or "股票" in name:
        return "股票信息数据爬虫"
    return name or "上传项目"


def _safe_zip_files(data: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProjectUploadError("项目压缩包不是有效的 ZIP 文件") from exc
    files: list[tuple[str, bytes]] = []
    try:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir() or path.is_absolute() or ".." in path.parts:
                continue
            if any(part.lower() in IGNORED_PARTS for part in path.parts):
                continue
            if len(files) >= MAX_PROJECT_FILES:
                break
            files.append((str(path), archive.read(info)[:MAX_TEXT_BYTES_PER_FILE]))
    finally:
        archive.close()
    if not files:
        raise ProjectUploadError("压缩包中没有可分析的项目文件")
    return files


def _single_file(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    suffix = filename.lower()
    if not suffix.endswith(tuple(TEXT_SUFFIXES)):
        raise ProjectUploadError("请上传 ZIP 项目压缩包，或单个源码/README/依赖文件")
    return [(filename.rsplit("/", 1)[-1], data[:MAX_TEXT_BYTES_PER_FILE])]


def analyze_project_upload(data: bytes, filename: str, profile_id: str, tailoring_id: str) -> ProjectUploadAnalysis:
    if not data:
        raise ProjectUploadError("项目文件为空")
    if len(data) > MAX_PROJECT_UPLOAD_BYTES:
        raise ProjectUploadError("项目文件超过 10 MB，请压缩后上传")
    files = _safe_zip_files(data) if filename.lower().endswith(".zip") else _single_file(filename, data)
    text_parts: list[str] = []
    for path, content in files:
        if _is_binary(content):
            continue
        text_parts.append(path + "\n" + content.decode("utf-8", errors="ignore")[:MAX_TEXT_BYTES_PER_FILE])
        if sum(len(item) for item in text_parts) >= MAX_ANALYSIS_TEXT_BYTES:
            break
    source_text = "\n".join(text_parts)
    searchable = source_text.lower()
    technologies: list[str] = []
    for label, patterns in TECHNOLOGY_RULES:
        if any(
            re.search(pattern, path.lower()) or re.search(pattern, searchable, re.I)
            for pattern in patterns for path, _content in files
        ):
            if label not in technologies:
                technologies.append(label)
    findings: list[str] = []
    if re.search(r"\b(requests|httpx|urllib)\b", searchable):
        findings.append("项目材料包含数据请求相关代码或依赖")
    if re.search(r"(beautifulsoup|bs4|scrapy|selenium|playwright)", searchable):
        findings.append("项目材料包含页面解析或浏览器采集相关代码或依赖")
    if re.search(r"(pandas|csv|json|sqlite|mysql|postgres|\.to_csv|\.to_sql)", searchable):
        findings.append("项目材料包含数据清洗、结构化处理或结果保存相关代码或依赖")
    if not findings:
        findings.append("已读取项目文件结构，但暂未识别出可直接归入简历的工作链路")
    project_name = _project_name(filename)
    visible_files = [path for path, _content in files[:40]]
    file_hint = "、".join(visible_files[:8])
    technology_hint = "、".join(technologies) or "未识别出明确技术栈"
    project_urls: list[str] = []
    for url in PROJECT_URL_RE.findall(source_text):
        cleaned = url.rstrip(".,;:!?，。；：！？")
        if cleaned not in project_urls:
            project_urls.append(cleaned)
        if len(project_urls) >= 10:
            break
    url_hint = "；项目地址：" + "、".join(project_urls) if project_urls else ""
    evidence_quote = (
        f"项目“{project_name}”上传材料静态分析：共读取 {len(files)} 个项目文件，"
        f"代表文件包括 {file_hint}；识别到技术或依赖：{technology_hint}。"
        f"可核验工作链路：{'；'.join(findings)}。{url_hint}"
        "以上结论只来自上传文件的结构和文本，需用户确认后才会写入简历。"
    )
    return ProjectUploadAnalysis(
        upload_id=f"project_upload_{uuid4().hex[:12]}", profile_id=profile_id,
        tailoring_id=tailoring_id, filename=filename.rsplit("/", 1)[-1],
        project_name=project_name, file_count=len(files), files=visible_files,
        technologies=technologies, findings=findings, project_urls=project_urls,
        evidence_quote=evidence_quote,
        created_at=now_iso(),
    )


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]
