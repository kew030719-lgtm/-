import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from career_radar.config import Settings
from career_radar.web import create_app
from career_radar.project_upload import ProjectUploadError, analyze_project_upload


def test_stock_project_upload_is_analyzed_without_claiming_unverified_results():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "stock-main/README.md",
            "股票信息数据采集\n项目地址：https://github.com/kew030719-lgtm/stock\n",
        )
        archive.writestr(
            "stock-main/fetch.py",
            "import requests\nimport pandas as pd\nfrom bs4 import BeautifulSoup\n",
        )
        archive.writestr("stock-main/requirements.txt", "requests\npandas\nbeautifulsoup4\n")
    analysis = analyze_project_upload(
        buffer.getvalue(), "stock-main.zip", "profile_1", "tailor_1",
    )
    assert analysis.project_name == "股票信息数据爬虫"
    assert analysis.status == "PENDING"
    assert {"Python", "Requests", "Pandas", "BeautifulSoup"}.issubset(analysis.technologies)
    assert any("数据请求" in item for item in analysis.findings)
    assert analysis.project_urls == ["https://github.com/kew030719-lgtm/stock"]
    assert "项目地址" in analysis.evidence_quote
    assert "需用户确认" in analysis.evidence_quote


def test_project_upload_rejects_unsafe_zip_paths():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.py", "print('no')")
    with pytest.raises(ProjectUploadError):
        analyze_project_upload(buffer.getvalue(), "project.zip", "profile_1", "tailor_1")


def test_project_upload_confirmation_becomes_project_evidence(tmp_path: Path):
    async def run():
        settings = Settings(
            data_dir=tmp_path, database_path=tmp_path / "test.db",
            model_base_url="https://example.invalid/v1", model_name="test",
            api_key="", crawl_delay_seconds=0, crawl_max_jobs=3,
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                profile_response = await client.post(
                    "/api/resumes", json={
                        "text": "张三\n电话：13800138000 邮箱：zhangsan@example.com\n"
                        "专业技能\nPython\n项目经历\n原有项目\n使用 Python 开发。",
                    },
                )
                assert profile_response.status_code == 200, profile_response.text
                profile = profile_response.json()
                target_response = await client.post("/api/target-jobs", json={
                    "profile_id": profile["profile_id"], "source_type": "pasted",
                    "company": "星图科技", "title": "Python 工程师",
                    "content": "负责 Python 数据处理和接口开发，维护数据请求、解析、清洗与保存流程；"
                    "要求具备 Python、接口开发和自动化测试经验，能够独立定位并修复线上问题。",
                })
                assert target_response.status_code == 200, target_response.text
                target = target_response.json()
                tailoring = (await client.post("/api/resume-tailorings", json={
                    "profile_id": profile["profile_id"],
                    "target_job_id": target["target_job_id"],
                })).json()
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("stock-main/fetch.py", "import requests\n")
                    archive.writestr(
                        "stock-main/README.md",
                        "股票信息数据采集\n项目地址：https://github.com/kew030719-lgtm/stock\n",
                    )
                uploaded = await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/project-uploads",
                    files={"file": ("stock-main.zip", buffer.getvalue(), "application/zip")},
                )
                assert uploaded.status_code == 200
                analysis = uploaded.json()
                assert analysis["status"] == "PENDING"
                confirmed = await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}"
                    f"/project-uploads/{analysis['upload_id']}/confirm",
                )
                assert confirmed.status_code == 200
                assert confirmed.json()["analysis"]["status"] == "CONFIRMED"
                stored_profile = app.state.database.get_profile(profile["profile_id"])
                projects = [
                    item for item in stored_profile.resume_entries if item.kind == "project"
                ]
                assert any(item.heading == "股票信息数据爬虫" for item in projects)
                project = next(item for item in projects if item.heading == "股票信息数据爬虫")
                evidence = {item.block_id: item for item in stored_profile.evidence}
                assert project.evidence_ids
                assert evidence[project.evidence_ids[0]].section == "项目"
                assert evidence[project.evidence_ids[0]].provenance == "user_confirmed"
                repeated = await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}"
                    f"/project-uploads/{analysis['upload_id']}/confirm",
                )
                assert repeated.status_code == 200
                assert len([
                    item for item in app.state.database.get_profile(profile["profile_id"]).resume_entries
                    if item.heading == "股票信息数据爬虫"
                ]) == 1

    asyncio.run(run())
