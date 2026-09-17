from __future__ import annotations

import html
import io
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .agent import AgentService
from .apply import ApplyService
from .chat import ChatQueue, ChatService
from .config import ROOT, Settings
from .database import Database, now_iso
from .interview import InterviewService
from .local_settings import LocalSettingsStore, MemorySecretStore
from .memory import MemoryService
from .metrics import failure_category, present_metric
from .privacy import delete_local_data, export_archive
from .resume import (
    SUPPORTED_CITIES,
    ResumeError,
    build_profile,
    extract_candidate_contact,
    ScannedResumeError, extract_resume_text,
)
from .vision import VisionResumeReader
from .schemas import (
    ChatActionKind,
    ResumeDraftVersion,
    ResumeExport,
    RoleRecommendation,
)
from .services import TaskQueue
from .sites import SITES, CrawlError, cities_for, get_site, job_identity, site_for_url
from .sites.base import role_search_terms
from .tailoring import TailoringService, target_from_pasted, target_from_snapshot


class ProfileUpdate(BaseModel):
    selected_roles: list[RoleRecommendation] = Field(min_length=1, max_length=2)
    cities: list[str] = Field(min_length=1, max_length=2)
    salary_preference: str | None = None
    experience_years: float | None = None
    work_type_preference: str | None = None
    expected_graduation_year: int | None = Field(default=None, ge=2000, le=2100)


def site_allow_map(site_keys: list[str]) -> dict[str, dict[str, object]]:
    """What the Chrome helper is allowed to navigate to, per site.

    Shipped to the extension rather than hardcoded there, so adding a job board
    needs no extension change. ``path_pattern`` always matches URL.pathname.
    """
    return {
        key: {
            "hosts": sorted(SITES[key].hosts),
            "path_pattern": SITES[key].job_path_pattern,
            "login_pattern": SITES[key].login_pattern,
        }
        for key in site_keys
        if key in SITES
    }


class InterviewPrepRequest(BaseModel):
    profile_id: str
    snapshot_id: str


class ApplicationRequest(BaseModel):
    profile_id: str
    snapshot_id: str


class ApplicationUpdate(BaseModel):
    status: str
    application_deadline: str | None = Field(default=None, max_length=20)
    reminder_at: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class DiscoveryRequest(BaseModel):
    profile_id: str
    mode: str = "browser"
    # Which job boards to crawl. Every site contributes its own adapters, cities
    # and rate limit.
    sites: list[str] = Field(default_factory=lambda: ["boss"])


class ComparisonRequest(BaseModel):
    profile_id: str
    run_id: str


class ManualContentRequest(BaseModel):
    url: str
    content: str = Field(min_length=30, max_length=80_000)
    city: str = ""


class BrowserCaptureRequest(BaseModel):
    run_id: str
    url: str
    html: str = Field(min_length=100, max_length=2_000_000)
    city: str = ""
    page_duration_ms: int | None = Field(default=None, ge=0, le=300_000)


class BrowserProgress(BaseModel):
    status: str
    message: str = Field(max_length=500)


class ModelSettingsUpdate(BaseModel):
    model_base_url: str = Field(min_length=8, max_length=2000)
    model_name: str = Field(min_length=1, max_length=200)
    vision_model_name: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=4000)


class ConversationCreate(BaseModel):
    profile_id: str | None = None
    run_id: str | None = None
    comparison_id: str | None = None


class ConversationContextUpdate(BaseModel):
    profile_id: str | None = None
    run_id: str | None = None
    comparison_id: str | None = None


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class TargetJobRequest(BaseModel):
    profile_id: str
    source_type: str
    snapshot_id: str | None = None
    company: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=80_000)
    url: str = Field(default="", max_length=2000)


class TailoringRequest(BaseModel):
    profile_id: str
    target_job_id: str
    conversation_id: str | None = None


class TailoringAnswers(BaseModel):
    answers: dict[str, str | None]


class ExportRequest(BaseModel):
    templates: list[str] = Field(default_factory=lambda: ["technical", "business"], min_length=1, max_length=2)


