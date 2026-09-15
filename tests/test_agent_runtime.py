import asyncio
from datetime import UTC, datetime
from pathlib import Path

from career_radar.agent import ChatAgentOutput
from career_radar.agent_runtime import HybridAgentRuntime
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.schemas import ChatAction
from career_radar.tools import ToolContext


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key="test",
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def test_langgraph_retries_pydantic_agent_once(tmp_path):
    runtime = HybridAgentRuntime(settings(tmp_path))
    calls = 0

    async def fake_invoke(_state, _tools):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary model error")
        return ChatAgentOutput(answer="已恢复", grounded=False)

    runtime._invoke_pydantic = fake_invoke
    result = asyncio.run(runtime.run(
        "hello", "task_test", ChatAgentOutput, system_prompt="test",
    ))
    assert result.output.answer == "已恢复"
    assert result.attempts == 2
    assert result.workflow_status == "SUCCEEDED"


def test_langgraph_routes_proposed_change_to_approval_state(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.create_conversation("conv_runtime")
    stamp = datetime.now(UTC).isoformat()
    database.create_chat_action(ChatAction(
        action_id="action_runtime", conversation_id="conv_runtime",
        source_message_id="msg_runtime", kind="UPDATE_PROFILE",
        status="PENDING", created_at=stamp, updated_at=stamp,
    ))
    runtime = HybridAgentRuntime(settings(tmp_path))

    async def fake_invoke(_state, _tools):
        return ChatAgentOutput(answer="请确认修改", grounded=False)

    runtime._invoke_pydantic = fake_invoke
    result = asyncio.run(runtime.run(
        "改到杭州", "task_test", ChatAgentOutput, system_prompt="test",
        tool_context=ToolContext(
            database_path=database.path, tool_mode="chat", message_id="msg_runtime",
        ),
    ))
    assert result.workflow_status == "WAITING_APPROVAL"
    # The tools are now resolved in-process rather than fetched over JSON-RPC.
    assert "career_radar__propose_profile_update" in result.registered_tools
    assert "career_radar__save_job_comparison" not in result.registered_tools


def test_runs_without_a_tool_context_register_no_tools(tmp_path):
    runtime = HybridAgentRuntime(settings(tmp_path))

    async def fake_invoke(_state, _tools):
        return ChatAgentOutput(answer="通用回答", grounded=False)

    runtime._invoke_pydantic = fake_invoke
    result = asyncio.run(runtime.run(
        "hello", "task_test", ChatAgentOutput, system_prompt="test",
    ))
    assert result.registered_tools == []
    assert result.workflow_status == "SUCCEEDED"