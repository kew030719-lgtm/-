"""Adapter-level tests.

The BOSS cases here are the regression suite for the extraction out of the old
crawler.py — same fixtures, same assertions, now expressed against SiteAdapter.
"""

from pathlib import Path

import httpx
import pytest

from career_radar.schemas import Evidence, JobSnapshot, RoleRecommendation
from career_radar.sites import (
    CrawlError, FetchResult, NeedsManualInput, SiteCrawler, Transport, get_site,
)
from career_radar.sites.boss import BossAdapter


FIXTURES = Path(__file__).parent / "fixtures"
BOSS = BossAdapter()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_search_and_job_fixtures_parse_structured_fields():
    links = BOSS.parse_search_page(fixture("search.html"))
    assert links == [
        "https://www.zhipin.com/job_detail/abc123.html",
        "https://www.zhipin.com/job_detail/def456.html",
    ]
    job = BOSS.parse_job_page(fixture("job.html"), links[0], "北京")
    assert job.title == "Python Agent 开发工程师"
    assert job.company == "星图科技"
    assert job.required_skills == ["Python", "FastAPI", "RAG"]
    assert job.platform_job_id == "abc123"
    assert job.site == "boss"
    assert all(block.source_id == job.snapshot_id for block in job.blocks)


@pytest.mark.parametrize("name", ["captcha.html", "login.html"])
def test_restricted_pages_require_manual_input(name):
    with pytest.raises(NeedsManualInput):
        BOSS.parse_search_page(fixture(name))


def test_hidden_verification_widget_does_not_block_jobs():
    html = fixture("search.html") + '<div hidden><p>请完成验证码</p></div><div style="display: none"><p>拖动滑块</p></div>'
    assert len(BOSS.parse_search_page(html)) == 2


def test_job_mention_of_captcha_does_not_block_but_active_challenge_does():
    html = fixture("search.html") + "<p>验证码开发经验</p>"
    assert len(BOSS.parse_search_page(html)) == 2
    with pytest.raises(NeedsManualInput):
        BOSS.parse_search_page(html + '<div role="dialog">请完成验证码后继续访问</div>')


def test_description_scope_excludes_recommendations_and_account_menu():
    html = """<html><head><title>「Python招聘」_测试科技招聘-BOSS直聘</title></head><body>
    <nav>个人中心 用户姓名</nav><h1>Python</h1><span class="salary">13-26K</span>
    <p>上海 经验不限 本科 感兴趣</p><div class="company-info"><div class="name">Python 13-26K</div></div>
    <div class="job-detail-section"><h3>职位 描述</h3><ul class="job-keyword-list"><li>Python</li></ul>
    <div>负责开发 FastAPI 服务。<br>维护异步任务。</div><h2>招聘者</h2>
    <h3>更多职位</h3><ul><li>另一个职位 Java开发</li></ul></div></body></html>"""
    job = BOSS.parse_job_page(html, "https://www.zhipin.com/job_detail/real-layout.html")
    assert job.company == "测试科技"
    assert job.experience == "经验不限"
    assert job.responsibilities == ["负责开发 FastAPI 服务。", "维护异步任务。"]
    assert "另一个职位" not in job.cleaned_text and "用户姓名" not in job.cleaned_text


def test_offline_job_is_preserved_as_a_snapshot():
    job = BOSS.parse_job_page(fixture("offline.html"), "https://www.zhipin.com/job_detail/offline.html")
    assert job.status == "offline"
    assert "停止招聘" in job.cleaned_text


@pytest.mark.parametrize("url", [
    "http://www.zhipin.com/job_detail/x", "https://evil.example/x",
    "https://127.0.0.1/x", "https://www.zhipin.com:444/x",
    "https://user:pass@www.zhipin.com/x",
])
def test_url_allowlist_rejects_unsafe_targets(url):
    with pytest.raises(CrawlError):
        BOSS.validate_url(url)


def test_canonical_url_strips_tracking_query():
    assert BOSS.canonicalize_url(
        "https://www.zhipin.com/job_detail/a/?lid=1&x=2"
    ) == "https://www.zhipin.com/job_detail/a?x=2"


def test_manual_description_extracts_core_fields():
    snapshot = BOSS.parse_manual_content(
        "Python 后端工程师\n公司：星图科技\n20-30K\n3-5年 本科\n负责 FastAPI、Redis 和 Docker 服务开发",
        "https://www.zhipin.com/job_detail/manual.html", "北京",
    )
    assert snapshot.title == "Python 后端工程师"
    assert snapshot.company == "星图科技"
    assert snapshot.salary == "20-30K"
    assert {"FastAPI", "Redis", "Docker"}.issubset(set(snapshot.required_skills))


