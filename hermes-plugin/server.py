from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


DB_PATH = Path(os.environ.get("CAREER_RADAR_DB", "career_radar.db"))


TOOLS = [
    {"name": "read_candidate_profile", "description": "Read one confirmed candidate profile.", "inputSchema": {"type": "object", "properties": {"profile_id": {"type": "string"}}, "required": ["profile_id"]}},
    {"name": "search_resume_evidence", "description": "Search exact resume evidence blocks.", "inputSchema": {"type": "object", "properties": {"profile_id": {"type": "string"}, "query": {"type": "string"}}, "required": ["profile_id", "query"]}},
    {"name": "list_job_snapshots", "description": "List latest job snapshots for a discovery run.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
    {"name": "search_job_evidence", "description": "Search exact evidence blocks from a job snapshot.", "inputSchema": {"type": "object", "properties": {"snapshot_id": {"type": "string"}, "query": {"type": "string"}}, "required": ["snapshot_id", "query"]}},
    {"name": "save_role_recommendations", "description": "Save evidence-backed role recommendations.", "inputSchema": {"type": "object", "properties": {"profile_id": {"type": "string"}, "recommendations": {"type": "array"}}, "required": ["profile_id", "recommendations"]}},
    {"name": "save_job_comparison", "description": "Save a completed structured comparison payload.", "inputSchema": {"type": "object", "properties": {"comparison": {"type": "object"}}, "required": ["comparison"]}},
]


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _profile(profile_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT payload FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise ValueError("profile not found")
    return json.loads(row["payload"])


def _citation_index(profile: dict[str, Any], jobs: list[dict[str, Any]] | None = None) -> dict[tuple[str, str, str], str]:
    index = {
        (item["source_type"], item["source_id"], item["block_id"]): item["quote"]
        for item in profile.get("evidence", [])
    }
    for job in jobs or []:
        for item in job.get("blocks", []):
            index[(item["source_type"], item["source_id"], item["block_id"])] = item["quote"]
    return index


def _validate(citations: list[dict[str, Any]], index: dict[tuple[str, str, str], str]) -> None:
    for citation in citations:
        key = (citation.get("source_type"), citation.get("source_id"), citation.get("block_id"))
        original = index.get(key)
        if original is None or citation.get("quote", "") not in original:
            raise ValueError(f"invalid evidence citation: {citation.get('block_id', '')}")


def call(name: str, args: dict[str, Any]) -> Any:
    if name == "read_candidate_profile":
        value = _profile(args["profile_id"])
        if not value.get("confirmed"):
            raise ValueError("profile is not confirmed")
        return value
    if name == "search_resume_evidence":
        items = _profile(args["profile_id"]).get("evidence", [])
        query = args["query"].lower()
        return [item for item in items if query in item.get("quote", "").lower()][:20]
    if name == "list_job_snapshots":
        with connect() as db:
            rows = db.execute("SELECT payload FROM job_snapshots WHERE run_id=? ORDER BY fetched_at DESC", (args["run_id"],)).fetchall()
        return [json.loads(row["payload"]) for row in rows]
    if name == "search_job_evidence":
        with connect() as db:
            row = db.execute("SELECT payload FROM job_snapshots WHERE id=?", (args["snapshot_id"],)).fetchone()
        if not row:
            raise ValueError("snapshot not found")
        query = args["query"].lower()
        return [item for item in json.loads(row["payload"]).get("blocks", []) if query in item.get("quote", "").lower()][:20]
    if name == "save_role_recommendations":
        profile = _profile(args["profile_id"])
        recommendations = args["recommendations"]
        if len(recommendations) != 3:
            raise ValueError("exactly three role recommendations are required")
        index = _citation_index(profile)
        for recommendation in recommendations:
            citations = recommendation.get("citations", [])
            if not citations:
                raise ValueError("each recommendation requires evidence")
            _validate(citations, index)
        profile["recommendations"] = recommendations
        with connect() as db:
            db.execute("UPDATE profiles SET payload=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), args["profile_id"]))
        return {"saved": True}
    if name == "save_job_comparison":
        comparison = args["comparison"]
        profile = _profile(comparison["profile_id"])
        with connect() as db:
            rows = db.execute("SELECT payload FROM job_snapshots").fetchall()
        jobs = [json.loads(row["payload"]) for row in rows]
        index = _citation_index(profile, jobs)
        citations = [citation for ranking in comparison.get("rankings", []) for citation in ranking.get("citations", [])]
        _validate(citations, index)
        with connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO comparisons(id,profile_id,task_id,payload,created_at) VALUES(?,?,?,?,?)",
                (comparison["comparison_id"], comparison["profile_id"],
                 f"plugin:{comparison['comparison_id']}", json.dumps(comparison, ensure_ascii=False),
                 comparison["created_at"]),
            )
        return {"accepted": True, "comparison_id": comparison["comparison_id"]}
    raise ValueError("unknown tool")


def respond(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "career-radar", "version": "0.1.0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            value = call(request["params"]["name"], request["params"].get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
        except Exception as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    for line in sys.stdin:
        try:
            response = respond(json.loads(line))
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(exc)}}), flush=True)


if __name__ == "__main__":
    main()
