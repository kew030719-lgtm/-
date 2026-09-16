import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime

import httpx

from career_radar.config import Settings
from career_radar.graduate import extract_graduate_metadata
from career_radar.local_settings import LocalSettingsStore, MemorySecretStore
from career_radar.metrics import failure_category, present_metric
from career_radar.schemas import CandidateProfile, JobSnapshot
from career_radar.scoring import score_job
from career_radar.web import create_app


def settings(tmp_path):
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key="",
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def test_local_settings_never_write_or_return_api_key(tmp_path):
    store = LocalSettingsStore(settings(tmp_path), MemorySecretStore())
    store.update(model_base_url="https://models.example/v1", model_name="graduate-model", api_key="super-secret")
    assert store.public()["api_key_configured"] is True
    assert "super-secret" not in store.path.read_text(encoding="utf-8")
    assert "api_key" not in store.public()


def test_graduate_metadata_is_explicit_and_unknown_stays_unknown():
    unknown = extract_graduate_metadata("负责 Python 平台开发", "")
    assert unknown["recruitment_type"] == "unknown"
    assert unknown["graduation_years"] == []
    assert unknown["application_deadline"] is None
    explicit = extract_graduate_metadata(
        "2027届校园招聘 秋招 实习可转正，网申截止：2026年10月9日", "经验不限",
    )
    assert explicit["recruitment_type"] == "internship"
    assert explicit["graduation_years"] == [2027]
    assert explicit["application_deadline"] == "2026-10-09"
    assert explicit["conversion_opportunity"] is True


def test_graduate_fit_flags_year_mismatch_without_changing_core_total():
    profile = CandidateProfile(profile_id="p", expected_graduation_year=2026)
    snapshot = JobSnapshot(
        snapshot_id="s", platform_job_id="s", canonical_url="https://example.com/job/s",
        title="开发", company="公司", content_hash="h", fetched_at=datetime.now(UTC).isoformat(),
        transport="manual", graduation_years=[2027], recruitment_type="campus",
    )
    score = score_job(profile, snapshot)
    assert score.graduate_fit == 0
    assert any("毕业年份" in risk for risk in score.risks)
    assert score.total == 0


def test_metrics_present_percentiles_and_failure_categories():
    metric = {
        "run_id": "r", "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00", "valid_jobs": 3,
        "page_durations_ms": [1000, 2000, 9000],
    }
    view = present_metric(metric)
    assert view["p50_page_ms"] == 2000 and view["p95_page_ms"] == 9000
    assert view["jobs_per_minute"] == 3
    assert failure_category("请完成滑块验证", "NEEDS_MANUAL_INPUT") == "manual_verification"
    assert failure_category("页面结构无法识别", "FAILED") == "page_structure"


def test_settings_metrics_export_and_delete_api(tmp_path):
    asyncio.run(_settings_metrics_export_and_delete_api(tmp_path))


async def _settings_metrics_export_and_delete_api(tmp_path):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/browser-helper/status")).json()["connected"] is False
            assert (await client.post("/api/browser-helper/heartbeat")).status_code == 200
            assert (await client.get("/api/browser-helper/status")).json()["connected"] is True
            updated = await client.put("/api/settings/model", json={
                "model_base_url": "https://models.example/v1", "model_name": "model-x", "api_key": "secret",
            })
            assert updated.status_code == 200
            assert updated.json() == {
                "model_base_url": "https://models.example/v1", "model_name": "model-x",
                "api_key_configured": True, "data_dir": str(tmp_path),
            }
            app.state.database.create_task("run_metric", "discovery", {"sites": ["boss"]})
            app.state.database.initialize_collection_metric("run_metric", ["boss"])
            app.state.database.update_collection_metric(
                "run_metric", site="boss", pages=1, valid_jobs=1, page_duration_ms=1200, status="SUCCEEDED",
            )
            metric = (await client.get("/api/collection-metrics/run_metric")).json()
            assert metric["valid_jobs"] == 1 and metric["p50_page_ms"] == 1200
            archive_response = await client.get("/api/local-data/export")
            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["settings"]["api_key_configured"] is True
                assert b"secret" not in archive.read("manifest.json")
            refused = await client.delete("/api/local-data")
            assert refused.status_code == 409
            deleted = await client.delete("/api/local-data?confirmation=DELETE_ALL_LOCAL_DATA")
            assert deleted.status_code == 204
            assert app.state.database.get_task("run_metric") is None
            assert (await client.get("/api/settings")).json()["api_key_configured"] is False
