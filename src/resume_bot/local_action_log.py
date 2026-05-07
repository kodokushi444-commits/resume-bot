from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import utcnow_iso


def append_action_log(path: Path, event: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": utcnow_iso(),
        "event": event,
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_recent_action_logs(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"timestamp": utcnow_iso(), "event": "corrupted_line", "raw": line})
    return rows
