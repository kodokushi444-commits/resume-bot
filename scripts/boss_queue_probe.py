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
from resume_bot.job_sources import BossBrowserSource


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the BOSS list queue phase.")
    parser.add_argument(
        "--mode",
        choices=("worktab", "current"),
        default="worktab",
        help="`worktab` opens a dedicated search tab inside the logged-in browser; `current` reuses the current results tab.",
    )
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--keyword", default="运营")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _build_parser().parse_args()
    config = load_config()
    source = BossBrowserSource(
        name="boss_browser_queue_probe",
        storage_state_path=config.boss_browser_state_path,
        profile_dir=config.boss_browser_profile_dir,
        base_url="https://www.zhipin.com/web/geek/jobs",
        max_queries=1,
        max_cards_per_query=max(1, min(int(args.limit or 1), 20)),
        max_detail_pages=0,
        timeout_ms=min(config.playwright_timeout_ms, 15000),
        headless=False,
        debug_dir=config.debug_dir,
        prefer_cdp_attach=True,
        cdp_url=config.boss_browser_cdp_url,
        cdp_port=config.boss_browser_cdp_port,
    )
    try:
        if args.mode == "current":
            payload = source.probe_current_job_queue(
                city=args.city,
                keyword=args.keyword,
                limit=args.limit,
                rounds=args.rounds,
            )
        else:
            payload = source.probe_worktab_job_queue(
                city=args.city,
                keyword=args.keyword,
                limit=args.limit,
                rounds=args.rounds,
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": args.mode,
            "city": args.city,
            "keyword": args.keyword,
            "limit": args.limit,
            "rounds": args.rounds,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
