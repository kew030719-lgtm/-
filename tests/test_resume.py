from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from career_radar.resume import MAX_UPLOAD_BYTES, ResumeError, build_profile, extract_resume_text, validate_citations
from career_radar.schemas import Evidence


TEXT = """技能
Python FastAPI SQL Redis Docker
项目经历
CareerRadar：使用 FastAPI 开发异步任务 API，并为结果提供证据引用。
工作经历
3 年 Python 后端开发经验
教育经历
软件工程 本科
"""


def test_text_profile_has_stable_evidence_and_structured_fields():
    one = build_profile(TEXT, "profile_test")
    two = build_profile(TEXT, "profile_test")
    assert one.skills[:2]
    assert one.experience_years == 3
    assert one.education == "本科"
    assert [item.block_id for item in one.evidence] == [item.block_id for item in two.evidence]


def test_docx_extracts_text_and_validates_actual_format():
    document = Document()
    document.add_paragraph(TEXT)
    stream = BytesIO()
    document.save(stream)
    extracted = extract_resume_text(
        stream.getvalue(), "resume.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "CareerRadar" in extracted
    with pytest.raises(ResumeError, match="不一致"):
        extract_resume_text(stream.getvalue(), "resume.pdf", "application/pdf")


def test_scanned_pdf_empty_and_oversized_are_rejected():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(300, 300)
    writer.write(stream)
    with pytest.raises(ResumeError, match="OCR"):
        extract_resume_text(stream.getvalue(), "scan.pdf", "application/pdf")
    with pytest.raises(ResumeError, match="10 MB"):
        extract_resume_text(b"x" * (MAX_UPLOAD_BYTES + 1), "resume.pdf", "application/pdf")


def test_empty_text_and_forged_citation_fail():
    with pytest.raises(ResumeError, match="过短"):
        build_profile("Python")
    profile = build_profile(TEXT, "profile_test")
    forged = Evidence(source_type="resume", source_id="profile_test", block_id="made-up", quote="Python")
    with pytest.raises(ResumeError, match="引用校验失败"):
        validate_citations([forged], profile.evidence)
