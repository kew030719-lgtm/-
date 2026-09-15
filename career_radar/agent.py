from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .agent_runtime import HybridAgentRuntime
from .config import Settings
from .resume import SKILLS, validate_citations
from .schemas import CandidateProfile, Comparison, Evidence, JobScore, JobSnapshot, RoleRecommendation
from .tools import ToolContext

if TYPE_CHECKING:
    from .schemas import CandidateContact, ResumeDraftVersion, ResumeTailoring, TargetJob


ROLE_RULES = (
    ("AI Agent 应用开发", ["Agent", "LLM", "RAG", "大模型", "LangChain"], ["AI Agent", "LLM 应用", "RAG"]),
    ("Python 后端开发", ["Python", "FastAPI", "Django", "Flask", "SQL", "Redis"], ["Python 后端", "FastAPI", "Web API"]),
    ("Python 爬虫工程师", ["爬虫", "数据采集", "Playwright", "Scrapy", "Requests", "HTTPX"], ["Python 爬虫", "数据采集", "Playwright"]),
    ("数据工程师", ["Python", "SQL", "Pandas", "MySQL", "PostgreSQL"], ["数据工程", "Python SQL", "ETL"]),
)


SYSTEM_PROMPT = """你是 CareerRadar 的职业分析 Agent。只根据输入的结构化证据推理，不得补充不存在的经历。
只输出请求所规定的 JSON，不使用 Markdown。所有能力判断都要关联给定证据。"""

CHAT_SYSTEM_PROMPT = """你是 CareerRadar 的可对话求职 Agent。使用 CareerRadar 工具读取候选人、岗位证据和固定评分。
对候选人、岗位、排名、能力和缺口的事实判断必须引用工具返回的 source_type/source_id/block_id；不要输出 quote，后端会补齐原文。
用户要求修改画像或搜索条件、重新搜索、开始分析或为具体岗位修改简历时，必须调用相应 propose 工具创建确认卡。propose 只提交建议，绝不能说操作已经执行。
通用职业咨询可以不调用工具。不得修改固定评分，不得虚构经历、职位或证据。
面向用户使用产品语言，不提“前端”“工具调用”等实现细节，不使用 Markdown 标记。
最终只输出 JSON：{"answer":"给用户的中文回复","citations":[{"source_type":"resume|job","source_id":"...","block_id":"..."}],"grounded":true|false}。"""


class RoleRecommendationsOutput(BaseModel):
    recommendations: list[RoleRecommendation] = Field(min_length=3, max_length=3)


class ChatAgentOutput(BaseModel):
    answer: str
    citations: list[dict[str, str]] = Field(default_factory=list)
    grounded: bool = False


class ComparisonAgentOutput(BaseModel):
    explanations: dict[str, str] = Field(default_factory=dict)
    plan: list[str] = Field(min_length=7, max_length=7)


class InterviewProseOutput(BaseModel):
    """One string. See the note on InterviewPrepOutput for why."""

    text: str = ""


class GreetingOutput(BaseModel):
    greeting: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class InterviewPrepOutput(BaseModel):
    """Two prose blocks, not nested JSON.

    Measured against the configured endpoint: asked for objects with five keys it
    fills only the first and narrates the rest; asked for two top-level lists it
    returns nothing usable; asked for `类别|问题` lines it returns exactly that,
    six for six, correctly categorised and grounded in the supplied evidence.
    Structured output is not reliable here, so the model writes prose and
    interview.py does the structuring and the grounding.

    That split is the right one anyway: the backend is what decides whether a
    citation holds, so it should not depend on the model supplying one.
    """

    questions: str = ""
    days: str = ""


class ResumeBulletOutput(BaseModel):
    bullet_id: str = ""
    text: str = ""
    name: str = ""
    skill: str = ""
    content: str = ""
    evidence_ids: list[str]
    provenance: str = ""
    priority: int = 50


class ResumeEntryOutput(BaseModel):
    bullets: list[ResumeBulletOutput] = Field(default_factory=list)


