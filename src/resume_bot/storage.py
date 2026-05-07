from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .config import AppConfig
from .db import connect, ensure_schema
from .preferences import normalize_settings_lists
from .types import JobPosting, ResumeProfile, UserSettings, utcnow_iso


class ResumeBotStore:
    def __init__(self, config: AppConfig):
        self.config = config
        ensure_schema(config.db_path)

    def _conn(self):
        return connect(self.config.db_path)

    def load_default_settings(self, user_id: str) -> UserSettings:
        payload = json.loads(self.config.default_settings_path.read_text(encoding="utf-8"))
        return normalize_settings_lists(UserSettings.from_dict(payload, user_id=user_id))

    def ensure_user(self, user_id: str) -> None:
        now = utcnow_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (user_id, now, now),
            )
            conn.commit()

    def get_settings(self, user_id: str) -> UserSettings:
        self.ensure_user(user_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT settings_json FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            settings = self.load_default_settings(user_id)
            self.save_settings(settings)
            return settings
        raw_settings = UserSettings.from_dict(json.loads(row["settings_json"]), user_id=user_id)
        normalized = normalize_settings_lists(raw_settings)
        if normalized.to_dict() != raw_settings.to_dict():
            self.save_settings(normalized)
        return normalized

    def save_settings(self, settings: UserSettings) -> None:
        settings.updated_at = utcnow_iso()
        self.ensure_user(settings.user_id)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_id, settings_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    settings_json=excluded.settings_json,
                    updated_at=excluded.updated_at
                """,
                (
                    settings.user_id,
                    json.dumps(settings.to_dict(), ensure_ascii=False),
                    settings.updated_at,
                ),
            )
            conn.commit()

    def save_resume(
        self,
        user_id: str,
        file_name: str,
        file_hash: str,
        source_path: str,
        raw_text: str,
        profile: ResumeProfile,
    ) -> None:
        now = utcnow_iso()
        with self._conn() as conn:
            conn.execute("UPDATE resumes SET is_active = 0 WHERE user_id = ?", (user_id,))
            conn.execute(
                """
                INSERT INTO resumes (
                    user_id, file_name, file_hash, source_path, raw_text, profile_json, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    user_id,
                    file_name,
                    file_hash,
                    source_path,
                    raw_text,
                    json.dumps(profile.to_dict(), ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()

    def get_active_resume(self, user_id: str) -> tuple[ResumeProfile | None, str]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT profile_json, raw_text
                FROM resumes
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None, ""
        return ResumeProfile.from_dict(json.loads(row["profile_json"])), row["raw_text"]

    def get_active_resume_record(self, user_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, file_name, file_hash, source_path, raw_text, profile_json, created_at
                FROM resumes
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_jobs(self, jobs: Iterable[JobPosting]) -> dict[str, int]:
        inserted = 0
        updated = 0
        touched = 0
        now = utcnow_iso()
        with self._conn() as conn:
            for job in jobs:
                job.ensure_ids()
                payload = job.to_dict()
                row = conn.execute(
                    "SELECT content_hash FROM jobs WHERE fingerprint = ?",
                    (job.fingerprint,),
                ).fetchone()
                if row is None:
                    inserted += 1
                    conn.execute(
                        """
                        INSERT INTO jobs (
                            fingerprint, source, url, source_job_id, content_hash, title, company_name,
                            city, company_stage, published_at, discovered_at, last_seen_at, job_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job.fingerprint,
                            job.source,
                            job.url,
                            job.source_job_id,
                            job.content_hash,
                            job.title,
                            job.company_name,
                            job.city,
                            job.company_stage,
                            job.published_at,
                            job.discovered_at,
                            now,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                else:
                    touched += 1
                    if row["content_hash"] != job.content_hash:
                        updated += 1
                    conn.execute(
                        """
                        UPDATE jobs
                        SET source=?, url=?, source_job_id=?, content_hash=?, title=?, company_name=?,
                            city=?, company_stage=?, published_at=?, last_seen_at=?, job_json=?
                        WHERE fingerprint=?
                        """,
                        (
                            job.source,
                            job.url,
                            job.source_job_id,
                            job.content_hash,
                            job.title,
                            job.company_name,
                            job.city,
                            job.company_stage,
                            job.published_at,
                            now,
                            json.dumps(payload, ensure_ascii=False),
                            job.fingerprint,
                        ),
                    )
            conn.commit()
        return {"inserted": inserted, "updated": updated, "touched": touched}

    def load_jobs(self, source_names: list[str] | None = None) -> list[JobPosting]:
        with self._conn() as conn:
            if source_names is None:
                rows = conn.execute("SELECT job_json FROM jobs ORDER BY last_seen_at DESC").fetchall()
            elif not source_names:
                rows = []
            else:
                placeholders = ",".join("?" for _ in source_names)
                rows = conn.execute(
                    f"SELECT job_json FROM jobs WHERE source IN ({placeholders}) ORDER BY last_seen_at DESC",
                    tuple(source_names),
                ).fetchall()
        return [JobPosting.from_dict(json.loads(row["job_json"])) for row in rows]

    def prune_jobs_for_source(self, source_name: str, keep_fingerprints: list[str]) -> int:
        with self._conn() as conn:
            if keep_fingerprints:
                placeholders = ",".join("?" for _ in keep_fingerprints)
                cursor = conn.execute(
                    f"DELETE FROM jobs WHERE source = ? AND fingerprint NOT IN ({placeholders})",
                    (source_name, *keep_fingerprints),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM jobs WHERE source = ?",
                    (source_name,),
                )
            conn.commit()
        return int(cursor.rowcount or 0)

    def was_pushed(self, user_id: str, fingerprint: str, content_hash: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM pushes
                WHERE user_id = ? AND fingerprint = ? AND content_hash = ?
                LIMIT 1
                """,
                (user_id, fingerprint, content_hash),
            ).fetchone()
        return row is not None

    def record_push(
        self,
        user_id: str,
        fingerprint: str,
        content_hash: str,
        score: float,
        reasons: list[str],
        *,
        delivery_kind: str,
        job: JobPosting,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO pushes (user_id, fingerprint, content_hash, score, reasons_json, pushed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    fingerprint,
                    content_hash,
                    score,
                    json.dumps(reasons, ensure_ascii=False),
                    utcnow_iso(),
                ),
            )
            conn.execute(
                """
                INSERT INTO delivery_history (
                    user_id, fingerprint, content_hash, delivery_kind, title, company_name, city,
                    source, url, apply_url, score, reasons_json, job_json, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    fingerprint,
                    content_hash,
                    delivery_kind,
                    job.title,
                    job.company_name,
                    job.city,
                    job.source,
                    job.url,
                    job.apply_url,
                    score,
                    json.dumps(reasons, ensure_ascii=False),
                    json.dumps(job.to_dict(), ensure_ascii=False),
                    utcnow_iso(),
                ),
            )
            conn.commit()

    def record_interaction(self, user_id: str, fingerprint: str, action: str, notes: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO interactions (user_id, fingerprint, action, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, fingerprint, action, notes, utcnow_iso()),
            )
            conn.commit()

    def last_action_for_job(self, user_id: str, fingerprint: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT action
                FROM interactions
                WHERE user_id = ? AND fingerprint = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id, fingerprint),
            ).fetchone()
        return row["action"] if row else ""

    def record_source_run(self, source_name: str, status: str, detail: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO source_runs (source_name, status, detail_json, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    status,
                    json.dumps(detail, ensure_ascii=False),
                    detail.get("started_at", utcnow_iso()),
                    detail.get("finished_at", utcnow_iso()),
                ),
            )
            conn.commit()

    def has_scheduler_run(self, user_id: str, task_name: str, run_key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM scheduler_runs
                WHERE user_id = ? AND task_name = ? AND run_key = ?
                LIMIT 1
                """,
                (user_id, task_name, run_key),
            ).fetchone()
        return row is not None

    def record_scheduler_run(self, user_id: str, task_name: str, run_key: str, detail: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO scheduler_runs (user_id, task_name, run_key, executed_at, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    task_name,
                    run_key,
                    utcnow_iso(),
                    json.dumps(detail, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_last_delivery_time(self, user_id: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT delivered_at
                FROM delivery_history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return row["delivered_at"] if row else ""

    def list_delivery_history(self, user_id: str, limit: int = 20, keyword: str = "") -> list[dict]:
        with self._conn() as conn:
            if keyword:
                pattern = f"%{keyword}%"
                rows = conn.execute(
                    """
                    SELECT *
                    FROM delivery_history
                    WHERE user_id = ?
                      AND (title LIKE ? OR company_name LIKE ? OR city LIKE ? OR source LIKE ?)
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, pattern, pattern, pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM delivery_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_interactions(self, user_id: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM interactions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_source_runs(self, limit: int = 12, source_names: list[str] | None = None) -> list[dict]:
        with self._conn() as conn:
            if source_names is None:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM source_runs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            elif not source_names:
                rows = []
            else:
                placeholders = ",".join("?" for _ in source_names)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM source_runs
                    WHERE source_name IN ({placeholders})
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (*source_names, limit),
                ).fetchall()
        return [dict(row) for row in rows]
