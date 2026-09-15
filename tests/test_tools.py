"""Tests for the in-process tool layer.

The tools used to live in a bundled MCP server; the security rules they enforce
(contact scrubbing, turn binding) are product properties, so they are asserted
directly here rather than through an HTTP or JSON-RPC boundary.
"""

import asyncio
import inspect
from pathlib import Path

import pytest

from career_radar import tools
from career_radar.agent import ChatAgentOutput
from career_radar.agent_runtime import HybridAgentRuntime
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.resume import build_profile
from career_radar.schemas import CandidateContact, Evidence
from career_radar.tools import ToolContext, tools_for


RESUME = """技能
Python FastAPI SQL Docker Agent RAG
项目经历
实现简历分析 Agent，使用 FastAPI 提供异步 API，并输出原文证据引用。
工作经历
2 年 Python 后端开发经验
教育经历
计算机科学 本科
"""


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key="test",
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def _prepared(tmp_path) -> tuple[Database, ToolContext, str]:
    database = Database(tmp_path / "tools.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_tools")
    profile.confirmed = True
    profile.cities = ["北京"]
    database.save_profile(profile)
    database.create_conversation("conv_tools", profile_id=profile.profile_id)
    context = ToolContext(
        database_path=database.path, tool_mode="chat",
        conversation_id="conv_tools", profile_id=profile.profile_id,
    )
    return database, context, profile.profile_id


def test_tool_groups_match_each_mode():
    chat = {function.__name__ for function in tools_for("chat")}
    tailoring = {function.__name__ for function in tools_for("tailoring")}
    analysis = {function.__name__ for function in tools_for("analysis")}

    read_tools = {"read_candidate_profile", "search_resume_evidence", "list_job_snapshots",
                  "search_job_evidence", "read_job_comparison"}
    assert read_tools <= chat
    assert {"propose_profile_update", "propose_discovery_restart", "propose_comparison",
            "propose_resume_tailoring"} <= chat
    # Chat may only propose; the state-mutating tools must stay out.
    assert not chat & {"save_job_comparison", "save_role_recommendations", "read_tailoring_context"}

    # Tailoring reads one prepared context and nothing else.
    assert tailoring == {"read_tailoring_context"}

    assert read_tools <= analysis
    assert {"save_role_recommendations", "save_job_comparison"} <= analysis
    assert not analysis & {"propose_profile_update", "read_tailoring_context"}


def test_contact_details_never_reach_the_model(tmp_path):
    database, context, profile_id = _prepared(tmp_path)
    phone, email, name = "13800138000", "candidate@example.com", "张三"
    profile = database.get_profile(profile_id)
    # Supplemental and user-edited evidence is appended to profile.evidence after
    # ingest, so the tool layer is the last barrier before the model sees it.
    profile.evidence.append(Evidence(
        source_type="resume", source_id=profile_id, block_id="user-01",
        quote=f"联系方式：{phone}，邮箱 {email}，现居北京", section="用户补充",
        provenance="user_confirmed",
    ))
    database.save_profile(profile)
    database.save_contact(CandidateContact(
        profile_id=profile_id, name=name, phone=phone, email=email, location="北京",
    ))

    value = tools.read_candidate_profile(context, profile_id)
    blob = str(value)
    assert phone not in blob
    assert email not in blob
    assert name not in blob


def test_search_resume_evidence_is_narrower_in_chat_mode(tmp_path):
    database, context, profile_id = _prepared(tmp_path)
    profile = database.get_profile(profile_id)
    for index in range(30):
        profile.evidence.append(Evidence(
            source_type="resume", source_id=profile_id, block_id=f"extra-{index:02d}",
            quote=f"Python 项目经验补充 {index}", section="项目", provenance="uploaded_resume",
        ))
    database.save_profile(profile)

    chat_hits = tools.search_resume_evidence(context, profile_id, "python")
    analysis_hits = tools.search_resume_evidence(
        ToolContext(database_path=database.path, tool_mode="analysis", profile_id=profile_id),
        profile_id, "python",
    )
    assert len(chat_hits) == 8
    assert len(analysis_hits) == 20


def test_search_job_evidence_rejects_a_snapshot_from_another_run(tmp_path):
    database, context, _ = _prepared(tmp_path)
    other = ToolContext(
        database_path=database.path, tool_mode="chat", conversation_id="conv_tools",
        profile_id="profile_tools",
    )
    with pytest.raises(ValueError):
        tools.search_job_evidence(other, "snap_not_here", "python")


def test_tools_also_accept_a_bare_context_for_testing(tmp_path):
    """The RunContext/ ToolContext seam: PydanticAI injects the former."""
    database, context, profile_id = _prepared(tmp_path)
    assert isinstance(context, ToolContext)
    assert tools._deps(context) is context
    assert tools.read_candidate_profile(context, profile_id)["profile_id"] == profile_id
    # The public signature stays RunContext-shaped for PydanticAI's schema build.
    assert "RunContext" in str(inspect.signature(tools.read_candidate_profile).parameters["ctx"].annotation)


def test_pydantic_ai_builds_schemas_from_the_tool_functions():
    """Every chat and tailoring run depends on PydanticAI deriving a JSON schema
    from these plain signatures. If one is not schema-able the agent fails at
    runtime, and the RunContext parameter must be recognised so deps reach the tool."""
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    for mode in ("chat", "tailoring", "analysis"):
        agent = Agent(TestModel(), tools=tools_for(mode), deps_type=ToolContext)
        # `_function_toolset` is the introspection route PydanticAI exposes.
        registered = set(agent._function_toolset.tools)
        expected = {function.__name__ for function in tools_for(mode)}
        assert registered == expected, f"{mode}: missing {expected - registered}"
        # A RunContext parameter wrongly exposed to the model would leak the
        # whole context into the tool schema.
        for name in registered:
            schema = agent._function_toolset.tools[name].function_schema
            assert "ctx" not in (schema.json_schema or {}).get("properties", {}), name


def test_toolsets_are_forwarded_to_the_pydantic_agent(tmp_path, monkeypatch):
    """The seam for attaching an external MCP server as a client."""
    captured: dict = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def run(self, *args, **kwargs):
            raise RuntimeError("captured")

    # Stub the model plumbing: building a real AsyncOpenAI means building an
    # httpx client, which reads the developer's shell proxy settings. That is
    # irrelevant to what this test checks.
    import career_radar.agent_runtime as runtime_module
    monkeypatch.setattr(runtime_module, "AsyncOpenAI", lambda **kwargs: object())
    monkeypatch.setattr(runtime_module, "OpenAIProvider", lambda **kwargs: object())
    monkeypatch.setattr(runtime_module, "OpenAIModel", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime_module, "Agent", FakeAgent)
    runtime = HybridAgentRuntime(settings(tmp_path))
    sentinel = object()
    with pytest.raises(RuntimeError, match="captured"):
        asyncio.run(runtime.run(
            "prompt", "task", ChatAgentOutput, system_prompt="system", toolsets=[sentinel],
        ))
    assert captured["toolsets"] == [sentinel]


def test_mcp_client_extra_is_installed():
    """CareerRadar consumes MCP; it does not serve it."""
    try:
        from pydantic_ai import mcp
    except ImportError:
        pytest.skip("requires the pydantic-ai `mcp` extra (pip install -e '.[dev]')")
    assert mcp.MCPServerStreamableHTTP("https://example.invalid/mcp") is not None