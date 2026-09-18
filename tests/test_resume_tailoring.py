import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from docx import Document

from career_radar.config import Settings
from career_radar.resume import ResumeError, build_profile, extract_candidate_contact
from career_radar.schemas import Evidence, JobSnapshot, ResumeBullet, ResumeDraftVersion, TailoringQuestion
from career_radar.sites import FetchResult
from career_radar.tailoring import (
    _fallback_draft, _public_profile, _questions, target_from_pasted, validate_draft,
)
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


def install_successful_tailor(app):
    app.state.agent.settings.api_key = "configured-for-test"

    async def fake_tailor(_profile, _target, _tailoring, _contact, fallback, **_kwargs):
        fallback.source = "langgraph"
        fallback.quality_report.relevance_score = 90
        fallback.quality_report.specificity_score = 90
        fallback.quality_report.structure_score = 90
        fallback.quality_report.conciseness_score = 90
        fallback.quality_report.passed = True
        return fallback

    app.state.agent.tailor_resume = fake_tailor


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
                install_successful_tailor(app)
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


def test_jd_requirements_create_questions_when_required_skills_are_empty(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.database.initialize()
    profile = build_profile(RESUME, "profile_jd_questions")
    target = target_from_pasted(
        profile.profile_id, "星图科技", "AI 产品经理",
        "负责建立 AI Agent 产品效果评测体系和评分标准，需要分析失败案例并推动改进。",
    )
    target.required_skills = []
    target.responsibilities = [
        "负责建立 AI Agent 产品效果评测体系和评分标准。",
        "具备数据分析和问题归因能力，能从失败案例提出验证假设。",
        "至少 1 年大模型或 AI Agent 产品相关经验。",
        "在这里可以接触优秀团队并获得成长机会。",
    ]

    questions, missing = _questions(profile, target, [])

    assert missing == []
    assert 1 <= len(questions) <= 5
    assert any("评测体系" in item.requirement or "问题归因" in item.requirement for item in questions)
    assert all("成长机会" not in item.requirement for item in questions)


def test_benefits_never_create_tailoring_questions(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.database.initialize()
    profile = build_profile(RESUME, "profile_benefit_questions")
    target = target_from_pasted(
        profile.profile_id, "星图科技", "AI 产品经理",
        "负责 AI Agent 产品评测体系建设，要求能够分析失败案例并推动产品持续改进。",
    )
    target.required_skills = [
        "交通补助", "节日福利", "免费班车", "团建聚餐", "零食下午茶",
        "法定节假日三薪", "节假日加班费", "企业年金", "保底工资", "意外险",
    ]
    target.responsibilities = ["负责 AI Agent 产品评测体系建设，能够分析失败案例并推动改进。"]

    questions, missing = _questions(profile, target, [])

    assert missing == []
    assert questions
    assert all(item.requirement not in target.required_skills for item in questions)
    assert any("评测体系" in item.requirement for item in questions)


def test_old_failed_tailoring_backfills_empty_questions(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.database.initialize()
    profile = build_profile(RESUME, "profile_old_empty_questions")
    profile.confirmed = True
    app.state.database.save_profile(profile)
    target = target_from_pasted(
        profile.profile_id, "星图科技", "AI 产品经理",
        "具备 AI 产品评测设计能力，能够建立评分标准并分析失败案例，推动产品持续改进。",
    )
    target.required_skills = []
    target.responsibilities = ["具备评测设计能力，能够建立评分标准并分析失败案例。"]
    app.state.database.save_target_job(target)
    tailoring = app.state.tailoring_service.create_tailoring(profile.profile_id, target.target_job_id)
    tailoring.questions = [TailoringQuestion(
        question_id="question_bad_benefit", requirement="交通补助",
        question="岗位要求交通补助，你是否使用过？",
    )]
    tailoring.status = "FAILED"
    tailoring.task_id = "task_old_failed"
    tailoring.error = "旧任务失败"
    app.state.database.save_tailoring(tailoring)

    restored = app.state.tailoring_service.restore_missing_questions(tailoring)

    assert restored.status == "COLLECTING"
    assert restored.task_id is None and restored.error is None
    assert restored.questions
    assert all(item.requirement != "交通补助" for item in restored.questions)


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


def test_resume_parser_keeps_projects_as_separate_evidence_units():
    profile = build_profile("""李同学
项目经历
校园招聘分析平台 2025.01-2025.04
使用 Python 和 FastAPI 开发岗位匹配接口
课程管理系统 2024.03-2024.06
使用 JavaScript 实现课程检索页面
教育经历
示例大学 2022.09-2026.06
计算机科学 本科
""")
    projects = [item for item in profile.resume_entries if item.kind == "project"]
    assert len(projects) == 2
    assert projects[0].heading.startswith("校园招聘分析平台")
    assert projects[1].heading.startswith("课程管理系统")
    assert set(projects[0].evidence_ids).isdisjoint(projects[1].evidence_ids)


def test_tailoring_evaluation_set_contains_ten_anonymized_graduate_cases():
    cases = json.loads((Path(__file__).parents[1] / "evaluation" / "tailoring_cases.json").read_text())
    assert len(cases) >= 10
    assert len({item["case_id"] for item in cases}) == len(cases)
    assert all(item["resume"] and item["job"]["requirements"] for item in cases)


def test_missing_model_fails_without_exposing_fallback_and_can_retry(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile, _, snapshot = await setup_profile_job(client, app)
                target_id = (await client.post("/api/target-jobs", json={
                    "profile_id": profile["profile_id"], "source_type": "snapshot",
                    "snapshot_id": snapshot.snapshot_id,
                })).json()["target_job_id"]
                tailoring = (await client.post("/api/resume-tailorings", json={
                    "profile_id": profile["profile_id"], "target_job_id": target_id,
                })).json()
                answers = {item["question_id"]: None for item in tailoring["questions"]}
                await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/answers", json={"answers": answers},
                )
                first = (await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/confirm",
                )).json()["task_id"]
                failed_task = await wait_task(client, first)
                failed = (await client.get(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}",
                )).json()["tailoring"]
                assert failed_task["status"] == "FAILED"
                assert failed["status"] == "FAILED" and failed["draft_id"] is None
                assert "未展示低质量整理版" in failed["error"]

                install_successful_tailor(app)
                ready = (await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/answers", json={"answers": answers},
                )).json()
                assert ready["status"] == "READY" and ready["task_id"] is None
                second = (await client.post(
                    f"/api/resume-tailorings/{tailoring['tailoring_id']}/confirm",
                )).json()["task_id"]
                assert second != first
                assert (await wait_task(client, second))["status"] == "SUCCEEDED"

    asyncio.run(run())


