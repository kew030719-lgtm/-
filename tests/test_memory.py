"""Long-term memory for the chat agent.

Two properties matter here: a rejection must be remembered exactly once however
often it is mined, and the memory must reach the prompt without the prompt
growing with the conversation.
"""

import asyncio
import sqlite3
from pathlib import Path

from career_radar.agent import AgentService
from career_radar.chat import ChatService
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.memory import MemoryService, summarize_messages
from career_radar.resume import build_profile
from career_radar.schemas import ChatAction, ChatActionKind, ChatMessage, MemoryKind


RESUME = """技能
Python FastAPI SQL Docker Agent RAG
项目经历
实现简历分析 Agent，使用 FastAPI 提供异步 API，并输出原文证据引用。
工作经历
2 年 Python 后端开发经验
教育经历
计算机科学 本科
"""


def settings(tmp_path: Path, api_key: str = "", **overrides) -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key=api_key,
        crawl_delay_seconds=0, crawl_max_jobs=3, **overrides,
    )


def prepared(tmp_path, **overrides):
    database = Database(tmp_path / "test.db")
    database.initialize()
    profile = build_profile(RESUME, "profile_mem")
    profile.confirmed = True
    database.save_profile(profile)
    conversation = database.create_conversation("conv_mem", profile_id=profile.profile_id)
    return database, profile, conversation


