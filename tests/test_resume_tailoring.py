import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from docx import Document

from career_radar.config import Settings
from career_radar.sites import FetchResult
from career_radar.resume import ResumeError, build_profile, extract_candidate_contact
from career_radar.schemas import Evidence, JobSnapshot, ResumeBullet, ResumeDraftVersion
from career_radar.tailoring import _fallback_draft, _public_profile, validate_draft
from career_radar.web import create_app


RESUME = """张三
电话：13800138000 邮箱：zhangsan@example.com 现居：上海
专业技能
Python FastAPI SQL Docker
项目经历
实现简历分析 Agent，使用 FastAPI 提供异步 API，并编写自动化测试。
工作经历
2 年 Python 后端开发经验
教育经历
计算机科学 本科
"""


def settings(tmp_path: Path, api_key: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


async def wait_task(client: httpx.AsyncClient, task_id: str):
    for _ in range(100):
        task = (await client.get(f"/api/tasks/{task_id}")).json()
        if task["status"] not in {"QUEUED", "RUNNING"}:
            return task
        await asyncio.sleep(.02)
    raise AssertionError("task did not finish")


async def setup_profile_job(client, app):
    profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
    profile_id = profile["profile_id"]
    run_id = "run_tailoring"
    snapshot_id = "snap_tailoring"
    app.state.database.create_task(run_id, "discovery", {"profile_id": profile_id, "mode": "browser"})
    snapshot = JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id="tailoring1",
        canonical_url="https://www.zhipin.com/job_detail/tailoring1.html",
        title="Python 后端工程师", company="星图科技", city="上海", salary="20-30K",
        responsibilities=["负责 FastAPI 服务、Redis 缓存和自动化测试"],
        required_skills=["Python", "FastAPI", "Redis"], content_hash="tailoring-hash",
        fetched_at=datetime.now(UTC).isoformat(), transport="manual",
        cleaned_text="Python 后端工程师 星图科技 负责 FastAPI 服务、Redis 缓存和自动化测试",
        blocks=[Evidence(
            source_type="job", source_id=snapshot_id, block_id="job-tailoring-1",
            quote="负责 FastAPI 服务、Redis 缓存和自动化测试", section="职位原文",
        )],
    )
    app.state.database.save_snapshot("boss:tailoring1", run_id, snapshot)
    return profile, run_id, snapshot


def test_target_tailoring_questions_confirm_and_version_history(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile, _, snapshot = await setup_profile_job(client, app)
                profile_id = profile["profile_id"]
                assert all("13800138000" not in item["quote"] for item in profile["evidence"])
                contact = app.state.database.get_contact(profile_id)
                assert contact.name == "张三" and contact.phone == "13800138000"
                assert contact.email == "zhangsan@example.com"

                target_response = await client.post("/api/target-jobs", json={
                    "profile_id": profile_id, "source_type": "snapshot",
                    "snapshot_id": snapshot.snapshot_id,
                })
                assert target_response.status_code == 200
                target_id = target_response.json()["target_job_id"]
                tailoring = (await client.post("/api/resume-tailorings", json={
                    "profile_id": profile_id, "target_job_id": target_id,
                })).json()
                redis_question = next(item for item in tailoring["questions"] if item["requirement"] == "Redis")
                answers = {item["question_id"]: None for item in tailoring["questions"]}
                answers[redis_question["question_id"]] = "在缓存模块中使用 Redis，将接口响应时间降低 30%。"
                ready = (await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/answers",
                    json={"answers": answers},
                )).json()
                assert ready["status"] == "READY"
                assert app.state.database.list_supplemental_evidence(profile_id) == []

                queued = (await client.post(f"/api/resume-tailorings/{tailoring['tailoring_id']}/confirm")).json()
                repeated = (await client.post(f"/api/resume-tailorings/{tailoring['tailoring_id']}/confirm")).json()
                assert repeated["task_id"] == queued["task_id"]
                task = await wait_task(client, queued["task_id"])
                assert task["status"] == "SUCCEEDED"
                completed = (await client.get(f"/api/resume-tailorings/{tailoring['tailoring_id']}")).json()["tailoring"]
                assert completed["status"] == "SUCCEEDED" and completed["draft_id"]
                assert len(app.state.database.list_supplemental_evidence(profile_id)) == 1

                bundle = (await client.get(f"/api/resume-drafts/{completed['draft_id']}")).json()
                original = bundle["current"]
                assert original["contact"]["phone"] == "13800138000"
                assert all(item["evidence_ids"] for item in original["summary"])
                with app.state.database.connect() as connection:
                    stored = connection.execute(
                        "SELECT payload FROM resume_draft_versions WHERE id=?", (original["version_id"],),
                    ).fetchone()["payload"]
                assert "13800138000" not in stored and "zhangsan@example.com" not in stored
                edited = ResumeDraftVersion.model_validate(original)
                edited.summary[0].text = "用户确认后的个人概述"
                edited.contact.profile_id = "profile_other"
                saved = (await client.post(
                    f"/api/resume-drafts/{completed['draft_id']}/versions",
                    json=edited.model_dump(),
                )).json()
                assert saved["version"] == 2 and saved["source"] == "user_edit"
                assert saved["contact"]["profile_id"] == profile_id
                assert app.state.database.get_contact("profile_other").email == ""
                versions = (await client.get(f"/api/resume-drafts/{completed['draft_id']}")).json()["versions"]
                assert [item["version"] for item in versions] == [2, 1]
    asyncio.run(run())


