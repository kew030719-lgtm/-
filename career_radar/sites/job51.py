"""前程无忧 (51job.com) adapter.

Verified against rendered pages: the search DOM, its `sensorsdata` metadata
attribute, and all ten city codes (confirmed by rendering `?jobArea=<code>` and
reading the areas back off the returned cards).

NOT verified: the detail page. `jobs.51job.com` answers automated browsers with a
slider challenge ("访问验证 … 请按住滑块"), so `parse_job_page` below has never seen
a real 51job posting. It therefore only accepts a description it can positively
identify — JSON-LD, or through a candidate container and a length floor — and
otherwise raises, which routes the run to 粘贴岗位描述 instead of inventing
responsibilities out of navigation text.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..schemas import JobSnapshot
from .base import CrawlError, NeedsManualInput, SiteAdapter, build_snapshot, clean_text, json_ld, one_text


class Job51Adapter(SiteAdapter):
    key = "job51"
    label = "前程无忧"
    hosts = frozenset({"www.51job.com", "we.51job.com", "jobs.51job.com", "m.51job.com"})
    base_url = "https://jobs.51job.com"
    # Verified by rendering ?jobArea=<code> and reading jobArea off the cards.
    cities = {
        "北京": "010000", "上海": "020000", "广州": "030200", "深圳": "040000", "杭州": "080200",
        "成都": "090200", "武汉": "180200", "南京": "070200", "西安": "200200", "苏州": "070300",
    }
    tracking_query = frozenset({"from", "refer", "utm_source", "utm_medium", "sensorsname"})
    min_delay_seconds = 12.0
    render_wait_ms = 4000
    job_path_pattern = r"jobs\.51job\.com/[a-z]+/\d+\.html"
    login_pattern = r"/pc/login|/user/login|passport"
    job_content_selector = ".joblist-item, .job_msg, .bmsg"
    challenge_tokens = (
        "请完成验证", "请完成验证码", "拖动滑块", "访问过于频繁", "请通过验证", "访问验证",
        "请按住滑块", "验证通过后即可继续",
    )

    def search_url(self, query: str, city: str, page: int) -> str:
        code = self.cities.get(city)
        if not code:
            raise CrawlError(f"{self.label} 暂不支持城市：{city}")
        return (
            f"https://we.51job.com/pc/search?jobArea={code}"
            f"&keyword={quote_plus(query)}&pageNum={page}"
        )

    def platform_id(self, url: str) -> str:
        match = re.search(r"/(\d{6,})\.html", url)
        return match.group(1) if match else ""

    # ------------------------------------------------------------- search
    @staticmethod
    def _cards(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[dict] = []
        for node in soup.select("[sensorsdata]"):
            raw = node.get("sensorsdata")
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict) and value.get("jobId") and value.get("jobTitle"):
                out.append(value)
        return out

    def parse_search_page(self, html: str, base_url: str | None = None) -> list[str]:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        links: list[str] = []
        for card in self._cards(html):
            # The listing carries the posting's own city slug, which is what the
            # detail URL is keyed on — and it can differ from the searched city.
            area = str(card.get("jobArea") or "")
            slug = self._slug(area.split("·")[0].strip())
            url = f"https://jobs.51job.com/{slug or 'all'}/{card['jobId']}.html"
            if url not in links:
                links.append(url)
        # Returns [] rather than raising, so Transport.fetch can fall back to the
        # browser when the plain fetch yields nothing usable. See zhaopin.py.
        return links

    @staticmethod
    def _slug(city: str) -> str:
        from .slugs import CITY_SLUGS

        return CITY_SLUGS.get(city, "")

    # ------------------------------------------------------------- detail
    def parse_job_page(self, html: str, url: str, city_hint: str = "", transport: str = "http") -> JobSnapshot:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        canonical = self.canonicalize_url(url)
        structured = json_ld(BeautifulSoup(html, "html.parser"))
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(("script", "style", "noscript")):
            node.decompose()

        title = clean_text(str(structured.get("title") or "")) or one_text(
            soup, ("h1", ".joblist-item-jobname", ".cn h1", "[class*='job-title']")
        )
        organization = structured.get("hiringOrganization") or {}
        company = clean_text(str(organization.get("name") or "")) if isinstance(organization, dict) else ""
        company = company or one_text(soup, (".cname", ".company-name", "[class*='company-name']", "[class*='com-name']"))
        salary = one_text(soup, (".salary", "[class*='salary']"))
        experience = one_text(soup, ("[class*='experience']", "[class*='job-year']"))
        education = one_text(soup, ("[class*='degree']", "[class*='education']"))

        responsibilities: list[str] = []
        structured_description = structured.get("description")
        if isinstance(structured_description, str) and structured_description.strip():
            responsibilities = [clean_text(item) for item in re.split(r"[\n；;]", structured_description) if clean_text(item)]
        if not responsibilities:
            for selector in (".job_msg", ".bmsg", "[class*='job-detail']", "[class*='describ']"):
                node = soup.select_one(selector)
                if not node:
                    continue
                values = [clean_text(value) for value in node.stripped_strings]
                values = [value for value in values if len(value) >= 8]
                if sum(len(value) for value in values) >= 60:
                    responsibilities = values
                    break
        skills = [item for item in one_text(soup, ("[class*='job-keyword']", "[class*='tags']")).split() if item]

        if not title:
            raise CrawlError("页面缺少可识别的职位数据")
        if not responsibilities:
            # Unverified selectors meet an unexpected layout: stop rather than
            # store a posting whose 职责 is actually page furniture.
            raise CrawlError("未识别到独立的职位描述区域，暂停以避免把导航文本作为职责")
        return build_snapshot(
            site=self.key, canonical_url=canonical, platform_job_id=self.platform_id(url),
            title=title, company=company, city=city_hint, salary=salary, experience=experience,
            education=education, responsibilities=responsibilities, required_skills=skills,
            transport=transport,
        )