class ResumeSectionOutput(BaseModel):
    section_id: str = ""
    title: str = ""
    bullets: list[ResumeBulletOutput] = Field(default_factory=list)
    entries: list[ResumeEntryOutput] = Field(default_factory=list)


class ResumeDraftOutput(BaseModel):
    headline: str
    summary: list[ResumeBulletOutput]
    skills: list[ResumeBulletOutput]
    sections: list[ResumeSectionOutput]


def _extract_json(text: str):
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start_candidates = [index for index in (stripped.find("["), stripped.find("{")) if index >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        closer = "]" if stripped[start] == "[" else "}"
        end = stripped.rfind(closer)
        return json.loads(stripped[start:end + 1])


class AgentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.runtime = HybridAgentRuntime(settings)

    async def _run_structured(
        self, prompt: str, task_id: str, output_type: Any, *, system_prompt: str = SYSTEM_PROMPT,
        tool_context: ToolContext | None = None, timeout_seconds: float = 150,
        max_tokens: int | None = None, api_max_retries: int = 2,
    ) -> tuple[Any, list[str], str]:
        result = await asyncio.wait_for(
            self.runtime.run(
                prompt, task_id, output_type, system_prompt=system_prompt,
                tool_context=tool_context,
                max_tokens=max_tokens, api_max_retries=api_max_retries,
            ), timeout=timeout_seconds,
        )
        return result.output, result.registered_tools, "langgraph"

    async def run_chat(self, prompt: str, task_id: str, conversation_id: str,
                       message_id: str) -> tuple[str, list[str]]:
        output, registered, _source = await self._run_structured(
            prompt, task_id, ChatAgentOutput, system_prompt=CHAT_SYSTEM_PROMPT,
            tool_context=ToolContext(
                database_path=self.settings.database_path, tool_mode="chat",
                conversation_id=conversation_id, message_id=message_id,
            ),
        )
        return output.model_dump_json(), registered

    async def tailor_resume(self, profile: "CandidateProfile", target: "TargetJob",
                            tailoring: "ResumeTailoring", contact: "CandidateContact",
                            fallback: "ResumeDraftVersion") -> "ResumeDraftVersion":
        from .schemas import ResumeDraftVersion

        prompt = """为当前目标岗位生成一份中文定向简历内容。只调用一次 read_tailoring_context；返回内容已经包含目标 JD 和高相关候选人证据，不再搜索。
只使用工具返回的候选人证据，不得输出姓名、电话、邮箱或地址，不得虚构技能、职责、公司、学校、日期或数字。
每个 summary、skills、sections 中的 bullet 都必须给出 evidence_ids，值必须是候选人证据的 block_id。
返回 JSON，字段为 headline、summary、skills、sections。ResumeBullet 字段：bullet_id、text、evidence_ids、provenance、priority。
ResumeSection 字段：section_id、title、entries、bullets。entries 可以为空数组。priority 为 0 到 100。
只输出 2 条个人概述、最多 8 个技能、最多 4 个区块且每区块最多 4 条；不要重复同一事实。适合 1 至 2 页简历。
当前 tailoring_id：""" + tailoring.tailoring_id
        output, registered, runtime_source = await self._run_structured(
            prompt, f"tailor-{tailoring.tailoring_id}", ResumeDraftOutput,
            system_prompt="你是 CareerRadar 定向简历 Agent。所有简历事实必须有候选人证据，只返回指定 JSON。",
            tool_context=ToolContext(
                database_path=self.settings.database_path, tool_mode="tailoring",
                profile_id=profile.profile_id, target_job_id=target.target_job_id,
                tailoring_id=tailoring.tailoring_id,
            ), timeout_seconds=150,
            max_tokens=5000, api_max_retries=1,
        )
        if not registered:
            raise RuntimeError("CareerRadar 定向简历工具未加载")
        value = output.model_dump()
        evidence = {item.block_id: item for item in profile.evidence}

        def normalize_bullet(raw: object, index: int) -> dict:
            if not isinstance(raw, dict):
                raise ValueError("模型返回了无效的简历条目")
            ids = [str(item) for item in raw.get("evidence_ids", []) if str(item) in evidence]
            if not ids:
                raise ValueError("模型返回的简历条目缺少有效证据")
            try:
                priority = max(0, min(100, int(raw.get("priority", 50))))
            except (TypeError, ValueError):
                priority = 50
            text = str(
                raw.get("text") or raw.get("skill") or raw.get("name") or
                raw.get("content") or evidence[ids[0]].quote
            ).strip()
            cited_text = " ".join(evidence[item].quote for item in ids).lower()
            claims = [token for token in SKILLS if token in text.lower() and token not in cited_text]
            claims.extend(number for number in re.findall(r"\d+(?:\.\d+)?%?", text) if number not in cited_text)
            for claim in claims:
                match = next((item for item in evidence.values() if claim.lower() in item.quote.lower()), None)
                if match and match.block_id not in ids:
                    ids.append(match.block_id)
                    cited_text += " " + match.quote.lower()
            provenances = {evidence[item].provenance or "uploaded_resume" for item in ids}
            provenance = "user_confirmed" if "user_confirmed" in provenances else "uploaded_resume"
            return {
                "bullet_id": str(raw.get("bullet_id") or f"bullet_{uuid4().hex[:10]}"),
                "text": text,
                "evidence_ids": ids, "provenance": provenance, "priority": priority,
            }

        summary = [normalize_bullet(item, index) for index, item in enumerate(value.get("summary") or [])][:3]
        skills = [normalize_bullet(item, index) for index, item in enumerate(value.get("skills") or [])][:8]
        sections = []
        for section_index, raw_section in enumerate(value.get("sections") or []):
            if not isinstance(raw_section, dict):
                continue
            section_bullets = [
                normalize_bullet(item, index)
                for index, item in enumerate(raw_section.get("bullets") or [])
            ]
            # Entry headings and date ranges do not carry evidence IDs in the public
            # schema. Flatten their cited bullets so uncited company/date metadata can
            # never enter an exported resume.
            for raw_entry in raw_section.get("entries") or []:
                if not isinstance(raw_entry, dict):
                    continue
                section_bullets.extend(
                    normalize_bullet(item, index)
                    for index, item in enumerate(raw_entry.get("bullets") or [])
                )
            sections.append({
                "section_id": str(raw_section.get("section_id") or f"section_{section_index + 1}"),
                "title": str(raw_section.get("title") or "相关经历").strip(),
                "entries": [], "bullets": section_bullets[:5],
            })
        return ResumeDraftVersion.model_validate({
            **fallback.model_dump(),
            "headline": value.get("headline", fallback.headline),
            "summary": summary or fallback.summary,
            "skills": skills or fallback.skills,
            "sections": sections or fallback.sections,
            "contact": contact.model_dump(), "source": runtime_source,
        })

    async def compose_greeting(self, profile: CandidateProfile, snapshot: "JobSnapshot") -> tuple[GreetingOutput, str]:
        """The message a candidate sends a recruiter alongside their resume.

        Short, specific, and grounded: it may only claim what the candidate's
        evidence supports, and it must not contain contact details — the model
        never receives them, so any that appear were invented.
        """
        prompt = """为这个岗位写一条给招聘者的打招呼语。中文，不超过 120 字。
只使用给定候选人证据里真实存在的技能与经历，不得编造公司、项目或数字。
用一句话点出与岗位最相关的匹配点，语气礼貌、具体，避免空话套话。
不要写姓名、电话、邮箱或地址——后端会在导出时合并联系方式。
返回 JSON：{"greeting": "...", "evidence_ids": ["block_id", ...]}。
evidence_ids 只能取候选人证据的 block_id，至少一个。
evidence_ids 必须覆盖打招呼语里的每一项事实：提到某项技能就引用该技能所在的证据，
提到工作年限就引用说明年限的证据，提到项目就引用该项目的证据。
后端会逐字核对——少引一条，整条打招呼语都会被判为无法证实。
输入：\n""" + json.dumps({
            "profile": {"skills": profile.skills, "experience_years": profile.experience_years,
                        "education": profile.education,
                        "evidence": [item.model_dump() for item in profile.evidence]},
            "job": {"title": snapshot.title, "company": snapshot.company,
                    "responsibilities": snapshot.responsibilities[:10],
                    "required_skills": snapshot.required_skills[:10]},
        }, ensure_ascii=False)
        output, _registered, runtime_source = await self._run_structured(
            prompt, f"greeting-{snapshot.snapshot_id}", GreetingOutput,
            system_prompt=SYSTEM_PROMPT, timeout_seconds=90, max_tokens=800,
        )
        return output, runtime_source

    async def prepare_interview(self, profile: CandidateProfile, snapshot: "JobSnapshot",
                                missing_skills: list[str]) -> tuple[InterviewPrepOutput, str]:
        """Per-posting preparation: a day-by-day plan and grounded questions.

        Unlike enrich_comparison's single plan for the top three, this is scoped to
        one posting. Every question cites an evidence block id — the model never
        authors quotes, the backend fills them in.
        """
        # Two calls, each asking for one plain string. A single-field object is
        # the shape this endpoint answers reliably; the multi-field nested one it
        # does not. Two calls cost more wall-clock than one, but a single call
        # asking for both lists came back empty and unusable.
        evidence = json.dumps({
            "profile": {"skills": profile.skills, "experience_years": profile.experience_years,
                        "education": profile.education,
                        "evidence": [item.model_dump() for item in profile.evidence]},
            "job": {"title": snapshot.title, "company": snapshot.company,
                    "responsibilities": snapshot.responsibilities[:10],
                    "required_skills": snapshot.required_skills[:10]},
            "missing_skills": missing_skills,
        }, ensure_ascii=False)

        questions_prompt = """根据下面的证据，为这个岗位面试列出 6 个问题。
每行一个，格式固定为：类别|问题
类别只能是：技术深挖、项目经历、能力缺口、行为面、反问
问题必须扣住证据里真实出现的技能或经历；针对岗位要求但简历没有的技能，用「能力缺口」类别，如实说明缺口。
除此之外不要输出任何内容：不要编号，不要解释，不要复述本提示，不要输出推理过程。
输入：\n""" + evidence
        days_prompt = """根据下面的证据，为这个岗位面试安排 7 天准备计划。
每行一天，格式固定为：重点|交付物
重点不超过 30 字，交付物不超过 20 字。第 1 天到第 7 天按顺序排列。
除此之外不要输出任何内容：不要编号，不要解释，不要复述本提示，不要输出推理过程。
输入：\n""" + evidence

        questions_text, runtime_source = await self._prose(
            questions_prompt, f"interview-q-{snapshot.snapshot_id}", max_tokens=2000,
        )
        days_text, _ = await self._prose(
            days_prompt, f"interview-d-{snapshot.snapshot_id}", max_tokens=1500,
        )
        return InterviewPrepOutput(questions=questions_text, days=days_text), runtime_source

    async def _prose(self, prompt: str, task_id: str, *, max_tokens: int) -> tuple[str, str]:
        output, _registered, runtime_source = await self._run_structured(
            prompt, task_id, InterviewProseOutput,
            system_prompt=SYSTEM_PROMPT, timeout_seconds=240, max_tokens=max_tokens,
        )
        return output.text or "", runtime_source

    @staticmethod
    def _fallback_recommendations(profile: CandidateProfile) -> list[RoleRecommendation]:
        evidence_text = " ".join(item.quote.lower() for item in profile.evidence)
        scored = []
        for role, signals, keywords in ROLE_RULES:
            hits = [signal for signal in signals if signal.lower() in evidence_text]
            scored.append((len(hits), role, signals, keywords, hits))
        scored.sort(key=lambda item: item[0], reverse=True)
        recommendations: list[RoleRecommendation] = []
        for count, role, signals, keywords, hits in scored[:3]:
            relevant = [item for item in profile.evidence if any(signal.lower() in item.quote.lower() for signal in signals)]
            citations = relevant[:2] or profile.evidence[:1]
            confidence = "高" if count >= 3 else "中" if count >= 1 else "低"
            recommendations.append(RoleRecommendation(
                role=role, keywords=keywords, confidence=confidence,
                rationale=f"简历中识别到 {('、'.join(hits) if hits else '可迁移能力')}，适合进一步验证该方向。",
                strengths=hits or profile.skills[:3],
                gaps=[skill for skill in signals if skill not in hits][:3], citations=citations,
            ))
        return recommendations

    async def recommend_roles(self, profile: CandidateProfile) -> CandidateProfile:
        prompt = """根据候选人证据推荐恰好 3 个岗位方向。返回 JSON 数组，每项字段：
role, keywords(1-4个), confidence(高/中/低), rationale, strengths, gaps, citations。
citations 中每项必须逐字复制给定 evidence 的 source_type/source_id/block_id/quote。
候选人资料：\n""" + json.dumps({
            "skills": profile.skills, "experience_years": profile.experience_years,
            "education": profile.education,
            "evidence": [item.model_dump() for item in profile.evidence],
        }, ensure_ascii=False)
        try:
            prompt = prompt.replace("返回 JSON 数组", "返回包含 recommendations 字段的 JSON 对象")
            output, _registered, runtime_source = await self._run_structured(
                prompt, f"roles-{profile.profile_id}", RoleRecommendationsOutput,
            )
            recommendations = output.recommendations
            validate_citations([citation for item in recommendations for citation in item.citations], profile.evidence)
            profile.recommendations = recommendations
            profile.analysis_source = runtime_source
        except Exception:
            profile.recommendations = self._fallback_recommendations(profile)
            profile.analysis_source = "fallback"
        return profile

    async def enrich_comparison(self, profile: CandidateProfile, jobs: list[JobSnapshot], scores: list[JobScore],
                                comparison_id: str | None = None) -> Comparison:
        top = sorted(scores, key=lambda item: item.total, reverse=True)[:3]
        plan = [
            "第 1 天：阅读前三岗位证据，列出共同必需技能与硬性风险。",
            "第 2 天：补齐最高频缺口，完成一个最小可运行示例。",
            "第 3 天：把示例扩展为带测试和错误处理的小项目。",
            "第 4 天：按岗位职责改写一段项目经历，并保留可核验指标。",
            "第 5 天：准备 8 个项目追问，使用 STAR 结构口述回答。",
            "第 6 天：完成一次限时模拟面试，记录薄弱问题。",
            "第 7 天：修订简历与作品说明，优先投递排名最高且无硬性冲突的岗位。",
        ]
        source = "fallback"
        if self.settings.api_key and top:
            prompt = """基于固定评分和证据，为每个 job_id 写一条 80 字内解释，并给出 7 条逐日准备计划。
不得修改分数，不得声称证据之外的能力。只返回 {"explanations":{"job_id":"..."},"plan":["..."]}。
输入：\n""" + json.dumps({
                "profile": {"skills": profile.skills, "evidence": [e.model_dump() for e in profile.evidence]},
                "jobs": [job.model_dump() for job in jobs if job.snapshot_id in {s.job_id for s in top}],
                "scores": [score.model_dump() for score in top],
            }, ensure_ascii=False)
            try:
                output, _registered, runtime_source = await self._run_structured(
                    prompt, f"compare-{profile.profile_id}", ComparisonAgentOutput,
                )
                explanations = output.explanations
                for score in scores:
                    if score.job_id in explanations:
                        score.explanation = str(explanations[score.job_id])[:500]
                plan = [str(item)[:500] for item in output.plan]
                source = runtime_source
            except Exception:
                pass
        return Comparison(
            comparison_id=comparison_id or f"cmp_{uuid4().hex[:12]}", profile_id=profile.profile_id,
            rankings=sorted(scores, key=lambda item: item.total, reverse=True), action_plan=plan,
            source=source, created_at=datetime.now(UTC).isoformat(),
        )
