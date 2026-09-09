import asyncio
from pathlib import Path

import httpx

from career_radar.config import Settings
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


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db", model_base_url="https://example.invalid/v1",
        model_name="test", api_key="", crawl_delay_seconds=0, crawl_max_jobs=3,
        hermes_path=tmp_path / "missing-hermes",
    )


async def wait_task(client: httpx.AsyncClient, task_id: str):
    for _ in range(80):
        value = (await client.get(f"/api/tasks/{task_id}")).json()
        if value["status"] not in {"QUEUED", "RUNNING"}:
            return value
        await asyncio.sleep(.025)
    raise AssertionError("task did not finish")


def test_submit_confirm_manual_job_compare_flow(tmp_path):
    asyncio.run(run_flow(tmp_path))


async def run_flow(tmp_path):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        profile_response = await client.post("/api/resumes", json={"text": RESUME})
        assert profile_response.status_code == 200
        profile = profile_response.json()
        assert len(profile["recommendations"]) == 3
        profile_id = profile["profile_id"]

        update = await client.put(f"/api/profiles/{profile_id}", json={
            "selected_roles": profile["recommendations"][:1], "cities": ["北京"],
            "salary_preference": "20-30K", "experience_years": 2,
        })
        assert update.status_code == 200 and update.json()["confirmed"] is True

        database = app.state.database
        run_id = "task_manual"
        database.create_task(run_id, "discovery", {"profile_id": profile_id})
        manual = await client.post(f"/api/jobs/{run_id}/manual-content", json={
            "url": "https://www.zhipin.com/job_detail/manual001.html", "city": "北京",
            "content": "Python 后端工程师\n公司：星图科技\n薪资 20-30K\n2 年以上 本科\n负责 FastAPI 异步 API、SQL 数据处理和 Docker 部署。",
        })
        assert manual.status_code == 200

        queued = (await client.post("/api/comparisons", json={"profile_id": profile_id, "run_id": run_id})).json()
        task = await wait_task(client, queued["task_id"])
        assert task["status"] == "SUCCEEDED"
        report = await client.get(f"/api/tasks/{queued['task_id']}/comparison")
        assert report.status_code == 200
        assert (await client.get(f"/api/comparisons/{queued['comparison_id']}")).status_code == 200
        assert report.json()["rankings"][0]["citations"]
        assert len(report.json()["action_plan"]) == 7


def test_failed_task_can_be_retried(tmp_path):
    asyncio.run(run_retry(tmp_path))


async def run_retry(tmp_path):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        app.state.database.create_task("failed_one", "comparison", {"profile_id": "missing", "run_id": "missing"})
        app.state.database.update_task("failed_one", status="FAILED", error="original failure")
        response = await client.post("/api/tasks/failed_one/retry")
        assert response.status_code == 202
        assert response.json()["retried_from"] == "failed_one"
        assert response.json()["task_id"] != "failed_one"