def test_manual_description_tolerates_a_url_from_another_site():
    """Pasted text is not crawled, so an unrecognised host must not block it."""
    snapshot = BOSS.parse_manual_content("数据分析师\n公司：某公司\n负责 SQL 与报表", "https://example.invalid/x", "北京")
    assert snapshot.title == "数据分析师"


@pytest.mark.asyncio
async def test_cross_domain_redirect_is_rejected(monkeypatch):
    async def no_sleep(_: float):
        return None

    monkeypatch.setattr("career_radar.sites.transport.asyncio.sleep", no_sleep)

    async def handler(_: httpx.Request):
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = Transport(BOSS, 0, client)
    with pytest.raises(CrawlError):
        await transport.fetch_http("https://www.zhipin.com/job_detail/a")
    await client.aclose()


@pytest.mark.asyncio
async def test_http_content_falls_back_to_browser_rendering():
    class FakeTransport(Transport):
        async def fetch_http(self, url: str) -> str:
            return "<html><body><div id='app'></div></body></html>"

        async def fetch_browser(self, url: str) -> str:
            return fixture("search.html")

    client = httpx.AsyncClient()
    result = await FakeTransport(BOSS, 0, client).fetch(
        "https://www.zhipin.com/web/geek/job?query=Python", "search"
    )
    assert result.transport == "browser"
    assert BOSS.parse_search_page(result.text)
    await client.aclose()


@pytest.mark.asyncio
async def test_unrecognized_browser_page_requires_manual_input():
    class FakeTransport(Transport):
        async def fetch_http(self, url: str) -> str:
            return "<html><body><div id='app'></div></body></html>"

        async def fetch_browser(self, url: str) -> str:
            return "<html><body><main>页面结构已经变化</main></body></html>"

    client = httpx.AsyncClient()
    with pytest.raises(NeedsManualInput, match="结构无法识别"):
        await FakeTransport(BOSS, 0, client).fetch(
            "https://www.zhipin.com/web/geek/job?query=Python", "search"
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_scheduler_stops_at_maximum_unique_jobs():
    class FakeTransport:
        async def fetch(self, url: str, expects: str):
            return FetchResult(
                fixture("search.html") if expects == "search" else fixture("job.html"), "http"
            )

    evidence = Evidence(source_type="resume", source_id="p", block_id="r1", quote="Python")
    role = RoleRecommendation(
        role="Python 后端", keywords=["Python"], confidence="高", rationale="有 Python 证据",
        citations=[evidence],
    )
    updates = []

    async def progress(value, message):
        updates.append((value, message))

    crawler = SiteCrawler(BOSS, FakeTransport(), max_jobs=1)
    jobs = await crawler.discover([role], ["北京"], progress)
    assert len(jobs) == 1
    assert updates


def test_every_adapter_declares_a_search_url_and_city_table():
    """A new adapter that forgets these fails here rather than mid-crawl."""
    from career_radar.sites import SITES

    for key, adapter in SITES.items():
        assert adapter.key == key and adapter.label, key
        assert adapter.hosts, key
        assert adapter.cities, key
        assert adapter.job_path_pattern, key
        url = adapter.search_url("Python", next(iter(adapter.cities)), 1)
        assert url.startswith("https://"), key
        adapter.validate_url(url)


def test_site_lookup_by_key_and_by_host():
    from career_radar.sites import get_site, site_for_url

    assert get_site("boss").label == "BOSS 直聘"
    assert site_for_url("https://www.zhipin.com/job_detail/a.html").key == "boss"
    assert site_for_url("https://sou.zhaopin.com/?jl=530").key == "zhaopin"
    with pytest.raises(KeyError):
        site_for_url("https://example.invalid/x")


def test_job_identity_is_namespaced_by_site():
    """Platform ids are only unique within a board."""
    from career_radar.sites import job_identity

    def snap(site: str, pid: str) -> JobSnapshot:
        return JobSnapshot(
            snapshot_id="s", platform_job_id=pid, canonical_url=f"https://{site}.example/{pid}",
            site=site, title="t", company="c", content_hash="h", fetched_at="now", transport="http",
        )

    assert job_identity(snap("boss", "123")) == "boss:123"
    assert job_identity(snap("zhaopin", "123")) == "zhaopin:123"
    assert job_identity(snap("boss", "123")) != job_identity(snap("zhaopin", "123"))