def rejected_tailoring_action(database, conversation) -> str:
    action = ChatAction(
        action_id="action_rejected", conversation_id=conversation.conversation_id,
        kind=ChatActionKind.START_RESUME_TAILORING, status="PENDING",
        preview={"company": "星图科技", "title": "Python 后端工程师"},
        arguments={"snapshot_id": "snap_mem"},
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    database.create_chat_action(action)
    stored, rejected = database.reject_chat_action(action.action_id)
    assert rejected
    return action.action_id


def add_messages(database, conversation_id: str, count: int, *, prefix: str = "m") -> None:
    for index in range(count):
        database.save_chat_message(ChatMessage(
            message_id=f"msg_{prefix}{index}", conversation_id=conversation_id,
            role="user" if index % 2 == 0 else "assistant", content=f"{prefix}{index}",
            created_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
        ))


def test_rejected_action_becomes_a_memory_and_mining_is_idempotent(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    rejected_tailoring_action(database, conversation)
    service = MemoryService(database)

    mined = service.mine_rejected_actions(profile.profile_id)

    assert len(mined) == 1
    assert mined[0].kind == "rejected_job"
    assert mined[0].text == "用户放弃了为「星图科技 · Python 后端工程师」定向简历"
    assert mined[0].source_type == "chat_action" and mined[0].source_id == "action_rejected"

    # The miner runs on every rejection, so a second pass must be a no-op.
    assert len(service.mine_rejected_actions(profile.profile_id)) == 1
    assert len(database.list_memories(profile.profile_id)) == 1


def test_a_rejected_profile_change_remembers_the_preference(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    action = ChatAction(
        action_id="action_profile", conversation_id=conversation.conversation_id,
        kind=ChatActionKind.UPDATE_PROFILE, status="PENDING",
        preview={"before": {"cities": ["北京"]}, "after": {"cities": ["杭州"]}},
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    database.create_chat_action(action)
    database.reject_chat_action(action.action_id)

    mined = MemoryService(database).mine_rejected_actions(profile.profile_id)

    assert mined[0].kind == "feedback"
    assert "目标城市改为 杭州" in mined[0].text


def test_pending_actions_are_not_mined(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    action = ChatAction(
        action_id="action_pending", conversation_id=conversation.conversation_id,
        kind=ChatActionKind.RESTART_DISCOVERY, status="PENDING", preview={},
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
    )
    database.create_chat_action(action)

    assert MemoryService(database).mine_rejected_actions(profile.profile_id) == []
    assert database.list_memories(profile.profile_id) == []


def test_a_superseded_memory_leaves_the_context(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    service = MemoryService(database)
    first = service.remember(profile.profile_id, MemoryKind.PREFERENCE, "用户偏好远程")
    second = service.remember(profile.profile_id, MemoryKind.PREFERENCE, "用户改为偏好坐班")

    database.supersede_memory(first.memory_id, second.memory_id)

    live = database.list_memories(profile.profile_id)
    assert [item.memory_id for item in live] == [second.memory_id]
    assert database.list_memories(profile.profile_id, kind=MemoryKind.FEEDBACK) == []


def test_memories_reach_the_prompt(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    rejected_tailoring_action(database, conversation)
    MemoryService(database).mine_rejected_actions(profile.profile_id)

    service = ChatService(database, AgentService(settings(tmp_path)))
    conversation = database.get_conversation(conversation.conversation_id)
    prompt = service._prompt(conversation, "还能帮我改改简历吗？")

    memories = service._context(conversation)["memories"]
    assert {"kind": "rejected_job", "text": "用户放弃了为「星图科技 · Python 后端工程师」定向简历"} in memories
    assert "星图科技" in prompt
    assert "memories" in prompt


def test_memories_are_absent_without_a_profile(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.create_conversation("conv_anon")

    service = ChatService(database, AgentService(settings(tmp_path)))
    loaded = database.get_conversation("conv_anon")
    assert service._context(loaded)["memories"] == []


def test_a_long_conversation_still_builds_a_bounded_prompt(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    add_messages(database, conversation.conversation_id, 30, prefix="m")
    service = ChatService(database, AgentService(settings(tmp_path)))

    prompt = service._prompt(database.get_conversation(conversation.conversation_id), "继续")

    # The live window is the newest 24, so the 6 oldest turns drop out.
    assert "m29" in prompt and "m6" in prompt
    assert "m5" not in prompt
    assert len(prompt) < 8000


def test_the_guarded_alter_migrates_an_older_conversations_table(tmp_path):
    path = tmp_path / "test.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            profile_id TEXT,
            run_id TEXT,
            comparison_id TEXT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO conversations(id,profile_id,title,created_at,updated_at)
        VALUES('conv_old','profile_old','旧对话','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()

    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)").fetchall()}
    connection.close()
    assert {"summary", "summary_upto_message_id"} <= columns

    # The migration is additive: the row written by the old schema survives.
    old = database.get_conversation("conv_old")
    assert old is not None and old.title == "旧对话"
    assert old.summary == "" and old.summary_upto_message_id is None


def test_summarization_is_off_by_default(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    add_messages(database, conversation.conversation_id, 40, prefix="s")

    service = ChatService(database, AgentService(settings(tmp_path)))
    asyncio.run(service.answer(conversation.conversation_id, "msg_s0", "继续", "task_mem"))

    stored = database.get_conversation(conversation.conversation_id)
    assert stored.summary == "" and stored.summary_upto_message_id is None


def test_enabled_summarization_folds_older_turns_into_the_prompt(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    add_messages(database, conversation.conversation_id, 12, prefix="s")
    service = ChatService(database, AgentService(
        settings(tmp_path, chat_history_limit=4, chat_summarize_after=4),
    ))

    asyncio.run(service.answer(conversation.conversation_id, "msg_s0", "继续", "task_mem"))

    stored = database.get_conversation(conversation.conversation_id)
    assert stored.summary and stored.summary_upto_message_id == "msg_s7"
    prompt = service._prompt(stored, "继续")
    assert "较早对话摘要：" in prompt
    # The newest four turns stay verbatim; the older ones only appear folded.
    assert "s11" in prompt and "s0" not in prompt


def test_summarization_failure_never_costs_the_reply(tmp_path):
    database, profile, conversation = prepared(tmp_path)
    add_messages(database, conversation.conversation_id, 12, prefix="s")

    def broken(*_args, **_kwargs):
        raise RuntimeError("summarizer unavailable")

    service = ChatService(database, AgentService(
        settings(tmp_path, chat_history_limit=4, chat_summarize_after=4),
    ))
    service.database.save_conversation_summary = broken  # type: ignore[method-assign]

    turn = asyncio.run(service.answer(conversation.conversation_id, "msg_s0", "继续", "task_mem"))
    assert turn.content


def test_summarize_messages_returns_nothing_before_the_window_overflows(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.create_conversation("conv_small")
    add_messages(database, "conv_small", 3)

    loaded = database.get_conversation("conv_small")
    assert summarize_messages(loaded.messages, keep=4) is None