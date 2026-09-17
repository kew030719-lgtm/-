"""Job-site adapters.

Everything site-specific — hosts, city codes, search URL shapes, selectors,
anti-bot detection — lives behind :class:`SiteAdapter`. ``scoring.py`` and
``database.py`` stay site-neutral: an adapter's whole job is to turn a page into
a :class:`JobSnapshot`, and :func:`build_snapshot` makes sure all of them do it
the same way, because citation validation downstream depends on the evidence
block contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from ..graduate import extract_graduate_metadata
from ..schemas import Evidence, JobSnapshot, RoleRecommendation


class CrawlError(RuntimeError):
    pass


def role_search_terms(role: RoleRecommendation, limit: int = 2) -> list[str]:
    """Turn the model's role recommendation into independent board queries."""
    terms: list[str] = []
    seen: set[str] = set()
    for value in (role.role, *role.keywords):
        term = clean_text(value)
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


class NeedsManualInput(CrawlError):
    pass


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def list_text(soup: BeautifulSoup, selectors: Iterable[str]) -> list[str]:
    for selector in selectors:
        values = [clean_text(node.get_text(" ")) for node in soup.select(selector)]
        if any(values):
            return [value for value in values if value]
    return []


def one_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node and clean_text(node.get_text(" ")):
            return clean_text(node.get_text(" "))
    return ""


_ENUMERATOR = re.compile(r"^[\d一二三四五六七八九十]+[、.．)）]")
_SENTENCE_END = ("。", "！", "？", ".", "!", "?", "；", ";", "，", ",")


def is_heading(value: str) -> bool:
    """A section label rather than job content.

    Sites interleave these into the description block — as a trailing "：" or,
    on 智联, as a bare text node ("岗位职责", "【岗位定位】") followed by a <br>.
    Left alone they become evidence blocks and citations, so they are dropped.
    """
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.endswith(("：", ":")):
        return True
    # A bare label: short, not an enumerated item, and with no sentence ending.
    if len(stripped) > 8 or _ENUMERATOR.match(stripped):
        return False
    return not stripped.endswith(_SENTENCE_END)


def drop_headings(values: list[str]) -> list[str]:
    return [value for value in values if not is_heading(value)]


def json_ld(soup: BeautifulSoup) -> dict:
    """Return the first JSON-LD JobPosting block, if the page ships one."""
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


_YUAN_PER_MONTH = {"k": 1000, "千": 1000, "万": 10000, "w": 10000}


def parse_salary(text: str | None) -> tuple[int | None, int | None]:
    """Normalise a salary string to (monthly_min_yuan, monthly_max_yuan).

    Sites disagree about units: BOSS writes "20-35K", 51job writes
    "15-25万/年" or "8000-12000元/月", 智联 writes "1.5-2.5万". Returning a
    common unit is what lets scoring stay site-neutral.

    Returns (None, None) when nothing recognisable is present ("薪资面议").
    """
    if not text:
        return None, None
    value = re.sub(r"\s+", "", str(text)).replace(",", "").replace("，", "")
    numbers = re.search(r"(\d+(?:\.\d+)?)[-–—~至](\d+(?:\.\d+)?)", value)
    if numbers:
        low, high = float(numbers.group(1)), float(numbers.group(2))
    else:
        single = re.search(r"(\d+(?:\.\d+)?)", value)
        if not single:
            return None, None
        low = high = float(single.group(1))

    lowered = value.lower()
    if "万" in value or "w" in lowered:
        scale = _YUAN_PER_MONTH["万"]
    elif re.search(r"\d[k千]", lowered):
        scale = _YUAN_PER_MONTH["k"]
    else:
        # "元/月" and bare numbers are already monthly yuan.
        scale = 1
    # A yearly figure has to come back down to a month.
    if re.search(r"[/每]年|年薪", value):
        scale /= 12
    return round(low * scale), round(high * scale)


