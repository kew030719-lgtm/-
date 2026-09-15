"""HTTP/browser transport and the discovery loop, both driven by an adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from ..schemas import JobSnapshot, RoleRecommendation
from .base import CrawlError, NeedsManualInput, SiteAdapter

# A headless browser announcing itself as HeadlessChrome is served anti-bot
# challenge pages by several boards: 智联 answers the default Playwright UA with
# 16 KB of "正在验证连接安全性，请勾选下方复选框", and a full 1.7 MB result page to a
# normal Chrome UA at the same URL. The fallback exists to render a public page
# the way a real visitor would see it, so it presents a normal browser identity
# and a zh-CN locale.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
BROWSER_LOCALE = "zh-CN"


@dataclass
class FetchResult:
    text: str
    transport: str


class Transport:
    """Fetches pages for one site: HTTP first, headless browser as a fallback."""

    def __init__(self, adapter: SiteAdapter, delay_seconds: float = 10,
                 client: httpx.AsyncClient | None = None,
                 browser_storage_state: dict | None = None):
        self.adapter = adapter
        self.delay_seconds = delay_seconds
        self.client = client or httpx.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False,
            headers={"User-Agent": "CareerRadar/0.1 (+local job research; public pages only)"},
        )
        self._owns_client = client is None
        self._last_request = 0.0
        self.browser_storage_state = browser_storage_state

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _wait_rate_limit(self) -> None:
        loop = asyncio.get_running_loop()
        wait = self.delay_seconds - (loop.time() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = loop.time()

    async def fetch_http(self, url: str) -> str:
        self.adapter.validate_url(url)
        delays = (0, 5, 15)
        last_error: Exception | None = None
        for wait in delays:
            if wait:
                await asyncio.sleep(wait)
            await self._wait_rate_limit()
            try:
                current = url
                for _ in range(4):
                    response = await self.client.get(current)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise CrawlError("重定向缺少目标地址")
                        current = urljoin(current, location)
                        self.adapter.validate_url(current)
                        continue
                    response.raise_for_status()
                    return response.text
                raise CrawlError("重定向次数过多")
            except (httpx.HTTPError, CrawlError) as exc:
                last_error = exc
        raise CrawlError(f"页面请求失败：{last_error}")

    async def fetch_browser(self, url: str) -> str:
        self.adapter.validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise CrawlError("浏览器降级不可用，请安装 Playwright") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=self.browser_storage_state,
                user_agent=BROWSER_USER_AGENT,
                locale=BROWSER_LOCALE,
            )
            page = await context.new_page()
            await self._wait_rate_limit()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if response:
                self.adapter.validate_url(response.url)
            await page.wait_for_timeout(max(0, self.adapter.render_wait_ms))
            html = await page.content()
            await browser.close()
        return html

    async def fetch(self, url: str, expects: str) -> FetchResult:
        html = await self.fetch_http(url)
        blocked = self.adapter.detect_blocked(html)
        if blocked and not self.browser_storage_state:
            raise NeedsManualInput(blocked)
        valid = False if blocked else self._usable(html, url, expects)
        if valid:
            return FetchResult(html, "http")
        browser_html = await self.fetch_browser(url)
        blocked = self.adapter.detect_blocked(browser_html)
        if blocked:
            raise NeedsManualInput(blocked)
        if not self._usable(browser_html, url, expects):
            raise NeedsManualInput("浏览器已渲染页面，但页面结构无法识别，请手动粘贴岗位描述")
        return FetchResult(browser_html, "browser")

    def _usable(self, html: str, url: str, expects: str) -> bool:
        if expects == "search":
            return bool(self.adapter.parse_search_page(html, url))
        return self.adapter.looks_like_job(html)


Progress = Callable[[int, str], Awaitable[None]]


class SiteCrawler:
    """Walks roles x cities x pages for one site, collecting snapshots."""

    def __init__(self, adapter: SiteAdapter, transport: Transport, max_jobs: int = 20):
        self.adapter = adapter
        self.transport = transport
        self.max_jobs = min(20, max(1, max_jobs))

    async def discover(self, roles: list[RoleRecommendation], cities: list[str],
                       progress: Progress, site_key: str | None = None) -> list[JobSnapshot]:
        snapshots: list[JobSnapshot] = []
        seen_urls: set[str] = set()
        seen_platform: set[str] = set()
        seen_hashes: set[str] = set()
        combinations = max(1, len(roles) * len(cities) * 2)
        step = 0
        for role in roles:
            query = role.keywords[0] if role.keywords else role.role
            for city in cities:
                for page_number in (1, 2):
                    if len(snapshots) >= self.max_jobs:
                        return snapshots
                    step += 1
                    await progress(int(step / combinations * 80), f"搜索 {city} · {query} · 第 {page_number} 页")
                    url = self.adapter.search_url(query, city, page_number)
                    search = await self.transport.fetch(url, "search")
                    links = self.adapter.parse_search_page(search.text, url)
                    if not links:
                        continue
                    for link in links:
                        canonical = self.adapter.canonicalize_url(link)
                        if canonical in seen_urls:
                            continue
                        detail = await self.transport.fetch(canonical, "job")
                        try:
                            snapshot = self.adapter.parse_job_page(detail.text, canonical, city, detail.transport)
                        except CrawlError as exc:
                            raise NeedsManualInput(f"职位详情结构无法识别：{canonical}") from exc
                        if snapshot.platform_job_id and snapshot.platform_job_id in seen_platform:
                            continue
                        if snapshot.content_hash in seen_hashes:
                            continue
                        seen_urls.add(canonical)
                        seen_hashes.add(snapshot.content_hash)
                        if snapshot.platform_job_id:
                            seen_platform.add(snapshot.platform_job_id)
                        snapshots.append(snapshot)
                        await progress(min(95, 80 + len(snapshots)), f"已获取 {len(snapshots)} 个有效岗位")
                        if len(snapshots) >= self.max_jobs:
                            return snapshots
        return snapshots