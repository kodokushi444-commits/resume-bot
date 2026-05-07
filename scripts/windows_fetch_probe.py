#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config
from resume_bot.debug_report import write_debug_report
from resume_bot.pipeline import ResumeBotPipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one Windows-side fetch probe and write the result into data/debug."
    )
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--selected-source", action="append", default=[])
    parser.add_argument("--max-nowcoder-pages", type=int, default=0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    user_id = args.user_id or config.default_user_id
    selected_source_names = pipeline.resolve_selected_source_names(args.selected_source or None)

    if args.max_nowcoder_pages > 0:
        original_loader = pipeline._load_source_registry

        def _patched_source_registry():
            payload = original_loader()
            for item in payload.get("sources", []):
                if item.get("name") == "nowcoder_direct":
                    item["max_detail_pages"] = int(args.max_nowcoder_pages)
            return payload

        pipeline._load_source_registry = _patched_source_registry

    started = time.perf_counter()
    recent_before = pipeline.store.list_recent_source_runs(limit=6)
    payload: dict = {
        "summary": [
            f"user_id={user_id}",
            f"project_root={config.project_root}",
        ],
        "project_root": str(config.project_root),
        "db_path": str(config.db_path),
        "debug_dir": str(config.debug_dir),
        "recent_source_runs_before": recent_before,
        "selected_source_names": selected_source_names,
        "max_nowcoder_pages": int(args.max_nowcoder_pages or 0),
    }
    start_report = write_debug_report(
        config.debug_dir,
        "windows-fetch-probe-state",
        user_id,
        {
            "summary": payload["summary"] + ["status=started"],
            "project_root": str(config.project_root),
            "db_path": str(config.db_path),
            "debug_dir": str(config.debug_dir),
            "recent_source_runs_before": recent_before,
        },
    )
    payload["start_report"] = start_report

    exit_code = 0
    try:
        fetch_report = pipeline.fetch_jobs(user_id, selected_source_names=selected_source_names)
        payload["fetch_report"] = fetch_report
        payload["summary"].append(
            "status=ok "
            f"raw_total={fetch_report.get('total_jobs', 0)} "
            f"inserted={fetch_report.get('upsert', {}).get('inserted', 0)} "
            f"updated={fetch_report.get('upsert', {}).get('updated', 0)}"
        )
    except Exception as exc:
        exit_code = 1
        payload["error"] = str(exc)
        payload["traceback"] = traceback.format_exc()
        payload["summary"].append(f"status=error {exc}")

    payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
    payload["recent_source_runs_after"] = pipeline.store.list_recent_source_runs(limit=8)
    payload["jobs_count_after"] = len(pipeline.store.load_jobs())

    report = write_debug_report(config.debug_dir, "windows-fetch-probe", user_id, payload)
    result = {
        "ok": exit_code == 0,
        "report": report,
        "summary": payload["summary"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
