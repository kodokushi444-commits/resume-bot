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
    parser = argparse.ArgumentParser(description="Diagnose one current BOSS results page action.")
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--keyword", default="运营")
    parser.add_argument("--step", choices=["read", "scroll", "click"], default="read")
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
        name="boss_browser_diagnose",
        storage_state_path=config.boss_browser_state_path,
        profile_dir=config.boss_browser_profile_dir,
        base_url="https://www.zhipin.com/web/geek/jobs",
        max_queries=1,
        max_cards_per_query=4,
        max_detail_pages=2,
        timeout_ms=min(config.playwright_timeout_ms, 12000),
        headless=False,
        debug_dir=config.debug_dir,
        prefer_cdp_attach=True,
        cdp_url=config.boss_browser_cdp_url,
        cdp_port=config.boss_browser_cdp_port,
    )
    try:
        payload = source.diagnose_current_results_page(
            city=args.city,
            keyword=args.keyword,
            step=args.step,
        )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "diagnose",
            "step": args.step,
            "city": args.city,
            "keyword": args.keyword,
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
