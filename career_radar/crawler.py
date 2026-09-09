from __future__ import annotations

import asyncio
import hashlib
import html as html_module
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from .schemas import Evidence, JobSnapshot, RoleRecommendation


CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "深圳": "101280600", "广州": "101280100",
    "杭州": "101210100", "成都": "101270100", "武汉": "101200100", "南京": "101190100",
    "西安": "101110100", "苏州": "101190400",
}
ALLOWED_HOSTS = {"www.zhipin.com", "m.zhipin.com"}
TRACKING_QUERY = {"ka", "lid", "securityId", "sessionId"}
COMMON_JOB_SKILLS = (
    "Python", "FastAPI", "Django", "Flask", "Java", "Go", "SQL", "MySQL", "PostgreSQL",
    "Redis", "Docker", "Kubernetes", "Linux", "Git", "Playwright", "Selenium", "Scrapy",
    "HTTPX", "Requests", "Agent", "LLM", "RAG", "LangChain", "Pandas", "NumPy",
)


class CrawlError(RuntimeError):
    pass


class NeedsManualInput(CrawlError):
    pass


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise CrawlError("仅允许访问 BOSS 直聘公开 HTTPS 页面")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise CrawlError("URL 包含不允许的认证信息或端口")
    return url


def canonicalize_url(url: str) -> str:
    validate_url(url)
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in TRACKING_QUERY]
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    return urlunsplit(("https", parts.hostname or "", path, urlencode(sorted(query)), ""))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def detect_blocked_page(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ")).lower()
    if any(token in text for token in ("安全验证", "验证码", "captcha", "访问过于频繁")):
        return "页面触发验证码，请手动粘贴岗位描述"
    has_job_content = bool(soup.select_one("a[href*='/job_detail/'], h1, .job-sec-text, .job-description"))
    if not has_job_content and any(token in text for token in ("登录后查看", "请先登录", "扫码登录")):
        return "页面要求登录，请手动粘贴岗位描述"
    return None


def _json_ld(soup: BeautifulSoup) -> dict:
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.string or node.get_text())
        except json.JSONDecodeError:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and item.get("@type") in {"JobPosting", "Job"}:
                return item
    return {}


def parse_search_page(html: str, base_url: str = "https://www.zhipin.com") -> list[str]:
    blocked = detect_blocked_page(html)
    if blocked:
        raise NeedsManualInput(blocked)
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    selectors = ("a.job-card-left", "a.job-name", "a[href*='/job_detail/']", "a[href*='/job_detail']")
    for selector in selectors:
        for node in soup.select(selector):
            href = node.get("href")
            if not href:
                continue
            url = urljoin(base_url, href)
            try:
                canonical = canonicalize_url(url)
            except CrawlError:
                continue
            if canonical not in links:
                links.append(canonical)
    return links


def _list_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[str]:
    for selector in selectors:
        values = [clean_text(node.get_text(" ")) for node in soup.select(selector)]
        if any(values):
            return [value for value in values if value]
    return []


def _one_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node and clean_text(node.get_text(" ")):
            return clean_text(node.get_text(" "))
    return ""