def test_model_tailoring_preserves_cited_entries_and_runs_quality_review(tmp_path):
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
        source_entry = next(item for item in public.resume_entries if item.kind == "skills")
        source = next(item for item in public.evidence if item.block_id in source_entry.evidence_ids)
        writer_calls = 0
        token_limits = {}

        async def fake_run(_prompt, _task_id, output_type, **kwargs):
            nonlocal writer_calls
            from career_radar.agent import (
                ResumePlanOutput,
                ResumeQualityReviewTextOutput,
                ResumeWritingOutput,
            )
            token_limits.setdefault(output_type, []).append(kwargs.get("max_tokens"))
            if output_type is ResumePlanOutput:
                assert kwargs.get("tool_context") is None
                assert "read_tailoring_context" not in _prompt
                assert target.title in _prompt and source.block_id in _prompt
                return ResumePlanOutput.model_validate({
                    "requirements": [{"requirement": "Python", "importance": 100,
                                      "match": "strong", "evidence_ids": [source.block_id]}],
                    "selected_entry_ids": [source_entry.entry_id], "strategy": "突出后端技能",
                }), [], "langgraph"
            if output_type is ResumeQualityReviewTextOutput:
                return ResumeQualityReviewTextOutput(
                    text="SCORES|90|88|92|90\nPASS|true",
                ), [], "langgraph"
            assert output_type is ResumeWritingOutput
            writer_calls += 1
            lines = [
                f"HEADLINE|{target.title}",
                f"SUMMARY|{source.block_id}|{source.quote}",
                f"SKILL|{source.block_id}|Python",
            ]
            lines.extend(
                f"ENTRY|{source_entry.entry_id}|{source.block_id}|{source.quote}（要点{label}）"
                for label in "甲乙丙丁戊"
            )
            return ResumeWritingOutput(text="\n".join(lines)), [], "langgraph"

        app.state.agent._run_structured = fake_run
        validation_calls = 0

        def validate_after_feedback(_version):
            nonlocal validation_calls
            validation_calls += 1
            if validation_calls < 3:
                raise ResumeError("需要消除跨经历事实")

        version = await app.state.agent.tailor_resume(
            public, target, tailoring, contact, fallback, validator=validate_after_feedback,
        )
        assert version.source == "langgraph"
        assert version.summary[0].provenance == "uploaded_resume"
        assert version.skills[0].text == "Python"
        assert version.sections[0].entries[0].entry_id == source_entry.entry_id
        assert version.sections[0].entries[0].evidence_ids == [source.block_id]
        assert len(version.sections[0].entries[0].bullets) == 4
        assert version.quality_report.passed is True
        assert writer_calls == 3 and validation_calls == 3
        from career_radar.agent import (
            ResumePlanOutput,
            ResumeQualityReviewTextOutput,
            ResumeWritingOutput,
            TAILOR_PLAN_MAX_TOKENS,
            TAILOR_REVIEW_MAX_TOKENS,
            TAILOR_WRITE_MAX_TOKENS,
        )
        assert token_limits[ResumePlanOutput] == [TAILOR_PLAN_MAX_TOKENS]
        assert token_limits[ResumeWritingOutput] == [TAILOR_WRITE_MAX_TOKENS] * 3
        assert token_limits[ResumeQualityReviewTextOutput] == [TAILOR_REVIEW_MAX_TOKENS]

    asyncio.run(run())


