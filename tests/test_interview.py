"""Per-posting interview preparation.

The interesting property is grounding: a question may only name something it can
point at, so a fabricated question fails the task instead of reaching the user.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from career_radar.agent import AgentService
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.interview import InterviewService, fallback_interview, validate_interview
from career_radar.resume import ResumeError, build_profile
from career_radar.schemas import Evidence, InterviewPrep, InterviewQuestion, JobSnapshot
from career_radar.web import create_app


RESUME = """技能
Python FastAPI SQL Docker Agent RAG
项目经历
实现简历分析 Agent，使用 FastAPI 提供异步 API，并输出原文证据引用。
工作经历
2 年 Python 后端开发经验
教育经历
计算机科学 本科
"""

JOB_BLOCK = Evidence(
    source_type="job", source_id="snap_iv", block_id="job-iv-01",
    quote="负责 Kubernetes 集群运维与 Python 服务开发", section="职位原文",
)


def settings(tmp_path, api_key: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def snapshot_for(snapshot_id: str = "snap_iv", title: str = "Python 后端工程师") -> JobSnapshot:
    return JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id=snapshot_id, site="boss",
        canonical_url=f"https://www.zhipin.com/job_detail/{snapshot_id}.html", title=title,
        company="星图科技", city="北京", salary="20-30K",
        responsibilities=["负责 Kubernetes 集群运维与 Python 服务开发"],
        required_skills=["Python", "Kubernetes"],
        content_hash=f"hash-{snapshot_id}", fetched_at=datetime.now(UTC).isoformat(),
        transport="manual", blocks=[JOB_BLOCK.model_copy(update={"source_id": snapshot_id})],
    )


def prepared(tmp_path, *, api_key: str = ""):
    database = Database(tmp_path / "test.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_iv")
    profile.confirmed = True
    database.save_profile(profile)
    run_id = "run_iv"
    database.create_task(run_id, "discovery", {"profile_id": profile.profile_id, "mode": "browser"})
    snapshot = snapshot_for()
    database.save_snapshot("boss:iv1", run_id, snapshot)
    service = InterviewService(database, AgentService(settings(tmp_path, api_key=api_key)))
    return database, service, profile, snapshot


def test_fallback_preparation_is_grounded_and_per_posting(tmp_path):
    """With no model configured the deterministic path must still be citable."""
    database, service, profile, snapshot = prepared(tmp_path)
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))

    assert result.status == "SUCCEEDED"
    assert result.source == "fallback"
    assert result.snapshot_id == snapshot.snapshot_id
    assert len(result.days) == 7
    assert result.questions

    known = {block.block_id for block in snapshot.blocks} | {item.block_id for item in profile.evidence}
    for question in result.questions:
        assert question.evidence_ids, question.question
        assert set(question.evidence_ids) <= known, question.question
        # The backend fills quotes; the model/fixture never author them.
        assert question.citations, question.question
        assert all(citation.quote for citation in question.citations)


def test_gap_question_is_grounded_in_the_posting_not_the_candidate(tmp_path):
    """A question about a skill the candidate lacks cites the job requirement."""
    database, service, profile, snapshot = prepared(tmp_path)
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))

    gaps = [item for item in result.questions if item.category == "能力缺口"]
    assert gaps, "expected at least one gap question"
    assert all(item.evidence_ids == ["job-iv-01"] for item in gaps)
    assert any("Kubernetes" in item.question for item in gaps)
    # It is named as a gap, not claimed as an existing ability.
    assert "kubernetes" in {skill.lower() for skill in result.missing_skills}


def test_question_naming_an_ungrounded_skill_fails_validation(tmp_path):
    """The candidate never mentions Kubernetes as a skill they hold."""
    database, service, profile, snapshot = prepared(tmp_path)
    prep = InterviewPrep(
        prep_id="prep_x", profile_id=profile.profile_id, snapshot_id=snapshot.snapshot_id,
        company="星图科技", title="Python 后端工程师", status="RUNNING",
        created_at="now", updated_at="now", missing_skills=[],
        questions=[InterviewQuestion(
            question_id="q1", question="请说明你如何用 Kubernetes 做多集群灰度发布。",
            category="技术深挖", evidence_ids=[profile.evidence[0].block_id],
        )],
    )
    with pytest.raises(ResumeError, match="未经证实的技能"):
        validate_interview(prep, profile, snapshot)


def test_unknown_evidence_id_fails_validation(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    prep = InterviewPrep(
        prep_id="prep_y", profile_id=profile.profile_id, snapshot_id=snapshot.snapshot_id,
        status="RUNNING", created_at="now", updated_at="now",
        questions=[InterviewQuestion(
            question_id="q1", question="你做过什么项目？", category="项目经历",
            evidence_ids=["made-up-block"],
        )],
    )
    with pytest.raises(ResumeError, match="引用了无效证据"):
        validate_interview(prep, profile, snapshot)


def test_a_question_cannot_even_be_constructed_without_evidence(tmp_path):
    """The schema enforces this, so validate_interview never has to see one."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InterviewQuestion(
            question_id="q1", question="随便问一个", category="行为面", evidence_ids=[],
        )


