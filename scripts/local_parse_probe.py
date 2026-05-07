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

from resume_bot.config import load_config
from resume_bot.pipeline import ResumeBotPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local resume parse probe.")
    parser.add_argument("file", help="Absolute path to the resume file")
    parser.add_argument("--user-id", default="me")
    args = parser.parse_args()

    pipeline = ResumeBotPipeline(load_config())
    result = pipeline.ingest_resume(args.user_id, Path(args.file))
    payload = {
        "parse_method": result["profile"].raw_sections.get("_parse_method", ""),
        "parse_warning": result["profile"].raw_sections.get("_parse_warning", ""),
        "extraction": result["extraction"],
        "experiences": result["profile"].experiences[:3],
        "skills": result["profile"].skills[:8],
        "profile_summary": result["profile_summary"],
        "debug_report": result["debug_report"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