def test_model_cross_entry_citation_is_retried_without_merging_facts(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        app.state.database.initialize()
        profile = build_profile(RESUME, "profile_cross_entry_retry")
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
        public = _public_profile(profile, contact)
        fallback = _fallback_draft(public, contact, target, tailoring, "draft_cross_entry_retry")
        project_entry = next(item for item in public.resume_entries if item.kind == "project")
        project_evidence = project_entry.evidence_ids[0]
        project_quote = next(item.quote for item in public.evidence if item.block_id == project_evidence)
        other_entry = next(item for item in public.resume_entries if item.entry_id != project_entry.entry_id)
        other_evidence = other_entry.evidence_ids[0]
        writer_calls = 0

        async def fake_run(_prompt, _task_id, output_type, **_kwargs):
            nonlocal writer_calls
            from career_radar.agent import ResumePlanOutput, ResumeQualityReviewTextOutput, ResumeWritingOutput
            if output_type is ResumePlanOutput:
                return ResumePlanOutput.model_validate({
                    "requirements": [], "selected_entry_ids": [project_entry.entry_id],
                    "strategy": "只突出股票数据采集项目",
                }), [], "langgraph"
            if output_type is ResumeQualityReviewTextOutput:
                return ResumeQualityReviewTextOutput(text="SCORES|90|90|90|90\nPASS|true"), [], "langgraph"
            writer_calls += 1
            cited = f"{project_evidence},{other_evidence}" if writer_calls == 1 else project_evidence
            return ResumeWritingOutput(text="\n".join([
                f"HEADLINE|{target.title}",
                f"SUMMARY|{project_evidence}|{project_quote}",
                f"ENTRY|{project_entry.entry_id}|{cited}|{project_quote}",
            ])), [], "langgraph"

        app.state.agent._run_structured = fake_run
        version = await app.state.agent.tailor_resume(
            public, target, tailoring, contact, fallback,
            validator=lambda draft: validate_draft(draft, profile),
        )
        assert writer_calls == 2
        entry = version.sections[0].entries[0]
        assert entry.entry_id == project_entry.entry_id
        assert entry.bullets[0].evidence_ids == [project_evidence]

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

        failed_draft = {}

        async def fake_tailor(_profile, _target, active, active_contact, fallback, **_kwargs):
            failed_draft["id"] = fallback.draft_id
            fallback.source = "langgraph"
            fallback.summary[0].text = "虚构将系统性能提升 99%"
            return fallback

        app.state.agent.tailor_resume = fake_tailor
        with pytest.raises(ResumeError, match="未经证实的数字"):
            await app.state.tailoring_service.generate(tailoring.tailoring_id)
        failed = app.state.database.get_tailoring(tailoring.tailoring_id)
        assert failed.status == "FAILED_VALIDATION"
        assert failed.draft_id is None, "an invalid first draft must not be displayed as the current resume"
        version = app.state.database.get_latest_draft(failed_draft["id"])
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
                install_successful_tailor(app)
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
                install_successful_tailor(app)
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
