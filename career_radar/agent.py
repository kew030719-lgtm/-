from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .resume import validate_citations
from .schemas import CandidateProfile, Comparison, Evidence, JobScore, JobSnapshot, RoleRecommendation


ROLE_RULES = (
    ("AI Agent 应用开发", ["Agent", "LLM", "RAG", "大模型", "LangChain"], ["AI Agent", "LLM 应用", "RAG"]),
    ("Python 后端开发", ["Python", "FastAPI", "Django", "Flask", "SQL", "Redis"], ["Python 后端", "FastAPI", "Web API"]),
    ("Python 爬虫工程师", ["爬虫", "数据采集", "Playwright", "Scrapy", "Requests", "HTTPX"], ["Python 爬虫", "数据采集", "Playwright"]),
    ("数据工程师", ["Python", "SQL", "Pandas", "MySQL", "PostgreSQL"], ["数据工程", "Python SQL", "ETL"]),
)


SYSTEM_PROMPT = """你是 CareerRadar 的职业分析 Agent。只根据输入的结构化证据推理，不得补充不存在的经历。
只输出请求所规定的 JSON，不使用 Markdown。所有能力判断都要关联给定证据。"""


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

    async def _run_hermes(self, prompt: str, task_id: str) -> str:
        if not self.settings.api_key:
            raise RuntimeError("未配置 TOKEN_PLAN_API_KEY")
        python = self.settings.hermes_path / ".venv" / "bin" / "python"
        if not python.exists():
            python = Path(sys.executable)
        payload = {
            "hermes_path": str(self.settings.hermes_path),
            "hermes_home": str(self.settings.data_dir / "hermes-home"),
            "base_url": self.settings.model_base_url,
            "model": self.settings.model_name,
            "system_prompt": SYSTEM_PROMPT,
            "prompt": prompt,
            "task_id": task_id,
        }
        env = os.environ.copy()
        env["TOKEN_PLAN_API_KEY"] = self.settings.api_key
        process = await asyncio.create_subprocess_exec(
            str(python), "-m", "career_radar.hermes_runner",
            cwd=str(Path(__file__).resolve().parents[1]), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload, ensure_ascii=False).encode()), timeout=150,
        )
        if process.returncode != 0:
            message = stderr.decode(errors="replace")[-1200:]
            raise RuntimeError(f"Hermes 调用失败：{message}")
        if not stdout.strip():
            raise RuntimeError(f"Hermes 未返回结果：{stderr.decode(errors='replace')[-1200:]}")
        for line in reversed(stdout.decode(errors="replace").splitlines()):
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(response, dict) and "final_response" in response:
                return str(response["final_response"])
        raise RuntimeError("Hermes 输出中没有可识别的 final_response")

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
            raw = await self._run_hermes(prompt, f"roles-{profile.profile_id}")
            values = _extract_json(raw)
            recommendations = [RoleRecommendation.model_validate(item) for item in values]
            if len(recommendations) != 3:
                raise ValueError("模型没有返回恰好 3 个岗位方向")
            validate_citations([citation for item in recommendations for citation in item.citations], profile.evidence)
            profile.recommendations = recommendations
            profile.analysis_source = "hermes"
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
                value = _extract_json(await self._run_hermes(prompt, f"compare-{profile.profile_id}"))
                explanations = value.get("explanations", {})
                for score in scores:
                    if score.job_id in explanations:
                        score.explanation = str(explanations[score.job_id])[:500]
                if isinstance(value.get("plan"), list) and len(value["plan"]) == 7:
                    plan = [str(item)[:500] for item in value["plan"]]
                source = "hermes"
            except Exception:
                pass
        return Comparison(
            comparison_id=comparison_id or f"cmp_{uuid4().hex[:12]}", profile_id=profile.profile_id,
            rankings=sorted(scores, key=lambda item: item.total, reverse=True), action_plan=plan,
            source=source, created_at=datetime.now(UTC).isoformat(),
        )
