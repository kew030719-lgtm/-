"""智联招聘 (zhaopin.com) adapter.

Selectors and city codes below were read off rendered pages, not guessed:
`https://sou.zhaopin.com/?jl=<code>&kw=<query>` and one
`https://www.zhaopin.com/jobdetail/<id>.htm` detail page.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..schemas import JobSnapshot
from .base import (
    CrawlError, NeedsManualInput, SiteAdapter, build_snapshot, drop_headings, list_text, one_text,
)


class ZhaopinAdapter(SiteAdapter):
    key = "zhaopin"
    label = "智联招聘"
    hosts = frozenset({"www.zhaopin.com", "sou.zhaopin.com", "m.zhaopin.com"})
    base_url = "https://www.zhaopin.com"
    # Verified by rendering ?jl=<code> and reading back the city in the page title.
    cities = {
        "北京": "530", "上海": "538", "广州": "763", "深圳": "765", "杭州": "653",
        "成都": "801", "武汉": "736", "南京": "635", "西安": "854", "苏州": "639",
    }
    tracking_query = frozenset({"srccode", "refcode", "preactionid", "data_identity", "utm_source", "utm_medium"})
    # 智联 is stricter about automated traffic than BOSS.
    min_delay_seconds = 12.0
    # Renders fast, but leave headroom for slower first paints.
    render_wait_ms = 2000
    job_path_pattern = r"/jobdetail/[A-Za-z0-9]+\.htm"
    login_pattern = r"passport\.zhaopin\.com|/user/login"
    job_content_selector = ".job-card, .describtion-card, .summary-planes__title"
    challenge_tokens = (
        "请完成验证", "请完成验证码", "拖动滑块", "访问过于频繁", "请通过验证", "访问验证",
    )

    def search_url(self, query: str, city: str, page: int) -> str:
        code = self.cities.get(city)
        if not code:
            raise CrawlError(f"{self.label} 暂不支持城市：{city}")
        return f"https://sou.zhaopin.com/?jl={code}&kw={quote_plus(query)}&p={page}"

    def platform_id(self, url: str) -> str:
        match = re.search(r"/jobdetail/([A-Za-z0-9]+)\.htm", url)
        return match.group(1) if match else ""

    def parse_search_page(self, html: str, base_url: str | None = None) -> list[str]:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        # Result cards navigate by JS. On a real 20-result page the posting ids appear
        # in an embedded application/json island (`positionURL`) but only one of
        # them is ever a real <a href>, so following anchors finds 1 job out of 20.
        # Reading the ids out of the body is what makes this page usable at all.
        links: list[str] = []
        for job_id in re.findall(r"/jobdetail/([A-Za-z0-9]+)\.htm", html):
            url = f"https://www.zhaopin.com/jobdetail/{job_id}.htm"
            if url not in links:
                links.append(url)
        # Deliberately returns [] rather than raising. A plain HTTP fetch gets an
        # SEO shell with no ids, and Transport.fetch treats an empty parse as
        # "try the browser instead". Raising here would abort that fallback and
        # make every 智联 run look like it needs manual input.
        return links

    def parse_job_page(self, html: str, url: str, city_hint: str = "", transport: str = "http") -> JobSnapshot:
        blocked = self.detect_blocked(html)
        if blocked:
            raise NeedsManualInput(blocked)
        canonical = self.canonicalize_url(url)
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(("script", "style", "noscript")):
            node.decompose()

        title = one_text(soup, ("h1.summary-planes__title", "h1"))
        # Salary is masked as "**-**元" unless the viewer is signed in; parse_salary
        # returns (None, None) for that, which scoring already treats as 无法判断.
        salary = one_text(soup, (".summary-planes__salary",))
        info = one_text(soup, (".summary-planes__info",))
        # .company-info is the whole card: listing meta, badges and the company
        # description too. Only __name is the name.
        company = one_text(soup, (".company-info__name", ".company-info .company-name"))

        experience_match = re.search(r"\d+\s*[-–—~至]\s*\d+\s*年|经验不限|应届生?", info)
        education_match = re.search(r"博士|硕士|本科|大专|中专|高中|学历不限", info)
        city = city_hint
        if info:
            first = info.split()[0] if info.split() else ""
            if first and re.fullmatch(r"[一-龥]{2,6}", first):
                city = city_hint or first

        # The description lives in its own card; 相似职位 is a *sibling* seo-card, so
        # scoping to .describtion-card keeps recommendations out by construction.
        skills = list_text(soup, (".describtion-card__skills-item", ".describtion-card__skills-content span"))
        detail = soup.select_one(".describtion-card__detail-content")
        responsibilities: list[str] = []
        if detail:
            responsibilities = drop_headings(
                [value for value in (t.strip() for t in detail.stripped_strings) if value]
            )
        elif soup.select_one(".describtion-card"):
            card = soup.select_one(".describtion-card")
            values = list(card.stripped_strings)
            # drop the 职位描述 heading and the skills run that precedes the body
            start = values.index("职位描述") + 1 if "职位描述" in values else 0
            responsibilities = [value for value in values[start:] if value not in skills][:30]

        if not title:
            raise CrawlError("页面缺少可识别的职位数据")
        if not responsibilities:
            raise CrawlError("未识别到独立的职位描述区域，暂停以避免把相似职位作为职责")
        return build_snapshot(
            site=self.key, canonical_url=canonical, platform_job_id=self.platform_id(url),
            title=title, company=company, city=city, salary=salary,
            experience=experience_match.group(0) if experience_match else "",
            education=education_match.group(0) if education_match else "",
            responsibilities=responsibilities, required_skills=skills, transport=transport,
        )