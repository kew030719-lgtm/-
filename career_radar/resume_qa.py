"""Post-generation and post-export checks for tailored resumes.

The checks are deliberately deterministic.  The model may suggest wording, but
it never decides whether an exported file is readable or whether a private
evidence identifier leaked into the document.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from docx import Document

from .schemas import (
    ResumeDraftVersion,
    ResumeExportQAReport,
    ResumeQualityCheck,
    TargetJob,
)


def _check(check_id: str, status: str, message: str, suggestion: str = "") -> ResumeQualityCheck:
    return ResumeQualityCheck(id=check_id, status=status, message=message, suggestion=suggestion)


def _overall(checks: list[ResumeQualityCheck]) -> str:
    if any(item.status == "FAIL" for item in checks):
        return "FAIL"
    if any(item.status == "WARN" for item in checks):
        return "WARN"
    return "PASS"


def _version_text(version: ResumeDraftVersion) -> str:
    values = [version.headline, version.contact.name, version.contact.phone,
              version.contact.email, version.contact.location]
    values.extend(item.text for item in [*version.summary, *version.skills])
    for section in version.sections:
        values.append(section.title)
        values.extend(item.text for item in section.bullets)
        for entry in section.entries:
            values.extend([entry.heading, entry.subheading, entry.date_range, *entry.links])
            values.extend(item.text for item in entry.bullets)
    return "\n".join(item for item in values if item)


def _supported_target_keywords(version: ResumeDraftVersion, target: TargetJob) -> list[str]:
    text = _version_text(version).lower()
    values = [target.title, target.company]
    # A missing requirement is a real gap and is reported by the generation
    # quality report; it must not become a spurious export failure.
    values.extend(skill for skill in target.required_skills if skill.lower() in text)
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def _extract_pdf_text(path: Path) -> tuple[str, int, str]:
    """Return text, page count and extractor name, with a PyMuPDF fallback."""
    def normalise(value: str) -> str:
        # Chromium may expose CJK glyphs as Kangxi radicals in the PDF text
        # map even though the page renders correctly. NFKC restores the
        # searchable Chinese characters for ATS and evidence checks.
        return unicodedata.normalize("NFKC", value)

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = normalise("\f".join(page.extract_text() or "" for page in reader.pages))
        if text.strip():
            return text, len(reader.pages), "pypdf"
        page_count = len(reader.pages)
    except Exception:
        page_count = 0
    try:
        import fitz  # type: ignore[import-not-found]

        document = fitz.open(str(path))
        try:
            text = normalise("\f".join(page.get_text() or "" for page in document))
            return text, len(document), "pymupdf"
        finally:
            document.close()
    except Exception:
        return "", page_count, "none"


def _check_text_layer(text: str, version: ResumeDraftVersion, target: TargetJob,
                      page_count: int, extractor: str) -> list[ResumeQualityCheck]:
    checks: list[ResumeQualityCheck] = []
    compact_text = re.sub(r"\s+", "", text).casefold()
    if not text.strip():
        # Some browser/PDF combinations render CJK visually but expose no text
        # layer. Keep the artifact downloadable for manual review while making
        # the ATS limitation explicit; a missing layer is never reported as a
        # false PASS.
        checks.append(_check(
            "pdf_text_layer", "WARN", "PDF 未提取到可读文本层（提取器：%s）" % extractor,
            "请重新导出或使用 DOCX；ATS 无法读取该 PDF 的文字。",
        ))
        checks.append(_check(
            "pdf_core_keywords", "WARN", "PDF 文本层为空，无法核验岗位关键词和经历",
            "重新导出后检查岗位名称、公司、技能和经历是否可复制。",
        ))
        return checks
    if "�" in text or re.search(r"\(cid:\d+\)", text, re.I):
        checks.append(_check(
            "pdf_text_layer", "FAIL", "PDF 文本层包含乱码或字体替换标记",
            "更换可嵌入中文字体后重新导出。",
        ))
    else:
        checks.append(_check("pdf_text_layer", "PASS", f"PDF 文本层可解析（{extractor}）"))
    leaked = [token for token in re.findall(r"(?:bullet|resume|source|target|entry)[_-][A-Za-z0-9_-]+", text, re.I)]
    if leaked:
        checks.append(_check(
            "no_internal_ids", "FAIL", "PDF 暴露了内部证据或条目 ID：" + ", ".join(leaked[:3]),
            "不要把证据 ID 放进可见简历正文。",
        ))
    else:
        checks.append(_check("no_internal_ids", "PASS", "PDF 未发现内部证据 ID"))
    missing_contact = [value for value in (version.contact.email, version.contact.phone)
                       if value and value not in text]
    if missing_contact:
        checks.append(_check(
            "contact_text", "FAIL", "PDF 文本层缺少联系方式：" + "、".join(missing_contact),
            "确保邮箱和电话号码以文本形式写入文档。",
        ))
    else:
        checks.append(_check("contact_text", "PASS", "联系方式在 PDF 文本层中可读取"))
    missing_keywords = [keyword for keyword in _supported_target_keywords(version, target)
                        if re.sub(r"\s+", "", keyword).casefold() not in compact_text]
    if missing_keywords:
        checks.append(_check(
            "pdf_core_keywords", "WARN", "PDF 文本层缺少部分岗位关键词：" + "、".join(missing_keywords[:5]),
            "保留有证据支持的岗位名称、公司和技能表述。",
        ))
    else:
        checks.append(_check("pdf_core_keywords", "PASS", "岗位名称、公司和有证据的核心关键词可提取"))
    entry_headings = [entry.heading for section in version.sections for entry in section.entries
                      if entry.heading and entry.heading not in {"补充经历", "补充项目经历"}]
    missing_entries = [heading for heading in entry_headings[:5]
                       if re.sub(r"\s+", "", heading).casefold() not in compact_text]
    if missing_entries:
        checks.append(_check(
            "pdf_experience_text", "FAIL", "PDF 文本层缺少主要经历：" + "、".join(missing_entries),
            "确认项目/工作经历标题没有被渲染或分页截断。",
        ))
    else:
        checks.append(_check("pdf_experience_text", "PASS", "主要项目/工作经历可从 PDF 文本层提取"))
    if page_count:
        pages = text.split("\f")
        last_page_lines = [line.strip() for line in pages[-1].splitlines() if line.strip()]
        if len(last_page_lines) <= 2 and page_count > 1:
            checks.append(_check(
                "orphan_last_page", "WARN", "最后一页内容过少，可能存在孤立标题或条目",
                "减少间距或调整经历顺序后重新导出。",
            ))
        else:
            checks.append(_check("orphan_last_page", "PASS", "未发现明显的孤立末页内容"))
    return checks


def inspect_export(version: ResumeDraftVersion, target: TargetJob, template: str,
                   docx_path: Path, pdf_path: Path) -> ResumeExportQAReport:
    """Inspect actual files after rendering, returning a user-facing report."""
    checks: list[ResumeQualityCheck] = []
    if not docx_path.is_file() or docx_path.stat().st_size == 0:
        checks.append(_check("docx_file", "FAIL", "DOCX 文件不存在或为空", "重新生成导出文件。"))
    else:
        try:
            document = Document(str(docx_path))
            docx_text = "\n".join(item.text for item in document.paragraphs)
            checks.append(_check("docx_file", "PASS", "DOCX 文件可打开"))
            if "�" in docx_text:
                checks.append(_check("docx_text", "FAIL", "DOCX 包含乱码字符", "更换字体后重新导出。"))
            else:
                checks.append(_check("docx_text", "PASS", "DOCX 文本可读取"))
            missing_docx = [keyword for keyword in (target.title, target.company)
                            if keyword and keyword.lower() not in docx_text.lower()]
            if missing_docx:
                checks.append(_check(
                    "docx_core_keywords", "FAIL", "DOCX 缺少岗位核心信息：" + "、".join(missing_docx),
                    "确认岗位名称和公司名称已写入导出文件。",
                ))
            else:
                checks.append(_check("docx_core_keywords", "PASS", "DOCX 包含岗位名称和公司名称"))
        except Exception as exc:
            checks.append(_check("docx_file", "FAIL", f"DOCX 无法打开：{exc}", "重新生成导出文件。"))

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        checks.append(_check("pdf_file", "FAIL", "PDF 文件不存在或为空", "重新生成导出文件。"))
        return ResumeExportQAReport(status=_overall(checks), checks=checks, page_count=0, ats_extractable=False)

    text, page_count, extractor = _extract_pdf_text(pdf_path)
    checks.append(_check("pdf_file", "PASS", "PDF 文件可打开"))
    if page_count == 0:
        checks.append(_check("page_count", "FAIL", "无法读取 PDF 页数", "重新生成导出文件。"))
    elif page_count > 2:
        checks.append(_check("page_count", "FAIL", f"PDF 共 {page_count} 页，超过两页上限", "精简内容后重新生成。"))
    elif page_count == 2:
        checks.append(_check("page_count", "WARN", "PDF 共 2 页，已超过一页优先策略", "确认内容无法安全压缩后再提交。"))
    else:
        checks.append(_check("page_count", "PASS", "PDF 为 1 页"))
    checks.extend(_check_text_layer(text, version, target, page_count, extractor))
    return ResumeExportQAReport(
        status=_overall(checks), checks=checks, page_count=page_count,
        ats_extractable=bool(text.strip()) and not any(
            item.id == "pdf_text_layer" and item.status == "FAIL" for item in checks
        ),
    )
