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
    parser = argparse.ArgumentParser(description="Inspect attached BOSS detail surfaces without navigation.")
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
    source = pipeline._build_boss_browser_runtime_source()
    try:
        payload = source.inspect_detail_surfaces()
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "detail_surface_inspect",
            "error": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
