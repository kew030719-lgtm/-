"""In-process agent tools.

These were previously served over stdio JSON-RPC by a bundled MCP server, which
cost one subprocess spawn per ``tools/list`` and per ``tools/call``. The
security checks below (contact scrubbing, turn binding) are business rules, not
transport concerns, so they moved with the tools.

External MCP servers can still be attached to the agent as a client; this module
is only about the tools CareerRadar owns itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Union
from uuid import uuid4

from pydantic_ai import RunContext


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool needs to know about the run it belongs to.

    Field names mirror the payload the former MCP server received through
    ``CAREER_RADAR_*`` environment variables.
    """

    database_path: Path
    tool_mode: str = "analysis"
    conversation_id: str = ""
    message_id: str = ""
    profile_id: str = ""
    target_job_id: str = ""
    tailoring_id: str = ""


AnyContext = Union[RunContext[ToolContext], ToolContext]


def _deps(context: AnyContext) -> ToolContext:
    """Unwrap the context.

    PydanticAI injects a ``RunContext``, which carries ~20 required fields that
    make it impractical to build in tests. The tools therefore also accept a
    bare ``ToolContext`` so the security rules below stay unit-testable.
    """
    return context if isinstance(context, ToolContext) else context.deps


def _connect(context: AnyContext) -> sqlite3.Connection:
    db = sqlite3.connect(_deps(context).database_path)
    db.row_factory = sqlite3.Row
    return db


def _profile(context: AnyContext, profile_id: str) -> dict[str, Any]:
    with _connect(context) as db:
        row = db.execute("SELECT payload FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise ValueError("profile not found")
    return json.loads(row["payload"])


def _public_profile(context: AnyContext, profile_id: str) -> dict[str, Any]:
    """Strip contact details before a profile reaches the model.

    ``build_profile`` already drops contact fields at ingest time; this is the
    second, independent barrier, because supplemental and user-edited evidence
    is appended to ``profile.evidence`` after ingest.
    """
    value = _profile(context, profile_id)
    with _connect(context) as db:
        row = db.execute("SELECT payload FROM candidate_contacts WHERE profile_id=?", (profile_id,)).fetchone()
    contact = json.loads(row["payload"]) if row else {}
    private_values = {str(contact.get(key, "")).strip() for key in ("name", "phone", "email", "location")}
    private_values.discard("")
    phone = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    evidence = []
    noisy_phrases = ("温馨提示", "简历模板", "虚构示例", "请根据实际情况", "仅供参考")
    section_labels = {"基本信息", "个人信息", "个人简历", "求职简历", "简历", "联系方式"}
    for item in value.get("evidence", []):
        quote = str(item.get("quote", ""))
        if quote.strip(" ：:") in section_labels or any(phrase in quote for phrase in noisy_phrases):
            continue
        if quote.strip() in private_values:
            continue
        for private in private_values:
            quote = quote.replace(private, "")
        quote = email.sub("", phone.sub("", quote))
        quote = re.sub(r"(?:电话|手机|邮箱|电子邮箱|现居|所在地|地址|城市)\s*[:：]?", "", quote).strip(" |｜·，,；;")
        if quote:
            evidence.append({**item, "quote": quote})
    return {**value, "evidence": evidence}


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


def _conversation(context: AnyContext, conversation_id: str) -> sqlite3.Row:
    deps = _deps(context)
    # The propose tools accept conversation_id as a *model* argument, so an empty
    # binding must not be read as "any conversation is fine".
    if deps.tool_mode == "chat" and not deps.conversation_id:
        raise ValueError("chat tools require an active conversation")
    if deps.conversation_id and conversation_id != deps.conversation_id:
        raise ValueError("conversation does not match the active chat turn")
    with _connect(context) as db:
        row = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if not row:
        raise ValueError("conversation not found")
    return row


def _assert_chat_context(context: AnyContext, field: str, value: str) -> None:
    deps = _deps(context)
    if deps.tool_mode != "chat" or not deps.conversation_id:
        return
    conversation = _conversation(context, deps.conversation_id)
    if conversation[field] != value:
        raise ValueError(f"{field} does not match the active conversation")


def _assert_profile_context(context: AnyContext, profile_id: str) -> None:
    deps = _deps(context)
    if deps.profile_id and profile_id != deps.profile_id:
        raise ValueError("profile does not match the active operation")


def _create_action(context: AnyContext, conversation_id: str, kind: str, user_intent: str,
                   changes: dict[str, Any] | None = None, *, early_finish: bool = False) -> dict[str, Any]:
    conversation = _conversation(context, conversation_id)
    profile = _profile(context, conversation["profile_id"]) if conversation["profile_id"] else None
    if not profile:
        raise ValueError("submit a resume before changing personalized settings")
    allowed = {"selected_roles", "cities", "salary_preference", "experience_years", "work_type_preference"}
    changes = changes or {}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"unsupported profile fields: {', '.join(sorted(unknown))}")
    before = {key: profile.get(key) for key in changes}
    arguments = {
        "user_intent": str(user_intent)[:500], "changes": changes,
        "profile_id": conversation["profile_id"], "run_id": conversation["run_id"],
        "early_finish": bool(early_finish),
    }
    run = None
    if conversation["run_id"]:
        with _connect(context) as db:
            run = db.execute("SELECT status FROM tasks WHERE id=?", (conversation["run_id"],)).fetchone()
            count = db.execute("SELECT COUNT(*) AS n FROM job_snapshots WHERE run_id=?", (conversation["run_id"],)).fetchone()["n"]
    else:
        count = 0
    preview = {
        "user_intent": str(user_intent)[:500], "before": before, "after": changes,
        "current_run_id": conversation["run_id"], "current_run_status": run["status"] if run else None,
        "current_job_count": count,
    }
    action_id = f"action_{uuid4().hex[:12]}"
    stamp = datetime.now(UTC).isoformat()
    with _connect(context) as db:
        db.execute(
            "INSERT INTO chat_actions(id,conversation_id,source_message_id,kind,arguments,preview,status,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (action_id, conversation_id, _deps(context).message_id or None, kind,
             json.dumps(arguments, ensure_ascii=False), json.dumps(preview, ensure_ascii=False),
             "PENDING", "{}", stamp, stamp),
        )
    return {"action_id": action_id, "status": "PENDING", "preview": preview,
            "message": "A confirmation card was created. Tell the user to review it."}