def create_app(settings: Settings | None = None) -> FastAPI:
    injected_settings = settings is not None
    settings = settings or Settings.load()
    # Injected Settings in tests must never touch an OS credential backend.
    setting_store = LocalSettingsStore(
        settings, MemorySecretStore(settings.api_key) if injected_settings else None,
    )
    settings = setting_store.settings
    database = Database(settings.database_path)
    agent = AgentService(settings)
    tailoring_service = TailoringService(database, agent, settings.data_dir)
    interview_service = InterviewService(database, agent)
    apply_service = ApplyService(database, agent)
    vision_reader = VisionResumeReader(settings)
    worker = TaskQueue(
        database, agent, crawl_delay=settings.crawl_delay_seconds,
        max_jobs=settings.crawl_max_jobs, tailoring_service=tailoring_service,
        interview_service=interview_service, apply_service=apply_service,
    )
    chat_queue = ChatQueue(database, ChatService(database, agent))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        await worker.start()
        await chat_queue.start()
        yield
        await chat_queue.stop()
        await worker.stop()

    app = FastAPI(title="CareerRadar", version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(ROOT / "career_radar" / "templates"))
    app.mount("/static", StaticFiles(directory=str(ROOT / "career_radar" / "static")), name="static")
    # The React SPA, built by `npm run build` in frontend/. It is absent from a
    # fresh checkout, in which case the legacy server-rendered page at / is the
    # UI and /app simply 404s.
    spa_dir = ROOT / "career_radar" / "static" / "app"
    if (spa_dir / "index.html").is_file():
        app.mount("/app", StaticFiles(directory=str(spa_dir), html=True), name="spa")
    app.state.database = database
    app.state.agent = agent
    app.state.worker = worker
    app.state.chat_queue = chat_queue
    app.state.tailoring_service = tailoring_service
    app.state.interview_service = interview_service
    app.state.apply_service = apply_service
    app.state.vision_reader = vision_reader
    app.state.settings = settings
    app.state.setting_store = setting_store

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"cities": SUPPORTED_CITIES})

    @app.get("/api/sites")
    async def sites():
        # Replaces the city list the Jinja template used to inject, so the SPA can
        # render the picker without server-side templating. Derived from the
        # registry, so a new adapter shows up here without touching this file.
        return [
            {"key": adapter.key, "label": adapter.label,
             # The site's own home, so the UI can link to whichever boards the
             # user actually picked instead of a hardcoded one.
             "home": adapter.default_base_url(),
             "cities": list(adapter.cities), "enabled": True}
            for adapter in SITES.values()
        ]

    @app.post("/api/browser-helper/heartbeat")
    async def browser_helper_heartbeat():
        database.set_runtime_state("browser_helper", {"connected": True})
        return {"ok": True}

    @app.get("/api/browser-helper/status")
    async def browser_helper_status():
        state = database.get_runtime_state("browser_helper")
        if not state:
            return {"connected": False, "last_seen_at": None, "help": "请安装并启用 CareerRadar Chrome 助手"}
        last_seen = datetime.fromisoformat(state["last_seen_at"])
        connected = (datetime.now(UTC) - last_seen).total_seconds() <= 75
        return {
            "connected": connected, "last_seen_at": state["last_seen_at"],
            "help": None if connected else "Chrome 助手超过 75 秒未连接，请检查扩展是否启用",
        }

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": settings.model_name, "runtime": "pydantic_langgraph",
                "api_key_configured": bool(settings.api_key)}

    @app.get("/api/settings")
    async def read_settings():
        return setting_store.public()

    @app.get("/api/local-data/export")
    async def export_local_data():
        content = export_archive(database, settings.data_dir, setting_store.public())
        return Response(
            content=content, media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="career-radar-data.zip"'},
        )

    @app.delete("/api/local-data", status_code=204)
    async def delete_all_local_data(confirmation: str = ""):
        if confirmation != "DELETE_ALL_LOCAL_DATA":
            raise HTTPException(409, "删除全部本地数据需要明确确认")
        delete_local_data(database, settings.data_dir, setting_store)
        return Response(status_code=204)

    @app.put("/api/settings/model")
    async def update_model_settings(body: ModelSettingsUpdate):
        try:
            setting_store.update(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return setting_store.public()

    @app.post("/api/settings/model/test")
    async def test_model_connection():
        if not settings.api_key:
            raise HTTPException(409, "请先配置模型密钥")
        try:
            async with httpx.AsyncClient(
                base_url=settings.model_base_url, timeout=10, trust_env=False,
                headers={"Authorization": f"Bearer {settings.api_key}"},
            ) as client:
                response = await client.get("/models")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, "模型服务连接失败，请检查地址、模型名和密钥") from exc
        return {"ok": True, "model_name": settings.model_name}

    @app.get("/fragments/health", response_class=HTMLResponse)
    async def health_fragment():
        model_state = html.escape(settings.model_name) if settings.api_key else "未配置模型密钥"
        runtime = "PydanticAI + LangGraph"
        return f'<span class="pulse"></span> 本地服务正常 · {html.escape(runtime)} · {model_state}'

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
                    try:
                        text = extract_resume_text(data, upload.filename, upload.content_type or "")
                    except ScannedResumeError:
                        text = await vision_reader.extract(data)
            profile = build_profile(text)
            contact = extract_candidate_contact(profile)
            profile = await agent.recommend_roles(profile)
            database.save_profile(profile)
            database.save_contact(contact)
            return profile
        except ResumeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/conversations")
    async def create_conversation(body: ConversationCreate):
        if (body.run_id or body.comparison_id) and not body.profile_id:
            raise HTTPException(422, "关联抓取任务或报告时必须同时关联候选人画像")
        if body.profile_id and not database.get_profile(body.profile_id):
            raise HTTPException(404, "候选人画像不存在")
        if body.run_id:
            run = database.get_task(body.run_id)
            if not run or run["kind"] != "discovery":
                raise HTTPException(404, "抓取任务不存在")
            if body.profile_id and run["payload"].get("profile_id") != body.profile_id:
                raise HTTPException(409, "抓取任务不属于当前候选人")
        if body.comparison_id:
            comparison = database.get_comparison(body.comparison_id)
            if not comparison:
                raise HTTPException(404, "分析报告不存在")
            if body.profile_id and comparison.profile_id != body.profile_id:
                raise HTTPException(409, "分析报告不属于当前候选人")
        return database.create_conversation(
            f"conv_{uuid4().hex[:12]}", profile_id=body.profile_id,
            run_id=body.run_id, comparison_id=body.comparison_id,
        )

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str):
        conversation = database.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(404, "对话不存在")
        return conversation

    @app.patch("/api/conversations/{conversation_id}/context")
    async def update_conversation_context(conversation_id: str, body: ConversationContextUpdate):
        current = database.get_conversation(conversation_id)
        if not current:
            raise HTTPException(404, "对话不存在")
        supplied = set(body.model_fields_set)
        if "profile_id" in supplied and body.profile_id and not database.get_profile(body.profile_id):
            raise HTTPException(404, "候选人画像不存在")
        target_profile_id = body.profile_id if "profile_id" in supplied else current.profile_id
        target_run_id = body.run_id if "run_id" in supplied else current.run_id
        target_comparison_id = body.comparison_id if "comparison_id" in supplied else current.comparison_id
        if (target_run_id or target_comparison_id) and not target_profile_id:
            raise HTTPException(409, "请先关联候选人画像，或同时清除任务和报告")
        if target_run_id:
            run = database.get_task(target_run_id)
            if not run or run["kind"] != "discovery":
                raise HTTPException(404, "抓取任务不存在")
            if target_profile_id and run["payload"].get("profile_id") != target_profile_id:
                raise HTTPException(409, "抓取任务不属于当前候选人")
        if target_comparison_id:
            comparison = database.get_comparison(target_comparison_id)
            if not comparison:
                raise HTTPException(404, "分析报告不存在")
            if target_profile_id and comparison.profile_id != target_profile_id:
                raise HTTPException(409, "分析报告不属于当前候选人")
        return database.update_conversation_context(
            conversation_id, profile_id=body.profile_id, run_id=body.run_id,
            comparison_id=body.comparison_id, supplied=supplied,
        )

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    async def create_chat_message(conversation_id: str, body: ChatMessageRequest):
        try:
            task_id, message_id = await chat_queue.submit(conversation_id, body.message.strip())
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"task_id": task_id, "message_id": message_id, "status": "QUEUED"}

    @app.get("/api/chat-turns/{task_id}")
    async def get_chat_turn(task_id: str):
        task = database.get_task(task_id)
        if not task or task["kind"] != "chat":
            raise HTTPException(404, "对话任务不存在")
        message = database.get_chat_message_by_task(task_id)
        actions = database.list_chat_actions(
            task["payload"]["conversation_id"], source_message_id=task["payload"]["message_id"],
        )
        return {"task": task, "message": message, "actions": actions}

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
        allowed = set(cities_for(list(SITES)))
        if any(city not in allowed for city in update.cities):
            raise HTTPException(status_code=422, detail="包含暂不支持的城市")
        profile.selected_roles = update.selected_roles
        profile.cities = update.cities
        profile.salary_preference = update.salary_preference
        profile.work_type_preference = update.work_type_preference
        profile.expected_graduation_year = update.expected_graduation_year
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
        if body.mode not in {"browser", "http"}:
            raise HTTPException(422, "不支持的采集方式")
        # Carry the navigation rules on the task itself. The helper can then pick them
