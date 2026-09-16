#!/usr/bin/env python3
"""Generate aggregate JSON and Markdown evaluation reports from a local database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))]


def summarize_run(metric: dict) -> dict:
    durations = [float(value) for value in metric.get("page_durations_ms", [])]
    return {
        key: value
        for key, value in {
            **metric,
            "p50_page_ms": percentile(durations, 0.50),
            "p95_page_ms": percentile(durations, 0.95),
        }.items()
        if key != "page_durations_ms"
    }


def build_report(database_path: Path, manifest_path: Path | None = None) -> dict:
    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    has_metrics = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_metrics'"
    ).fetchone()
    metrics = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM collection_metrics")] if has_metrics else []
    snapshots = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM job_snapshots")]
    profiles = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM profiles")]
    comparisons = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM comparisons")]
    db.close()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path else {}
    sites = Counter(snapshot.get("site") or "boss" for snapshot in snapshots)
    completeness = {
        field: round(100 * sum(bool(item.get(field)) for item in snapshots) / len(snapshots), 2) if snapshots else 0
        for field in ("title", "company", "canonical_url", "responsibilities")
    }
    all_rankings = [ranking for comparison in comparisons for ranking in comparison.get("rankings", [])]
    ranked_ids = [item.get("job_id") for item in all_rankings]
    cited_rankings = sum(bool(item.get("citations")) for item in all_rankings)
    recommendations = [item for profile in profiles for item in profile.get("recommendations", [])]
    cited_recommendations = sum(bool(item.get("citations")) for item in recommendations)
    human = manifest.get("top5_relevance", [])
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "resume_count": len(manifest.get("resumes", [])),
            "snapshot_count": len(snapshots),
            "snapshots_by_site": dict(sites),
        },
        "collection_runs": [summarize_run(metric) for metric in metrics],
        "quality": {
            "completeness_percent": completeness,
            "duplicate_rankings": len(ranked_ids) - len(set(ranked_ids)),
            "ranking_citation_coverage_percent": round(100 * cited_rankings / len(all_rankings), 2) if all_rankings else None,
            "recommendation_citation_coverage_percent": round(100 * cited_recommendations / len(recommendations), 2) if recommendations else None,
            "top5_human_relevance_percent": round(100 * sum(bool(item.get("relevant")) for item in human) / len(human), 2) if human else None,
        },
        "release_gates": {
            "ten_resumes": len(manifest.get("resumes", [])) >= 10,
            "fifty_pages_each_site": all(sites.get(site, 0) >= 50 for site in ("boss", "zhaopin", "job51")),
            "required_fields": all(completeness[field] == 100 for field in ("title", "company", "canonical_url")),
            "responsibilities_95": completeness["responsibilities"] >= 95,
            "no_duplicate_rankings": len(ranked_ids) == len(set(ranked_ids)),
            "citation_coverage_100": all(value == 100 for value in (
                round(100 * cited_rankings / len(all_rankings), 2) if all_rankings else 0,
                round(100 * cited_recommendations / len(recommendations), 2) if recommendations else 0,
            )),
            "top5_relevance_80": bool(human) and 100 * sum(bool(item.get("relevant")) for item in human) / len(human) >= 80,
        },
    }
    return report


def markdown(report: dict) -> str:
    dataset, quality, gates = report["dataset"], report["quality"], report["release_gates"]
    lines = [
        "# CareerRadar 评测报告", "", f"生成时间：{report['generated_at']}", "",
        "## 数据集", "", f"- 脱敏简历：{dataset['resume_count']} 份",
        f"- 岗位快照：{dataset['snapshot_count']} 条（{dataset['snapshots_by_site']}）", "",
        "## 质量", "",
        f"- 字段完整率：{quality['completeness_percent']}",
        f"- 排名重复数：{quality['duplicate_rankings']}",
        f"- 排名引用覆盖率：{quality['ranking_citation_coverage_percent']}",
        f"- 推荐引用覆盖率：{quality['recommendation_citation_coverage_percent']}",
        f"- Top 5 人工相关性：{quality['top5_human_relevance_percent']}", "", "## 采集运行", "",
    ]
    if report["collection_runs"]:
        for run in report["collection_runs"]:
            lines.append(
                f"- {run.get('run_id')} · {run.get('status')} · 有效 {run.get('valid_jobs', 0)} · "
                f"重复 {run.get('duplicates', 0)} · P50/P95 "
                f"{run.get('p50_page_ms')}/{run.get('p95_page_ms')} ms · "
                f"失败类别 {run.get('failure_category')}"
            )
    else:
        lines.append("- 旧数据库中没有持久化采集指标；不能反推为本轮成功。")
    lines.extend(["", "## 发布门槛", ""])
    lines.extend(f"- {'通过' if passed else '未通过'}：{name}" for name, passed in gates.items())
    lines.extend(["", "未通过表示尚缺真实证据，不会被自动标记为成功。", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/career_radar.db"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation"))
    args = parser.parse_args()
    report = build_report(args.database, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")


if __name__ == "__main__":
    main()
