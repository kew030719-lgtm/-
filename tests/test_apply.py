"""Assisted application preparation.

The rules worth testing: a greeting may only claim what the evidence supports,
and it must never contain contact details — the model is not given them, so any
that appear were invented.
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from career_radar.agent import AgentService
from career_radar.apply import ApplyService, fallback_greeting, validate_greeting
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.resume import ResumeError, build_profile
from career_radar.schemas import Application, Evidence, JobSnapshot
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
    source_type="job", source_id="snap_ap", block_id="job-ap-01",
    quote="负责 Kubernetes 集群运维与 Python 服务开发", section="职位原文",
)


def settings(tmp_path, api_key: str = ""):
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def snapshot_for(snapshot_id: str = "snap_ap", title: str = "Python 后端工程师") -> JobSnapshot:
    return JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id=snapshot_id, site="boss",
        canonical_url=f"https://www.zhipin.com/job_detail/{snapshot_id}.html", title=title,
        company="星图科技", city="北京", salary="20-30K",
        responsibilities=["负责 Kubernetes 集群运维与 Python 服务开发"],
        required_skills=["Python", "Kubernetes"], content_hash=f"hash-{snapshot_id}",
        fetched_at=datetime.now(UTC).isoformat(), transport="manual",
        blocks=[JOB_BLOCK.model_copy(update={"source_id": snapshot_id})],
    )


def prepared(tmp_path, *, api_key: str = ""):
    database = Database(tmp_path / "test.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_ap")
    profile.confirmed = True
    database.save_profile(profile)
    database.create_task("run_ap", "discovery", {"profile_id": profile.profile_id, "mode": "browser"})
    snapshot = snapshot_for()
    database.save_snapshot("boss:ap1", "run_ap", snapshot)
    service = ApplyService(database, AgentService(settings(tmp_path, api_key=api_key)))
    return database, service, profile, snapshot


def _application(profile, snapshot, greeting: str, ids: list[str]) -> Application:
    return Application(
        application_id="app_x", profile_id=profile.profile_id, snapshot_id=snapshot.snapshot_id,
        company=snapshot.company, title=snapshot.title, status="READY",
        greeting=greeting, greeting_evidence_ids=ids, created_at="now", updated_at="now",
    )


# ------------------------------------------------------------- 生成与校验

def test_fallback_greeting_is_grounded_and_cites_a_real_block(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    application = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.prepare(application.application_id))

    assert result.status == "READY"
    assert result.source == "fallback"
    assert result.greeting
    known = {item.block_id for item in profile.evidence}
    assert set(result.greeting_evidence_ids) <= known
    # The backend fills the quotes; the model never authors them.
    assert result.greeting_citations
    assert all(citation.quote for citation in result.greeting_citations)


def test_a_greeting_containing_contact_details_is_refused(tmp_path):
    """The model never receives contact details, so any that appear are invented."""
    database, service, profile, snapshot = prepared(tmp_path)
    application = _application(
        profile, snapshot, "您好，我是张三，电话 13800138000，希望应聘该岗位。",
        [profile.evidence[0].block_id],
    )
    with pytest.raises(ResumeError, match="电话号码"):
        validate_greeting(application, profile, snapshot)

    application.greeting = "您好，我的邮箱是 candidate@example.com，期待沟通。"
    with pytest.raises(ResumeError, match="邮箱"):
        validate_greeting(application, profile, snapshot)


def test_a_greeting_claiming_an_unbacked_skill_is_refused(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    application = _application(
        profile, snapshot, "您好，我精通 React 前端开发，希望应聘该岗位。",
        [profile.evidence[0].block_id],
    )
    with pytest.raises(ResumeError, match="未经证实的技能"):
        validate_greeting(application, profile, snapshot)


def test_naming_the_role_does_not_count_as_a_skill_claim(tmp_path):
    """「贵司的 Python 岗位」is a fact about the posting, not about the candidate."""
    database, service, profile, snapshot = prepared(tmp_path)
    # Evidence that mentions no Python at all, but a title that does.
    honest = [item for item in profile.evidence if "python" not in item.quote.lower()]
    if not honest:
        pytest.skip("fixture has no non-Python evidence block")
    application = _application(
        profile, snapshot, "您好，我对贵司的「Python 后端工程师」岗位很感兴趣。",
        [honest[0].block_id],
    )
    validate_greeting(application, profile, snapshot)


def test_a_greeting_without_evidence_is_refused(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    with pytest.raises(ResumeError, match="缺少证据"):
        validate_greeting(_application(profile, snapshot, "您好，希望应聘该岗位。", []), profile, snapshot)


def test_a_greeting_citing_a_made_up_block_is_refused(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    with pytest.raises(ResumeError, match="引用了无效证据"):
        validate_greeting(
            _application(profile, snapshot, "您好，希望应聘该岗位。", ["made-up-block"]),
            profile, snapshot,
        )


def test_an_overlong_greeting_is_refused(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    application = _application(profile, snapshot, "您好" * 200, [profile.evidence[0].block_id])
    with pytest.raises(ResumeError, match="过长"):
        validate_greeting(application, profile, snapshot)


def test_fallback_never_asserts_a_match_it_cannot_point_at():
    profile = build_profile(RESUME, "profile_nooverlap")
    snapshot = snapshot_for()
    snapshot.required_skills = ["Rust"]          # nothing in the resume matches
    greeting, ids = fallback_greeting(profile, snapshot)
    assert "Rust" not in greeting
    assert ids          # still citable, just not claiming a match


def test_an_uncitable_model_greeting_degrades_with_a_visible_reason(tmp_path):
    """A true-but-undercited model greeting must not leave the user with nothing,
    and must not be swapped silently either."""
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def uncited(_profile, _snapshot):
        from career_radar.agent import GreetingOutput

        return GreetingOutput(greeting="我有 99 年经验，希望应聘。", evidence_ids=[
            profile.evidence[0].block_id]), "langgraph"

    service.agent.compose_greeting = uncited
    application = service.create(profile.profile_id, snapshot.snapshot_id)
    result = asyncio.run(service.prepare(application.application_id))

    assert result.status == "READY"
    assert result.source == "fallback"
    assert "99" not in result.greeting
    assert "证据校验" in result.note


def test_a_failed_preparation_leaves_no_greeting_on_the_record(tmp_path):
    """Nothing may read as ready just because text is present.

    The UI and the helper both decide "prepared" from a non-empty greeting, so an
    unvalidated one that stayed on the record would be shown as usable.
    """
    database, service, profile, snapshot = prepared(tmp_path, api_key="configured")

    async def unusable(_profile, _snapshot):
        from career_radar.agent import GreetingOutput

        return GreetingOutput(greeting="我有 99 年经验。", evidence_ids=[
            profile.evidence[0].block_id]), "langgraph"

    # Make even the deterministic fallback unusable, so nothing can be salvaged.
    service.agent.compose_greeting = unusable
    original_fallback = __import__("career_radar.apply", fromlist=["fallback_greeting"]).fallback_greeting
    import career_radar.apply as apply_module

    apply_module.fallback_greeting = lambda *_a, **_k: ("我有 99 年经验。", [profile.evidence[0].block_id])
    try:
        application = service.create(profile.profile_id, snapshot.snapshot_id)
        with pytest.raises(ResumeError):
            asyncio.run(service.prepare(application.application_id))
        stored = database.get_application(application.application_id)
        assert stored.status == "DRAFT"
        assert stored.greeting == "", "an unvalidated greeting must not survive"
        assert stored.greeting_evidence_ids == []
        assert stored.error
    finally:
        apply_module.fallback_greeting = original_fallback


def test_status_transitions_are_whitelisted(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    application = asyncio.run(service.prepare(service.create(profile.profile_id, snapshot.snapshot_id).application_id))

    assert service.set_status(application.application_id, "OPENED").status == "OPENED"
    submitted = service.set_status(application.application_id, "SUBMITTED")
    assert submitted.status == "SUBMITTED" and submitted.submitted_at
    assert service.set_status(application.application_id, "SKIPPED").status == "SKIPPED"
    with pytest.raises(ResumeError, match="无效的投递状态"):
        service.set_status(application.application_id, "HIRED")


def test_creating_twice_reuses_the_existing_application(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    first = service.create(profile.profile_id, snapshot.snapshot_id)
    second = service.create(profile.profile_id, snapshot.snapshot_id)
    assert first.application_id == second.application_id
    assert len(database.list_applications(profile.profile_id)) == 1


def test_application_is_refused_for_another_candidates_snapshot(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    with pytest.raises(ResumeError):
        service.create("someone_else", snapshot.snapshot_id)


def test_submitting_requires_a_greeting(tmp_path):
    database, service, profile, snapshot = prepared(tmp_path)
    application = service.create(profile.profile_id, snapshot.snapshot_id)   # DRAFT, no greeting
    with pytest.raises(ResumeError, match="还没有生成打招呼语"):
        service.set_status(application.application_id, "SUBMITTED")


# --------------------------------------------------------------------- API

def test_apply_api_end_to_end(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                await client.put(f"/api/profiles/{profile['profile_id']}", json={
                    "selected_roles": [{
                        "role": "Python 后端", "keywords": ["Python"], "confidence": "高",
                        "rationale": "有证据", "strengths": ["Python"], "gaps": [],
                        "citations": [profile["evidence"][0]],
                    }],
                    "cities": ["北京"],
                })
                app.state.database.create_task(
                    "run_api_ap", "discovery", {"profile_id": profile["profile_id"], "mode": "browser"},
                )
                app.state.database.save_snapshot("boss:api_ap", "run_api_ap", snapshot_for("snap_api_ap"))

                queued = await client.post("/api/applications", json={
                    "profile_id": profile["profile_id"], "snapshot_id": "snap_api_ap",
                })
                assert queued.status_code == 202
                body = queued.json()
                for _ in range(100):
                    task = (await client.get(f"/api/tasks/{body['task_id']}")).json()
                    if task["status"] not in {"QUEUED", "RUNNING"}:
                        break
                    await asyncio.sleep(0.02)
                assert task["status"] == "SUCCEEDED", task

                application = (await client.get(f"/api/applications/{body['application_id']}")).json()
                assert application["status"] == "READY"
                assert application["greeting"] and application["greeting_citations"]

                opened = (await client.post(f"/api/applications/{body['application_id']}/opened")).json()
                assert opened["status"] == "OPENED"
                sent = (await client.post(f"/api/applications/{body['application_id']}/submitted")).json()
                assert sent["status"] == "SUBMITTED" and sent["submitted_at"]

                listed = (await client.get(f"/api/applications?profile_id={profile['profile_id']}")).json()
                assert len(listed) == 1

                # Asking again after it exists must not re-queue a preparation.
                again = await client.post("/api/applications", json={
                    "profile_id": profile["profile_id"], "snapshot_id": "snap_api_ap",
                })
                assert again.json()["status"] == "SUBMITTED"

                unknown = await client.post(f"/api/applications/{body['application_id']}/hired")
                assert unknown.status_code == 404

    asyncio.run(run())