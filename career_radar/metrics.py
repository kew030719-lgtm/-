from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any


def failure_category(message: str, status: str) -> str | None:
    value = (message or "").lower()
    if status == "SUCCEEDED":
        return None
    if any(token in value for token in ("验证码", "滑块", "验证", "登录")):
        return "manual_verification"
    if any(token in value for token in ("网络", "连接", "超时", "http")):
        return "network"
    if any(token in value for token in ("结构", "解析", "没有有效岗位", "未加载")):
        return "page_structure"
    if "模型" in value:
        return "model_degraded"
    return "unknown" if status in {"FAILED", "FAILED_VALIDATION", "NEEDS_MANUAL_INPUT"} else None


def _percentile(values: list[int], percent: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * percent) - 1)]


def present_metric(metric: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in metric.items() if key != "page_durations_ms"}
    durations = [int(value) for value in metric.get("page_durations_ms", [])]
    result["p50_page_ms"] = _percentile(durations, 0.50)
    result["p95_page_ms"] = _percentile(durations, 0.95)
    try:
        start = datetime.fromisoformat(metric["started_at"])
        end = datetime.fromisoformat(metric["finished_at"]) if metric.get("finished_at") else datetime.now(start.tzinfo)
        elapsed_ms = max(0, round((end - start).total_seconds() * 1000))
    except (KeyError, TypeError, ValueError):
        elapsed_ms = 0
    result["elapsed_ms"] = elapsed_ms
    minutes = elapsed_ms / 60_000
    result["jobs_per_minute"] = round(int(metric.get("valid_jobs", 0)) / minutes, 2) if minutes else 0
    return result
