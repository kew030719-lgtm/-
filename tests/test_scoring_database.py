from datetime import UTC, datetime
from pathlib import Path

from career_radar.database import Database
from career_radar.resume import build_profile
from career_radar.schemas import Evidence, JobSnapshot
from career_radar.scoring import normalize_skill, score_job


TEXT = """技能
Python FastAPI SQL Docker
项目经历
使用 FastAPI 开发异步 API，接入 SQL 和 Docker 部署。
工作经历
2 年 Python 后端开发经验
教育经历
本科
"""


def job(snapshot_id="snap_one", salary="20-30K", body="负责异步 API 开发"):
    evidence = Evidence(source_type="job", source_id=snapshot_id, block_id=f"job-{snapshot_id}", quote=body)
    return JobSnapshot(
        snapshot_id=snapshot_id, platform_job_id="abc", canonical_url="https://www.zhipin.com/job_detail/abc.html",
        title="Python 后端", company="星图", city="北京", salary=salary, experience="3-5年", education="本科",
        responsibilities=[body], required_skills=["Python Web", "Redis", "Docker"], content_hash=f"hash-{snapshot_id}",
        fetched_at=datetime.now(UTC).isoformat(), transport="http", blocks=[evidence],
    )


def test_synonyms_missing_fields_and_hard_conflicts():
    assert normalize_skill("FastAPI") == normalize_skill("Python Web")
    profile = build_profile(TEXT, "profile_score")
    profile.cities = ["上海"]
    result = score_job(profile, job())
    assert "python后端" in result.matched_skills
    assert "redis" in result.missing_skills
    assert result.education == 100
    assert result.experience < 100
    assert len(result.risks) == 2


def test_unknown_components_are_not_zero():
    profile = build_profile(TEXT, "profile_score")
    blank = job()
    blank.experience = ""
    blank.education = ""
    blank.city = ""
    result = score_job(profile, blank)
    assert result.experience is None and result.education is None and result.preference is None
    assert result.total > 0


def test_database_dedupes_and_keeps_changed_snapshots(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    first = job("snap_one", "20-30K")
    second = job("snap_two", "25-35K")
    database.save_snapshot("boss:abc", "run_one", first)
    database.save_snapshot("url:different", "run_two", second)
    latest = database.list_snapshots()
    assert len(latest) == 1
    assert latest[0].salary == "25-35K"
    assert "salary" in latest[0].changed_fields
