"""Durable, deterministic memory for the chat agent.

Division of labour with ``CandidateProfile.evidence``: evidence remains the
authoritative store for *facts*. Every fact carries ``provenance`` and only
enters through resume parsing, ``TailoringService.confirm_facts`` or
``save_user_version``, so a claim about what the candidate can do is always
traceable to where it came from. ``agent_memories`` holds *preferences,
constraints and feedback* only — soft signals the user expressed but never
confirmed as a fact ("不想投外包", "放弃了这个岗位"). Copying facts here would
bypass provenance and validation, which is the one thing evidence exists to
prevent.

Everything in this module is deterministic: no model calls, no embeddings, no
vector store. CareerRadar is a single-user local tool with a few hundred
messages, so the existing ``LIKE`` search over evidence is still enough and
memory stays cheap enough to build on every turn.

Summarization (``summarize_messages``) is extractive for the same reason, plus
one more: it runs inside ``ChatService.answer``, and it is gated off by default,
so it must never be able to fail the reply.
"""

from __future__ import annotations

from uuid import uuid4

from .database import Database, now_iso
from .schemas import AgentMemory, ChatAction, ChatActionKind, ChatMessage, MemoryKind


# The prompt carries JSON with English field names, but a memory is read by the
# model as Chinese prose, so the change is described with its product label.
FIELD_LABELS = {
    "cities": "目标城市",
    "salary_preference": "薪资期望",
    "experience_years": "工作年限",
    "work_type_preference": "工作类型",
    "selected_roles": "目标岗位",
}

_REJECTION_OPENERS = {
    ChatActionKind.RESTART_DISCOVERY: "用户放弃了重新搜索岗位",
    ChatActionKind.START_COMPARISON: "用户放弃了生成岗位分析",
}


def _format_change(key: str, value: object) -> str:
    label = FIELD_LABELS.get(key, key)
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            value = [item.get("role", "") for item in value if item.get("role")]
        return f"{label}改为 {'、'.join(str(item) for item in value) or '（空）'}"
    return f"{label}改为 {value}"


def _describe_rejection(action: ChatAction) -> tuple[MemoryKind, str]:
    """Turn one rejected action into the sentence the agent should remember."""
    preview = action.preview or {}
    if action.kind == ChatActionKind.START_RESUME_TAILORING:
        company = str(preview.get("company") or "").strip()
        title = str(preview.get("title") or "").strip()
        if company or title:
            return MemoryKind.REJECTED_JOB, f"用户放弃了为「{company} · {title}」定向简历"
        return MemoryKind.FEEDBACK, "用户放弃了为一个岗位定向简历"

    opener = _REJECTION_OPENERS.get(action.kind)
    if opener:
        return MemoryKind.FEEDBACK, opener

    # UPDATE_PROFILE: name the change so the preference survives without the
    # proposal it came from.
    changes = preview.get("after") or action.arguments.get("changes") or {}
    described = "；".join(_format_change(key, value) for key, value in changes.items())
    if described:
        return MemoryKind.FEEDBACK, f"用户拒绝了画像修改：{described}"
    return MemoryKind.FEEDBACK, "用户拒绝了一次画像修改提议"


def summarize_messages(messages: list[ChatMessage], *, keep: int,
                       budget: int = 800) -> tuple[str, str] | None:
    """Extract a bounded summary of everything older than the live window.

    Returns ``(summary, upto_message_id)`` or ``None`` when there is nothing
    older to fold in. Built newest-first and then reversed so a tight budget
    keeps the older turns closest to the live window, not the opening greeting.
    """
    older = [item for item in messages[:-keep] if item.status == "SUCCEEDED"] if keep else []
    if not older:
        return None
    lines: list[str] = []
    used = 0
    for item in reversed(older):
        speaker = "用户" if item.role == "user" else "助手"
        line = f"{speaker}：{item.content.strip()[:80]}"
        if used + len(line) > budget and lines:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(reversed(lines)), older[-1].message_id


class MemoryService:
    def __init__(self, database: Database):
        self.database = database

    def remember(self, profile_id: str, kind: MemoryKind, text: str, *,
                 source_type: str = "", source_id: str = "", confidence: float = 1.0) -> AgentMemory:
        """Record one signal, keyed on its source when it has one.

        A source is what makes mining idempotent: re-deriving a memory from the
        same action returns the stored row instead of a second copy.
        """
        if source_type and source_id:
            existing = self.database.find_memory(profile_id, source_type=source_type, source_id=source_id)
            if existing:
                return existing
        return self.database.save_memory(AgentMemory(
            memory_id=f"mem_{uuid4().hex[:12]}", profile_id=profile_id, kind=MemoryKind(kind),
            text=text.strip()[:500], source_type=source_type, source_id=source_id,
            confidence=confidence, created_at=now_iso(),
        ))

    def mine_rejected_actions(self, profile_id: str) -> list[AgentMemory]:
        """Fold every unreviewed rejection into a memory.

        Rejections are free negative feedback: the row already exists, nothing
        ever read it back, and it costs nothing to re-run.
        """
        mined: list[AgentMemory] = []
        for action in self.database.list_rejected_chat_actions(profile_id):
            kind, text = _describe_rejection(action)
            mined.append(self.remember(
                profile_id, kind, text, source_type="chat_action", source_id=action.action_id,
            ))
        return mined

    def recent(self, profile_id: str, *, limit: int = 12) -> list[AgentMemory]:
        return self.database.list_memories(profile_id, limit=limit)