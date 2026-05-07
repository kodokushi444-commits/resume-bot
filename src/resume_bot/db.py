from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    source_path TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resumes_user_id ON resumes (user_id);

CREATE TABLE IF NOT EXISTS jobs (
    fingerprint TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    company_name TEXT NOT NULL,
    city TEXT NOT NULL,
    company_stage TEXT NOT NULL,
    published_at TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    job_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs (source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs (last_seen_at);

CREATE TABLE IF NOT EXISTS pushes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    pushed_at TEXT NOT NULL,
    UNIQUE (user_id, fingerprint, content_hash)
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    action TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interactions_user_job ON interactions (user_id, fingerprint);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    run_key TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    UNIQUE (user_id, task_name, run_key)
);

CREATE TABLE IF NOT EXISTS delivery_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    delivery_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    company_name TEXT NOT NULL,
    city TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    apply_url TEXT NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    job_json TEXT NOT NULL,
    delivered_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_history_user_time ON delivery_history (user_id, delivered_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_history_user_fingerprint ON delivery_history (user_id, fingerprint);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