def build_snapshot(
    *,
    site: str,
    canonical_url: str,
    platform_job_id: str,
    title: str,
    company: str,
    city: str = "",
    salary: str = "",
    experience: str = "",
    education: str = "",
    responsibilities: list[str] | None = None,
    required_skills: list[str] | None = None,
    bonus_skills: list[str] | None = None,
    benefits: list[str] | None = None,
    work_type: str = "",
    status: str = "active",
    transport: str = "http",
    cleaned_text: str | None = None,
    max_responsibilities: int = 30,
    max_skills: int = 20,
) -> JobSnapshot:
    """Assemble a JobSnapshot the way every downstream consumer expects.

    Centralised because the evidence blocks, content hash and cleaned text are a
    contract, not a per-site detail: `validate_citations` and the tailoring
    pipeline both read `blocks`, and `cleaned_text` must never contain account
    menus or recommendation lists.
    """
    responsibilities = [item for item in (responsibilities or []) if item][:max_responsibilities]
    skills = [item for item in (required_skills or []) if item][:max_skills]
    snapshot_id = f"snap_{uuid4().hex[:12]}"
    content_for_hash = "\n".join([title, company, salary, experience, education, *responsibilities, *skills])
    content_hash = hashlib.sha256(content_for_hash.encode()).hexdigest()
    body = cleaned_text if cleaned_text is not None else clean_text(
        " ".join([title, company, salary, experience, education, *skills, *responsibilities,
                  "停止招聘" if status == "offline" else ""])
    )
    blocks: list[Evidence] = []
    for index, value in enumerate([title, company, salary, experience, education, *responsibilities], start=1):
        if value:
            blocks.append(Evidence(
                source_type="job", source_id=snapshot_id,
                block_id=f"job-{index:02d}-{hashlib.sha256(value.encode()).hexdigest()[:10]}",
                quote=value, section="职位原文",
            ))
    salary_min, salary_max = parse_salary(salary)
    graduate = extract_graduate_metadata(body, experience)
    return JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id=platform_job_id, canonical_url=canonical_url,
        site=site, title=title, company=company or "公司名称未识别", city=city, salary=salary,
        salary_min=salary_min, salary_max=salary_max, experience=experience, education=education,
        responsibilities=responsibilities, required_skills=skills,
        bonus_skills=list(bonus_skills or []), benefits=list(benefits or []), work_type=work_type,
        **graduate,
        content_hash=content_hash, status=status, cleaned_text=body[:20_000],
        fetched_at=datetime.now(UTC).isoformat(), transport=transport, blocks=blocks,
    )


def job_identity(snapshot: JobSnapshot) -> str:
    """Stable id for a posting, namespaced by site.

    The site prefix is required: platform ids are only unique within a board, so
    a bare `boss:12345`-style key would collide across sites.
    """
    site = snapshot.site or "boss"
    if snapshot.platform_job_id:
        return f"{site}:{snapshot.platform_job_id}"
    return "url:" + hashlib.sha256(snapshot.canonical_url.encode()).hexdigest()[:24]


