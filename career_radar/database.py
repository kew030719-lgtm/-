from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .schemas import (
    AgentMemory, Application, CandidateContact, CandidateProfile, ChatAction, ChatActionKind,
    ChatMessage, Comparison, Conversation, InterviewPrep, JobSnapshot, MemoryKind,
    ResumeDraftVersion, ResumeExport, ResumeTailoring, SupplementalEvidence, TargetJob, TaskStatus,
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    platform_job_id TEXT NOT NULL DEFAULT '',
                    site_id TEXT NOT NULL DEFAULT '',
                    canonical_url TEXT NOT NULL,
                    latest_snapshot_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_url ON jobs(canonical_url);
                CREATE INDEX IF NOT EXISTS jobs_platform_id ON jobs(platform_job_id);
                CREATE TABLE IF NOT EXISTS job_snapshots (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    run_id TEXT,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshots_run ON job_snapshots(run_id);
                CREATE INDEX IF NOT EXISTS snapshots_hash ON job_snapshots(content_hash);
                CREATE TABLE IF NOT EXISTS comparisons (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT,
                    run_id TEXT,
                    comparison_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    citations TEXT NOT NULL DEFAULT '[]',
                    task_id TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_messages_conversation
                    ON chat_messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS chat_actions (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    source_message_id TEXT,
                    kind TEXT NOT NULL,
                    arguments TEXT NOT NULL DEFAULT '{}',
                    preview TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_actions_conversation
                    ON chat_actions(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS candidate_contacts (
                    profile_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS target_jobs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS target_jobs_profile ON target_jobs(profile_id, created_at);
                CREATE TABLE IF NOT EXISTS supplemental_evidence (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    tailoring_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS supplemental_profile ON supplemental_evidence(profile_id, created_at);
                CREATE TABLE IF NOT EXISTS resume_tailorings (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    target_job_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tailorings_profile ON resume_tailorings(profile_id, updated_at);
                CREATE TABLE IF NOT EXISTS resume_draft_versions (
                    id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    tailoring_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(draft_id, version)
                );
                CREATE INDEX IF NOT EXISTS draft_versions_draft ON resume_draft_versions(draft_id, version);
                CREATE TABLE IF NOT EXISTS resume_exports (
                    id TEXT PRIMARY KEY,
                    version_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS exports_version ON resume_exports(version_id, created_at);
                CREATE TABLE IF NOT EXISTS interview_preps (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS interview_preps_snapshot ON interview_preps(snapshot_id, created_at);
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS applications_profile ON applications(profile_id, created_at);
                CREATE TABLE IF NOT EXISTS collection_metrics (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT '',
                    source_id TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    superseded_by TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_memories_profile
                    ON agent_memories(profile_id, kind, created_at);
                """
            )
            db.execute(
                "UPDATE tasks SET status=?, message=?, error=?, updated_at=? WHERE kind='chat' AND status=?",
                (TaskStatus.FAILED, "对话生成因服务重启中断，请重新发送", "服务重启中断", now_iso(), TaskStatus.RUNNING),
            )
            db.execute(
                "UPDATE tasks SET status=?, message=?, updated_at=? WHERE kind!='chat' AND status=?",
                (TaskStatus.QUEUED, "服务重启，可重新执行", now_iso(), TaskStatus.RUNNING),
            )
            db.execute(
                "UPDATE chat_actions SET status='FAILED', result=?, updated_at=? WHERE status='EXECUTING'",
                (json.dumps({"error": "服务重启中断，操作未自动重试"}, ensure_ascii=False), now_iso()),
            )
            # Contact details live only in candidate_contacts. Older draft rows
            # may contain a hydrated contact object from before this separation.
            for row in db.execute("SELECT id,payload FROM resume_draft_versions").fetchall():
                payload = json.loads(row["payload"])
                contact = payload.get("contact") or {}
                if any(contact.get(key) for key in ("name", "phone", "email", "location")):
                    payload["contact"] = {"profile_id": payload["profile_id"]}
                    db.execute(
                        "UPDATE resume_draft_versions SET payload=? WHERE id=?",
                        (json.dumps(payload, ensure_ascii=False), row["id"]),
                    )
            # The alternate Hermes runtime is gone, so "hermes" is no longer a valid
            # source tag. Rewrite historical rows eagerly: the schema would otherwise
            # reject them on read, turning an old report into a 500 instead of a
            # clear error.
            for table, key in (("profiles", "analysis_source"), ("comparisons", "source"),
                               ("resume_draft_versions", "source")):
                for row in db.execute(f"SELECT id,payload FROM {table}").fetchall():
                    payload = json.loads(row["payload"])
                    if payload.get(key) == "hermes":
                        payload[key] = "langgraph"
                        db.execute(
                            f"UPDATE {table} SET payload=? WHERE id=?",
                            (json.dumps(payload, ensure_ascii=False), row["id"]),
                        )
            db.execute("UPDATE chat_messages SET source='langgraph' WHERE source='hermes'")
            # Multi-site support: `CREATE TABLE IF NOT EXISTS` never migrates an
            # existing database, so the column is added explicitly.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "site_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN site_id TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE jobs SET site_id='boss' WHERE site_id=''")
            # Same reason as `jobs.site_id`: a database written before compaction
            # existed keeps its old `conversations` shape. Additive columns with
            # defaults, so existing rows stay valid and untouched.
            conversation_columns = {row["name"] for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
            if "summary" not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
            if "summary_upto_message_id" not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN summary_upto_message_id TEXT")
            # Everything stored before multi-site support came from BOSS. Filling
            # `site` also keeps job_identity stable for rows already on disk.
            for table in ("job_snapshots", "target_jobs"):
                for row in db.execute(f"SELECT id,payload FROM {table}").fetchall():
                    payload = json.loads(row["payload"])
                    changed = False
                    if not payload.get("site"):
                        payload["site"] = "boss"
                        changed = True
                    if table == "target_jobs" and payload.get("source_type") == "boss_url":
                        payload["source_type"] = "external_url"
                        changed = True
                    if changed:
                        db.execute(
                            f"UPDATE {table} SET payload=? WHERE id=?",
                            (json.dumps(payload, ensure_ascii=False), row["id"]),
                        )

    def initialize_collection_metric(self, run_id: str, sites: list[str]) -> dict[str, Any]:
        stamp = now_iso()
        payload: dict[str, Any] = {
            "run_id": run_id,
            "status": "QUEUED",
            "started_at": stamp,
            "finished_at": None,
            "pages": 0,
            "valid_jobs": 0,
            "duplicates": 0,
            "page_durations_ms": [],
            "failure_category": None,
            "pause_reason": None,
            "sites": {site: {"pages": 0, "valid_jobs": 0, "duplicates": 0} for site in sites},
        }
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO collection_metrics(run_id,payload,updated_at) VALUES(?,?,?)",
                (run_id, json.dumps(payload, ensure_ascii=False), stamp),
            )
        return self.get_collection_metric(run_id) or payload

    def update_collection_metric(
        self, run_id: str, *, site: str | None = None, pages: int = 0,
        valid_jobs: int = 0, duplicates: int = 0, page_duration_ms: int | None = None,
        status: str | None = None, failure_category: str | None = None,
        pause_reason: str | None = None,
    ) -> dict[str, Any]:
        metric = self.get_collection_metric(run_id) or self.initialize_collection_metric(run_id, [site] if site else [])
        for key, increment in (("pages", pages), ("valid_jobs", valid_jobs), ("duplicates", duplicates)):
            metric[key] = int(metric.get(key, 0)) + increment
        if site:
            site_metric = metric.setdefault("sites", {}).setdefault(
                site, {"pages": 0, "valid_jobs": 0, "duplicates": 0},
            )
            for key, increment in (("pages", pages), ("valid_jobs", valid_jobs), ("duplicates", duplicates)):
                site_metric[key] = int(site_metric.get(key, 0)) + increment
        if page_duration_ms is not None and page_duration_ms >= 0:
            metric.setdefault("page_durations_ms", []).append(min(page_duration_ms, 300_000))
        if status:
            metric["status"] = status
            if status in {"SUCCEEDED", "FAILED", "FAILED_VALIDATION"}:
                metric["finished_at"] = now_iso()
        if failure_category is not None:
            metric["failure_category"] = failure_category
        if pause_reason is not None:
            metric["pause_reason"] = pause_reason
        with self.connect() as db:
            db.execute(
                "INSERT INTO collection_metrics(run_id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (run_id, json.dumps(metric, ensure_ascii=False), now_iso()),
            )
        return self.get_collection_metric(run_id) or metric

    def get_collection_metric(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM collection_metrics WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_collection_metrics(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM collection_metrics ORDER BY updated_at DESC").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def export_all_data(self) -> dict[str, list[dict[str, Any]]]:
        """Return every user-owned row in a portable JSON-safe shape."""
        tables = (
            "profiles", "tasks", "jobs", "job_snapshots", "comparisons", "conversations",
            "chat_messages", "chat_actions", "candidate_contacts", "target_jobs",
            "supplemental_evidence", "resume_tailorings", "resume_draft_versions",
            "resume_exports", "interview_preps", "applications", "agent_memories",
            "collection_metrics",
        )
        exported: dict[str, list[dict[str, Any]]] = {}
        with self.connect() as db:
            for table in tables:
                exported[table] = [dict(row) for row in db.execute(f"SELECT * FROM {table}").fetchall()]
        return exported

    def delete_all_data(self) -> None:
        tables = (
            "chat_actions", "chat_messages", "conversations", "supplemental_evidence",
            "resume_exports", "resume_draft_versions", "resume_tailorings", "interview_preps",
            "applications", "target_jobs", "comparisons", "agent_memories", "candidate_contacts",
            "job_snapshots", "jobs", "collection_metrics", "tasks", "profiles",
        )
        with self.connect() as db:
            for table in tables:
                db.execute(f"DELETE FROM {table}")

    def set_runtime_state(self, key: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO runtime_state(key,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (key, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    def get_runtime_state(self, key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT payload,updated_at FROM runtime_state WHERE key=?", (key,)).fetchone()
        return {**json.loads(row["payload"]), "last_seen_at": row["updated_at"]} if row else None

    def save_profile(self, profile: CandidateProfile) -> None:
        stamp = now_iso()
        with self.connect() as db:
            db.execute(
                """INSERT INTO profiles(id,payload,confirmed,created_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload, confirmed=excluded.confirmed, updated_at=excluded.updated_at""",
                (profile.profile_id, profile.model_dump_json(), int(profile.confirmed), stamp, stamp),
            )

    def get_profile(self, profile_id: str) -> CandidateProfile | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM profiles WHERE id=?", (profile_id,)).fetchone()
        return CandidateProfile.model_validate_json(row["payload"]) if row else None

    def create_task(self, task_id: str, kind: str, payload: dict[str, Any]) -> None:
        stamp = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO tasks(id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, kind, TaskStatus.QUEUED, json.dumps(payload, ensure_ascii=False), stamp, stamp),
            )

    def update_task(self, task_id: str, *, status: TaskStatus | None = None,
                    progress: int | None = None, message: str | None = None,
                    error: str | None = None) -> None:
        fields: list[str] = ["updated_at=?"]
        values: list[Any] = [now_iso()]
        for column, value in (("status", status), ("progress", progress),
                              ("message", message), ("error", error)):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(value)
        values.append(task_id)
        with self.connect() as db:
            db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def save_snapshot(self, job_id: str, run_id: str, snapshot: JobSnapshot) -> None:
        with self.connect() as db:
            site = snapshot.site or "boss"
            # platform_job_id and content_hash are only unique *within* a site:
            # two boards listing the same posting share a content hash, and their
            # ids are unrelated. Matching them globally would collapse the pair
            # into one `jobs` row and rewrite canonical_url, losing one site's URL.
            # canonical_url is already globally unique because the host differs.
            existing = db.execute(
                """SELECT j.id FROM jobs j LEFT JOIN job_snapshots s ON s.job_id=j.id
                WHERE j.id=? OR j.canonical_url=?
                   OR (j.site_id=? AND ? != '' AND j.platform_job_id=?)
                   OR (j.site_id=? AND s.content_hash=?)
                ORDER BY CASE WHEN j.id=? THEN 0 WHEN j.canonical_url=? THEN 1 ELSE 2 END LIMIT 1""",
                (job_id, snapshot.canonical_url,
                 site, snapshot.platform_job_id, snapshot.platform_job_id,
                 site, snapshot.content_hash, job_id, snapshot.canonical_url),
            ).fetchone()
            if existing:
                job_id = existing["id"]
            old = db.execute(
                "SELECT payload FROM job_snapshots WHERE job_id=? ORDER BY fetched_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if old:
                previous = JobSnapshot.model_validate_json(old["payload"])
                tracked = (
                    "salary", "responsibilities", "required_skills", "status", "recruitment_type",
                    "graduation_years", "experience_requirement_years", "recruitment_batch",
                    "published_date", "application_deadline", "conversion_opportunity",
                )
                snapshot.changed_fields = [name for name in tracked if getattr(previous, name) != getattr(snapshot, name)]
            stamp = snapshot.fetched_at
            db.execute(
                """INSERT INTO jobs(id,platform_job_id,site_id,canonical_url,latest_snapshot_id,first_seen_at,last_seen_at,status)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                platform_job_id=excluded.platform_job_id, site_id=excluded.site_id,
                canonical_url=excluded.canonical_url,
                latest_snapshot_id=excluded.latest_snapshot_id, last_seen_at=excluded.last_seen_at,
                status=excluded.status""",
                (job_id, snapshot.platform_job_id, site, snapshot.canonical_url,
                 snapshot.snapshot_id, stamp, stamp, snapshot.status),
            )
            db.execute(
                "INSERT INTO job_snapshots(id,job_id,run_id,content_hash,payload,fetched_at) VALUES(?,?,?,?,?,?)",
                (snapshot.snapshot_id, job_id, run_id, snapshot.content_hash,
                 snapshot.model_dump_json(), snapshot.fetched_at),
            )

    def list_snapshots(self, run_id: str | None = None) -> list[JobSnapshot]:
        query = "SELECT s.payload FROM job_snapshots s"
        params: tuple[Any, ...] = ()
        if run_id:
            query += " WHERE s.run_id=? AND s.fetched_at=(SELECT MAX(s2.fetched_at) FROM job_snapshots s2 WHERE s2.job_id=s.job_id AND s2.run_id=s.run_id)"
            params = (run_id,)
        else:
            query += " JOIN jobs j ON j.latest_snapshot_id=s.id"
        query += " ORDER BY s.fetched_at DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [JobSnapshot.model_validate_json(row["payload"]) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> JobSnapshot | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM job_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return JobSnapshot.model_validate_json(row["payload"]) if row else None

    def save_comparison(self, comparison: Comparison, task_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO comparisons(id,profile_id,task_id,payload,created_at) VALUES(?,?,?,?,?)",
                (comparison.comparison_id, comparison.profile_id, task_id,
                 comparison.model_dump_json(), comparison.created_at),
            )

    def get_comparison(self, comparison_id: str) -> Comparison | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM comparisons WHERE id=?", (comparison_id,)).fetchone()
        return Comparison.model_validate_json(row["payload"]) if row else None

    def get_comparison_by_task(self, task_id: str) -> Comparison | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM comparisons WHERE task_id=?", (task_id,)).fetchone()
        return Comparison.model_validate_json(row["payload"]) if row else None

    def create_conversation(self, conversation_id: str, *, profile_id: str | None = None,
                            run_id: str | None = None, comparison_id: str | None = None,
                            title: str | None = None) -> Conversation:
        stamp = now_iso()
        label = title or ("求职分析对话" if profile_id else "通用职业咨询")
        with self.connect() as db:
            db.execute(
                "INSERT INTO conversations(id,profile_id,run_id,comparison_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (conversation_id, profile_id, run_id, comparison_id, label, stamp, stamp),
            )
        return self.get_conversation(conversation_id)  # type: ignore[return-value]

    def update_conversation_context(self, conversation_id: str, *, profile_id: str | None = None,
                                    run_id: str | None = None, comparison_id: str | None = None,
                                    supplied: set[str] | None = None) -> Conversation | None:
        if supplied is None:
            supplied = {"profile_id", "run_id", "comparison_id"}
        fields = ["updated_at=?"]
        values: list[Any] = [now_iso()]
        for name, value in (("profile_id", profile_id), ("run_id", run_id), ("comparison_id", comparison_id)):
            if name in supplied:
                fields.append(f"{name}=?")
                values.append(value)
        values.append(conversation_id)
        with self.connect() as db:
            db.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id=?", values)
        return self.get_conversation(conversation_id)

    def get_conversation(self, conversation_id: str, *, message_limit: int = 100) -> Conversation | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row:
                return None
            messages = db.execute(
                "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, message_limit),
            ).fetchall()
            actions = db.execute(
                "SELECT * FROM chat_actions WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()
        return Conversation(
            conversation_id=row["id"], profile_id=row["profile_id"], run_id=row["run_id"],
            comparison_id=row["comparison_id"], title=row["title"], created_at=row["created_at"],
            updated_at=row["updated_at"],
            messages=[self._message_from_row(item) for item in reversed(messages)],
            actions=[self._action_from_row(item) for item in actions],
            summary=row["summary"] or "", summary_upto_message_id=row["summary_upto_message_id"],
        )

    def save_conversation_summary(self, conversation_id: str, summary: str,
                                  upto_message_id: str | None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE conversations SET summary=?,summary_upto_message_id=? WHERE id=?",
                (summary, upto_message_id, conversation_id),
            )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            message_id=row["id"], conversation_id=row["conversation_id"], role=row["role"],
            content=row["content"], status=row["status"], citations=json.loads(row["citations"]),
            task_id=row["task_id"], source=row["source"], created_at=row["created_at"],
        )

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> ChatAction:
        return ChatAction(
            action_id=row["id"], conversation_id=row["conversation_id"],
            source_message_id=row["source_message_id"], kind=ChatActionKind(row["kind"]),
            arguments=json.loads(row["arguments"]), preview=json.loads(row["preview"]),
            status=row["status"], result=json.loads(row["result"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def save_chat_message(self, message: ChatMessage) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO chat_messages(id,conversation_id,role,content,status,citations,task_id,source,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (message.message_id, message.conversation_id, message.role, message.content, message.status,
                 json.dumps([item.model_dump() for item in message.citations], ensure_ascii=False),
                 message.task_id, message.source, message.created_at),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_iso(), message.conversation_id))

    def get_chat_message(self, message_id: str) -> ChatMessage | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        return self._message_from_row(row) if row else None

    def get_chat_message_by_task(self, task_id: str) -> ChatMessage | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM chat_messages WHERE task_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._message_from_row(row) if row else None

    def create_chat_action(self, action: ChatAction) -> ChatAction:
        with self.connect() as db:
            db.execute(
                "INSERT INTO chat_actions(id,conversation_id,source_message_id,kind,arguments,preview,status,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (action.action_id, action.conversation_id, action.source_message_id, action.kind,
                 json.dumps(action.arguments, ensure_ascii=False), json.dumps(action.preview, ensure_ascii=False),
                 action.status, json.dumps(action.result, ensure_ascii=False), action.created_at, action.updated_at),
            )
        return action

    def get_chat_action(self, action_id: str) -> ChatAction | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chat_actions WHERE id=?", (action_id,)).fetchone()
        return self._action_from_row(row) if row else None

    def list_chat_actions(self, conversation_id: str, *, source_message_id: str | None = None) -> list[ChatAction]:
        query = "SELECT * FROM chat_actions WHERE conversation_id=?"
        params: list[Any] = [conversation_id]
        if source_message_id:
            query += " AND source_message_id=?"
            params.append(source_message_id)
        query += " ORDER BY created_at"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._action_from_row(row) for row in rows]

    def claim_chat_action(self, action_id: str) -> tuple[ChatAction | None, bool]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM chat_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                return None, False
            action = self._action_from_row(row)
            if action.status != "PENDING":
                return action, False
            db.execute("UPDATE chat_actions SET status='EXECUTING',updated_at=? WHERE id=?", (now_iso(), action_id))
        return self.get_chat_action(action_id), True

    def update_chat_action(self, action_id: str, *, status: str, result: dict[str, Any] | None = None) -> ChatAction | None:
        with self.connect() as db:
            db.execute(
                "UPDATE chat_actions SET status=?,result=?,updated_at=? WHERE id=?",
                (status, json.dumps(result or {}, ensure_ascii=False), now_iso(), action_id),
            )
        return self.get_chat_action(action_id)

    def reject_chat_action(self, action_id: str) -> tuple[ChatAction | None, bool]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM chat_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                return None, False
            action = self._action_from_row(row)
            if action.status != "PENDING":
                return action, False
            db.execute("UPDATE chat_actions SET status='REJECTED',updated_at=? WHERE id=?", (now_iso(), action_id))
        return self.get_chat_action(action_id), True

    def list_rejected_chat_actions(self, profile_id: str) -> list[ChatAction]:
        """Every rejection this candidate ever made, newest last.

        Rejections are the only negative feedback the UI produces, and they are
        already persisted; this is how the miner finds them across conversations.
        """
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.* FROM chat_actions a JOIN conversations c ON c.id=a.conversation_id "
                "WHERE c.profile_id=? AND a.status='REJECTED' ORDER BY a.created_at",
                (profile_id,),
            ).fetchall()
        return [self._action_from_row(row) for row in rows]

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> AgentMemory:
        return AgentMemory(
            memory_id=row["id"], profile_id=row["profile_id"], kind=MemoryKind(row["kind"]),
            text=row["text"], source_type=row["source_type"], source_id=row["source_id"],
            confidence=row["confidence"], superseded_by=row["superseded_by"],
            created_at=row["created_at"],
        )

    def save_memory(self, memory: AgentMemory) -> AgentMemory:
        with self.connect() as db:
            db.execute(
                "INSERT INTO agent_memories(id,profile_id,kind,text,source_type,source_id,confidence,superseded_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (memory.memory_id, memory.profile_id, memory.kind, memory.text, memory.source_type,
                 memory.source_id, memory.confidence, memory.superseded_by, memory.created_at),
            )
        return memory

    def find_memory(self, profile_id: str, *, source_type: str, source_id: str) -> AgentMemory | None:
        """Whether this exact source already produced a memory."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_memories WHERE profile_id=? AND source_type=? AND source_id=? LIMIT 1",
                (profile_id, source_type, source_id),
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def list_memories(self, profile_id: str, *, limit: int = 50,
                      kind: MemoryKind | None = None) -> list[AgentMemory]:
        """Newest live memories first. Superseded rows are historical, not context."""
        query = "SELECT * FROM agent_memories WHERE profile_id=? AND superseded_by IS NULL"
        params: list[Any] = [profile_id]
        if kind:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def supersede_memory(self, memory_id: str, by_id: str) -> None:
        """Retire a memory in favour of a newer one without deleting history."""
        with self.connect() as db:
            db.execute("UPDATE agent_memories SET superseded_by=? WHERE id=?", (by_id, memory_id))

    def save_contact(self, contact: CandidateContact) -> CandidateContact:
        with self.connect() as db:
            db.execute(
                "INSERT INTO candidate_contacts(profile_id,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(profile_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (contact.profile_id, contact.model_dump_json(), now_iso()),
            )
        return contact

    def get_contact(self, profile_id: str) -> CandidateContact:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM candidate_contacts WHERE profile_id=?", (profile_id,)).fetchone()
        return CandidateContact.model_validate_json(row["payload"]) if row else CandidateContact(profile_id=profile_id)

    def save_target_job(self, target: TargetJob) -> TargetJob:
        with self.connect() as db:
            db.execute(
                "INSERT INTO target_jobs(id,profile_id,payload,created_at) VALUES(?,?,?,?)",
                (target.target_job_id, target.profile_id, target.model_dump_json(), target.created_at),
            )
        return target

    def get_target_job(self, target_job_id: str) -> TargetJob | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM target_jobs WHERE id=?", (target_job_id,)).fetchone()
        return TargetJob.model_validate_json(row["payload"]) if row else None

    def save_application(self, application: Application) -> Application:
        application.updated_at = now_iso()
        with self.connect() as db:
            db.execute(
                """INSERT INTO applications(id,profile_id,snapshot_id,payload,created_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (application.application_id, application.profile_id, application.snapshot_id,
                 application.model_dump_json(), application.created_at, application.updated_at),
            )
        return application

    def get_application(self, application_id: str) -> Application | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM applications WHERE id=?", (application_id,)).fetchone()
        return Application.model_validate_json(row["payload"]) if row else None

    def get_application_for_snapshot(self, snapshot_id: str, profile_id: str) -> Application | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM applications WHERE snapshot_id=? AND profile_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (snapshot_id, profile_id),
            ).fetchone()
        return Application.model_validate_json(row["payload"]) if row else None

    def list_applications(self, profile_id: str) -> list[Application]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM applications WHERE profile_id=? ORDER BY created_at DESC",
                (profile_id,),
            ).fetchall()
        return [Application.model_validate_json(row["payload"]) for row in rows]

    def list_all_applications(self) -> list[Application]:
        """Every application, for the browser helper's popup.

        Safe because CareerRadar is a single-user local tool: there is no other
        user whose rows could leak.
        """
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM applications ORDER BY created_at DESC").fetchall()
        return [Application.model_validate_json(row["payload"]) for row in rows]

    def latest_export_for_snapshot(self, snapshot_id: str, profile_id: str) -> str | None:
        """The tailored resume exported for this posting, if one exists.

        Walked through the tailoring chain rather than stored on the snapshot,
        because an export only exists once the user has actually generated one.
        """
        with self.connect() as db:
            row = db.execute(
                """SELECT e.id FROM resume_exports e
                JOIN resume_draft_versions v ON v.id = e.version_id
                JOIN resume_tailorings t ON t.id = v.tailoring_id
                JOIN target_jobs j ON j.id = t.target_job_id
                WHERE t.profile_id=? AND json_extract(j.payload, '$.snapshot_id')=?
                ORDER BY e.created_at DESC LIMIT 1""",
                (snapshot_id, profile_id),
            ).fetchone()
        return row["id"] if row else None

    def save_interview_prep(self, prep: InterviewPrep) -> InterviewPrep:
        prep.updated_at = now_iso()
        with self.connect() as db:
            db.execute(
                """INSERT INTO interview_preps(id,profile_id,snapshot_id,payload,created_at,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (prep.prep_id, prep.profile_id, prep.snapshot_id, prep.model_dump_json(),
                 prep.created_at, prep.updated_at),
            )
        return prep

    def get_interview_prep(self, prep_id: str) -> InterviewPrep | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM interview_preps WHERE id=?", (prep_id,)).fetchone()
        return InterviewPrep.model_validate_json(row["payload"]) if row else None

    def get_interview_prep_for_snapshot(self, snapshot_id: str, profile_id: str) -> InterviewPrep | None:
        """The newest preparation for one posting, if it belongs to this candidate."""
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM interview_preps WHERE snapshot_id=? AND profile_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (snapshot_id, profile_id),
            ).fetchone()
        return InterviewPrep.model_validate_json(row["payload"]) if row else None

    def snapshot_belongs_to_profile(self, snapshot_id: str, profile_id: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM job_snapshots s JOIN tasks t ON t.id=s.run_id "
                "WHERE s.id=? AND json_extract(t.payload, '$.profile_id')=?",
                (snapshot_id, profile_id),
            ).fetchone()
        return bool(row)

    def save_supplemental_evidence(self, evidence: SupplementalEvidence) -> SupplementalEvidence:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO supplemental_evidence(id,profile_id,tailoring_id,payload,created_at) VALUES(?,?,?,?,?)",
                (evidence.evidence_id, evidence.profile_id, evidence.tailoring_id,
                 evidence.model_dump_json(), evidence.created_at),
            )
        return evidence

    def list_supplemental_evidence(self, profile_id: str) -> list[SupplementalEvidence]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM supplemental_evidence WHERE profile_id=? ORDER BY created_at",
                (profile_id,),
            ).fetchall()
        return [SupplementalEvidence.model_validate_json(row["payload"]) for row in rows]

    def save_tailoring(self, tailoring: ResumeTailoring) -> ResumeTailoring:
        tailoring.updated_at = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO resume_tailorings(id,profile_id,target_job_id,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (tailoring.tailoring_id, tailoring.profile_id, tailoring.target_job_id,
                 tailoring.model_dump_json(), tailoring.created_at, tailoring.updated_at),
            )
        return tailoring

    def get_tailoring(self, tailoring_id: str) -> ResumeTailoring | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM resume_tailorings WHERE id=?", (tailoring_id,)).fetchone()
        return ResumeTailoring.model_validate_json(row["payload"]) if row else None

    def save_draft_version(self, version: ResumeDraftVersion) -> ResumeDraftVersion:
        payload = version.model_dump()
        payload["contact"] = {"profile_id": version.profile_id}
        with self.connect() as db:
            db.execute(
                "INSERT INTO resume_draft_versions(id,draft_id,tailoring_id,profile_id,version,payload,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (version.version_id, version.draft_id, version.tailoring_id, version.profile_id,
                 version.version, json.dumps(payload, ensure_ascii=False), version.created_at),
            )
        return version

    def _draft_from_payload(self, payload: str) -> ResumeDraftVersion:
        version = ResumeDraftVersion.model_validate_json(payload)
        version.contact = self.get_contact(version.profile_id)
        return version

    def get_draft_version(self, version_id: str) -> ResumeDraftVersion | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM resume_draft_versions WHERE id=?", (version_id,)).fetchone()
        return self._draft_from_payload(row["payload"]) if row else None

    def get_latest_draft(self, draft_id: str) -> ResumeDraftVersion | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM resume_draft_versions WHERE draft_id=? ORDER BY version DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
        return self._draft_from_payload(row["payload"]) if row else None

    def list_draft_versions(self, draft_id: str) -> list[ResumeDraftVersion]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM resume_draft_versions WHERE draft_id=? ORDER BY version DESC",
                (draft_id,),
            ).fetchall()
        return [self._draft_from_payload(row["payload"]) for row in rows]

    def save_resume_export(self, export: ResumeExport) -> ResumeExport:
        export.updated_at = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO resume_exports(id,version_id,payload,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (export.export_id, export.version_id, export.model_dump_json(), export.created_at, export.updated_at),
            )
        return export

    def get_resume_export(self, export_id: str) -> ResumeExport | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM resume_exports WHERE id=?", (export_id,)).fetchone()
        return ResumeExport.model_validate_json(row["payload"]) if row else None