# up from GET /api/tasks/{id} when it holds a job persisted by an older version,
# which avoids re-calling /start and throwing away the run's crawl progress.
        payload = {**body.model_dump(), "allow": site_allow_map(body.sites)}
        if body.mode == "browser":
            task_id = f"task_{uuid4().hex[:12]}"
            database.create_task(task_id, "discovery", payload)
            database.update_task(task_id, message="等待 Chrome 浏览器助手自动接单，请安装并启用助手")
        else:
            task_id = await worker.submit("discovery", payload)
        database.initialize_collection_metric(task_id, body.sites)
        return {"task_id": task_id, "status": "QUEUED"}

    @app.get("/api/tasks/{task_id}")
    async def task_status(task_id: str):
        task = database.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        # Runs created before the navigation rules were stored on the task still
        # need them, and they are derivable from the run's sites. The Chrome
        # helper reads them from here so it never has to re-call /start, which
        # would hand back a fresh queue and discard the run's progress.
        if task["kind"] == "discovery" and not task["payload"].get("allow"):
            task["payload"]["allow"] = site_allow_map(task["payload"].get("sites") or ["boss"])
        return task

    @app.delete("/api/tasks/{task_id}", status_code=202)
    async def cancel_task(task_id: str):
        if not database.get_task(task_id):
            raise HTTPException(status_code=404, detail="任务不存在")
        worker.cancel(task_id)
        task = database.get_task(task_id)
        if task["payload"].get("mode") == "browser":
            database.update_task(task_id, status="FAILED", message="任务已取消", error="任务已取消")
            database.update_collection_metric(task_id, status="FAILED", failure_category="cancelled")
        return {"task_id": task_id, "cancel_requested": True}

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    async def retry_task(task_id: str):
        task = database.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] in {"QUEUED", "RUNNING"}:
            raise HTTPException(status_code=409, detail="任务仍在运行")
        if task["kind"] == "chat":
            raise HTTPException(status_code=409, detail="为避免重复执行，请重新发送这条消息")
        if task["payload"].get("mode") == "browser":
            new_task_id = f"task_{uuid4().hex[:12]}"
            database.create_task(new_task_id, task["kind"], task["payload"])
        else:
            new_task_id = await worker.submit(task["kind"], task["payload"])
        if task["kind"] == "discovery":
            database.initialize_collection_metric(new_task_id, task["payload"].get("sites") or ["boss"])
        return {"task_id": new_task_id, "status": "QUEUED", "retried_from": task_id}

    @app.get("/browser-helper.zip")
    async def browser_helper_download():
        buffer = io.BytesIO()
        helper_dir = ROOT / "browser-extension"
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in helper_dir.iterdir():
                if path.is_file():
                    archive.write(path, path.name)
        return Response(
            content=buffer.getvalue(), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="career-radar-browser-helper.zip"'},
        )

    @app.get("/api/browser-runs/pending")
    async def pending_browser_run(preferred_run_id: str | None = None):
        if preferred_run_id:
            preferred = database.get_task(preferred_run_id)
            if (
                preferred
                and preferred["kind"] == "discovery"
                and preferred["payload"].get("mode") == "browser"
                and preferred["status"] in {"QUEUED", "NEEDS_MANUAL_INPUT"}
            ):
                return {"task_id": preferred_run_id}
            # A browser page that names a task is an explicit binding.  Once
            # that task is complete (or otherwise ineligible), do not silently
            # fall through to an unrelated queued run.
            return {"task_id": None}
        with database.connect() as db:
            rows = db.execute(
                "SELECT id FROM tasks WHERE kind='discovery' AND status IN ('QUEUED','NEEDS_MANUAL_INPUT') "
                "ORDER BY created_at DESC"
            ).fetchall()
        for row in rows:
            task = database.get_task(row["id"])
            if task["payload"].get("mode") == "browser":
                return {"task_id": task["id"]}
        return {"task_id": None}

    @app.post("/api/browser-runs/{run_id}/start")
    async def start_browser_run(run_id: str):
        task = database.get_task(run_id)
        if not task or task["kind"] != "discovery":
            raise HTTPException(404, "任务不存在")
        if task["payload"].get("mode") != "browser" or task["status"] not in {"QUEUED", "NEEDS_MANUAL_INPUT"}:
            raise HTTPException(409, "任务已被接单或已完成")
        profile = database.get_profile(task["payload"]["profile_id"])
        if not profile or not profile.confirmed:
            raise HTTPException(409, "请先确认画像")
        site_keys = task["payload"].get("sites") or ["boss"]
        searches = []
        for site_key in site_keys:
            adapter = get_site(site_key)
            for role in profile.selected_roles[:2]:
                for query in role_search_terms(role):
                    for city in profile.cities[:2]:
                        for page in (1, 2):
                            try:
                                url = adapter.search_url(query, city, page)
                            except CrawlError:
                                # This site does not serve that city; skip just this pair.
                                continue
                            searches.append({
                                "url": url, "city": city, "kind": "search", "site": site_key,
                                "render_wait_ms": adapter.render_wait_ms,
                            })
        if not searches:
            raise HTTPException(422, "所选站点都不支持当前城市")
        database.update_task(run_id, status="RUNNING", message="浏览器助手已接单，正在自动搜索岗位")
        database.update_collection_metric(run_id, status="RUNNING")
        # The helper stays site-agnostic: the host allow-list, URL shapes and login
        # detection travel with the queue rather than being hard-coded in the
        # extension, so adding a site needs no extension change.
        return {
            "task_id": run_id, "queue": searches,
            "max_jobs": min(20, settings.crawl_max_jobs),
            # The floor is the slowest selected site's own minimum, not just the
            # global CRAWL_DELAY_SECONDS: 智联 and 前程无忧 are less tolerant of
            # rapid traffic than BOSS.
            "interval_ms": max(
                10000,
                int(settings.crawl_delay_seconds * 1000),
                int(max((SITES[key].min_delay_seconds for key in site_keys if key in SITES), default=0) * 1000),
            ),
            "allow": site_allow_map(site_keys),
        }

    @app.post("/api/browser-runs/{run_id}/progress")
    async def browser_progress(run_id: str, body: BrowserProgress):
        task = database.get_task(run_id)
        if not task or task["kind"] != "discovery":
            raise HTTPException(404, "任务不存在")
        if task["status"] in {"SUCCEEDED", "FAILED", "FAILED_VALIDATION"}:
            raise HTTPException(409, "任务已结束")
        if body.status not in {"RUNNING", "NEEDS_MANUAL_INPUT", "FAILED"}:
            raise HTTPException(422, "无效状态")
        database.update_task(run_id, status=body.status, message=body.message)
        database.update_collection_metric(
            run_id, status=body.status,
            failure_category=failure_category(body.message, body.status),
            pause_reason=body.message if body.status == "NEEDS_MANUAL_INPUT" else None,
        )
        return {"status": body.status}

    @app.post("/api/browser-search-pages")
    async def browser_search(body: BrowserCaptureRequest):
        task = database.get_task(body.run_id)
        if not task or task["status"] != "RUNNING":
            raise HTTPException(409, "任务未运行")
        try:
            adapter = site_for_url(body.url)
            adapter.validate_url(body.url)
            database.update_collection_metric(
                body.run_id, site=adapter.key, pages=1, page_duration_ms=body.page_duration_ms,
            )
            links = adapter.parse_search_page(body.html, body.url)
            if not links and not any(word in body.html for word in ("暂无相关职位", "没有找到相关职位", "没有找到")):
                raise CrawlError("搜索页已加载，但没有识别到岗位卡片；可能搜索词无结果或页面结构已变化")
            return {"links": links}
        except (CrawlError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/browser-captures")
    async def browser_capture(body: BrowserCaptureRequest):
        task = database.get_task(body.run_id)
        if not task or task["kind"] != "discovery":
            raise HTTPException(status_code=404, detail="抓取任务不存在")
        if task["status"] in {"SUCCEEDED", "FAILED", "FAILED_VALIDATION"}:
            raise HTTPException(409, "任务已结束")
        if len(database.list_snapshots(body.run_id)) >= min(20, settings.crawl_max_jobs):
            raise HTTPException(409, "已达到岗位上限")
        try:
            adapter = site_for_url(body.url)
            database.update_collection_metric(
                body.run_id, site=adapter.key, pages=1, page_duration_ms=body.page_duration_ms,
            )
            snapshot = adapter.parse_job_page(body.html, body.url, body.city, "browser")
        except (CrawlError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        before = len(database.list_snapshots(body.run_id))
        database.save_snapshot(job_identity(snapshot), body.run_id, snapshot)
        count = len(database.list_snapshots(body.run_id))
        database.update_collection_metric(
            body.run_id, site=adapter.key,
            valid_jobs=1 if count > before else 0, duplicates=1 if count == before else 0,
        )
        database.update_task(
            body.run_id, status="RUNNING" if task["status"] == "RUNNING" else "NEEDS_MANUAL_INPUT", progress=min(90, 10 + count * 4),
            message=f"浏览器助手已采集 {count} 个唯一岗位",
        )
        return {"snapshot": snapshot, "count": count}

    @app.post("/api/browser-captures/{run_id}/finish")
    async def finish_browser_capture(run_id: str):
        task = database.get_task(run_id)
        if not task or task["kind"] != "discovery":
            raise HTTPException(status_code=404, detail="抓取任务不存在")
        if task["status"] in {"FAILED", "FAILED_VALIDATION"}:
            raise HTTPException(409, "任务已结束")
        count = len(database.list_snapshots(run_id))
        if not count:
            raise HTTPException(status_code=409, detail="请先使用浏览器助手采集至少一个岗位")
        database.update_task(
            run_id, status="SUCCEEDED", progress=100,
            message=f"浏览器助手采集完成，共保存 {count} 个唯一岗位",
        )
        database.update_collection_metric(run_id, status="SUCCEEDED")
        profile_id = task["payload"].get("profile_id")
        comparison = None
        if database.get_profile(profile_id):
            comparison = await create_comparison(ComparisonRequest(profile_id=profile_id, run_id=run_id))
        return {"run_id": run_id, "count": count, "status": "SUCCEEDED", "comparison": comparison}

    @app.post("/api/jobs/{run_id}/manual-content")
    async def manual_content(run_id: str, body: ManualContentRequest):
        task = database.get_task(run_id)
        if not task:
            raise HTTPException(status_code=404, detail="抓取任务不存在")
        try:
            adapter = site_for_url(body.url) if body.url else get_site("boss")
        except KeyError:
            # An unrecognised host still gets a parser; pasted text needs no host.
            adapter = get_site("boss")
        try:
            snapshot = adapter.parse_manual_content(body.content, body.url, body.city)
        except CrawlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        database.save_snapshot(job_identity(snapshot), run_id, snapshot)
        database.update_task(run_id, status="SUCCEEDED", progress=100, message="已保存手动岗位描述")
        database.update_collection_metric(run_id, site=snapshot.site, pages=1, valid_jobs=1, status="SUCCEEDED")
        return snapshot

    @app.get("/api/jobs")
    async def list_jobs(run_id: str | None = None):
        return database.list_snapshots(run_id)

    @app.get("/api/collection-metrics")
    async def collection_metrics():
        return [present_metric(item) for item in database.list_collection_metrics()]

    @app.get("/api/collection-metrics/{run_id}")
    async def collection_metric(run_id: str):
        metric = database.get_collection_metric(run_id)
        if not metric:
            raise HTTPException(404, "采集指标不存在")
        return present_metric(metric)

    @app.post("/api/comparisons", status_code=202)
    async def create_comparison(body: ComparisonRequest):
        with database.connect() as db:
            row = db.execute(
                "SELECT id, payload FROM tasks WHERE kind='comparison' AND json_extract(payload, '$.run_id')=? AND json_extract(payload, '$.profile_id')=? AND status IN ('QUEUED','RUNNING','SUCCEEDED') ORDER BY created_at DESC LIMIT 1",
                (body.run_id, body.profile_id),
            ).fetchone()
        if row:
            return {"task_id": row["id"], "status": database.get_task(row["id"])["status"]}
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

    @app.post("/api/target-jobs")
    async def create_target_job(body: TargetJobRequest):
        profile = database.get_profile(body.profile_id)
        if not profile:
            raise HTTPException(404, "候选人画像不存在")
        contact = database.get_contact(profile.profile_id)
        if not any((contact.name, contact.phone, contact.email, contact.location)):
            contact = extract_candidate_contact(profile.model_copy(deep=True))
            database.save_contact(contact)
        if body.source_type not in {"snapshot", "pasted", "external_url"}:
            raise HTTPException(422, "目标岗位来源无效")
        try:
            if body.source_type == "snapshot":
                if not body.snapshot_id or not database.snapshot_belongs_to_profile(body.snapshot_id, body.profile_id):
                    raise HTTPException(409, "岗位快照不属于当前候选人")
                snapshot = database.get_snapshot(body.snapshot_id)
                target = target_from_snapshot(body.profile_id, snapshot)
                database.save_target_job(target)
                return {"target_job_id": target.target_job_id, "status": "SUCCEEDED", "target_job": target}
            if body.source_type == "pasted":
                if not body.company.strip() or not body.title.strip():
                    raise HTTPException(422, "请填写目标公司和岗位名称")
                if body.url:
                    # A pasted JD may cite a URL from any site, or none at all; only
                    # enforce the rules when we actually recognise the host.
                    try:
                        site_for_url(body.url).validate_url(body.url)
                    except KeyError:
                        pass
                target = target_from_pasted(body.profile_id, body.company, body.title, body.content, body.url)
                database.save_target_job(target)
                return {"target_job_id": target.target_job_id, "status": "SUCCEEDED", "target_job": target}
            try:
                adapter = site_for_url(body.url)
            except KeyError as exc:
                # An unrecognised host is a client error, not a server fault.
                raise HTTPException(422, str(exc)) from exc
            adapter.validate_url(body.url)
            target_job_id = f"target_{uuid4().hex[:12]}"
            task_id = await worker.submit("target_job", {
                "profile_id": body.profile_id, "target_job_id": target_job_id,
                "url": body.url, "site": adapter.key,
            })
            return {"target_job_id": target_job_id, "task_id": task_id, "status": "QUEUED"}
        except CrawlError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ResumeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/target-jobs/{target_job_id}")
    async def get_target_job(target_job_id: str):
        target = database.get_target_job(target_job_id)
        if not target:
            raise HTTPException(404, "目标岗位尚未读取完成或不存在")
        return target

    @app.post("/api/interview-preps", status_code=202)
    async def create_interview_prep(body: InterviewPrepRequest):
        try:
            prep = interview_service.create(body.profile_id, body.snapshot_id)
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc
        task_id = await worker.submit("interview_prep", {"prep_id": prep.prep_id})
        prep.task_id = task_id
        database.save_interview_prep(prep)
        return {"prep_id": prep.prep_id, "task_id": task_id, "status": "QUEUED"}

    @app.get("/api/interview-preps/{prep_id}")
    async def get_interview_prep(prep_id: str):
        prep = database.get_interview_prep(prep_id)
        if not prep:
            raise HTTPException(404, "面试准备不存在")
        return prep

    @app.get("/api/jobs/{snapshot_id}/interview-prep")
    async def interview_prep_for_job(snapshot_id: str, profile_id: str):
        prep = database.get_interview_prep_for_snapshot(snapshot_id, profile_id)
        if not prep:
            raise HTTPException(404, "该岗位还没有面试准备")
        return prep

    @app.post("/api/applications", status_code=202)
    async def create_application(body: ApplicationRequest):
        try:
            application = apply_service.create(body.profile_id, body.snapshot_id)
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not application.greeting:
            task_id = await worker.submit("apply_prep", {"application_id": application.application_id})
            application.task_id = task_id
            database.save_application(application)
            return {"application_id": application.application_id, "task_id": task_id, "status": "QUEUED"}
        return {"application_id": application.application_id, "status": application.status}

    @app.get("/api/applications")
    async def list_applications(profile_id: str | None = None):
        # The helper's popup has no profile in hand and this is a single-user
        # local tool, so an omitted profile lists everything.
        if profile_id:
            return database.list_applications(profile_id)
        return database.list_all_applications()

    @app.get("/api/applications/{application_id}")
    async def get_application(application_id: str):
        application = database.get_application(application_id)
        if not application:
            raise HTTPException(404, "投递记录不存在")
        return application

    @app.patch("/api/applications/{application_id}")
    async def update_application_board(application_id: str, body: ApplicationUpdate):
        try:
            application = apply_service.set_status(application_id, body.status)
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if body.application_deadline is not None:
            application.application_deadline = body.application_deadline or None
        if body.reminder_at is not None:
            application.reminder_at = body.reminder_at or None
        if body.note is not None:
            application.note = body.note
        return database.save_application(application)

    @app.post("/api/applications/{application_id}/{state}")
    async def update_application(application_id: str, state: str):
        """Status callbacks from the helper and the user.

        Nothing here submits anything: `opened` means the helper filled the page
        and the user is about to send, `submitted` means the user says they did.
        """
        if state not in {"opened", "submitted", "skip"}:
            raise HTTPException(404, "未知的投递状态")
        try:
            application = apply_service.set_status(
                application_id, {"opened": "OPENED", "submitted": "SUBMITTED", "skip": "SKIPPED"}[state]
            )
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return application

    @app.post("/api/resume-tailorings")
    async def create_resume_tailoring(body: TailoringRequest):
        if body.conversation_id:
            conversation = database.get_conversation(body.conversation_id)
            if not conversation or conversation.profile_id != body.profile_id:
                raise HTTPException(409, "对话与候选人画像不匹配")
        try:
            return tailoring_service.create_tailoring(
                body.profile_id, body.target_job_id, body.conversation_id,
            )
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/resume-tailorings/{tailoring_id}")
    async def get_resume_tailoring(tailoring_id: str):
        tailoring = database.get_tailoring(tailoring_id)
        if not tailoring:
            raise HTTPException(404, "定向简历任务不存在")
        tailoring = tailoring_service.restore_missing_questions(tailoring)
        return {"tailoring": tailoring, "target_job": database.get_target_job(tailoring.target_job_id)}

    @app.post("/api/resume-tailorings/{tailoring_id}/answers")
    async def answer_tailoring_questions(tailoring_id: str, body: TailoringAnswers):
        try:
            return tailoring_service.save_answers(tailoring_id, body.answers)
        except ResumeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/resume-tailorings/{tailoring_id}/confirm", status_code=202)
    async def confirm_resume_tailoring(tailoring_id: str):
        tailoring = database.get_tailoring(tailoring_id)
        if not tailoring:
            raise HTTPException(404, "定向简历任务不存在")
        if tailoring.task_id:
            task = database.get_task(tailoring.task_id)
            if task and task["status"] not in {"FAILED", "FAILED_VALIDATION"}:
                return {"task_id": tailoring.task_id, "status": task["status"], "draft_id": tailoring.draft_id}
            tailoring.task_id = None
            tailoring.status = "READY"
            tailoring.error = None
        if tailoring.status != "READY":
            raise HTTPException(409, "请先回答或跳过全部追问")
        task_id = f"task_{uuid4().hex[:12]}"
        tailoring.task_id = task_id
        tailoring.status = "GENERATING"
        database.save_tailoring(tailoring)
        await worker.submit("tailoring", {"tailoring_id": tailoring_id}, task_id=task_id)
        return {"task_id": task_id, "status": "QUEUED", "draft_id": tailoring.draft_id}

    @app.get("/api/resume-drafts/{draft_id}")
    async def get_resume_draft(draft_id: str):
        current = database.get_latest_draft(draft_id)
        if not current:
            raise HTTPException(404, "定向简历尚未生成")
        return {"current": current, "versions": database.list_draft_versions(draft_id)}

    @app.post("/api/resume-drafts/{draft_id}/versions")
    async def create_resume_draft_version(draft_id: str, body: ResumeDraftVersion):
        try:
            return tailoring_service.save_user_version(draft_id, body)
        except ResumeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/resume-drafts/{draft_id}/exports", status_code=202)
    async def create_resume_exports(draft_id: str, body: ExportRequest):
        version = database.get_latest_draft(draft_id)
        if not version:
            raise HTTPException(404, "定向简历尚未生成")
        templates = list(dict.fromkeys(body.templates))
        if any(value not in {"technical", "business"} for value in templates):
            raise HTTPException(422, "导出模板无效")
        stamp = now_iso()
        exports = [database.save_resume_export(ResumeExport(
            export_id=f"export_{uuid4().hex[:12]}", version_id=version.version_id,
            template=template, status="QUEUED", created_at=stamp, updated_at=stamp,
        )) for template in templates]
        task_id = await worker.submit("resume_export", {"export_ids": [item.export_id for item in exports]})
        return {"task_id": task_id, "exports": exports}

    @app.get("/api/resume-exports/{export_id}")
    async def get_resume_export(export_id: str):
        export = database.get_resume_export(export_id)
        if not export:
            raise HTTPException(404, "导出任务不存在")
        return export

    @app.get("/api/resume-exports/{export_id}/download")
    async def download_resume_export(export_id: str, format: str):
        export = database.get_resume_export(export_id)
        if not export or export.status != "SUCCEEDED":
            raise HTTPException(409, "文件尚未生成")
        if format not in {"docx", "pdf"}:
            raise HTTPException(422, "下载格式无效")
        path = Path(export.docx_path if format == "docx" else export.pdf_path).resolve()
        root = tailoring_service.export_dir.resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "导出文件不存在")
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if format == "docx" else "application/pdf"
        return FileResponse(path, media_type=media, filename=path.name)

    def apply_action_profile_changes(profile_id: str, changes: dict):
        allowed = {"selected_roles", "cities", "salary_preference", "experience_years", "work_type_preference"}
        if set(changes) - allowed:
            raise HTTPException(422, "操作包含不支持的画像字段")
        profile = database.get_profile(profile_id)
        if not profile:
            raise HTTPException(404, "候选人画像不存在")
        if "selected_roles" in changes:
            try:
                roles = [RoleRecommendation.model_validate(item) for item in changes["selected_roles"]]
            except Exception as exc:
                raise HTTPException(422, "岗位方向格式无效") from exc
            if not 1 <= len(roles) <= 2:
                raise HTTPException(422, "需要选择一至两个岗位方向")
            profile.selected_roles = roles
        if "cities" in changes:
            cities = changes["cities"]
            if not isinstance(cities, list) or not 1 <= len(cities) <= 2 or any(city not in SUPPORTED_CITIES for city in cities):
                raise HTTPException(422, "目标城市无效或超过两个")
            profile.cities = cities
        if "salary_preference" in changes:
            value = changes["salary_preference"]
            if value is not None and (not isinstance(value, str) or len(value) > 100):
                raise HTTPException(422, "薪资偏好格式无效")
            profile.salary_preference = value
        if "experience_years" in changes:
            value = changes["experience_years"]
            if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 50):
                raise HTTPException(422, "工作年限应在 0 至 50 年之间")
            profile.experience_years = value
        if "work_type_preference" in changes:
            value = changes["work_type_preference"]
            if value is not None and (not isinstance(value, str) or len(value) > 50):
                raise HTTPException(422, "工作类型格式无效")
            profile.work_type_preference = value
        profile.confirmed = True
        database.save_profile(profile)
        return profile

    @app.post("/api/chat-actions/{action_id}/confirm")
    async def confirm_chat_action(action_id: str):
        action, claimed = database.claim_chat_action(action_id)
        if not action:
            raise HTTPException(404, "待确认操作不存在")
        if not claimed:
            if action.status == "EXECUTED":
                return action
            raise HTTPException(409, f"操作当前状态为 {action.status}")
        try:
            conversation = database.get_conversation(action.conversation_id)
            if not conversation or not conversation.profile_id:
                raise HTTPException(409, "对话尚未关联简历")
            if action.arguments.get("profile_id") != conversation.profile_id:
                raise HTTPException(409, "画像上下文已经变化，请重新提出修改")
            if action.kind in {ChatActionKind.RESTART_DISCOVERY, ChatActionKind.START_COMPARISON,
                               ChatActionKind.START_RESUME_TAILORING} and action.arguments.get("run_id") != conversation.run_id:
                raise HTTPException(409, "任务上下文已经变化，请重新提出操作")
            profile = apply_action_profile_changes(conversation.profile_id, action.arguments.get("changes") or {})
            result: dict = {"profile_id": profile.profile_id}
            if action.kind == ChatActionKind.RESTART_DISCOVERY:
                old_run_id = conversation.run_id
                if old_run_id:
                    old = database.get_task(old_run_id)
                    if old and old["status"] in {"QUEUED", "RUNNING", "NEEDS_MANUAL_INPUT"}:
                        worker.cancel(old_run_id)
                        database.update_task(old_run_id, status="FAILED", message="已按确认操作停止旧任务", error="用户调整搜索条件")
                new_run_id = f"task_{uuid4().hex[:12]}"
                database.create_task(new_run_id, "discovery", {"profile_id": profile.profile_id, "mode": "browser"})
                database.update_task(new_run_id, message="等待 Chrome 浏览器助手自动接单，请安装并启用助手")
                database.update_conversation_context(action.conversation_id, run_id=new_run_id,
                                                     comparison_id=None, supplied={"run_id", "comparison_id"})
                result.update({"old_run_id": old_run_id, "run_id": new_run_id, "status": "QUEUED"})
            elif action.kind == ChatActionKind.START_COMPARISON:
                run_id = conversation.run_id
                if not run_id or not database.list_snapshots(run_id):
                    raise HTTPException(409, "当前任务还没有可分析的岗位")
                current = database.get_task(run_id)
                if current and current["status"] in {"QUEUED", "RUNNING", "NEEDS_MANUAL_INPUT"}:
                    worker.cancel(run_id)
                    database.update_task(run_id, status="SUCCEEDED", progress=100,
                                         message=f"用户确认提前结束，共保留 {len(database.list_snapshots(run_id))} 个岗位")
                queued = await create_comparison(ComparisonRequest(profile_id=profile.profile_id, run_id=run_id))
                result.update(dict(queued))
                existing = database.get_comparison_by_task(result["task_id"])
                if existing:
                    database.update_conversation_context(action.conversation_id, comparison_id=existing.comparison_id,
                                                         supplied={"comparison_id"})
                    result["comparison_id"] = existing.comparison_id
            elif action.kind == ChatActionKind.START_RESUME_TAILORING:
                snapshot_id = action.arguments.get("snapshot_id")
                if not snapshot_id or not database.snapshot_belongs_to_profile(snapshot_id, profile.profile_id):
                    raise HTTPException(409, "目标岗位不属于当前候选人")
                snapshot = database.get_snapshot(snapshot_id)
                target = target_from_snapshot(profile.profile_id, snapshot)
                database.save_target_job(target)
                tailoring = tailoring_service.create_tailoring(
                    profile.profile_id, target.target_job_id, action.conversation_id,
                )
                result.update({
                    "target_job_id": target.target_job_id,
                    "tailoring_id": tailoring.tailoring_id,
                    "tailoring_status": tailoring.status,
                })
            result["message"] = "操作已执行"
            return database.update_chat_action(action_id, status="EXECUTED", result=result)
        except HTTPException as exc:
            database.update_chat_action(action_id, status="FAILED", result={"error": str(exc.detail)})
            raise
        except Exception as exc:
            database.update_chat_action(action_id, status="FAILED", result={"error": str(exc)})
            raise HTTPException(500, "操作执行失败") from exc

    @app.post("/api/chat-actions/{action_id}/reject")
    async def reject_chat_action(action_id: str):
        action, rejected = database.reject_chat_action(action_id)
        if not action:
            raise HTTPException(404, "待确认操作不存在")
        if not rejected and action.status != "REJECTED":
            raise HTTPException(409, f"操作当前状态为 {action.status}")
        # A rejection is the only explicit negative signal the product emits, so
        # it is folded into memory right away. Best-effort: the rejection itself
        # already succeeded and must not be undone by a memory failure.
        try:
            conversation = database.get_conversation(action.conversation_id)
            if conversation and conversation.profile_id:
                MemoryService(database).mine_rejected_actions(conversation.profile_id)
        except Exception:
            pass
        return action

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("career_radar.web:app", host="0.0.0.0", port=8000, reload=False)