class SiteAdapter(ABC):
    """One job site: its identity, its URL rules, and its parsers."""

    key: str = ""
    label: str = ""
    hosts: frozenset[str] = frozenset()
    # Host used to resolve relative links. Stated explicitly: deriving it from
    # set ordering picks whichever host sorts first, which for BOSS is the
    # mobile host and silently rewrites every discovered URL.
    base_url: str = ""
    # City name -> the site's own city identifier.
    cities: dict[str, str] = {}
    tracking_query: frozenset[str] = frozenset()
    # Some sites are more aggressive about rate limits than others.
    min_delay_seconds: float = 10.0
    # How long the headless browser waits after domcontentloaded for the SPA to
    # render results. 前程无忧 needs ~4s: at 1.2s and 2.5s its result list has no
    # metadata attributes at all, and only at ~4s do all twenty appear.
    render_wait_ms: int = 1200
    # Regex applied to URL.pathname by the Chrome extension when deciding
    # whether a discovered URL is a job detail page.
    job_path_pattern: str = ""
    login_pattern: str = ""
    # Selector that proves real job content rendered (used by detect_blocked).
    job_content_selector: str = ""
    challenge_tokens: tuple[str, ...] = (
        "请完成验证", "请完成验证码", "拖动滑块", "访问过于频繁", "请通过验证",
        # 智联's anti-bot interstitial: "正在验证连接安全性，请勾选下方复选框".
        # Without these it reads as "structure changed" rather than "verify me".
        "正在验证连接安全性", "请勾选下方复选框", "访问验证",
    )
    login_tokens: tuple[str, ...] = ("登录后查看", "请先登录", "扫码登录")

    # ------------------------------------------------------------- URLs
    def validate_url(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self.hosts:
            raise CrawlError(f"仅允许访问 {self.label} 公开 HTTPS 页面")
        if parts.username or parts.password or parts.port not in (None, 443):
            raise CrawlError("URL 包含不允许的认证信息或端口")
        return url

    def canonicalize_url(self, url: str) -> str:
        self.validate_url(url)
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key not in self.tracking_query]
        path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
        return urlunsplit(("https", parts.hostname or "", path, urlencode(sorted(query)), ""))

    def default_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        return f"https://{sorted(self.hosts)[0]}" if self.hosts else ""

    def search_url(self, query: str, city: str, page: int) -> str:
        raise NotImplementedError

    @abstractmethod
    def platform_id(self, url: str) -> str:
        """The site's own id for a posting, or '' when the URL carries none."""

    # -------------------------------------------------------- page reading
    def detect_blocked(self, html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select('script, style, noscript, template, [hidden], [aria-hidden="true"]'):
            node.decompose()
        for node in list(soup.select("[style]")):
            if node.attrs and re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.get("style", ""), re.I):
                node.decompose()
        text = clean_text(soup.get_text(" ")).lower()
        has_job_content = bool(self.job_content_selector and soup.select_one(self.job_content_selector))
        challenge = any(token in text for token in self.challenge_tokens)
        if challenge or (not has_job_content and any(token in text for token in ("安全验证", "验证码", "captcha"))):
            return "页面需要验证，请在采集标签页完成验证，再点击助手中的继续任务"
        if not has_job_content and any(token in text for token in self.login_tokens):
            return "页面要求登录，请在采集标签页登录，再点击助手中的继续任务"
        return None

    @abstractmethod
    def parse_search_page(self, html: str, base_url: str | None = None) -> list[str]:
        """Absolute, canonical job URLs found on a search results page."""

    @abstractmethod
    def parse_job_page(self, html: str, url: str, city_hint: str = "", transport: str = "http") -> JobSnapshot:
        """One posting's snapshot, or raise CrawlError/NeedsManualInput."""

    def looks_like_job(self, html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one('script[type="application/ld+json"]'):
            return True
        return bool(self.job_content_selector and soup.select_one(self.job_content_selector))

    def parse_manual_content(self, content: str, url: str, city_hint: str = "") -> JobSnapshot:
        """Build a snapshot from text the user pasted, with no HTML to parse."""
        lines = [clean_text(line) for line in content.splitlines() if clean_text(line)]
        if not lines:
            raise CrawlError("手动岗位描述为空")
        skills = self.extract_skills(content)
        salary = re.search(r"\d+(?:\.\d+)?\s*[-–—~至]\s*\d+(?:\.\d+)?\s*[Kk万]", content)
        experience = re.search(r"(?:经验不限|应届生?|\d+\s*[-–—~至]\s*\d+\s*年|\d+\s*年以上)", content)
        education = re.search(r"(?:学历不限|博士|硕士|本科|大专|中专|高中)", content)
        company = next((line.split("：", 1)[-1].strip() for line in lines[:5] if "公司" in line), "")
        try:
            canonical = self.canonicalize_url(url)
        except CrawlError:
            # Pasted content is not crawled, so the URL is informational and a URL
            # from an unrecognised host must not block saving the description.
            canonical = url
        snapshot = build_snapshot(
            site=self.key, canonical_url=canonical, platform_job_id=self.platform_id(url),
            title=lines[0][:120], company=company, city=city_hint,
            salary=salary.group(0) if salary else "", experience=experience.group(0) if experience else "",
            education=education.group(0) if education else "",
            responsibilities=lines[1:], required_skills=skills, transport="manual",
            cleaned_text=clean_text(content)[:20_000],
        )
        return snapshot

    def extract_skills(self, content: str) -> list[str]:
        from ..resume import SKILLS

        # SKILLS maps a lowercase token to its display form; callers want the
        # display form, matching how required_skills reads everywhere else.
        lowered = content.lower()
        return sorted({display for token, display in SKILLS.items() if token in lowered})
