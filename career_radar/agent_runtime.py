from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from .config import Settings
from .tools import ToolContext, tools_for


class WorkflowState(TypedDict, total=False):
    prompt: str
    system_prompt: str
    task_id: str
    output_type: Any
    tool_context: ToolContext | None
    toolsets: list[Any] | None
    max_tokens: int | None
    api_max_retries: int
    output: Any
    registered_tools: list[str]
    workflow_status: str
    error: str
    attempts: int


@dataclass
class HybridRunResult:
    output: Any
    registered_tools: list[str]
    workflow_status: str
    attempts: int


class HybridAgentRuntime:
    """PydanticAI runs one typed agent; LangGraph controls its execution lifecycle."""

    def __init__(self, settings: Settings):
        self.settings = settings
        graph = StateGraph(WorkflowState)
        graph.add_node("pydantic_agent", self._agent_node)
        graph.add_node("approval_gate", self._approval_node)
        graph.add_edge(START, "pydantic_agent")
        graph.add_conditional_edges(
            "pydantic_agent", self._route_after_agent,
            {"retry": "pydantic_agent", "review": "approval_gate", "failed": END},
        )
        graph.add_edge("approval_gate", END)
        self.graph = graph.compile()

    async def run(
        self, prompt: str, task_id: str, output_type: Any, *, system_prompt: str,
        tool_context: ToolContext | None = None, toolsets: list[Any] | None = None,
        max_tokens: int | None = None, api_max_retries: int = 2,
    ) -> HybridRunResult:
        state = await self.graph.ainvoke({
            "prompt": prompt, "system_prompt": system_prompt, "task_id": task_id,
            "output_type": output_type, "tool_context": tool_context, "toolsets": toolsets,
            "max_tokens": max_tokens,
            "api_max_retries": api_max_retries, "attempts": 0,
            "registered_tools": [], "workflow_status": "RUNNING",
        })
        if state.get("error"):
            raise RuntimeError(str(state["error"]))
        output = TypeAdapter(output_type).validate_python(state["output"])
        return HybridRunResult(
            output=output, registered_tools=list(state.get("registered_tools") or []),
            workflow_status=str(state.get("workflow_status") or "SUCCEEDED"),
            attempts=int(state.get("attempts") or 1),
        )

    async def _agent_node(self, state: WorkflowState) -> dict[str, Any]:
        attempts = int(state.get("attempts") or 0) + 1
        try:
            tools, registered = self._tools(state.get("tool_context"))
            output = await self._invoke_pydantic(state, tools)
            serialized = TypeAdapter(state["output_type"]).dump_python(output, mode="json")
            return {
                "output": serialized, "registered_tools": registered,
                "workflow_status": "MODEL_COMPLETED", "attempts": attempts, "error": "",
            }
        except Exception as exc:
            return {"attempts": attempts, "error": str(exc), "workflow_status": "RETRYING"}

    async def _invoke_pydantic(self, state: WorkflowState, tools: list[Any]) -> Any:
        # trust_env=False, for the same reason the crawler transport sets it: the
        # model endpoint is explicitly configured, so ambient proxy variables are
        # a shell/desktop concern the app should not inherit. A proxy URL in a
        # scheme httpx rejects (Clash Verge exports `socks://`) otherwise makes
        # AsyncOpenAI raise at construction, which every caller catches and
        # silently degrades to the deterministic fallback — the model would never
        # be reached and nothing would say so.
        http_client = httpx.AsyncClient(trust_env=False, timeout=60)
        try:
            client = AsyncOpenAI(
                base_url=self.settings.model_base_url, api_key=self.settings.api_key,
                max_retries=max(0, int(state.get("api_max_retries") or 0)),
                http_client=http_client,
            )
            provider = OpenAIProvider(openai_client=client)
            model = OpenAIModel(self.settings.model_name, provider=provider)
            model_settings = {"temperature": 0}
            if state.get("max_tokens"):
                model_settings["max_tokens"] = state["max_tokens"]
            deps = state.get("tool_context")
            # deps_type must only be declared when a context actually travels with
            # the run; PydanticAI rejects deps without it, and rejects a declared
            # deps_type with no deps.
            agent_kwargs: dict[str, Any] = {}
            if deps is not None:
                agent_kwargs["deps_type"] = ToolContext
            agent = Agent(
                model, output_type=state["output_type"], instructions=state["system_prompt"],
                tools=tools, toolsets=state.get("toolsets") or [], retries=1,
                model_settings=model_settings, **agent_kwargs,
            )
            result = await agent.run(
                state["prompt"], deps=deps,
                usage_limits=UsageLimits(request_limit=4, tool_calls_limit=3),
            )
            return result.output
        finally:
            await http_client.aclose()

    @staticmethod
    async def _route_after_agent(state: WorkflowState) -> str:
        if not state.get("error"):
            return "review"
        return "retry" if int(state.get("attempts") or 0) < 2 else "failed"

    async def _approval_node(self, state: WorkflowState) -> dict[str, Any]:
        context = state.get("tool_context")
        if context is not None and context.tool_mode == "chat" and context.message_id:
            with sqlite3.connect(context.database_path) as db:
                row = db.execute(
                    "SELECT 1 FROM chat_actions WHERE source_message_id=? AND status='PENDING' LIMIT 1",
                    (context.message_id,),
                ).fetchone()
            if row:
                return {"workflow_status": "WAITING_APPROVAL"}
        return {"workflow_status": "SUCCEEDED"}

    @staticmethod
    def _tools(context: ToolContext | None) -> tuple[list[Any], list[str]]:
        """Resolve the run's tools in-process; the former MCP round-trip is gone."""
        if context is None:
            return [], []
        functions = tools_for(context.tool_mode)
        return list(functions), [f"career_radar__{function.__name__}" for function in functions]