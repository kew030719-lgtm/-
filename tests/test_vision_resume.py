from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter

from career_radar.config import Settings
from career_radar.resume import ResumeError
from career_radar.vision import MAX_VISION_PAGES, VisionResumeReader, render_pdf_pages
from career_radar.web import create_app


def settings(tmp_path, *, vision_model_name: str = "", api_key: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="text-model",
        vision_model_name=vision_model_name, api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def scanned_pdf(pages: int = 1) -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(595, 842)
    writer.write(stream)
    return stream.getvalue()


def test_pdf_pages_render_to_png_and_enforce_page_limit():
    pages = render_pdf_pages(scanned_pdf(2))
    assert len(pages) == 2
    assert all(page.startswith(b"\x89PNG") for page in pages)
    with pytest.raises(ResumeError, match=str(MAX_VISION_PAGES)):
        render_pdf_pages(scanned_pdf(MAX_VISION_PAGES + 1))


@pytest.mark.asyncio
async def test_scanned_pdf_requires_a_configured_vision_model(tmp_path):
    reader = VisionResumeReader(settings(tmp_path))
    with pytest.raises(ResumeError, match="视觉模型名称"):
        await reader.extract(scanned_pdf())


@pytest.mark.asyncio
async def test_resume_upload_falls_back_to_visual_ocr(tmp_path):
    app = create_app(settings(tmp_path, vision_model_name="vision-model"))
    calls = []

    async def fake_extract(data: bytes) -> str:
        calls.append(data)
        return """张三
技能
Python FastAPI SQL
项目经历
使用 FastAPI 开发 CareerRadar，并编写自动化测试。
教育经历
软件工程 本科"""

    app.state.vision_reader.extract = fake_extract
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/resumes",
                files={"file": ("scan.pdf", scanned_pdf(), "application/pdf")},
            )

    assert response.status_code == 200
    assert "Python" in response.json()["skills"]
    assert len(calls) == 1
