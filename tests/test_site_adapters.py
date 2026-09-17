"""Adapter tests for the non-BOSS sites, and salary normalisation.

The fixtures are verbatim markup lifted from pages rendered in a real browser,
so these assert against the structure the sites actually serve rather than a
structure we imagined.
"""

from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx
import pytest

from career_radar.sites import Transport, build_snapshot, parse_salary
from career_radar.sites.job51 import Job51Adapter
from career_radar.sites.zhaopin import ZhaopinAdapter


FIXTURES = Path(__file__).parent / "fixtures"
ZHAOPIN = ZhaopinAdapter()
JOB51 = Job51Adapter()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_snapshot_separates_benefit_tags_from_required_skills():
    job = build_snapshot(
        site="zhaopin", canonical_url="https://example.com/job", platform_job_id="benefits",
        title="AI 产品经理", company="测试公司", responsibilities=["负责 Agent 产品评测。"],
        required_skills=["Python", "交通补助", "节日福利", "免费班车", "团建聚餐", "零食下午茶"],
    )

    assert job.required_skills == ["Python"]
    assert job.benefits == ["交通补助", "节日福利", "免费班车", "团建聚餐", "零食下午茶"]


# ------------------------------------------------------------------- 智联

def test_zhaopin_search_extracts_ids_the_anchors_do_not_carry():
    """Only one of twenty result ids is a real <a href>; the rest need the body."""
    links = ZHAOPIN.parse_search_page(fixture("zhaopin_search.html"))
    assert links == [
        "https://www.zhaopin.com/jobdetail/CCL1520254400J40888296014.htm",
        "https://www.zhaopin.com/jobdetail/CC599187620J40922796703.htm",
    ]


def test_zhaopin_job_page_parses_the_real_detail_layout():
    job = ZHAOPIN.parse_job_page(
        fixture("zhaopin_job.html"), "https://www.zhaopin.com/jobdetail/CC000544460J40844274616.htm", "北京"
    )
    assert job.site == "zhaopin"
    assert job.title == "Golang后端开发工程师"
    assert job.platform_job_id == "CC000544460J40844274616"
    # Exact, not a substring: .company-info wraps the listing meta, badges and
    # the company description too, so a looser selector swallows all of it.
    assert job.company == "软通动力信息技术(集团)股份有限公司"
    assert "在招职位" not in job.company and "公司介绍" not in job.company
    assert job.experience == "3-5年"
    assert job.education == "本科"
    assert {"C++", "Python", "Java"}.issubset(set(job.required_skills))
    assert any("软件开发" in item for item in job.responsibilities)
    assert all(block.source_id == job.snapshot_id for block in job.blocks)


def test_zhaopin_keeps_similar_jobs_out_of_responsibilities():
    """相似职位 is a sibling card; it must never become the job description."""
    job = ZHAOPIN.parse_job_page(
        fixture("zhaopin_job.html"), "https://www.zhaopin.com/jobdetail/CC000544460J40844274616.htm", "北京"
    )
    assert "另一个推荐职位" not in job.cleaned_text
    assert not any("另一个推荐职位" in item for item in job.responsibilities)


def test_zhaopin_masked_salary_is_reported_as_unknown_not_zero():
    """智联 shows **-**元 unless signed in; that must not score as a real range."""
    job = ZHAOPIN.parse_job_page(
        fixture("zhaopin_job.html"), "https://www.zhaopin.com/jobdetail/CC000544460J40844274616.htm", "北京"
    )
    assert job.salary_min is None and job.salary_max is None


@pytest.mark.asyncio
async def test_an_unparseable_search_page_falls_back_to_the_browser():
    """A plain fetch gets 智联's SEO shell; the parse must return [] so
    Transport.fetch escalates to the browser instead of aborting the run.

    Raising here made every real 智联 run report "needs manual input".
    """
    class ShellThenBrowser(Transport):
        async def fetch_http(self, url: str) -> str:
            return "<html><body><main>正在加载</main></body></html>"

        async def fetch_browser(self, url: str) -> str:
            return fixture("zhaopin_search.html")

    client = httpx.AsyncClient()
    transport = ShellThenBrowser(ZHAOPIN, 0, client)
    result = await transport.fetch(ZHAOPIN.search_url("Python", "北京", 1), "search")
    assert result.transport == "browser"
    assert len(ZHAOPIN.parse_search_page(result.text)) == 2
    await client.aclose()


def test_an_empty_search_page_returns_nothing_rather_than_raising():
    assert ZHAOPIN.parse_search_page("<html><body>nothing here</body></html>") == []
    assert JOB51.parse_search_page("<html><body>nothing here</body></html>") == []


def test_section_headings_are_not_treated_as_job_content():
    """智联 interleaves labels like "职位概述：" into the description block."""
    from career_radar.sites.base import drop_headings, is_heading

    # 智联 emits these as bare text nodes, not just colon-terminated labels.
    assert is_heading("职位概述：") and is_heading("岗位职责:")
    assert is_heading("岗位职责") and is_heading("【岗位定位】")
    assert not is_heading("负责后端服务的开发与维护")
    assert not is_heading("1、负责 AWS 与 Kubernetes 集群的日常运维")
    assert drop_headings(["岗位职责", "1、负责开发后端服务。"]) == ["1、负责开发后端服务。"]