def test_a_forged_model_answer_is_replaced_never_shown(tmp_path):
    """The invariant: ungrounded content must not reach the user.

    React is neither required by the posting (so it is not an exempted gap) nor
    present in the candidate's evidence, so naming it asserts an ability the
    resume does not support. The answer is replaced by the grounded deterministic
    plan and the reason recorded — rather than failing the task, which against an
    endpoint that reliably mangles this schema would leave the feature empty.
    """
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def forged(_profile, _snapshot, _missing):
        from career_radar.agent import InterviewPrepOutput, InterviewQuestionOutput

        return InterviewPrepOutput(questions=[InterviewQuestionOutput(
            question="请说明你用 React 做过的前端项目。", category="技术深挖",
            evidence_ids=[profile.evidence[0].block_id],
        )]), "langgraph"

    service.agent.prepare_interview = forged
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))
    stored = database.get_interview_prep(prep.prep_id)

    assert result.status == "SUCCEEDED"
    assert stored.source == "fallback"
    assert "证据校验" in stored.note and "React" in stored.note
    # The forgery is quoted in the note and appears nowhere else.
    assert not any("React" in question.question for question in stored.questions)
    assert stored.questions and all(question.citations for question in stored.questions)


def test_a_model_question_without_evidence_is_dropped_not_fatal(tmp_path):
    """InterviewQuestion requires an id, so a malformed one would raise a pydantic
    ValidationError — not a ResumeError, so it would bypass the degrade path."""
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def ungrounded(_profile, _snapshot, _missing):
        from career_radar.agent import InterviewPrepOutput, InterviewQuestionOutput

        return InterviewPrepOutput(questions=[
            InterviewQuestionOutput(question="没有证据的问题", category="技术深挖", evidence_ids=[]),
            InterviewQuestionOutput(question="   ", category="技术深挖", evidence_ids=["x"]),
        ]), "langgraph"

    service.agent.prepare_interview = ungrounded
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))

    assert result.status == "SUCCEEDED", "a malformed model question must not fail the run"
    assert result.source == "fallback"
    assert result.questions


def test_an_empty_model_answer_degrades_and_says_so(tmp_path):
    """An empty reply holds nothing to conceal, so it is replaced rather than
    failing — unlike ungrounded content, where substituting would hide it."""
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def empty(_profile, _snapshot, _missing):
        from career_radar.agent import InterviewPrepOutput

        return InterviewPrepOutput(), "langgraph"

    service.agent.prepare_interview = empty
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))

    assert result.status == "SUCCEEDED"
    assert result.source == "fallback"
    assert result.questions, "the deterministic plan must still be delivered"
    assert "没有返回可用的面试问题" in result.note


