#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess


PORT_START = 8765
PORT_END = 8785


def find_listener_pids() -> dict[int, list[int]]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    mapping: dict[int, list[int]] = {}
    if result.returncode != 0:
        return mapping
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if "LISTENING" not in line:
            continue
        port_match = re.search(r"127\.0\.0\.1:(\d+)", line)
        if not port_match:
            continue
        port = int(port_match.group(1))
        if port < PORT_START or port > PORT_END:
            continue
        parts = re.split(r"\s+", line)
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        mapping.setdefault(port, []).append(pid)
    return mapping


def kill_process(pid: int) -> bool:
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    return result.returncode == 0


def main() -> int:
    if os.name != "nt":
        print("cleanup_local_web.py: non-Windows environment, skip.")
        return 0
    mapping = find_listener_pids()
    if not mapping:
        print(f"No listener found between {PORT_START} and {PORT_END}.")
        return 0
    seen: set[int] = set()
    failed = False
    for port in sorted(mapping):
        for pid in mapping[port]:
            if pid in seen:
                continue
            seen.add(pid)
            if kill_process(pid):
                print(f"Stopped PID {pid} on port {port}.")
            else:
                failed = True
                print(f"Failed to stop PID {pid} on port {port}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