def parse_job_page(html: str, url: str, city_hint: str = "", transport: str = "http") -> JobSnapshot:
    blocked = detect_blocked_page(html)
    if blocked:
        raise NeedsManualInput(blocked)
    canonical = canonicalize_url(url)
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(("script", "style", "noscript")):
        node.decompose()
    structured = _json_ld(BeautifulSoup(html, "html.parser"))
    title = clean_text(str(structured.get("title") or "")) or _one_text(soup, ("h1", ".name h1", ".job-name"))
    organization = structured.get("hiringOrganization") or {}
    company = clean_text(str(organization.get("name") or "")) if isinstance(organization, dict) else ""
    company = company or _one_text(soup, (".company-info .name", ".sider-company .company-info a", ".company-name"))
    salary = _one_text(soup, (".salary", ".job-banner .salary", ".job-salary"))
    experience = _one_text(soup, (".job-limit .text-experiece", ".job-primary .job-limit", "[class*='experience']"))
    education = _one_text(soup, (".job-limit .text-degree", "[class*='degree']", "[class*='education']"))
    responsibilities = _list_text(soup, (".job-sec-text li", ".job-detail-section li", ".job-description li"))
    detail_text = _one_text(soup, (".job-sec-text", ".job-detail-section", ".job-description", "main"))
    if not responsibilities and detail_text:
        responsibilities = [clean_text(item) for item in re.split(r"[\n；;]", detail_text) if clean_text(item)]
    skill_nodes = _list_text(soup, (".job-keyword-list li", ".job-tags span", ".tag-list li"))
    body = clean_text(soup.get_text(" "))
    is_offline = any(token in body for token in ("职位已下线", "停止招聘", "职位不存在"))
    if not title or (len(body) < 40 and not is_offline):
        raise CrawlError("页面缺少可识别的职位数据")
    if not responsibilities and not is_offline:
        responsibilities = [body[:4000]]
    platform_match = re.search(r"/job_detail/([A-Za-z0-9_-]+)", url)
    platform_id = platform_match.group(1) if platform_match else ""
    content_for_hash = "\n".join([title, company, salary, experience, education, *responsibilities, *skill_nodes])
    content_hash = hashlib.sha256(content_for_hash.encode()).hexdigest()
    snapshot_id = f"snap_{uuid4().hex[:12]}"
    blocks: list[Evidence] = []
    for index, value in enumerate([title, company, salary, experience, education, *responsibilities], start=1):
        if value:
            block_hash = hashlib.sha256(value.encode()).hexdigest()[:10]
            blocks.append(Evidence(
                source_type="job", source_id=snapshot_id,
                block_id=f"job-{index:02d}-{block_hash}", quote=value,
                section="职位原文",
            ))
    return JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id=platform_id, canonical_url=canonical,
        title=title, company=company or "公司名称未识别", city=city_hint, salary=salary,
        experience=experience, education=education, responsibilities=responsibilities[:30],
        required_skills=skill_nodes[:20], bonus_skills=[], benefits=[], content_hash=content_hash,
        status="offline" if is_offline else "active", cleaned_text=body[:20_000],
        fetched_at=datetime.now(UTC).isoformat(), transport=transport, blocks=blocks,
    )


def parse_manual_content(content: str, url: str, city_hint: str = "") -> JobSnapshot:
    lines = [clean_text(line) for line in content.splitlines() if clean_text(line)]
    if not lines:
        raise CrawlError("手动岗位描述为空")
    title = lines[0][:120]
    company = next((line.split("：", 1)[-1].strip() for line in lines[:5] if "公司" in line), "")
    salary_match = re.search(r"\d+(?:\.\d+)?\s*[-–—~至]\s*\d+(?:\.\d+)?\s*[Kk]", content)
    experience_match = re.search(r"(?:经验不限|应届生?|\d+\s*[-–—~至]\s*\d+\s*年|\d+\s*年以上)", content)
    education_match = re.search(r"(?:学历不限|博士|硕士|本科|大专|中专|高中)", content)
    lowered = content.lower()
    skills = [skill for skill in COMMON_JOB_SKILLS if skill.lower() in lowered]
    list_items = "".join(f"<li>{html_module.escape(line)}</li>" for line in lines[1:])
    tags = "".join(f"<li>{html_module.escape(skill)}</li>" for skill in skills)
    generated = f"""<html><body><h1>{html_module.escape(title)}</h1>
    <div class="company-name">{html_module.escape(company)}</div>
    <span class="salary">{html_module.escape(salary_match.group(0) if salary_match else '')}</span>
    <div class="job-limit"><span class="text-experiece">{html_module.escape(experience_match.group(0) if experience_match else '')}</span>
    <span class="text-degree">{html_module.escape(education_match.group(0) if education_match else '')}</span></div>
    <ul class="job-keyword-list">{tags}</ul><div class="job-description"><ul>{list_items}</ul></div></body></html>"""
    snapshot = parse_job_page(generated, url, city_hint, "manual")
    snapshot.cleaned_text = clean_text(content)[:20_000]
    return snapshot


