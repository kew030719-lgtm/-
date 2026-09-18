from datetime import UTC, datetime
from pathlib import Path

from docx import Document
from pypdf import PdfWriter

from career_radar.resume_qa import inspect_export
from career_radar.schemas import (
    CandidateContact,
    CandidateProfile,
    Evidence,
    JobSnapshot,
    ResumeDraftVersion,
    ResumeSourceEntry,
    TargetJob,
)
from career_radar.tailoring import _public_profile, supplement_entry_metadata


def _version(target: TargetJob) -> ResumeDraftVersion:
    return ResumeDraftVersion(
        version_id="version_1", draft_id="draft_1", version=1, tailoring_id="tailor_1",
        profile_id=target.profile_id, target_job_id=target.target_job_id,
        headline=target.title, contact=CandidateContact(
            profile_id=target.profile_id, name="张三", phone="13800138000",
            email="zhangsan@example.com",
        ), created_at=datetime.now(UTC).isoformat(),
    )


def test_confirmed_project_answer_gets_a_project_heading():
    answer = (
        "使用 Python 编写股票信息数据采集程序，负责数据请求、解析、清洗与保存，"
        "项目代码仓库 https://github.com/kew030719-lgtm/stock"
    )
    assert supplement_entry_metadata(answer) == ("project", "股票信息数据采集程序")

    profile = CandidateProfile(
        profile_id="profile_1",
        evidence=[Evidence(
            source_type="resume", source_id="profile_1", block_id="supplement_1",
            quote=answer, section="用户补充", provenance="user_confirmed",
        )],
        resume_entries=[ResumeSourceEntry(
            entry_id="source-entry-1", kind="other", heading="用户确认的补充经历",
            evidence_ids=["supplement_1"], original_bullets=[answer],
        )],
    )
    public = _public_profile(profile, CandidateContact(profile_id="profile_1"))
    assert public.resume_entries[0].kind == "project"
    assert public.resume_entries[0].heading == "股票信息数据采集程序"
    assert supplement_entry_metadata("我有过爬虫经验，写过股票信息数据爬虫，项目仓库地址：https://example.com") == (
        "project", "股票信息数据爬虫",
    )


def test_export_qa_reports_page_count_and_ats_state(tmp_path: Path):
    target = TargetJob(
        target_job_id="target_1", profile_id="profile_1", source_type="snapshot",
        company="星图科技", title="Python 后端工程师", cleaned_text="Python 后端工程师",
        created_at=datetime.now(UTC).isoformat(),
    )
    version = _version(target)
    docx_path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("张三")
    document.add_paragraph("13800138000 · zhangsan@example.com")
    document.add_paragraph("星图科技 · Python 后端工程师")
    document.save(docx_path)
    pdf_path = tmp_path / "resume.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf_path)

    report = inspect_export(version, target, "technical", docx_path, pdf_path)
    assert report.page_count == 1
    assert report.ats_extractable is False
    assert report.status == "WARN"
    assert any(item.id == "pdf_text_layer" and item.status == "WARN" for item in report.checks)


def test_export_qa_blocks_more_than_two_pages(tmp_path: Path):
    target = TargetJob(
        target_job_id="target_1", profile_id="profile_1", source_type="snapshot",
        company="星图科技", title="Python 后端工程师", cleaned_text="Python 后端工程师",
        created_at=datetime.now(UTC).isoformat(),
    )
    version = _version(target)
    docx_path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("张三")
    document.add_paragraph("星图科技 · Python 后端工程师")
    document.save(docx_path)
    pdf_path = tmp_path / "resume.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=595, height=842)
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    report = inspect_export(version, target, "business", docx_path, pdf_path)
    assert report.status == "FAIL"
    assert any(item.id == "page_count" and item.status == "FAIL" for item in report.checks)
