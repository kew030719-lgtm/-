from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from .agent import AgentService
from .crawler import BossCrawler, BossTransport, CrawlError, NeedsManualInput, job_identity
from .database import Database
from .resume import ResumeError, validate_citations
from .schemas import TaskStatus
from .scoring import score_job


@dataclass
class WorkItem:
    task_id: str
    kind: str
    payload: dict[str, Any]


class TaskQueue:
    def __init__(self, database: Database, agent: AgentService, *, crawl_delay: float, max_jobs: int):
        self.database = database
        self.agent = agent
        self.crawl_delay = crawl_delay
        self.max_jobs = max_jobs
        self.queue: asyncio.Queue[WorkItem | None] = asyncio.Queue()
        self.consumer: asyncio.Task | None = None
        self.cancelled: set[str] = set()
        self.browser_state_provider: Callable[[], dict[str, Any] | None] = lambda: None

    async def start(self) -> None:
        if not self.consumer:
            self.consumer = asyncio.create_task(self._consume(), name="career-radar-worker")

    async def stop(self) -> None:
        if self.consumer:
            await self.queue.put(None)
            await self.consumer
            self.consumer = None

    async def submit(self, kind: str, payload: dict[str, Any]) -> str:
        task_id = f"task_{uuid4().hex[:12]}"
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
        transport = BossTransport(
            self.crawl_delay,
            browser_storage_state=self.browser_state_provider(),
        )
        crawler = BossCrawler(transport, self.max_jobs)

        async def progress(value: int, message: str) -> None:
            if item.task_id in self.cancelled:
                raise asyncio.CancelledError("任务已取消")
            self.database.update_task(item.task_id, progress=value, message=message)

        try:
            snapshots = await crawler.discover(profile.selected_roles, profile.cities, progress)
        finally:
            await transport.close()
        if not snapshots:
            raise NeedsManualInput("没有解析到有效岗位，可粘贴岗位描述继续")
        for snapshot in snapshots:
            self.database.save_snapshot(job_identity(snapshot), item.task_id, snapshot)
        self.database.update_task(
            item.task_id, status=TaskStatus.SUCCEEDED, progress=100,
            message=f"完成，共保存 {len(snapshots)} 个唯一岗位",
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