def job_identity(snapshot: JobSnapshot) -> str:
    if snapshot.platform_job_id:
        return f"boss:{snapshot.platform_job_id}"
    return "url:" + hashlib.sha256(snapshot.canonical_url.encode()).hexdigest()[:24]


@dataclass
class FetchResult:
    text: str
    transport: str


class BossTransport:
    def __init__(self, delay_seconds: float = 10, client: httpx.AsyncClient | None = None,
                 browser_storage_state: dict | None = None):
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
        validate_url(url)
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
                        validate_url(current)
                        continue
                    response.raise_for_status()
                    return response.text
                raise CrawlError("重定向次数过多")
            except (httpx.HTTPError, CrawlError) as exc:
                last_error = exc
        raise CrawlError(f"页面请求失败：{last_error}")

    async def fetch_browser(self, url: str) -> str:
        validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise CrawlError("浏览器降级不可用，请安装 Playwright") from exc
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=self.browser_storage_state)
            page = await context.new_page()
            await self._wait_rate_limit()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if response:
                validate_url(response.url)
            await page.wait_for_timeout(1200)
            html = await page.content()
            await browser.close()
        return html

    async def fetch(self, url: str, expects: str) -> FetchResult:
        html = await self.fetch_http(url)
        blocked = detect_blocked_page(html)
        if blocked and not self.browser_storage_state:
            raise NeedsManualInput(blocked)
        valid = False if blocked else (bool(parse_search_page(html, url)) if expects == "search" else self._looks_like_job(html))
        if valid:
            return FetchResult(html, "http")
        browser_html = await self.fetch_browser(url)
        blocked = detect_blocked_page(browser_html)
        if blocked:
            raise NeedsManualInput(blocked)
        browser_valid = bool(parse_search_page(browser_html, url)) if expects == "search" else self._looks_like_job(browser_html)
        if not browser_valid:
            raise NeedsManualInput("浏览器已渲染页面，但页面结构无法识别，请手动粘贴岗位描述")
        return FetchResult(browser_html, "browser")

    @staticmethod
    def _looks_like_job(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        return bool(soup.select_one('script[type="application/ld+json"], h1, .job-sec-text, .job-description'))


Progress = Callable[[int, str], Awaitable[None]]


class BossCrawler:
    def __init__(self, transport: BossTransport, max_jobs: int = 20):
        self.transport = transport
        self.max_jobs = min(20, max(1, max_jobs))

    async def discover(self, roles: list[RoleRecommendation], cities: list[str], progress: Progress) -> list[JobSnapshot]:
        snapshots: list[JobSnapshot] = []
        seen_urls: set[str] = set()
        seen_platform: set[str] = set()
        seen_hashes: set[str] = set()
        combinations = max(1, len(roles) * len(cities) * 2)
        step = 0
        for role in roles:
            query = role.keywords[0] if role.keywords else role.role
            for city in cities:
                city_code = CITY_CODES.get(city)
                if not city_code:
                    raise CrawlError(f"暂不支持城市：{city}")
                for page_number in (1, 2):
                    if len(snapshots) >= self.max_jobs:
                        return snapshots
                    step += 1
                    await progress(int(step / combinations * 80), f"搜索 {city} · {query} · 第 {page_number} 页")
                    url = "https://www.zhipin.com/web/geek/job?" + urlencode({
                        "query": query, "city": city_code, "page": page_number,
                    })
                    search = await self.transport.fetch(url, "search")
                    links = parse_search_page(search.text, url)
                    if not links:
                        continue
                    for link in links:
                        canonical = canonicalize_url(link)
                        if canonical in seen_urls:
                            continue
                        detail = await self.transport.fetch(canonical, "job")
                        try:
                            snapshot = parse_job_page(detail.text, canonical, city, detail.transport)
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