def _create_tailoring_action(context: AnyContext, conversation_id: str, user_intent: str,
                             snapshot_id: str) -> dict[str, Any]:
    conversation = _conversation(context, conversation_id)
    if not conversation["profile_id"] or not conversation["run_id"]:
        raise ValueError("the active conversation has no resume and discovery run")
    with _connect(context) as db:
        row = db.execute(
            "SELECT payload FROM job_snapshots WHERE id=? AND run_id=?",
            (snapshot_id, conversation["run_id"]),
        ).fetchone()
    if not row:
        raise ValueError("job snapshot does not match the active conversation")
    job = json.loads(row["payload"])
    action_id = f"action_{uuid4().hex[:12]}"
    stamp = datetime.now(UTC).isoformat()
    arguments = {
        "user_intent": str(user_intent)[:500], "profile_id": conversation["profile_id"],
        "run_id": conversation["run_id"], "snapshot_id": snapshot_id,
    }
    preview = {
        "user_intent": str(user_intent)[:500], "company": job.get("company", ""),
        "title": job.get("title", ""), "snapshot_id": snapshot_id,
        "jd_source": "当前岗位快照", "next_step": "对照岗位要求提出最多 5 个事实问题",
    }
    with _connect(context) as db:
        db.execute(
            "INSERT INTO chat_actions(id,conversation_id,source_message_id,kind,arguments,preview,status,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (action_id, conversation_id, _deps(context).message_id or None, "START_RESUME_TAILORING",
             json.dumps(arguments, ensure_ascii=False), json.dumps(preview, ensure_ascii=False),
             "PENDING", "{}", stamp, stamp),
        )
    return {"action_id": action_id, "status": "PENDING", "preview": preview,
            "message": "A confirmation card was created. Tell the user to review it."}


# ---------------------------------------------------------------- read tools

def read_candidate_profile(ctx: RunContext[ToolContext], profile_id: str) -> dict[str, Any]:
    """Read one confirmed candidate profile, with contact details removed."""
    _assert_chat_context(ctx, "profile_id", profile_id)
    _assert_profile_context(ctx, profile_id)
    value = _public_profile(ctx, profile_id)
    if not value.get("confirmed"):
        raise ValueError("profile is not confirmed")
    return value


def search_resume_evidence(ctx: RunContext[ToolContext], profile_id: str, query: str) -> list[dict[str, Any]]:
    """Search exact resume evidence blocks by substring match."""
    _assert_chat_context(ctx, "profile_id", profile_id)
    _assert_profile_context(ctx, profile_id)
    items = _public_profile(ctx, profile_id).get("evidence", [])
    query = query.lower()
    limit = 8 if _deps(ctx).tool_mode == "chat" else 20
    return [item for item in items if query in item.get("quote", "").lower()][:limit]


