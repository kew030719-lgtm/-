from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .schemas import CandidateProfile, Comparison, JobSnapshot, TaskStatus


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
                """
            )
            db.execute(
                "UPDATE tasks SET status=?, message=?, updated_at=? WHERE status=?",
                (TaskStatus.QUEUED, "服务重启，可重新执行", now_iso(), TaskStatus.RUNNING),
            )

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
            existing = db.execute(
                """SELECT j.id FROM jobs j LEFT JOIN job_snapshots s ON s.job_id=j.id
                WHERE j.id=? OR j.canonical_url=? OR (? != '' AND j.platform_job_id=?) OR s.content_hash=?
                ORDER BY CASE WHEN j.id=? THEN 0 WHEN j.canonical_url=? THEN 1 ELSE 2 END LIMIT 1""",
                (job_id, snapshot.canonical_url, snapshot.platform_job_id, snapshot.platform_job_id,
                 snapshot.content_hash, job_id, snapshot.canonical_url),
            ).fetchone()
            if existing:
                job_id = existing["id"]
            old = db.execute(
                "SELECT payload FROM job_snapshots WHERE job_id=? ORDER BY fetched_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if old:
                previous = JobSnapshot.model_validate_json(old["payload"])
                tracked = ("salary", "responsibilities", "required_skills", "status")
                snapshot.changed_fields = [name for name in tracked if getattr(previous, name) != getattr(snapshot, name)]
            stamp = snapshot.fetched_at
            db.execute(
                """INSERT INTO jobs(id,platform_job_id,canonical_url,latest_snapshot_id,first_seen_at,last_seen_at,status)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                platform_job_id=excluded.platform_job_id, canonical_url=excluded.canonical_url,
                latest_snapshot_id=excluded.latest_snapshot_id, last_seen_at=excluded.last_seen_at,
                status=excluded.status""",
                (job_id, snapshot.platform_job_id, snapshot.canonical_url, snapshot.snapshot_id, stamp, stamp, snapshot.status),
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
