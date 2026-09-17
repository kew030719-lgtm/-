from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from career_radar.agent import AgentService
from career_radar.config import Settings
from career_radar.database import Database
from career_radar.resume import build_profile, extract_candidate_contact
from career_radar.tailoring import TailoringService, _fallback_draft, _public_profile, target_from_pasted


SAMPLE_RESUME = """李同学
计算机科学 本科 2022.09-2026.06
专业技能
Python FastAPI SQL Docker Git
项目经历
校园招聘分析平台 2025.01-2025.05
使用 Python 和 FastAPI 开发岗位采集与匹配接口，并编写自动化测试。
实现任务断点恢复和岗位去重，完成接口文档与部署说明。
数据分析课程项目 2024.03-2024.06
使用 SQL 和 Pandas 清洗公开数据并制作分析报告。
"""

SAMPLE_JOB = """负责 Python 后端服务和 FastAPI 接口开发，编写自动化测试与接口文档。
要求掌握 Python、FastAPI、SQL、Git，具备良好的工程实践和问题定位能力。
有 Docker 部署经验优先。"""


async def run(output: Path, *, layout_only: bool = False) -> dict:
    configured = Settings.load()
    if not configured.api_key and not layout_only:
        raise SystemExit("未配置 TOKEN_PLAN_API_KEY，无法执行真实模型冒烟测试")
    with tempfile.TemporaryDirectory(prefix="career-radar-tailoring-") as folder:
        data_dir = Path(folder)
        settings = replace(configured, data_dir=data_dir, database_path=data_dir / "smoke.db")
        database = Database(settings.database_path)
        database.initialize()
        agent = AgentService(settings)
        service = TailoringService(database, agent, data_dir)

        profile = build_profile(SAMPLE_RESUME, "profile_live_tailoring")
        contact = extract_candidate_contact(profile)
        profile.confirmed = True
        database.save_profile(profile)
        database.save_contact(contact)
        target = target_from_pasted(
            profile.profile_id, "示例科技", "Python 后端工程师", SAMPLE_JOB,
        )
        database.save_target_job(target)
        tailoring = service.create_tailoring(profile.profile_id, target.target_job_id)
        if tailoring.questions:
            tailoring = service.save_answers(
                tailoring.tailoring_id, {item.question_id: None for item in tailoring.questions},
            )
        if layout_only:
            profile = service.confirm_facts(tailoring)
            public = _public_profile(profile, contact)
            version = _fallback_draft(public, contact, target, tailoring, "draft_layout_check")
        else:
            version = await service.generate(tailoring.tailoring_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        service.render_docx(version, target, "technical", output)
        return {
            "output": str(output), "source": version.source, "layout_only": layout_only,
            "quality_report": version.quality_report.model_dump(),
            "sections": len(version.sections),
            "entries": sum(len(section.entries) for section in version.sections),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="使用当前模型生成并导出一份脱敏定向简历")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--layout-only", action="store_true", help="仅生成脱敏排版样本，不调用模型")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.output.resolve(), layout_only=args.layout_only)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