def list_job_snapshots(ctx: RunContext[ToolContext], run_id: str) -> list[dict[str, Any]]:
    """List the latest job snapshots collected for a discovery run."""
    _assert_chat_context(ctx, "run_id", run_id)
    with _connect(ctx) as db:
        rows = db.execute("SELECT payload FROM job_snapshots WHERE run_id=? ORDER BY fetched_at DESC", (run_id,)).fetchall()
    values = [json.loads(row["payload"]) for row in rows]
    if _deps(ctx).tool_mode == "chat":
        keep = {"snapshot_id", "title", "company", "city", "salary", "experience", "education",
                "required_skills", "bonus_skills", "work_type", "status", "canonical_url"}
        return [{key: value for key, value in item.items() if key in keep} for item in values]
    return values


def search_job_evidence(ctx: RunContext[ToolContext], snapshot_id: str, query: str) -> list[dict[str, Any]]:
    """Search exact evidence blocks from one job snapshot by substring match."""
    deps = _deps(ctx)
    if deps.tool_mode == "chat" and deps.conversation_id:
        conversation = _conversation(ctx, deps.conversation_id)
        with _connect(ctx) as db:
            belongs = db.execute(
                "SELECT 1 FROM job_snapshots WHERE id=? AND run_id=?",
                (snapshot_id, conversation["run_id"]),
            ).fetchone()
        if not belongs:
            raise ValueError("snapshot does not match the active conversation")
    with _connect(ctx) as db:
        row = db.execute("SELECT payload FROM job_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        raise ValueError("snapshot not found")
    query = query.lower()
    limit = 8 if deps.tool_mode == "chat" else 20
    return [item for item in json.loads(row["payload"]).get("blocks", []) if query in item.get("quote", "").lower()][:limit]


def read_job_comparison(ctx: RunContext[ToolContext], comparison_id: str) -> dict[str, Any]:
    """Read a deterministic CareerRadar comparison report."""
    _assert_chat_context(ctx, "comparison_id", comparison_id)
    with _connect(ctx) as db:
        row = db.execute("SELECT payload FROM comparisons WHERE id=?", (comparison_id,)).fetchone()
    if not row:
        raise ValueError("comparison not found")
    return json.loads(row["payload"])


# ------------------------------------------------------------ analysis tools

def save_role_recommendations(ctx: RunContext[ToolContext], profile_id: str,
                              recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    """Save exactly three evidence-backed role recommendations onto a profile."""
    profile = _profile(ctx, profile_id)
    if len(recommendations) != 3:
        raise ValueError("exactly three role recommendations are required")
    index = _citation_index(profile)
    for recommendation in recommendations:
        citations = recommendation.get("citations", [])
        if not citations:
            raise ValueError("each recommendation requires evidence")
        _validate(citations, index)
    profile["recommendations"] = recommendations
    with _connect(ctx) as db:
        db.execute("UPDATE profiles SET payload=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), profile_id))
    return {"saved": True}


def save_job_comparison(ctx: RunContext[ToolContext], comparison: dict[str, Any]) -> dict[str, Any]:
    """Save a completed structured comparison payload after citation validation."""
    profile = _profile(ctx, comparison["profile_id"])
    with _connect(ctx) as db:
        rows = db.execute("SELECT payload FROM job_snapshots").fetchall()
    jobs = [json.loads(row["payload"]) for row in rows]
    index = _citation_index(profile, jobs)
    citations = [citation for ranking in comparison.get("rankings", []) for citation in ranking.get("citations", [])]
    _validate(citations, index)
    with _connect(ctx) as db:
        db.execute(
            "INSERT OR REPLACE INTO comparisons(id,profile_id,task_id,payload,created_at) VALUES(?,?,?,?,?)",
            (comparison["comparison_id"], comparison["profile_id"],
             f"agent:{comparison['comparison_id']}", json.dumps(comparison, ensure_ascii=False),
             comparison["created_at"]),
        )
    return {"accepted": True, "comparison_id": comparison["comparison_id"]}


# ---------------------------------------------------------------- chat tools

def propose_profile_update(ctx: RunContext[ToolContext], conversation_id: str, user_intent: str,
                           changes: dict[str, Any]) -> dict[str, Any]:
    """Create a confirmation card for profile changes. This never applies the changes."""
    return _create_action(ctx, conversation_id, "UPDATE_PROFILE", user_intent, changes)


def propose_discovery_restart(ctx: RunContext[ToolContext], conversation_id: str, user_intent: str,
                              changes: dict[str, Any]) -> dict[str, Any]:
    """Create a confirmation card to change filters, stop the current discovery and start a new browser discovery. This never executes the action."""
    return _create_action(ctx, conversation_id, "RESTART_DISCOVERY", user_intent, changes)


def propose_comparison(ctx: RunContext[ToolContext], conversation_id: str, user_intent: str,
                       early_finish: bool = False) -> dict[str, Any]:
    """Create a confirmation card to analyze jobs in the current discovery run. This never starts analysis."""
    return _create_action(ctx, conversation_id, "START_COMPARISON", user_intent,
                          early_finish=bool(early_finish))


def propose_resume_tailoring(ctx: RunContext[ToolContext], conversation_id: str, user_intent: str,
                             snapshot_id: str) -> dict[str, Any]:
    """Create a confirmation card to tailor the resume for one job snapshot. This never changes or generates a resume."""
    return _create_tailoring_action(ctx, conversation_id, user_intent, snapshot_id)


# ----------------------------------------------------------- tailoring tools

def read_tailoring_context(ctx: RunContext[ToolContext], tailoring_id: str) -> dict[str, Any]:
    """Read the active resume tailoring, public resume evidence, and target job without private contact data."""
    deps = _deps(ctx)
    if deps.tailoring_id and tailoring_id != deps.tailoring_id:
        raise ValueError("tailoring does not match the active operation")
    with _connect(ctx) as db:
        row = db.execute("SELECT payload FROM resume_tailorings WHERE id=?", (tailoring_id,)).fetchone()
    if not row:
        raise ValueError("tailoring not found")
    tailoring = json.loads(row["payload"])
    _assert_profile_context(ctx, tailoring["profile_id"])
    if deps.target_job_id and tailoring["target_job_id"] != deps.target_job_id:
        raise ValueError("target job does not match the active operation")
    with _connect(ctx) as db:
        target_row = db.execute("SELECT payload FROM target_jobs WHERE id=?", (tailoring["target_job_id"],)).fetchone()
    if not target_row:
        raise ValueError("target job not found")
    target = json.loads(target_row["payload"])
    profile = _public_profile(ctx, tailoring["profile_id"])
    terms = [str(item).lower() for item in target.get("required_skills", [])]
    terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", " ".join(target.get("responsibilities", []))))
    ranked_evidence = sorted(
        profile.get("evidence", []),
        key=lambda item: (
            sum(1 for term in terms if term and term in str(item.get("quote", "")).lower()),
            str(item.get("section", "")) in {"项目", "经历", "用户补充"},
        ), reverse=True,
    )[:36]
    public_profile = {
        key: profile.get(key) for key in
        ("profile_id", "skills", "experience_years", "education", "confirmed", "resume_entries")
    }
    public_profile["evidence"] = ranked_evidence
    public_target = {
        "target_job_id": target.get("target_job_id"), "company": target.get("company"),
        "title": target.get("title"), "source_type": target.get("source_type"),
        "responsibilities": target.get("responsibilities", [])[:15],
        "required_skills": target.get("required_skills", [])[:15],
    }
    public_tailoring = {
        "tailoring_id": tailoring.get("tailoring_id"), "profile_id": tailoring.get("profile_id"),
        "target_job_id": tailoring.get("target_job_id"), "missing_requirements": tailoring.get("missing_requirements", []),
        "questions": tailoring.get("questions", []),
    }
    return {"tailoring": public_tailoring, "profile": public_profile, "target_job": public_target}


READ_TOOLS = (read_candidate_profile, search_resume_evidence, list_job_snapshots,
              search_job_evidence, read_job_comparison)
ANALYSIS_TOOLS = (save_role_recommendations, save_job_comparison)
CHAT_TOOLS = (propose_profile_update, propose_discovery_restart, propose_comparison,
              propose_resume_tailoring)
TAILORING_TOOLS = (read_tailoring_context,)


def tools_for(tool_mode: str) -> list[Callable[..., Any]]:
    """Tools exposed for a run, replacing the old string allow-list."""
    if tool_mode == "chat":
        return [*READ_TOOLS, *CHAT_TOOLS]
    if tool_mode == "tailoring":
        return [*TAILORING_TOOLS]
    return [*READ_TOOLS, *ANALYSIS_TOOLS]
