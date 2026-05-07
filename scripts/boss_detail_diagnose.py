#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config
from resume_bot.pipeline import ResumeBotPipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose BOSS detail supplement modes for one stored session.")
    parser.add_argument("--fetch-session-id", required=True)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _build_parser().parse_args()
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    session_id = str(args.fetch_session_id or "").strip()
    all_jobs = [
        job
        for job in pipeline.store.load_jobs(["boss_browser"])
        if str(job.fetch_session_id or "").strip() == session_id
    ]
    pending_jobs = [
        job
        for job in all_jobs
        if not job.detail_fetched and str(job.apply_url or job.url or "").strip()
    ]
    source = pipeline._build_boss_browser_runtime_source()
    try:
        payload = source.diagnose_detail_jobs(
            pending_jobs,
            limit=args.limit,
            query=f"{session_id} boss_detail_diagnose",
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "detail_job_diagnose",
            "fetch_session_id": session_id,
            "session_job_count": len(all_jobs),
            "pending_job_count": len(pending_jobs),
            "limit": max(1, int(args.limit or 1)),
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    payload["fetch_session_id"] = session_id
    payload["session_job_count"] = len(all_jobs)
    payload["pending_job_count"] = len(pending_jobs)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