def test_a_crashing_model_call_degrades_with_the_reason_recorded(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def boom(_profile, _snapshot, _missing):
        raise RuntimeError("model unavailable")

    service.agent.prepare_interview = boom
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))
    assert result.status == "SUCCEEDED" and result.source == "fallback"
    assert "模型调用失败" in result.note


def test_a_failed_model_call_still_falls_back_to_the_deterministic_plan(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def boom(_profile, _snapshot, _missing):
        raise RuntimeError("model unavailable")

    service.agent.prepare_interview = boom
    prep = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.generate(prep.prep_id))
    assert result.status == "SUCCEEDED" and result.source == "fallback"
    assert result.questions


def test_two_postings_get_independent_preparations(tmp_path):
    """This is what makes the feature per-job rather than one plan for the top 3."""
    database, service, profile, _ = prepared(tmp_path)
    second = snapshot_for("snap_iv2", title="数据工程师")
    database.save_snapshot("boss:iv2", "run_iv", second)

    first_prep = asyncio.run(service.generate(service.create(profile.profile_id, "snap_iv").prep_id))
    second_prep = asyncio.run(service.generate(service.create(profile.profile_id, "snap_iv2").prep_id))

    assert first_prep.prep_id != second_prep.prep_id
    assert first_prep.snapshot_id == "snap_iv"
    assert second_prep.snapshot_id == "snap_iv2"
    assert first_prep.title == "Python 后端工程师" and second_prep.title == "数据工程师"
    assert database.get_interview_prep_for_snapshot("snap_iv2", profile.profile_id).prep_id == second_prep.prep_id


def test_preparation_is_refused_for_another_candidates_snapshot(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    with pytest.raises(ResumeError):
        service.create("someone_else", snapshot.snapshot_id)


def test_fallback_skips_a_gap_it_cannot_point_at():
    """Better to ask fewer questions than to invent an ungrounded one."""
    profile = build_profile(RESUME, "profile_noanchor")
    snapshot = snapshot_for()
    snapshot.required_skills = ["Rust"]      # no block mentions Rust
    days, questions = fallback_interview(profile, snapshot, ["rust"])
    assert days
    assert not [item for item in questions if item.category == "能力缺口"]


def test_interview_prep_api_end_to_end(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                await client.put(f"/api/profiles/{profile['profile_id']}", json={
                    "selected_roles": [{
                        "role": "Python 后端", "keywords": ["Python"], "confidence": "高",
                        "rationale": "有证据", "strengths": ["Python"], "gaps": [],
                        "citations": [profile.get("evidence", [{}])[0]] if profile.get("evidence") else [],
                    }],
                    "cities": ["北京"],
                })
                run_id = "run_api_iv"
                app.state.database.create_task(
                    run_id, "discovery", {"profile_id": profile["profile_id"], "mode": "browser"},
                )
                snapshot = snapshot_for("snap_api_iv")
                app.state.database.save_snapshot("boss:api_iv", run_id, snapshot)

                queued = await client.post("/api/interview-preps", json={
                    "profile_id": profile["profile_id"], "snapshot_id": "snap_api_iv",
                })
                assert queued.status_code == 202
                body = queued.json()
                for _ in range(100):
                    task = (await client.get(f"/api/tasks/{body['task_id']}")).json()
                    if task["status"] not in {"QUEUED", "RUNNING"}:
                        break
                    await asyncio.sleep(0.02)
                assert task["status"] == "SUCCEEDED", task

                prep = (await client.get(f"/api/interview-preps/{body['prep_id']}")).json()
                assert prep["status"] == "SUCCEEDED"
                assert prep["snapshot_id"] == "snap_api_iv"
                assert len(prep["days"]) == 7 and prep["questions"]

                by_job = await client.get(
                    f"/api/jobs/snap_api_iv/interview-prep?profile_id={profile['profile_id']}"
                )
                assert by_job.status_code == 200
                assert by_job.json()["prep_id"] == body["prep_id"]

                missing = await client.get("/api/jobs/snap_nope/interview-prep?profile_id=x")
                assert missing.status_code == 404

    asyncio.run(run())