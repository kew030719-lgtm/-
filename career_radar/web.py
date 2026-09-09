from __future__ import annotations

import html
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .agent import AgentService
from .browser_session import BossLoginSession, BrowserSessionError
from .config import ROOT, Settings
from .crawler import CrawlError, job_identity, parse_manual_content
from .database import Database
from .resume import ResumeError, SUPPORTED_CITIES, build_profile, extract_resume_text
from .schemas import RoleRecommendation
from .services import TaskQueue


class ProfileUpdate(BaseModel):
    selected_roles: list[RoleRecommendation] = Field(min_length=1, max_length=2)
    cities: list[str] = Field(min_length=1, max_length=2)
    salary_preference: str | None = None
    experience_years: float | None = None
    work_type_preference: str | None = None


class DiscoveryRequest(BaseModel):
    profile_id: str


class ComparisonRequest(BaseModel):
    profile_id: str
    run_id: str


class ManualContentRequest(BaseModel):
    url: str
    content: str = Field(min_length=30, max_length=80_000)
    city: str = ""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    database = Database(settings.database_path)
    agent = AgentService(settings)
    login_session = BossLoginSession()
    worker = TaskQueue(database, agent, crawl_delay=settings.crawl_delay_seconds, max_jobs=settings.crawl_max_jobs)
    worker.browser_state_provider = login_session.storage_state

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        await worker.start()
        yield
        await worker.stop()
        await login_session.close()

    app = FastAPI(title="CareerRadar", version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(ROOT / "career_radar" / "templates"))
    app.mount("/static", StaticFiles(directory=str(ROOT / "career_radar" / "static")), name="static")
    app.state.database = database
    app.state.agent = agent
    app.state.worker = worker
    app.state.login_session = login_session
    app.state.settings = settings

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"cities": SUPPORTED_CITIES})

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": settings.model_name, "api_key_configured": bool(settings.api_key)}

    @app.get("/fragments/health", response_class=HTMLResponse)
    async def health_fragment():
        model_state = html.escape(settings.model_name) if settings.api_key else "未配置模型密钥"
        return f'<span class="pulse"></span> 本地服务正常 · {model_state}'

    @app.post("/api/resumes")
    async def create_resume(request: Request):
        try:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                body = await request.json()
                text = str(body.get("text", ""))
            else:
                form = await request.form()
                text = str(form.get("text", ""))
                upload = form.get("file")
                if upload is not None and hasattr(upload, "read") and getattr(upload, "filename", ""):
                    data = await upload.read(10 * 1024 * 1024 + 1)
                    text = extract_resume_text(data, upload.filename, upload.content_type or "")
            profile = build_profile(text)
            profile = await agent.recommend_roles(profile)
            database.save_profile(profile)
            return profile
        except ResumeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/resumes/{profile_id}/profile")
    async def get_profile(profile_id: str):
        profile = database.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="候选人画像不存在")
        return profile

    @app.put("/api/profiles/{profile_id}")
    async def update_profile(profile_id: str, update: ProfileUpdate):
        profile = database.get_profile(profile_id)
        if not profile:
            raise HTTPException(status_code=404, detail="候选人画像不存在")
        allowed = {city for city in SUPPORTED_CITIES}
        if any(city not in allowed for city in update.cities):
            raise HTTPException(status_code=422, detail="包含暂不支持的城市")
        profile.selected_roles = update.selected_roles
        profile.cities = update.cities
        profile.salary_preference = update.salary_preference
        profile.work_type_preference = update.work_type_preference
        if update.experience_years is not None:
            profile.experience_years = update.experience_years
        profile.confirmed = True
        database.save_profile(profile)
        return profile

    @app.post("/api/discovery-runs", status_code=202)
    async def discovery_run(body: DiscoveryRequest):
        profile = database.get_profile(body.profile_id)
        if not profile or not profile.confirmed:
            raise HTTPException(status_code=409, detail="请先确认候选人画像和岗位方向")
        task_id = await worker.submit("discovery", body.model_dump())
        return {"task_id": task_id, "status": "QUEUED"}

    @app.get("/api/tasks/{task_id}")
    async def task_status(task_id: str):
        task = database.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @app.delete("/api/tasks/{task_id}", status_code=202)
    async def cancel_task(task_id: str):
        if not database.get_task(task_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        worker.cancel(task_id)
        return {"task_id": task_id, "cancel_requested": True}

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    async def retry_task(task_id: str):
        task = database.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] in {"QUEUED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="任务仍在运行")
        new_task_id = await worker.submit(task["kind"], task["payload"])
        return {"task_id": new_task_id, "status": "QUEUED", "retried_from": task_id}

    @app.get("/api/browser-session")
    async def browser_session_status():
        return login_session.status()

    @app.post("/api/browser-session/start")
    async def start_browser_session():
        try:
            return await login_session.start()
        except BrowserSessionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/browser-session/preview")
    async def browser_session_preview():
        try:
            screenshot = await login_session.screenshot()
            return Response(
                content=screenshot, media_type="image/png",
                headers={"Cache-Control": "no-store, max-age=0"},
            )
        except BrowserSessionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/browser-session/confirm")
    async def confirm_browser_session():
        try:
            return await login_session.confirm()
        except BrowserSessionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/browser-session")
    async def clear_browser_session():
        await login_session.clear()
        return {"confirmed": False, "session_in_memory": False}

    @app.post("/api/jobs/{run_id}/manual-content")
    async def manual_content(run_id: str, body: ManualContentRequest):
        task = database.get_task(run_id)
        if not task:
            raise HTTPException(status_code=404, detail="抓取任务不存在")
        try:
            snapshot = parse_manual_content(body.content, body.url, body.city)
        except CrawlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        database.save_snapshot(job_identity(snapshot), run_id, snapshot)
        database.update_task(run_id, status="SUCCEEDED", progress=100, message="已保存手动岗位描述")
        return snapshot

    @app.get("/api/jobs")
    async def list_jobs(run_id: str | None = None):
        return database.list_snapshots(run_id)

    @app.post("/api/comparisons", status_code=202)
    async def create_comparison(body: ComparisonRequest):
        comparison_id = f"cmp_{uuid4().hex[:12]}"
        task_id = await worker.submit("comparison", {**body.model_dump(), "comparison_id": comparison_id})
        return {"task_id": task_id, "comparison_id": comparison_id, "status": "QUEUED"}

    @app.get("/api/comparisons/{comparison_id}")
    async def get_comparison(comparison_id: str):
        comparison = database.get_comparison(comparison_id)
        if not comparison:
            raise HTTPException(status_code=404, detail="分析报告不存在")
        return comparison

    @app.get("/api/tasks/{task_id}/comparison")
    async def comparison_by_task(task_id: str):
        comparison = database.get_comparison_by_task(task_id)
        if not comparison:
            raise HTTPException(status_code=404, detail="分析尚未完成")
        return comparison

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("career_radar.web:app", host="0.0.0.0", port=8000, reload=False)
