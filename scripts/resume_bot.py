#!/usr/bin/env python3
from pathlib import Path
import sys

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))
else:
    sys.path.insert(0, str(CURRENT_DIR))

from resume_bot.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
