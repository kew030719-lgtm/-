from pathlib import Path

import httpx
import pytest

from career_radar.crawler import (
    BossCrawler, BossTransport, CrawlError, FetchResult, NeedsManualInput, canonicalize_url,
    parse_job_page, parse_manual_content, parse_search_page, validate_url,
)
from career_radar.schemas import Evidence, RoleRecommendation


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_search_and_job_fixtures_parse_structured_fields():
    links = parse_search_page(fixture("search.html"))
    assert links == [
        "https://www.zhipin.com/job_detail/abc123.html",
        "https://www.zhipin.com/job_detail/def456.html",
    ]
    job = parse_job_page(fixture("job.html"), links[0], "北京")
    assert job.title == "Python Agent 开发工程师"
    assert job.company == "星图科技"
    assert job.required_skills == ["Python", "FastAPI", "RAG"]
    assert job.platform_job_id == "abc123"
    assert all(block.source_id == job.snapshot_id for block in job.blocks)


@pytest.mark.parametrize("name", ["captcha.html", "login.html"])
def test_restricted_pages_require_manual_input(name):
    with pytest.raises(NeedsManualInput):
        parse_search_page(fixture(name))


def test_offline_job_is_preserved_as_a_snapshot():
    job = parse_job_page(fixture("offline.html"), "https://www.zhipin.com/job_detail/offline.html")
    assert job.status == "offline"
    assert "停止招聘" in job.cleaned_text


@pytest.mark.parametrize("url", [
    "http://www.zhipin.com/job_detail/x", "https://evil.example/x",
    "https://127.0.0.1/x", "https://www.zhipin.com:444/x",
    "https://user:pass@www.zhipin.com/x",
])
def test_url_allowlist_rejects_unsafe_targets(url):
    with pytest.raises(CrawlError):
        validate_url(url)


def test_canonical_url_strips_tracking_query():
    assert canonicalize_url("https://www.zhipin.com/job_detail/a/?lid=1&x=2") == "https://www.zhipin.com/job_detail/a?x=2"


def test_manual_description_extracts_core_fields():
    snapshot = parse_manual_content(
        "Python 后端工程师\n公司：星图科技\n20-30K\n3-5年 本科\n负责 FastAPI、Redis 和 Docker 服务开发",
        "https://www.zhipin.com/job_detail/manual.html", "北京",
    )
    assert snapshot.title == "Python 后端工程师"
    assert snapshot.company == "星图科技"
    assert snapshot.salary == "20-30K"
    assert {"FastAPI", "Redis", "Docker"}.issubset(snapshot.required_skills)


@pytest.mark.asyncio
async def test_cross_domain_redirect_is_rejected(monkeypatch):
    async def no_sleep(_: float):
        return None
    monkeypatch.setattr("career_radar.crawler.asyncio.sleep", no_sleep)
    async def handler(_: httpx.Request):
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = BossTransport(0, client)
    with pytest.raises(CrawlError):
        await transport.fetch_http("https://www.zhipin.com/job_detail/a")
    await client.aclose()


@pytest.mark.asyncio
async def test_http_content_falls_back_to_browser_rendering():
    class Transport(BossTransport):
        async def fetch_http(self, url: str) -> str:
            return "<html><body><div id='app'></div></body></html>"

        async def fetch_browser(self, url: str) -> str:
            return fixture("search.html")

    client = httpx.AsyncClient()
    result = await Transport(0, client).fetch("https://www.zhipin.com/web/geek/job?query=Python", "search")
    assert result.transport == "browser"
    assert parse_search_page(result.text)
    await client.aclose()


@pytest.mark.asyncio
async def test_unrecognized_browser_page_requires_manual_input():
    class Transport(BossTransport):
        async def fetch_http(self, url: str) -> str:
            return "<html><body><div id='app'></div></body></html>"

        async def fetch_browser(self, url: str) -> str:
            return "<html><body><main>页面结构已经变化</main></body></html>"

    client = httpx.AsyncClient()
    with pytest.raises(NeedsManualInput, match="结构无法识别"):
        await Transport(0, client).fetch("https://www.zhipin.com/web/geek/job?query=Python", "search")
    await client.aclose()


@pytest.mark.asyncio
async def test_scheduler_stops_at_maximum_unique_jobs():
    class Transport:
        async def fetch(self, url: str, expects: str):
            return FetchResult(fixture("search.html") if expects == "search" else fixture("job.html"), "http")

    evidence = Evidence(source_type="resume", source_id="p", block_id="r1", quote="Python")
    role = RoleRecommendation(
        role="Python 后端", keywords=["Python"], confidence="高", rationale="有 Python 证据",
        citations=[evidence],
    )
    updates = []

    async def progress(value, message):
        updates.append((value, message))

    jobs = await BossCrawler(Transport(), max_jobs=1).discover([role], ["北京"], progress)
    assert len(jobs) == 1
    assert updates