def test_pasted_target_is_profile_scoped_and_link_is_restricted(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = (await client.post("/api/resumes", json={"text": RESUME})).json()
                second = (await client.post("/api/resumes", json={"text": RESUME + "\n补充项目：数据采集"})).json()
                target = (await client.post("/api/target-jobs", json={
                    "profile_id": first["profile_id"], "source_type": "pasted",
                    "company": "目标公司", "title": "Agent 工程师",
                    "content": "负责 Python Agent 应用开发，需要 FastAPI、RAG、Docker 和可靠的自动化测试。",
                })).json()
                mismatch = await client.post("/api/resume-tailorings", json={
                    "profile_id": second["profile_id"], "target_job_id": target["target_job_id"],
                })
                assert mismatch.status_code == 409
                forbidden = await client.post("/api/target-jobs", json={
                    "profile_id": first["profile_id"], "source_type": "external_url",
                    "url": "http://127.0.0.1/private",
                })
                assert forbidden.status_code == 422
    asyncio.run(run())


def test_boss_link_target_uses_existing_safe_transport(tmp_path, monkeypatch):
    fixture = (Path(__file__).parent / "fixtures" / "job.html").read_text()

    async def fake_fetch(_self, _url, _expects):
        return FetchResult(fixture, "http")

    monkeypatch.setattr("career_radar.sites.transport.Transport.fetch", fake_fetch)

    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                queued = (await client.post("/api/target-jobs", json={
                    "profile_id": profile["profile_id"], "source_type": "external_url",
                    "url": "https://www.zhipin.com/job_detail/safe-link.html",
                })).json()
                task = await wait_task(client, queued["task_id"])
                assert task["status"] == "SUCCEEDED"
                target = (await client.get(f"/api/target-jobs/{queued['target_job_id']}")).json()
                assert target["source_type"] == "external_url"
                assert target["site"] == "boss"
                assert target["profile_id"] == profile["profile_id"]
    asyncio.run(run())


def test_forged_generated_bullet_fails_validation(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.database.initialize()
    profile = asyncio.run(_create_profile_for_validation(app))
    evidence = profile.evidence[0]
    version = ResumeDraftVersion(
        version_id="version_forged", draft_id="draft_forged", version=1,
        tailoring_id="tailor_forged", profile_id=profile.profile_id, target_job_id="target_forged",
        headline="Python 工程师", summary=[ResumeBullet(
            bullet_id="bullet_forged", text="将系统性能提升 99%", evidence_ids=[evidence.block_id],
            provenance="uploaded_resume",
        )], contact=app.state.database.get_contact(profile.profile_id), created_at=datetime.now(UTC).isoformat(),
    )
    with pytest.raises(ResumeError, match="未经证实的数字"):
        validate_draft(version, profile)


def test_generic_heading_is_not_used_as_name_and_template_notice_is_filtered():
    profile = build_profile("""基本信息
邮箱：candidate@example.com
温馨提示：以上内容为简历模板，个人信息为虚构示例
技能
Python FastAPI 后端开发
项目经历
使用 Python 编写接口服务并完成自动化测试
""")
    contact = extract_candidate_contact(profile)
    assert contact.name == ""
    assert contact.email == "candidate@example.com"


def test_model_tailoring_normalizes_fields_and_flattens_uncited_entries(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        app.state.database.initialize()
        profile = build_profile(RESUME, "profile_model_tailor")
        contact = extract_candidate_contact(profile)
        profile.confirmed = True
        app.state.database.save_profile(profile)
        app.state.database.save_contact(contact)
        target = (await _target_for_model_test(app, profile.profile_id))
        tailoring = app.state.tailoring_service.create_tailoring(profile.profile_id, target.target_job_id)
        if tailoring.questions:
            tailoring = app.state.tailoring_service.save_answers(
                tailoring.tailoring_id, {item.question_id: None for item in tailoring.questions},
            )
        public = _public_profile(profile, contact)
        fallback = _fallback_draft(public, contact, target, tailoring, "draft_model_tailor")
        source = public.evidence[0]

        async def fake_run(*_args, **_kwargs):
            from career_radar.agent import ResumeDraftOutput
            return ResumeDraftOutput.model_validate({
                    "headline": target.title,
                    "summary": [{"text": source.quote, "evidence_ids": [source.block_id], "provenance": "resume"}],
                    "skills": [{"name": "Python", "evidence_ids": [source.block_id], "provenance": "resume"}],
                    "sections": [{"title": "经历", "entries": [{
                        "heading": "模型虚构公司", "date_range": "2099年",
                        "bullets": [{"text": source.quote, "evidence_ids": [source.block_id], "provenance": "resume"}],
                    }]}],
                }), ["career_radar__read_tailoring_context"], "langgraph"

        app.state.agent._run_structured = fake_run
        version = await app.state.agent.tailor_resume(public, target, tailoring, contact, fallback)
        assert version.source == "langgraph"
        assert version.summary[0].provenance == "uploaded_resume"
        assert version.skills[0].text == "Python"
        assert version.sections[0].entries == []
        assert version.sections[0].bullets

    asyncio.run(run())


def test_failed_model_fact_is_saved_as_failed_validation_version(tmp_path):
    async def run():
        app = create_app(settings(tmp_path, api_key="configured"))
        app.state.database.initialize()
        profile = build_profile(RESUME, "profile_failed_tailor")
        contact = extract_candidate_contact(profile)
        profile.confirmed = True
        app.state.database.save_profile(profile)
        app.state.database.save_contact(contact)
        target = await _target_for_model_test(app, profile.profile_id)
        tailoring = app.state.tailoring_service.create_tailoring(profile.profile_id, target.target_job_id)
        if tailoring.questions:
            tailoring = app.state.tailoring_service.save_answers(
                tailoring.tailoring_id, {item.question_id: None for item in tailoring.questions},
            )

        async def fake_tailor(_profile, _target, active, active_contact, fallback):
            fallback.source = "langgraph"
            fallback.summary[0].text = "虚构将系统性能提升 99%"
            return fallback

        app.state.agent.tailor_resume = fake_tailor
        with pytest.raises(ResumeError, match="未经证实的数字"):
            await app.state.tailoring_service.generate(tailoring.tailoring_id)
        failed = app.state.database.get_tailoring(tailoring.tailoring_id)
        assert failed.status == "FAILED_VALIDATION"
        version = app.state.database.get_latest_draft(failed.draft_id)
        assert version.validation_status == "FAILED_VALIDATION"

    asyncio.run(run())


async def _target_for_model_test(app, profile_id):
    from career_radar.tailoring import target_from_pasted
    target = target_from_pasted(
        profile_id, "星图科技", "Python 后端工程师",
        "负责 Python FastAPI 接口服务开发，需要 SQL、Docker 和自动化测试能力。",
    )
    return app.state.database.save_target_job(target)


async def _create_profile_for_validation(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            value = (await client.post("/api/resumes", json={"text": RESUME})).json()
            return app.state.database.get_profile(value["profile_id"])


def test_docx_templates_are_readable(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile, _, snapshot = await setup_profile_job(client, app)
                target = (await client.post("/api/target-jobs", json={
                    "profile_id": profile["profile_id"], "source_type": "snapshot", "snapshot_id": snapshot.snapshot_id,
                })).json()["target_job"]
                tailoring = app.state.tailoring_service.create_tailoring(profile["profile_id"], target["target_job_id"])
                answers = {item.question_id: None for item in tailoring.questions}
                app.state.tailoring_service.save_answers(tailoring.tailoring_id, answers)
                version = await app.state.tailoring_service.generate(tailoring.tailoring_id)
                for template in ("technical", "business"):
                    path = tmp_path / f"resume-{template}.docx"
                    app.state.tailoring_service.render_docx(
                        version, app.state.database.get_target_job(target["target_job_id"]), template, path,
                    )
                    document = Document(path)
                    text = "\n".join(item.text for item in document.paragraphs)
                    assert "Python 后端工程师" in text and "FastAPI" in text
    asyncio.run(run())


def test_export_api_creates_two_version_bound_artifacts(tmp_path, monkeypatch):
    async def fake_pdf(_html, output):
        from pypdf import PdfWriter
        output.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with output.open("wb") as stream:
            writer.write(stream)

    async def run():
        app = create_app(settings(tmp_path))
        monkeypatch.setattr(app.state.tailoring_service, "render_pdf", fake_pdf)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile, _, snapshot = await setup_profile_job(client, app)
                target_id = (await client.post("/api/target-jobs", json={
                    "profile_id": profile["profile_id"], "source_type": "snapshot",
                    "snapshot_id": snapshot.snapshot_id,
                })).json()["target_job_id"]
                tailoring = app.state.tailoring_service.create_tailoring(profile["profile_id"], target_id)
                app.state.tailoring_service.save_answers(
                    tailoring.tailoring_id, {item.question_id: None for item in tailoring.questions},
                )
                version = await app.state.tailoring_service.generate(tailoring.tailoring_id)
                queued = (await client.post(f"/api/resume-drafts/{version.draft_id}/exports", json={
                    "templates": ["technical", "business"],
                })).json()
                task = await wait_task(client, queued["task_id"])
                assert task["status"] == "SUCCEEDED"
                assert len(queued["exports"]) == 2
                for item in queued["exports"]:
                    export = (await client.get(f"/api/resume-exports/{item['export_id']}")).json()
                    assert export["version_id"] == version.version_id
                    assert Path(export["docx_path"]).is_file() and Path(export["pdf_path"]).is_file()
    asyncio.run(run())