def test_zhaopin_city_codes_are_the_verified_ones():
    assert ZHAOPIN.cities["北京"] == "530"
    assert ZHAOPIN.cities["苏州"] == "639"
    assert ZHAOPIN.search_url("Python", "北京", 2).startswith("https://sou.zhaopin.com/?jl=530")
    with pytest.raises(Exception):
        ZHAOPIN.search_url("Python", "不存在的城市", 1)


# --------------------------------------------------------------- 前程无忧

def test_job51_search_reads_the_metadata_attribute():
    links = JOB51.parse_search_page(fixture("job51_search.html"))
    assert len(links) == 2
    assert all(link.startswith("https://jobs.51job.com/") for link in links)
    assert all(link.endswith(".html") for link in links)
    assert links[0].split("/")[-1].split(".")[0].isdigit()


def test_job51_detail_rule_matches_pathnames_including_district_slugs():
    assert re.fullmatch(JOB51.job_path_pattern, "/guangzhou/173657951.html")
    assert re.fullmatch(JOB51.job_path_pattern, "/guangzhou-thq/173657296.html")
    assert not re.fullmatch(JOB51.job_path_pattern, "/pc/search")


def test_job51_real_card_shape_can_queue_twenty_distinct_results():
    """A rendered result page exposes each posting through sensorsdata."""
    import json

    cards = []
    for index in range(20):
        metadata = json.dumps({
            "jobId": str(173650000 + index),
            "jobTitle": f"数据工程师 {index}",
            "jobArea": "广州·天河区" if index % 2 else "广州",
        }, ensure_ascii=False)
        cards.append(f"<div class='joblist-item-job' sensorsdata='{metadata}'></div>")
    links = JOB51.parse_search_page("<main>" + "".join(cards) + "</main>")
    assert len(links) == 20
    assert len(set(links)) == 20
    assert all(re.fullmatch(JOB51.job_path_pattern, urlsplit(link).path) for link in links)


def test_job51_detail_page_without_a_description_raises_rather_than_inventing_one():
    """Its detail selectors are unverified, so it must stop, not guess."""
    from career_radar.sites.base import CrawlError

    with pytest.raises(CrawlError, match="未识别到独立的职位描述区域"):
        JOB51.parse_job_page(
            "<html><body><h1>Python 工程师</h1><nav>首页 搜索 投递</nav></body></html>",
            "https://jobs.51job.com/beijing/171755299.html",
        )


def test_job51_city_codes_are_the_verified_ones():
    assert JOB51.cities["北京"] == "010000"
    assert JOB51.cities["苏州"] == "070300"


# ------------------------------------------------------------ 薪资归一化

@pytest.mark.parametrize("text,expected", [
    ("20-35K", (20000, 35000)),            # BOSS
    ("20-35K·14薪", (20000, 35000)),
    ("15-25万/年", (12500, 20833)),         # 51job: yearly 万 -> monthly yuan
    ("8000-12000元/月", (8000, 12000)),     # 51job: already monthly yuan
    ("1.5-2万", (15000, 20000)),           # 智联 / 51job sensorsdata
    ("30万", (300000, 300000)),            # bare 万 is ten-thousands, not thousands
    ("面议", (None, None)),
    ("**-**元", (None, None)),             # 智联's masked salary
    (None, (None, None)),
])
def test_salary_normalisation(text, expected):
    assert parse_salary(text) == expected


def test_salary_units_are_comparable_across_sites():
    """The whole point: a 51job yearly figure and a BOSS monthly one compare."""
    desired = parse_salary("25-40K")
    boss = parse_salary("20-35K")            # 20000-35000 yuan/month
    job51 = parse_salary("15-25万/年")        # 12500-20833 yuan/month
    assert (desired, boss, job51) == ((25000, 40000), (20000, 35000), (12500, 20833))

    def overlaps(a, b):
        return max(a[0], b[0]) <= min(a[1], b[1])

    # Same unit on both sides, so these are genuinely comparable: 25-40K clears
    # the BOSS posting but sits entirely above the 51job one.
    assert overlaps(desired, boss)
    assert not overlaps(desired, job51)


def test_build_snapshot_populates_normalised_salary_and_site():
    snapshot = build_snapshot(
        site="zhaopin", canonical_url="https://www.zhaopin.com/jobdetail/X.htm",
        platform_job_id="X", title="后端", company="某公司", salary="15-25万/年",
        responsibilities=["负责后端开发与维护，具备三年以上经验"],
    )
    assert snapshot.site == "zhaopin"
    assert (snapshot.salary_min, snapshot.salary_max) == (12500, 20833)
    assert snapshot.blocks and snapshot.blocks[0].source_type == "job"
