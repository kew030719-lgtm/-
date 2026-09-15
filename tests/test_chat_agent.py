import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from career_radar import tools
from career_radar.agent import AgentService
from career_radar.chat import ChatService
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.resume import ResumeError, build_profile
from career_radar.schemas import ChatAction, Comparison, Evidence, JobScore, JobSnapshot
from career_radar.tools import ToolContext, tools_for
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


def settings(tmp_path: Path, api_key: str = "") -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


async def wait_chat(client: httpx.AsyncClient, task_id: str):
    for _ in range(100):
        result = (await client.get(f"/api/chat-turns/{task_id}")).json()
        if result["task"]["status"] not in {"QUEUED", "RUNNING"}:
            return result
        await asyncio.sleep(.02)
    raise AssertionError("chat task did not finish")


def test_general_chat_persists_and_survives_reload(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                conversation = (await client.post("/api/conversations", json={})).json()
                queued = (await client.post(
                    f"/api/conversations/{conversation['conversation_id']}/messages",
                    json={"message": "怎么准备 Python 后端面试？"},
                )).json()
                turn = await wait_chat(client, queued["task_id"])
                assert turn["task"]["status"] == "SUCCEEDED"
                assert turn["message"]["source"] == "fallback"
                assert turn["message"]["citations"] == []
                restored = (await client.get(f"/api/conversations/{conversation['conversation_id']}")).json()
                assert [item["role"] for item in restored["messages"]] == ["user", "assistant"]
    asyncio.run(run())


def test_restart_action_waits_for_confirmation_and_is_idempotent(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                profile_id = profile["profile_id"]
                await client.put(f"/api/profiles/{profile_id}", json={
                    "selected_roles": profile["recommendations"][:1], "cities": ["北京"],
                    "salary_preference": "20-30K", "experience_years": 2,
                })
                old_run = "task_old"
                app.state.database.create_task(old_run, "discovery", {"profile_id": profile_id, "mode": "browser"})
                conversation = (await client.post("/api/conversations", json={
                    "profile_id": profile_id, "run_id": old_run,
                })).json()
                queued = (await client.post(
                    f"/api/conversations/{conversation['conversation_id']}/messages",
                    json={"message": "把目标城市改到杭州并重新搜索"},
                )).json()
                turn = await wait_chat(client, queued["task_id"])
                assert turn["task"]["status"] == "SUCCEEDED"
                assert len(turn["actions"]) == 1
                action = turn["actions"][0]
                assert action["kind"] == "RESTART_DISCOVERY" and action["status"] == "PENDING"
                assert app.state.database.get_profile(profile_id).cities == ["北京"]

                confirmed = (await client.post(f"/api/chat-actions/{action['action_id']}/confirm")).json()
                assert confirmed["status"] == "EXECUTED"
                assert app.state.database.get_profile(profile_id).cities == ["杭州"]
                assert app.state.database.get_task(old_run)["status"] == "FAILED"
                new_run = confirmed["result"]["run_id"]
                assert new_run != old_run and app.state.database.get_task(new_run)["status"] == "QUEUED"
                repeated = await client.post(f"/api/chat-actions/{action['action_id']}/confirm")
                assert repeated.status_code == 200
                assert repeated.json()["result"]["run_id"] == new_run
    asyncio.run(run())


def test_rejected_action_does_not_change_profile(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                profile_id = profile["profile_id"]
                await client.put(f"/api/profiles/{profile_id}", json={
                    "selected_roles": profile["recommendations"][:1], "cities": ["北京"],
                    "experience_years": 2,
                })
                conversation = (await client.post("/api/conversations", json={"profile_id": profile_id})).json()
                queued = (await client.post(
                    f"/api/conversations/{conversation['conversation_id']}/messages",
                    json={"message": "把目标城市改到上海"},
                )).json()
                action = (await wait_chat(client, queued["task_id"]))["actions"][0]
                rejected = (await client.post(f"/api/chat-actions/{action['action_id']}/reject")).json()
                assert rejected["status"] == "REJECTED"
                assert app.state.database.get_profile(profile_id).cities == ["北京"]
    asyncio.run(run())


def test_action_is_rejected_when_conversation_context_changes(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                profile_id = profile["profile_id"]
                await client.put(f"/api/profiles/{profile_id}", json={
                    "selected_roles": profile["recommendations"][:1], "cities": ["北京"],
                    "experience_years": 2,
                })
                app.state.database.create_task("task_first", "discovery", {"profile_id": profile_id, "mode": "browser"})
                app.state.database.create_task("task_second", "discovery", {"profile_id": profile_id, "mode": "browser"})
                conversation = (await client.post("/api/conversations", json={
                    "profile_id": profile_id, "run_id": "task_first",
                })).json()
                queued = (await client.post(
                    f"/api/conversations/{conversation['conversation_id']}/messages",
                    json={"message": "把目标城市改到杭州并重新搜索"},
                )).json()
                action = (await wait_chat(client, queued["task_id"]))["actions"][0]

                patched = await client.patch(
                    f"/api/conversations/{conversation['conversation_id']}/context",
                    json={"run_id": "task_second"},
                )
                assert patched.status_code == 200
                failed = await client.post(f"/api/chat-actions/{action['action_id']}/confirm")
                assert failed.status_code == 409
                assert app.state.database.get_profile(profile_id).cities == ["北京"]
                assert app.state.database.get_chat_action(action["action_id"]).status == "FAILED"
    asyncio.run(run())


def test_empty_context_patch_preserves_existing_links(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
                profile_id = profile["profile_id"]
                app.state.database.create_task("task_context", "discovery", {"profile_id": profile_id, "mode": "browser"})
                conversation = (await client.post("/api/conversations", json={
                    "profile_id": profile_id, "run_id": "task_context",
                })).json()
                patched = (await client.patch(
                    f"/api/conversations/{conversation['conversation_id']}/context", json={},
                )).json()
                assert patched["profile_id"] == profile_id
                assert patched["run_id"] == "task_context"
    asyncio.run(run())


def test_profile_switch_cannot_keep_another_profiles_run(tmp_path):
    async def run():
        app = create_app(settings(tmp_path))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = (await client.post("/api/resumes", json={"text": RESUME})).json()
                second = (await client.post("/api/resumes", json={"text": RESUME + "\n补充技能 Redis"})).json()
                app.state.database.create_task(
                    "task_first_profile", "discovery",
                    {"profile_id": first["profile_id"], "mode": "browser"},
                )
                conversation = (await client.post("/api/conversations", json={
                    "profile_id": first["profile_id"], "run_id": "task_first_profile",
                })).json()
                response = await client.patch(
                    f"/api/conversations/{conversation['conversation_id']}/context",
                    json={"profile_id": second["profile_id"]},
                )
                assert response.status_code == 409
                unchanged = app.state.database.get_conversation(conversation["conversation_id"])
                assert unchanged.profile_id == first["profile_id"]
                assert unchanged.run_id == "task_first_profile"
    asyncio.run(run())


def test_grounded_chat_resolves_exact_citations(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_chat")
    profile.confirmed = True
    profile.selected_roles = []
    database.save_profile(profile)
    snapshot_id = "snap_chat"
    job_evidence = Evidence(source_type="job", source_id=snapshot_id, block_id="job-chat-1", quote="负责 FastAPI 异步接口与 SQL 数据处理")
    snapshot = JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id="chat1",
        canonical_url="https://www.zhipin.com/job_detail/chat1.html", title="Python 后端工程师",
        company="星图科技", city="北京", salary="20-30K", required_skills=["FastAPI", "SQL"],
        content_hash="chat-hash", fetched_at=datetime.now(UTC).isoformat(), transport="manual",
        blocks=[job_evidence],
    )
    database.create_task("run_chat", "discovery", {"profile_id": profile.profile_id, "mode": "browser"})
    database.save_snapshot("boss:chat1", "run_chat", snapshot)
    score = JobScore(
        job_id=snapshot_id, total=88, skill=90, project=85, experience=80, education=100,
        preference=90, matched_skills=["fastapi", "sql"], missing_skills=["redis"],
        explanation="技能和项目职责匹配", citations=[profile.evidence[0], job_evidence],
    )
    comparison = Comparison(
        comparison_id="cmp_chat", profile_id=profile.profile_id, rankings=[score],
        action_plan=[f"第 {day} 天：准备" for day in range(1, 8)], source="fallback",
        created_at=datetime.now(UTC).isoformat(),
    )
    database.save_comparison(comparison, "task_cmp")
    conversation = database.create_conversation(
        "conv_chat", profile_id=profile.profile_id, run_id="run_chat", comparison_id="cmp_chat",
    )
    turn = ChatService(database, AgentService(settings(tmp_path)))._fallback_turn(
        conversation, "为什么第一名得分最高？",
    )
    assert turn.citations
    assert {item.quote for item in turn.citations} == {profile.evidence[0].quote, job_evidence.quote}


def test_forged_model_citation_is_rejected(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_forged")
    profile.confirmed = True
    database.save_profile(profile)
    database.create_conversation("conv_forged", profile_id=profile.profile_id)

    agent = AgentService(settings(tmp_path, api_key="configured"))

    async def fake_chat(*_args, **_kwargs):
        return json.dumps({
            "answer": "伪造结论", "grounded": True,
            "citations": [{"source_type": "resume", "source_id": profile.profile_id, "block_id": "fake"}],
        }, ensure_ascii=False), ["mcp__career-radar__read_candidate_profile"]

    agent.run_chat = fake_chat  # type: ignore[method-assign]
    with pytest.raises(ResumeError, match="引用校验失败"):
        asyncio.run(ChatService(database, agent).answer("conv_forged", "msg_forged", "我的能力怎么样", "task_forged"))


def test_chat_mode_exposes_proposals_without_direct_save_tools(tmp_path):
    database = Database(tmp_path / "chat.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_chat")
    profile.confirmed = True
    profile.cities = ["北京"]
    database.save_profile(profile)
    database.create_conversation("conv_chat", profile_id=profile.profile_id)

    context = ToolContext(
        database_path=database.path, tool_mode="chat",
        conversation_id="conv_chat", message_id="msg_chat",
    )
    names = {function.__name__ for function in tools_for("chat")}
    assert "propose_profile_update" in names
    assert "read_candidate_profile" in names
    # Chat may only propose; the state-mutating analysis and tailoring tools stay out.
    assert "save_job_comparison" not in names
    assert "save_role_recommendations" not in names
    assert "read_tailoring_context" not in names

    tools.propose_profile_update(context, "conv_chat", "改到杭州", {"cities": ["杭州"]})
    action = database.list_chat_actions("conv_chat")[0]
    assert action.status == "PENDING" and action.preview["after"] == {"cities": ["杭州"]}
    assert database.get_profile(profile.profile_id).cities == ["北京"]


def test_propose_requires_a_bound_conversation_in_chat_mode(tmp_path):
    database = Database(tmp_path / "chat.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_bound")
    profile.confirmed = True
    database.save_profile(profile)
    database.create_conversation("conv_other", profile_id=profile.profile_id)
    # No conversation bound to the run: the model must not be able to pick any
    # conversation it likes and write an action into it.
    unbound = ToolContext(database_path=database.path, tool_mode="chat", message_id="msg_bound")
    with pytest.raises(ValueError, match="active conversation"):
        tools.propose_profile_update(unbound, "conv_other", "改到杭州", {"cities": ["杭州"]})


def test_running_chat_task_is_failed_on_restart(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.create_task("task_interrupted", "chat", {"question": "hello"})
    database.update_task("task_interrupted", status="RUNNING")
    database.initialize()
    task = database.get_task("task_interrupted")
    assert task["status"] == "FAILED"
    assert "重新发送" in task["message"]


def test_executing_action_is_failed_without_replaying_after_restart(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.create_conversation("conv_restart")
    stamp = datetime.now(UTC).isoformat()
    action = database.create_chat_action(ChatAction(
        action_id="action_interrupted", conversation_id="conv_restart",
        kind="UPDATE_PROFILE", status="PENDING", created_at=stamp, updated_at=stamp,
    ))
    claimed, changed = database.claim_chat_action(action.action_id)
    assert changed and claimed.status == "EXECUTING"
    database.initialize()
    interrupted = database.get_chat_action(action.action_id)
    assert interrupted.status == "FAILED"
    assert "未自动重试" in interrupted.result["error"]
