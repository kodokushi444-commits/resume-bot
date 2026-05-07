from __future__ import annotations

import json
import shlex
import shutil
import subprocess

from .config import AppConfig


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        normalized = tuple(part.strip() for part in command if part and part.strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(list(normalized))
    return deduped


def _command_candidates(config: AppConfig) -> list[list[str]]:
    commands: list[list[str]] = []
    explicit = config.boss_cli_command.strip()
    if explicit:
        commands.append(shlex.split(explicit, posix=False))
    local_candidates = [
        config.project_root / ".venv" / "Scripts" / "boss.exe",
        config.project_root / ".venv" / "Scripts" / "boss.cmd",
        config.project_root / ".venv" / "Scripts" / "boss",
        config.project_root / ".venv" / "bin" / "boss",
    ]
    for candidate in local_candidates:
        if candidate.exists():
            commands.append([str(candidate)])
    for name in ["boss", "boss-cli"]:
        resolved = shutil.which(name)
        if resolved:
            commands.append([resolved])
    return _dedupe_commands(commands)


def resolve_boss_cli_command(config: AppConfig) -> list[str] | None:
    candidates = _command_candidates(config)
    return candidates[0] if candidates else None


def format_boss_cli_command(command: list[str] | None) -> str:
    if not command:
        return ""
    return " ".join(shlex.quote(part) for part in command)


def boss_cli_available(config: AppConfig) -> bool:
    return resolve_boss_cli_command(config) is not None


def boss_cli_install_hint(config: AppConfig) -> str:
    local_python = config.project_root / ".venv" / "Scripts" / "python.exe"
    install_command = (
        f"{local_python} -m pip install kabi-boss-cli"
        if local_python.exists()
        else "python -m pip install kabi-boss-cli"
    )
    return (
        "未找到 boss-cli 命令。先在项目虚拟环境里安装 `kabi-boss-cli`，例如："
        f"`{install_command}`；"
        "安装后再执行 `boss login --cookie-source edge`。"
    )


def _extract_cli_error(payload: dict) -> str:
    detail = payload.get("error")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        for key in ["message", "detail", "error"]:
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ["message", "detail", "error"]:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def run_boss_cli_json(
    config: AppConfig,
    args: list[str],
    *,
    timeout_sec: int | None = None,
    allow_failure_payload: bool = False,
) -> dict:
    command = resolve_boss_cli_command(config)
    if not command:
        raise RuntimeError(boss_cli_install_hint(config))
    full_command = [*command, *args]
    timeout_value = timeout_sec or config.boss_cli_timeout_sec
    try:
        completed = subprocess.run(
            full_command,
            cwd=str(config.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_value,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(boss_cli_install_hint(config)) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"boss-cli 执行超时（>{timeout_value}s）。") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if not stdout:
        if allow_failure_payload and stderr:
            return {"ok": False, "error": stderr}
        if completed.returncode != 0 and stderr:
            raise RuntimeError(f"boss-cli 执行失败：{stderr}")
        raise RuntimeError("boss-cli 没有返回 JSON 输出。")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        excerpt = stdout[:300].replace("\n", " ")
        raise RuntimeError(f"boss-cli 返回了无法解析的 JSON：{excerpt}") from exc

    if allow_failure_payload:
        return payload
    if completed.returncode != 0 or payload.get("ok") is False:
        detail = _extract_cli_error(payload) or stderr or stdout[:300]
        raise RuntimeError(f"boss-cli 执行失败：{detail}")
    return payload


def unwrap_boss_cli_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def get_boss_cli_status(config: AppConfig) -> dict:
    command = resolve_boss_cli_command(config)
    status = {
        "available": bool(command),
        "command": format_boss_cli_command(command),
        "authenticated": None,
        "health": {},
        "error": "" if command else boss_cli_install_hint(config),
    }
    if not command:
        return status
    try:
        payload = run_boss_cli_json(
            config,
            ["status", "--json"],
            timeout_sec=min(config.boss_cli_timeout_sec, 30),
            allow_failure_payload=True,
        )
        data = unwrap_boss_cli_data(payload)
        health = data.get("health", {}) if isinstance(data.get("health"), dict) else {}
        authenticated = data.get("authenticated")
        if authenticated is None and health:
            auth_flags = [health.get("search_authenticated"), health.get("recommend_authenticated")]
            known = [flag for flag in auth_flags if isinstance(flag, bool)]
            if known:
                authenticated = any(known)
        status["authenticated"] = authenticated if isinstance(authenticated, bool) else None
        status["health"] = health
        if payload.get("ok") is False:
            status["error"] = _extract_cli_error(payload)
    except Exception as exc:
        status["error"] = str(exc)
    return status
