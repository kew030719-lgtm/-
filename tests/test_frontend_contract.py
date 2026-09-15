"""Contracts between the React SPA and the FastAPI route table.

The SPA addresses the backend with URL string literals, which TypeScript cannot
typecheck. This suite closes that gap the way the old ui_chat.test.cjs did for
the server-rendered page, but against the real route table instead of by
grepping templates for markers.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import pytest

from career_radar.config import Settings
from career_radar.web import create_app

ROOT = Path(__file__).parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
SPA_INDEX = ROOT / "career_radar" / "static" / "app" / "index.html"

# An "/api/..." literal opened by a quote or backtick (template literals).
API_LITERAL = re.compile(r"""["'`](/api/[^"'`]*)""")
# {task_id} in a FastAPI route.
FASTAPI_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _strip_template_expressions(path: str) -> str:
    """Replace every ``${...}`` with ``{}``, matching braces by depth.

    A naive regex is not enough: an expression can contain braces of its own,
    e.g. ``${(task.payload as { profile_id: string }).profile_id}``.
    """
    out: list[str] = []
    index = 0
    while index < len(path):
        if path.startswith("${", index):
            depth = 0
            cursor = index + 1
            while cursor < len(path):
                if path[cursor] == "{":
                    depth += 1
                elif path[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            out.append("{}")
            index = cursor + 1
        else:
            out.append(path[index])
            index += 1
    return "".join(out)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path, database_path=tmp_path / "test.db",
        model_base_url="https://example.invalid/v1", model_name="test", api_key="",
        crawl_delay_seconds=0, crawl_max_jobs=3,
    )


def _normalise(path: str) -> str:
    without_query = path.split("?")[0]
    stripped = _strip_template_expressions(without_query)
    return FASTAPI_PLACEHOLDER.sub("{}", stripped).rstrip("/") or "/"


def _frontend_api_paths() -> set[str]:
    paths: set[str] = set()
    for file in sorted(FRONTEND_SRC.rglob("*")):
        if file.suffix not in {".ts", ".tsx"}:
            continue
        for match in API_LITERAL.finditer(file.read_text(encoding="utf-8")):
            paths.add(_normalise(match.group(1)))
    return paths


def _route_paths(app) -> set[str]:
    return {_normalise(getattr(route, "path", "")) for route in app.routes}


def test_the_scan_finds_the_spa_endpoints():
    """Guards the contract test below from passing vacuously."""
    paths = _frontend_api_paths()
    assert len(paths) > 20, f"only found {len(paths)} paths: {sorted(paths)}"
    assert "/api/comparisons" in paths
    assert "/api/tasks/{}" in paths


def test_every_api_path_the_spa_calls_exists(tmp_path):
    routes = _route_paths(create_app(settings(tmp_path)))
    missing = sorted(path for path in _frontend_api_paths() if path not in routes)
    assert not missing, "the SPA calls endpoints the backend does not define: " + ", ".join(missing)


def test_chat_action_endpoints_are_reachable_from_the_spa(tmp_path):
    """The confirmation-card flow is the one path where a typo breaks silently."""
    routes = _route_paths(create_app(settings(tmp_path)))
    assert "/api/chat-actions/{}/confirm" in routes
    assert "/api/chat-actions/{}/reject" in routes


def test_sites_endpoint_lists_the_registry_with_cities(tmp_path):
    """The SPA renders its city picker from this, so every site must be usable."""
    async def run():
        app = create_app(settings(tmp_path))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/sites")

    response = asyncio.run(run())
    assert response.status_code == 200
    body = response.json()
    keys = [item["key"] for item in body]
    assert "boss" in keys
    # Deriving the list from the registry is what makes a new adapter show up
    # without touching web.py; a silently empty list would break the city picker.
    assert len(keys) == len(set(keys)) and len(keys) >= 1
    for item in body:
        assert item["label"] and item["cities"] and item["enabled"] is True
    boss = next(item for item in body if item["key"] == "boss")
    assert "北京" in boss["cities"]


def test_built_spa_is_mounted_when_present(tmp_path):
    if not SPA_INDEX.is_file():
        pytest.skip("frontend not built; run `npm run build` in frontend/")
    app = create_app(settings(tmp_path))
    mounts = {getattr(route, "path", "") for route in app.routes}
    assert "/app" in mounts


def test_task_read_exposes_navigation_rules_even_for_older_runs(tmp_path):
    """The helper adopts rules from here, so a pre-multi-site run still works."""
    async def run():
        app = create_app(settings(tmp_path))
        app.state.database.initialize()
        # A run stored by an older version: sites, but no allow map.
        app.state.database.create_task("task_old", "discovery", {"profile_id": "p", "sites": ["zhaopin"]})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/tasks/task_old")

    body = asyncio.run(run()).json()
    allow = body["payload"]["allow"]
    assert set(allow) == {"zhaopin"}
    assert allow["zhaopin"]["hosts"] and allow["zhaopin"]["path_pattern"]


def test_legacy_page_still_serves_until_the_flip(tmp_path):
    """Phase 1 keeps / working; only the final flip removes it."""

    async def run():
        app = create_app(settings(tmp_path))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert "CareerRadar" in response.text