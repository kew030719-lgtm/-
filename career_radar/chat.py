from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .agent import AgentService, _extract_json
from .database import Database, now_iso
from .memory import MemoryService, summarize_messages
from .resume import ResumeError, SUPPORTED_CITIES
from .schemas import (
    ChatAction, ChatActionKind, ChatCitation, ChatMessage, ChatTurn, Conversation,
    Evidence, TaskStatus,
)


GROUNDED_TERMS = ("我", "简历", "岗位", "职位", "排名", "第一", "分数", "能力", "缺口", "证据", "公司", "薪资", "适合")
RESTART_TERMS = ("重新搜索", "重新搜", "重搜", "再搜", "重新抓", "再抓")
ANALYZE_TERMS = ("开始分析", "生成排名", "分析已有", "现在分析", "进行分析")
TAILOR_TERMS = ("改简历", "修改简历", "优化简历", "定向简历", "针对简历")
MEMORY_LIMIT = 12


def _evidence_to_citation(value: Evidence) -> ChatCitation:
    return ChatCitation(**value.model_dump())


class ChatService:
    def __init__(self, database: Database, agent: AgentService):
        self.database = database
        self.agent = agent
        self.memories = MemoryService(database)

    @property
    def history_limit(self) -> int:
        return self.agent.settings.chat_history_limit

    def _context(self, conversation: Conversation) -> dict[str, Any]:
        profile = self.database.get_profile(conversation.profile_id) if conversation.profile_id else None
        task = self.database.get_task(conversation.run_id) if conversation.run_id else None
        jobs = self.database.list_snapshots(conversation.run_id) if conversation.run_id else []
        comparison = self.database.get_comparison(conversation.comparison_id) if conversation.comparison_id else None
        return {
            "conversation_id": conversation.conversation_id,
            "profile_id": conversation.profile_id,
            "run_id": conversation.run_id,
            "comparison_id": conversation.comparison_id,
            "profile": None if not profile else {
                "confirmed": profile.confirmed, "skills": profile.skills,
                "experience_years": profile.experience_years, "education": profile.education,
                "selected_roles": [item.model_dump() for item in profile.selected_roles],
                "cities": profile.cities, "salary_preference": profile.salary_preference,
                "work_type_preference": profile.work_type_preference,
            },
            "discovery": None if not task else {
                "status": task["status"], "message": task["message"], "job_count": len(jobs),
            },
            "ranking_summary": [] if not comparison else [
                {"job_id": item.job_id, "score": item.total, "missing_skills": item.missing_skills,
                 "risks": item.risks} for item in comparison.rankings[:5]
            ],
            # One indexed read per turn. Preferences and feedback only; facts
            # still have to come from evidence.
            "memories": [] if not conversation.profile_id else [
                {"kind": item.kind, "text": item.text}
                for item in self.memories.recent(conversation.profile_id, limit=MEMORY_LIMIT)
            ],
        }

    def _prompt(self, conversation: Conversation, question: str) -> str:
        history = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages[-self.history_limit:]
            if message.status == "SUCCEEDED"
        ]
        # The prompt shape stays a JSON blob inside one user message on purpose:
        # LangGraph rebuilds history on every retry and approval re-check, and
        # native roles would change those semantics for no gain.
        sections = ["当前产品上下文：\n" + json.dumps(self._context(conversation), ensure_ascii=False)]
        if conversation.summary:
            sections.append("较早对话摘要：\n" + conversation.summary)
        sections.append("最近对话：\n" + json.dumps(history, ensure_ascii=False))
        sections.append("用户最新消息：\n" + question)
        sections.append("需要个性化事实时先调用读取或搜索工具。用户要求业务变更时调用 propose 工具，然后在回答中说明需要确认。")
        return "\n".join(sections)

    def _compact(self, conversation: Conversation) -> None:
        """Fold the messages that fell out of the live window into the summary.

        Best-effort on purpose: compaction is context shaping, and a bug in it
        must never cost the user a reply, so every path here is swallowed.
        """
        threshold = self.agent.settings.chat_summarize_after
        if threshold <= 0 or len(conversation.messages) <= threshold:
            return
        try:
            folded = summarize_messages(conversation.messages, keep=self.history_limit)
            if not folded:
                return
            summary, upto = folded
            if summary == conversation.summary and upto == conversation.summary_upto_message_id:
                return
            self.database.save_conversation_summary(conversation.conversation_id, summary, upto)
            conversation.summary = summary
            conversation.summary_upto_message_id = upto
        except Exception:
            return

    def _resolve_citations(self, conversation: Conversation,
                           requested: list[dict[str, Any]]) -> list[ChatCitation]:
        index: dict[tuple[str, str, str], Evidence] = {}
        if conversation.profile_id:
            profile = self.database.get_profile(conversation.profile_id)
            if profile:
                index.update({(item.source_type, item.source_id, item.block_id): item for item in profile.evidence})
        if conversation.run_id:
            for job in self.database.list_snapshots(conversation.run_id):
                index.update({(item.source_type, item.source_id, item.block_id): item for item in job.blocks})
        resolved: list[ChatCitation] = []
        seen: set[tuple[str, str, str]] = set()
        for item in requested:
            key = (str(item.get("source_type", "")), str(item.get("source_id", "")), str(item.get("block_id", "")))
            if key in seen:
                continue
            original = index.get(key)
            if not original:
                raise ResumeError(f"对话引用校验失败：{key[2] or '缺少证据 ID'}")
            resolved.append(_evidence_to_citation(original))
            seen.add(key)
        return resolved

    @staticmethod
    def _requires_grounding(conversation: Conversation, question: str) -> bool:
        return bool(conversation.profile_id and any(term in question for term in GROUNDED_TERMS))

    def _extract_changes(self, question: str, conversation: Conversation) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        cities = [city for city in SUPPORTED_CITIES if city in question]
        if cities:
            changes["cities"] = cities[:2]
        salary = re.search(r"(\d{1,3}\s*[-–~到]\s*\d{1,3}\s*[kK])", question)
        if salary:
            changes["salary_preference"] = salary.group(1).replace(" ", "").upper()
        years = re.search(r"(\d+(?:\.\d+)?)\s*年(?:经验|工作年限)?", question)
        if years and any(word in question for word in ("经验", "年限", "工作")):
            changes["experience_years"] = float(years.group(1))
        for work_type in ("全职", "兼职", "实习", "远程"):
            if work_type in question:
                changes["work_type_preference"] = work_type
                break
        role_match = re.search(r"岗位(?:改成|换成|设为)\s*([^，。,。\n]{2,30})", question)
        if role_match and conversation.profile_id:
            profile = self.database.get_profile(conversation.profile_id)
            if profile and (profile.selected_roles or profile.recommendations):
                role = (profile.selected_roles or profile.recommendations)[0].model_copy(deep=True)
                role.role = role_match.group(1).strip()
                role.keywords = [role.role]
                changes["selected_roles"] = [role.model_dump()]
        return changes

    def _create_action(self, conversation: Conversation, source_message_id: str, kind: ChatActionKind,
                       question: str, changes: dict[str, Any] | None = None) -> ChatAction:
        profile = self.database.get_profile(conversation.profile_id) if conversation.profile_id else None
        if not profile:
            raise ValueError("请先提交并确认简历，再修改个性化设置")
        changes = changes or {}
        before = {key: getattr(profile, key) for key in changes}
        task = self.database.get_task(conversation.run_id) if conversation.run_id else None
        count = len(self.database.list_snapshots(conversation.run_id)) if conversation.run_id else 0
        stamp = now_iso()
        return self.database.create_chat_action(ChatAction(
            action_id=f"action_{uuid4().hex[:12]}", conversation_id=conversation.conversation_id,
            source_message_id=source_message_id, kind=kind,
            arguments={
                "user_intent": question[:500], "changes": changes,
                "profile_id": conversation.profile_id, "run_id": conversation.run_id,
                "early_finish": bool(task and task["status"] in {"QUEUED", "RUNNING", "NEEDS_MANUAL_INPUT"}),
            },
            preview={
                "user_intent": question[:500], "before": before, "after": changes,
                "current_run_id": conversation.run_id,
                "current_run_status": task["status"] if task else None, "current_job_count": count,
            }, created_at=stamp, updated_at=stamp,
        ))

    def _create_tailoring_action(self, conversation: Conversation, source_message_id: str,
                                 question: str, snapshot_id: str) -> ChatAction:
        job = self.database.get_snapshot(snapshot_id)
        if not job or not conversation.profile_id or not conversation.run_id:
            raise ValueError("请选择当前岗位列表中的具体岗位")
        stamp = now_iso()
        return self.database.create_chat_action(ChatAction(
            action_id=f"action_{uuid4().hex[:12]}", conversation_id=conversation.conversation_id,
            source_message_id=source_message_id, kind=ChatActionKind.START_RESUME_TAILORING,
            arguments={
                "user_intent": question[:500], "profile_id": conversation.profile_id,
                "run_id": conversation.run_id, "snapshot_id": snapshot_id,
            },
            preview={
                "user_intent": question[:500], "company": job.company, "title": job.title,
                "snapshot_id": snapshot_id, "jd_source": "当前岗位快照",
                "next_step": "对照岗位要求提出最多 5 个事实问题",
            }, created_at=stamp, updated_at=stamp,
        ))

    def _ensure_fallback_action(self, conversation: Conversation, message_id: str, question: str) -> ChatAction | None:
        existing = self.database.list_chat_actions(conversation.conversation_id, source_message_id=message_id)
        if existing:
            return existing[-1]
        if not conversation.profile_id:
            return None
        if any(term in question for term in TAILOR_TERMS) and conversation.run_id:
            jobs = self.database.list_snapshots(conversation.run_id)
            matches = [job for job in jobs if job.company in question or job.title in question]
            if len(matches) == 1:
                return self._create_tailoring_action(
                    conversation, message_id, question, matches[0].snapshot_id,
                )
        changes = self._extract_changes(question, conversation)
        if any(term in question for term in ANALYZE_TERMS):
            if not conversation.run_id:
                return None
            return self._create_action(conversation, message_id, ChatActionKind.START_COMPARISON, question)
        if any(term in question for term in RESTART_TERMS):
            return self._create_action(conversation, message_id, ChatActionKind.RESTART_DISCOVERY, question, changes)
        if changes and any(term in question for term in ("改", "换", "设置", "调整", "目标")):
            return self._create_action(conversation, message_id, ChatActionKind.UPDATE_PROFILE, question, changes)
        return None

    def _fallback_turn(self, conversation: Conversation, question: str, action: ChatAction | None = None) -> ChatTurn:
        profile = self.database.get_profile(conversation.profile_id) if conversation.profile_id else None
        if action:
            name = {ChatActionKind.UPDATE_PROFILE: "画像修改", ChatActionKind.RESTART_DISCOVERY: "重新搜索",
                    ChatActionKind.START_COMPARISON: "岗位分析",
                    ChatActionKind.START_RESUME_TAILORING: "定向简历"}[action.kind]
            return ChatTurn(content=f"我已整理好{name}方案。请检查下面的变更内容，确认后才会执行。")
        if not profile:
            return ChatTurn(content=(
                "可以。你可以先告诉我目标方向、工作年限和想去的城市，我会帮你梳理岗位选择、简历结构和面试准备。"
                "提交简历后，我还能结合真实岗位和原文证据做个性化分析。"
            ))
        comparison = self.database.get_comparison(conversation.comparison_id) if conversation.comparison_id else None
        jobs = self.database.list_snapshots(conversation.run_id) if conversation.run_id else []
        job_map = {item.snapshot_id: item for item in jobs}
        if any(term in question for term in TAILOR_TERMS):
            return ChatTurn(content="请指定一个具体公司和岗位。你可以点击排名卡片中的“为这个岗位修改简历”，也可以在定向简历工作台粘贴 JD 或提供公开职位链接。")
        if comparison and comparison.rankings:
            top = comparison.rankings[0]
            job = job_map.get(top.job_id)
            citations = [_evidence_to_citation(item) for item in top.citations[:4]]
            if "面试" in question:
                skills = top.matched_skills[:3] + top.missing_skills[:3]
                content = "建议优先准备这些追问：\n" + "\n".join(
                    f"{index + 1}. 请结合真实项目说明你如何使用 {skill}，遇到什么问题，结果如何。"
                    for index, skill in enumerate(skills or ["核心技术", "系统设计", "问题排查"])
                )
            elif "计划" in question:
                content = "当前 7 天准备计划：\n" + "\n".join(comparison.action_plan)
            elif "缺口" in question or "能力" in question:
                missing = "、".join(top.missing_skills) or "没有识别出明确技能缺口"
                content = f"排名最高岗位的主要待补能力是：{missing}。建议先针对岗位职责做最小项目，再把可验证结果写入简历。"
            else:
                label = f"{job.title}（{job.company}）" if job else "排名第一的岗位"
                content = f"{label}当前适配分为 {top.total}。已匹配：{'、'.join(top.matched_skills) or '无法判断'}；待补：{'、'.join(top.missing_skills) or '暂无明确缺口'}。{top.explanation}"
            return ChatTurn(content=content, citations=citations)
        if conversation.run_id:
            task = self.database.get_task(conversation.run_id)
            return ChatTurn(
                content=f"当前岗位发现任务为 {task['status'] if task else '未知状态'}，已保存 {len(jobs)} 个岗位。排名生成后，我可以逐项解释分数和能力缺口。",
                citations=[_evidence_to_citation(item) for item in profile.evidence[:2]],
            )
        roles = "、".join(item.role for item in (profile.selected_roles or profile.recommendations)[:3])
        return ChatTurn(
            content=f"根据已解析的简历，可以先关注：{roles or '尚未确认岗位方向'}。确认岗位和城市后，我会搜索真实岗位并用证据解释排名。",
            citations=[_evidence_to_citation(item) for item in profile.evidence[:3]],
        )

    async def answer(self, conversation_id: str, message_id: str, question: str, task_id: str) -> ChatTurn:
        # Fetch the live window plus, when compaction is on, the older span it
        # folds in. Off by default this is just the live window.
        window = self.history_limit + max(0, self.agent.settings.chat_summarize_after)
        conversation = self.database.get_conversation(conversation_id, message_limit=window)
        if not conversation:
            raise ValueError("对话不存在")
        self._compact(conversation)
        if not self.agent.settings.api_key:
            action = self._ensure_fallback_action(conversation, message_id, question)
            return self._fallback_turn(conversation, question, action)
        try:
            raw, registered = await self.agent.run_chat(
                self._prompt(conversation, question), task_id, conversation_id, message_id,
            )
            if not registered:
                raise RuntimeError("CareerRadar 工具未加载")
            value = _extract_json(raw)
            if not isinstance(value, dict) or not str(value.get("answer", "")).strip():
                raise ValueError("模型未返回有效对话结构")
            citations = self._resolve_citations(conversation, list(value.get("citations") or []))
            action = self._ensure_fallback_action(conversation, message_id, question)
            if self._requires_grounding(conversation, question) and not citations and not action:
                raise ResumeError("个性化回复缺少可验证引用")
            return ChatTurn(content=str(value["answer"])[:6000], citations=citations, source="langgraph")
        except ResumeError:
            raise
        except Exception:
            action = self._ensure_fallback_action(conversation, message_id, question)
            return self._fallback_turn(conversation, question, action)


