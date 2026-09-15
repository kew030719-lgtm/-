"""BOSS 直聘 (zhipin.com) adapter."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup, NavigableString

from ..schemas import JobSnapshot
from .base import CrawlError, NeedsManualInput, SiteAdapter, build_snapshot, clean_text, json_ld, list_text, one_text


class BossAdapter(SiteAdapter):
    key = "boss"
    label = "BOSS 直聘"
    hosts = frozenset({"www.zhipin.com", "m.zhipin.com"})
    base_url = "https://www.zhipin.com"
    cities = {
        "北京": "101010100", "上海": "101020100", "深圳": "101280600", "广州": "101280100",
        "杭州": "101210100", "成都": "101270100", "武汉": "101200100", "南京": "101190100",
        "西安": "101110100", "苏州": "101190400",
    }
    tracking_query = frozenset({"ka", "lid", "securityId", "sessionId"})
    job_path_pattern = r"/job_detail/[A-Za-z0-9_-]+\.html"
    login_pattern = r"/web/user|/login"
    job_content_selector = "a[href*='/job_detail/'], .job-sec-text, .job-description"

    def search_url(self, query: str, city: str, page: int) -> str:
        city_code = self.cities.get(city)
        if not city_code:
            raise CrawlError(f"{self.label} 暂不支持城市：{city}")
        return "https://www.zhipin.com/web/geek/job?" + urlencode(
            {"query": query, "city": city_code, "page": page}
        )

    def platform_id(self, url: str) -> str:
        match = re.search(r"/job_detail/([A-Za-z0-9_-]+)", url)
        return match.group(1) if match else ""

    def parse_search_page(self, html: str, base_url: str | None = None) -> list[str]:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        soup = BeautifulSoup(html, "html.parser")
        base = base_url or self.default_base_url()
        links: list[str] = []
        selectors = ("a.job-card-left", "a.job-name", "a[href*='/job_detail/']", "a[href*='/job_detail']")
        for selector in selectors:
            for node in soup.select(selector):
                href = node.get("href")
                if not href:
                    continue
                try:
                    canonical = self.canonicalize_url(urljoin(base, href))
                except CrawlError:
                    continue
                if canonical not in links:
                    links.append(canonical)
        return links

    def parse_job_page(self, html: str, url: str, city_hint: str = "", transport: str = "http") -> JobSnapshot:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        canonical = self.canonicalize_url(url)
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(("script", "style", "noscript")):
            node.decompose()
        structured = json_ld(BeautifulSoup(html, "html.parser"))
        title = clean_text(str(structured.get("title") or "")) or one_text(soup, ("h1", ".name h1", ".job-name"))
        organization = structured.get("hiringOrganization") or {}
        company = clean_text(str(organization.get("name") or "")) if isinstance(organization, dict) else ""
        document_title = soup.title.get_text() if soup.title else ""
        title_company = re.search(r"招聘」_(.+?)招聘-BOSS直聘", document_title)
        company = company or (title_company.group(1) if title_company else "")
        company = company or one_text(soup, (".sider-company a[href*='/gongsi/']", ".company-info a[href*='/gongsi/']", ".company-name"))
        salary = one_text(soup, (".salary", ".job-banner .salary", ".job-salary"))
        experience = one_text(soup, (".job-limit .text-experiece", ".job-primary .job-limit", "[class*='experience']"))
        education = one_text(soup, (".job-limit .text-degree", "[class*='degree']", "[class*='education']"))

        responsibilities: list[str] = []
        heading = next(
            (node for node in soup.select("h3,h2")
             if re.sub(r"\s+|来自BOSS直聘|BOSS直聘|boss|kanzhun|直聘", "", node.get_text()) == "职位描述"),
            None,
        )
        if heading:
            # Stop at the next section/recruiter heading, never read recommendation lists.
            for node in heading.next_elements:
                if getattr(node, "name", None) in {"h2", "h3"}:
                    break
                if isinstance(node, NavigableString) and heading not in node.parents:
                    if node.find_parent("ul") or node.find_parent("script") or node.find_parent("style"):
                        continue
                    value = clean_text(str(node))
                    if value:
                        responsibilities.append(value)
        detail_text = one_text(soup, (".job-sec-text", ".job-description"))
        if not responsibilities and detail_text:
            responsibilities = [clean_text(item) for item in re.split(r"[\n；;]", detail_text) if clean_text(item)]

        skill_nodes = list_text(soup, (".job-keyword-list li", ".job-tags span", ".tag-list li"))
        body = clean_text(soup.get_text(" "))
        header = body.split("感兴趣", 1)[0]
        if not experience:
            match = re.search(r"经验不限|在校/应届|\d+\s*[-–—~至]\s*\d+\s*年|\d+\s*年以上", header)
            experience = match.group(0) if match else ""
        is_offline = any(token in body for token in ("职位已下线", "停止招聘", "职位不存在"))
        if not title or (len(body) < 40 and not is_offline):
            raise CrawlError("页面缺少可识别的职位数据")
        if not responsibilities and not is_offline:
            raise CrawlError("未识别到独立的职位描述区域，暂停以避免把推荐职位作为职责")
        return build_snapshot(
            site=self.key, canonical_url=canonical, platform_job_id=self.platform_id(url),
            title=title, company=company, city=city_hint, salary=salary, experience=experience,
            education=education, responsibilities=responsibilities, required_skills=skill_nodes,
            status="offline" if is_offline else "active", transport=transport,
        )