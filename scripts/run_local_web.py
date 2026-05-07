#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

import uvicorn

from resume_bot.local_web import create_app


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local Resume Bot web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