@dataclass
class ChatWorkItem:
    task_id: str
    conversation_id: str
    message_id: str
    question: str


class ChatQueue:
    def __init__(self, database: Database, service: ChatService):
        self.database = database
        self.service = service
        self.queue: asyncio.Queue[ChatWorkItem | None] = asyncio.Queue()
        self.consumer: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.consumer:
            self.consumer = asyncio.create_task(self._consume(), name="career-radar-chat-worker")

    async def stop(self) -> None:
        if self.consumer:
            await self.queue.put(None)
            await self.consumer
            self.consumer = None

    async def submit(self, conversation_id: str, question: str) -> tuple[str, str]:
        if not self.database.get_conversation(conversation_id):
            raise ValueError("对话不存在")
        task_id = f"task_{uuid4().hex[:12]}"
        message_id = f"msg_{uuid4().hex[:12]}"
        self.database.create_task(task_id, "chat", {
            "conversation_id": conversation_id, "message_id": message_id, "question": question,
        })
        self.database.save_chat_message(ChatMessage(
            message_id=message_id, conversation_id=conversation_id, role="user", content=question,
            task_id=task_id, source="user", created_at=now_iso(),
        ))
        await self.queue.put(ChatWorkItem(task_id, conversation_id, message_id, question))
        return task_id, message_id

    async def _consume(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                break
            try:
                self.database.update_task(item.task_id, status=TaskStatus.RUNNING, progress=10, message="Agent 正在读取上下文")
                turn = await self.service.answer(item.conversation_id, item.message_id, item.question, item.task_id)
                self.database.save_chat_message(ChatMessage(
                    message_id=f"msg_{uuid4().hex[:12]}", conversation_id=item.conversation_id,
                    role="assistant", content=turn.content, citations=turn.citations,
                    task_id=item.task_id, source=turn.source, created_at=now_iso(),
                ))
                self.database.update_task(item.task_id, status=TaskStatus.SUCCEEDED, progress=100, message="回复已生成")
            except ResumeError as exc:
                self.database.save_chat_message(ChatMessage(
                    message_id=f"msg_{uuid4().hex[:12]}", conversation_id=item.conversation_id,
                    role="assistant", content="这次回复的证据没有通过校验，未展示未经证实的结论。",
                    status="FAILED_VALIDATION", task_id=item.task_id, source="system", created_at=now_iso(),
                ))
                self.database.update_task(item.task_id, status=TaskStatus.FAILED_VALIDATION,
                                          progress=100, message="回复证据校验失败", error=str(exc))
            except Exception as exc:
                self.database.save_chat_message(ChatMessage(
                    message_id=f"msg_{uuid4().hex[:12]}", conversation_id=item.conversation_id,
                    role="assistant", content="Agent 暂时无法生成回复，请稍后重新发送。", status="FAILED",
                    task_id=item.task_id, source="system", created_at=now_iso(),
                ))
                self.database.update_task(item.task_id, status=TaskStatus.FAILED, progress=100,
                                          message="对话生成失败", error=str(exc))
            finally:
                self.queue.task_done()
