#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.boss_cli_bridge import get_boss_cli_status
from resume_bot.config import load_config
from resume_bot.job_sources.boss_common import PROFILE_READY_MARKER, resolve_cdp_endpoint
from resume_bot.pipeline import ResumeBotPipeline
from resume_bot.runtime_checks import llm_runtime_warning


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Resume Bot self-test.")
    parser.add_argument("--live", action="store_true", help="Also perform a live LLM connectivity check.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    boss_cli_status = get_boss_cli_status(config)
    user_id = config.default_user_id
    profile, _ = pipeline.store.get_active_resume(user_id)
    settings = pipeline.store.get_settings(user_id)
    active_sources = pipeline.active_source_names(user_id)
    source_runs = pipeline.store.list_recent_source_runs(limit=5, source_names=active_sources)
    boss_cdp_url = resolve_cdp_endpoint(
        config.boss_browser_cdp_port,
        config.boss_browser_cdp_url,
        timeout_seconds=1.0,
    )

    result = {
        "project_root": str(config.project_root),
        "db_path": str(config.db_path),
        "data_dir": str(config.data_dir),
        "active_sources": active_sources,
        "llm_ready": bool(config.llm_provider and config.llm_api_key and config.llm_base_url and config.llm_model),
        "vision_ready": bool(
            config.vision_provider and config.vision_api_key and config.vision_base_url and config.vision_model
        ),
        "tavily_ready": bool(config.tavily_api_key),
        "llm_warning": llm_runtime_warning(config),
        "boss_cli_available": boss_cli_status["available"],
        "boss_cli_command": boss_cli_status["command"],
        "boss_cli_authenticated": boss_cli_status["authenticated"],
        "boss_cli_error": boss_cli_status["error"],
        "boss_profile_ready": bool(
            config.boss_browser_profile_dir.exists()
            and (config.boss_browser_profile_dir / PROFILE_READY_MARKER).exists()
        ),
        "boss_cdp_ready": bool(boss_cdp_url),
        "boss_cdp_url": boss_cdp_url,
        "boss_state_ready": bool(
            config.boss_browser_state_path.exists()
            and config.boss_browser_state_path.is_file()
            and config.boss_browser_state_path.stat().st_size > 2
        ),
        "has_resume": profile is not None,
        "preferred_roles": settings.preferred_roles,
        "job_types": settings.job_types,
        "campus_role_mode": settings.campus_role_mode,
        "recent_source_runs": source_runs,
    }
    if args.live:
        try:
            reply = pipeline.text_client.complete_text(
                "You are a health check assistant. Reply with OK only.",
                "Reply with OK only.",
                max_tokens=12,
            )
            if not (reply or "").strip():
                raise RuntimeError("模型请求成功返回了空文本，这说明当前 MiniMax 响应解析可能不对。")
            result["llm_live_ok"] = True
            result["llm_live_reply"] = reply
        except Exception as exc:
            result["llm_live_ok"] = False
            result["llm_live_error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
