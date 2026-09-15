from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .agent import AgentService
from .apply import ApplyService
from .database import Database
from .interview import InterviewService
from .resume import ResumeError, validate_citations
from .schemas import TaskStatus
from .scoring import score_job
from .sites import (
    CrawlError, NeedsManualInput, SiteCrawler, Transport, get_site, job_identity,
)
from .tailoring import TailoringService, target_from_snapshot


@dataclass
class WorkItem:
    task_id: str
    kind: str
    payload: dict[str, Any]


class TaskQueue:
    def __init__(self, database: Database, agent: AgentService, *, crawl_delay: float, max_jobs: int,
                 tailoring_service: TailoringService | None = None,
                 interview_service: InterviewService | None = None,
                 apply_service: ApplyService | None = None):
        self.database = database
        self.agent = agent
        self.crawl_delay = crawl_delay
        self.max_jobs = max_jobs
        self.tailoring_service = tailoring_service
        self.interview_service = interview_service
        self.apply_service = apply_service
        self.queue: asyncio.Queue[WorkItem | None] = asyncio.Queue()
        self.consumer: asyncio.Task | None = None
        self.cancelled: set[str] = set()

    async def start(self) -> None:
        if not self.consumer:
            self.consumer = asyncio.create_task(self._consume(), name="career-radar-worker")

    async def stop(self) -> None:
        if self.consumer:
            await self.queue.put(None)
            await self.consumer
            self.consumer = None

    async def submit(self, kind: str, payload: dict[str, Any], *, task_id: str | None = None) -> str:
        task_id = task_id or f"task_{uuid4().hex[:12]}"
        self.database.create_task(task_id, kind, payload)
        await self.queue.put(WorkItem(task_id, kind, payload))
        return task_id

    def cancel(self, task_id: str) -> None:
        self.cancelled.add(task_id)

    async def _consume(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                break
            try:
                self.database.update_task(item.task_id, status=TaskStatus.RUNNING, progress=1, message="任务开始")
                if item.kind == "discovery":
                    await self._discovery(item)
                elif item.kind == "comparison":
                    await self._comparison(item)
                elif item.kind == "target_job":
                    await self._target_job(item)
                elif item.kind == "tailoring":
                    await self._tailoring(item)
                elif item.kind == "resume_export":
                    await self._resume_export(item)
                elif item.kind == "interview_prep":
                    await self._interview_prep(item)
                elif item.kind == "apply_prep":
                    await self._apply_prep(item)
                else:
                    raise RuntimeError(f"未知任务类型：{item.kind}")
            except NeedsManualInput as exc:
                self.database.update_task(item.task_id, status=TaskStatus.NEEDS_MANUAL_INPUT, message=str(exc))
            except ResumeError as exc:
                self.database.update_task(item.task_id, status=TaskStatus.FAILED_VALIDATION, error=str(exc), message="证据校验失败")
            except asyncio.CancelledError:
                self.database.update_task(item.task_id, status=TaskStatus.FAILED, error="任务已取消", message="任务已取消")
            except Exception as exc:
                self.database.update_task(item.task_id, status=TaskStatus.FAILED, error=str(exc), message="任务失败")
            finally:
                self.queue.task_done()

    async def _discovery(self, item: WorkItem) -> None:
        profile = self.database.get_profile(item.payload["profile_id"])
        if not profile or not profile.confirmed:
            raise CrawlError("必须先确认候选人画像和岗位方向")
        if not profile.selected_roles or not profile.cities:
            raise CrawlError("至少选择一个岗位方向和城市")
        site_keys = item.payload.get("sites") or ["boss"]

        async def progress(value: int, message: str) -> None:
            if item.task_id in self.cancelled:
                raise asyncio.CancelledError("任务已取消")
            self.database.update_task(item.task_id, progress=value, message=message)

        snapshots: list[Any] = []
        blocked: list[str] = []
        failures: list[str] = []
        for site_key in site_keys:
            adapter = get_site(site_key)
            # Respect the site's own floor: 智联 and 前程无忧 are less tolerant of
            # rapid traffic than BOSS.
            transport = Transport(adapter, max(self.crawl_delay, adapter.min_delay_seconds))
            crawler = SiteCrawler(adapter, transport, self.max_jobs)
            try:
                snapshots.extend(
                    await crawler.discover(profile.selected_roles, profile.cities, progress)
                )
            except NeedsManualInput as exc:
                blocked.append(f"{adapter.label}：{exc}")
            except CrawlError as exc:
                failures.append(f"{adapter.label}：{exc}")
            finally:
                await transport.close()

        if not snapshots:
            # Nothing landed anywhere: surface the most actionable reason.
            if blocked:
                raise NeedsManualInput("；".join(blocked))
            raise NeedsManualInput("没有解析到有效岗位，可粘贴岗位描述继续")
        for snapshot in snapshots:
            self.database.save_snapshot(job_identity(snapshot), item.task_id, snapshot)
        # A site that needed a login does not invalidate the sites that worked.
        note = f"（{len(blocked)} 个站点需要人工处理）" if blocked else ""
        if failures:
            note += f"（{len(failures)} 个站点失败）"
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"完成，共保存 {len(snapshots)} 个唯一岗位{note}",
        )

    async def _comparison(self, item: WorkItem) -> None:
        profile = self.database.get_profile(item.payload["profile_id"])
        if not profile:
            raise RuntimeError("候选人画像不存在")
        jobs = self.database.list_snapshots(item.payload.get("run_id"))
        if not jobs:
            raise RuntimeError("没有可分析的岗位快照")
        scores = [score_job(profile, job) for job in jobs]
        comparison = await self.agent.enrich_comparison(
            profile, jobs, scores, comparison_id=item.payload.get("comparison_id")
        )
        sources = profile.evidence + [block for job in jobs for block in job.blocks]
        validate_citations([citation for score in comparison.rankings for citation in score.citations], sources)
        self.database.save_comparison(comparison, item.task_id)
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message="排名与准备计划已生成",
        )

    async def _target_job(self, item: WorkItem) -> None:
        adapter = get_site(item.payload.get("site") or "boss")
        transport = Transport(adapter, max(self.crawl_delay, adapter.min_delay_seconds))
        try:
            result = await transport.fetch(item.payload["url"], "job")
            snapshot = adapter.parse_job_page(
                result.text, item.payload["url"], transport=result.transport,
            )
        finally:
            await transport.close()
        target = target_from_snapshot(
            item.payload["profile_id"], snapshot, source_type="external_url",
            target_id=item.payload["target_job_id"],
        )
        self.database.save_target_job(target)
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"已读取目标岗位：{target.company} · {target.title}",
        )

    async def _tailoring(self, item: WorkItem) -> None:
        if not self.tailoring_service:
            raise RuntimeError("定向简历服务未初始化")
        tailoring = self.database.get_tailoring(item.payload["tailoring_id"])
        if not tailoring:
            raise RuntimeError("定向简历任务不存在")
        tailoring.status = "GENERATING"
        self.database.save_tailoring(tailoring)
        try:
            version = await self.tailoring_service.generate(tailoring.tailoring_id)
        except ResumeError:
            tailoring = self.database.get_tailoring(tailoring.tailoring_id)
            tailoring.status = "FAILED_VALIDATION"
            self.database.save_tailoring(tailoring)
            raise
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"定向简历第 {version.version} 版已生成",
        )

    async def _apply_prep(self, item: WorkItem) -> None:
        if not self.apply_service:
            raise RuntimeError("投递服务未初始化")
        # Preparing only. Submitting is the user's action, never ours.
        application = await self.apply_service.prepare(item.payload["application_id"])
        note = "（已关联定向简历）" if application.resume_export_id else ""
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"打招呼语已生成{note}",
        )

    async def _interview_prep(self, item: WorkItem) -> None:
        if not self.interview_service:
            raise RuntimeError("面试准备服务未初始化")
        # A validation failure marks the prep itself FAILED_VALIDATION inside
        # generate(); the ResumeError then lands the task in the same state.
        prep = await self.interview_service.generate(item.payload["prep_id"])
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"面试准备已生成：{len(prep.days)} 天计划 · {len(prep.questions)} 道题",
        )

    async def _resume_export(self, item: WorkItem) -> None:
        if not self.tailoring_service:
            raise RuntimeError("定向简历服务未初始化")
        for export_id in item.payload["export_ids"]:
            export = self.database.get_resume_export(export_id)
            if not export:
                raise RuntimeError("导出记录不存在")
            export.status = "RUNNING"
            self.database.save_resume_export(export)
            try:
                await self.tailoring_service.export(export_id)
            except Exception as exc:
                export = self.database.get_resume_export(export_id)
                export.status = "FAILED"
                export.error = str(exc)
                self.database.save_resume_export(export)
                raise
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message="DOCX 与 PDF 已生成",
        )
