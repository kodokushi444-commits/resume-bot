from __future__ import annotations

import copy
import base64
import json
import math
import struct
import subprocess
import sys
import threading
import traceback
import time
import shutil
import zlib
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .boss_cli_bridge import format_boss_cli_command, resolve_boss_cli_command
from .config import load_config
from .debug_report import write_debug_report
from .fetch_funnel import build_fetch_funnel
from .fetch_source_rules import decorate_fetch_sources_for_settings, sanitize_selected_source_groups
from .job_sources.boss_common import (
    PROFILE_READY_MARKER,
    extract_page_snapshot,
    is_loading_page,
    is_security_verify_page,
    looks_like_login_page,
    resolve_cdp_endpoint,
    resolve_cdp_websocket_url,
)
from .local_action_log import append_action_log, read_recent_action_logs
from .local_web_assets import INDEX_HTML
from .llm import AnthropicVisionClient, MiniMaxAnthropicClient, OpenAICompatibleTextClient, VisionClient
from .pipeline import ResumeBotPipeline, normalize_boss_quick_filters
from .preferences import apply_manual_settings, remove_setting_value, settings_summary
from .resume_parser import render_profile_summary
from .runtime_checks import llm_runtime_warning
from .matching import heuristic_match, should_skip_job
from .xlsx_export import build_xlsx_workbook


class RefreshPayload(BaseModel):
    user_id: str = "me"
    fetch_jobs: bool = True
    selected_sources: list[str] = []
    fetch_limit: int = 40
    fetch_session_id: str = ""


class JobActionPayload(BaseModel):
    user_id: str = "me"
    action: str
    notes: str = ""


class FrontendLogPayload(BaseModel):
    user_id: str = "me"
    event: str
    detail: dict = {}


class RemoveSettingItemPayload(BaseModel):
    user_id: str = "me"
    field: str
    value: str


class ManualSettingsPayload(BaseModel):
    user_id: str = "me"
    preferred_roles: list[str] = []
    preferred_cities: list[str] = []
    preferred_keywords: list[str] = []
    excluded_keywords: list[str] = []
    job_scope: str = "campus_social"
    campus_role_mode: str = "full_time"
    salary_min: int = 0
    salary_max: int = 0
    max_degree_requirement: str = ""


class BossWorkbenchCapturePayload(BaseModel):
    user_id: str = "me"
    city: str = ""
    keyword: str = ""
    limit: int = 45
    rounds: int = 0
    review_limit: int = 5
    degree_filter: str = ""
    employment_mode_filter: str = ""


class BossWorkbenchSupplementPayload(BaseModel):
    user_id: str = "me"
    fetch_session_id: str = ""
    limit: int = 3
    review_limit: int = 5
    review_profile: str = ""


class AssistantBossStatusPayload(BaseModel):
    user_id: str = "me"
    fetch_session_id: str = ""
    review_limit: int = 5
    review_profile: str = ""


class AssistantBossCapturePayload(BossWorkbenchCapturePayload):
    pass


class AssistantBossSupplementPayload(BaseModel):
    user_id: str = "me"
    fetch_session_id: str = ""
    review_limit: int = 5
    review_profile: str = ""


class AIProviderSettingsPayload(BaseModel):
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    clear_api_key: bool = False


class AISettingsSavePayload(BaseModel):
    user_id: str = "me"
    text: AIProviderSettingsPayload = Field(default_factory=AIProviderSettingsPayload)
    vision: AIProviderSettingsPayload = Field(default_factory=AIProviderSettingsPayload)


class AISettingsTestPayload(BaseModel):
    user_id: str = "me"
    target: str = "text"
    text: AIProviderSettingsPayload = Field(default_factory=AIProviderSettingsPayload)
    vision: AIProviderSettingsPayload = Field(default_factory=AIProviderSettingsPayload)


class AISettingsModelsPayload(AISettingsTestPayload):
    pass


def _bool_ready(value: str) -> bool:
    return bool(value and value.strip())


def _mask_secret(value: str) -> dict:
    normalized = str(value or "").strip()
    if not normalized:
        return {"configured": False, "tail": ""}
    return {"configured": True, "tail": normalized[-4:] if len(normalized) >= 4 else normalized}


def _read_ai_settings_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ai_provider_to_response(config, section: str) -> dict:
    if section == "vision":
        provider = config.vision_provider
        base_url = config.vision_base_url
        model = config.vision_model
        secret = config.vision_api_key
    else:
        provider = config.llm_provider
        base_url = config.llm_base_url
        model = config.llm_model
        secret = config.llm_api_key
    masked = _mask_secret(secret)
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key_configured": masked["configured"],
        "api_key_tail": masked["tail"],
    }


def _safe_ai_settings_response(config) -> dict:
    return {
        "ok": True,
        "settings_path": str(config.ai_settings_path),
        "text": _ai_provider_to_response(config, "text"),
        "vision": _ai_provider_to_response(config, "vision"),
    }


def _merge_ai_provider_settings(existing: dict, payload: AIProviderSettingsPayload) -> dict:
    result = dict(existing or {})
    old_provider = str(result.get("provider", "") or "").strip()
    old_base_url = str(result.get("base_url", "") or "").strip()
    for field_name in ("provider", "base_url", "model"):
        value = str(getattr(payload, field_name, "") or "").strip()
        if value:
            result[field_name] = value
    api_key = str(payload.api_key or "").strip()
    if payload.clear_api_key:
        result.pop("api_key", None)
    elif api_key:
        result["api_key"] = api_key
    elif (
        str(result.get("provider", "") or "").strip() != old_provider
        or str(result.get("base_url", "") or "").strip() != old_base_url
    ):
        result.pop("api_key", None)
    return {key: value for key, value in result.items() if str(value or "").strip()}


def _write_ai_settings_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _effective_ai_provider_settings(config, section: str, payload: AIProviderSettingsPayload) -> dict:
    if section == "vision":
        current = {
            "provider": config.vision_provider,
            "base_url": config.vision_base_url,
            "model": config.vision_model,
            "api_key": config.vision_api_key,
        }
    else:
        current = {
            "provider": config.llm_provider,
            "base_url": config.llm_base_url,
            "model": config.llm_model,
            "api_key": config.llm_api_key,
        }
    effective = dict(current)
    for field_name in ("provider", "base_url", "model"):
        value = str(getattr(payload, field_name, "") or "").strip()
        if value:
            effective[field_name] = value
    if payload.clear_api_key:
        effective["api_key"] = ""
    elif str(payload.api_key or "").strip():
        effective["api_key"] = str(payload.api_key or "").strip()
    elif (
        str(effective.get("provider", "") or "").strip() != str(current.get("provider", "") or "").strip()
        or str(effective.get("base_url", "") or "").strip() != str(current.get("base_url", "") or "").strip()
    ):
        effective["api_key"] = ""
    return effective


def _model_list_url(provider: str, base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    provider_name = str(provider or "").strip().lower()
    if provider_name == "openai-compatible":
        if normalized.endswith("/chat/completions"):
            normalized = normalized[: -len("/chat/completions")]
        if normalized.endswith("/models"):
            return normalized
        return f"{normalized}/models"
    if provider_name in {"anthropic-compatible", "minimax-anthropic"}:
        if normalized.endswith("/v1/messages"):
            normalized = normalized[: -len("/messages")]
        if normalized.endswith("/v1/models"):
            return normalized
        return f"{normalized}/v1/models"
    raise RuntimeError("Provider must be openai-compatible, anthropic-compatible, or minimax-anthropic.")


def _model_list_headers(provider: str, api_key: str) -> dict[str, str]:
    provider_name = str(provider or "").strip().lower()
    if provider_name == "openai-compatible":
        return {"authorization": f"Bearer {api_key}"}
    headers = {"anthropic-version": "2023-06-01"}
    if provider_name == "minimax-anthropic":
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {api_key}"
    return headers


def _http_error_message(response: requests.Response) -> str:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text.strip()
        if len(body) > 800:
            body = body[:800] + "..."
        if body:
            return f"{exc}; response={body}"
        return str(exc)
    return ""


def _extract_model_items(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            raw_items = payload["data"]
        elif isinstance(payload.get("models"), list):
            raw_items = payload["models"]
        else:
            raw_items = []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    models: list[dict] = []
    for item in raw_items:
        if isinstance(item, str):
            models.append({"id": item, "name": "", "input": ""})
            continue
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        if not model_id:
            continue
        input_hint = item.get("input") or item.get("input_modalities") or item.get("modalities") or item.get("supported_input_modalities") or ""
        if isinstance(input_hint, list):
            input_hint = ", ".join(str(value) for value in input_hint)
        models.append(
            {
                "id": model_id,
                "name": str(item.get("display_name") or item.get("name") or "").strip(),
                "input": str(input_hint or "").strip(),
            }
        )
    return models


def _list_ai_models_with_payload(config, payload: AISettingsModelsPayload, target: str, started: float, log_event) -> dict:
    section = "vision" if target == "vision" else "text"
    settings = _effective_ai_provider_settings(config, section, getattr(payload, section))
    if not all(str(settings.get(key, "") or "").strip() for key in ("provider", "base_url", "api_key")):
        raise HTTPException(status_code=400, detail="Provider, Base URL, and API Key are required to list models.")
    provider = str(settings.get("provider", "") or "").strip().lower()
    url = _model_list_url(provider, str(settings.get("base_url", "") or ""))
    started_request = time.perf_counter()
    try:
        response = requests.get(
            url,
            headers=_model_list_headers(provider, str(settings.get("api_key", "") or "")),
            timeout=60,
        )
        error_message = _http_error_message(response)
        if error_message:
            raise RuntimeError(error_message)
        raw_payload = response.json()
        models = _extract_model_items(raw_payload)
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "ai_settings.models.ok",
            payload.user_id,
            target=target,
            provider=provider,
            duration_ms=duration_ms,
            model_count=len(models),
        )
        return {
            "ok": True,
            "target": target,
            "provider": provider,
            "models_url": url,
            "model_count": len(models),
            "models": models,
            "duration_ms": duration_ms,
            "request_ms": int((time.perf_counter() - started_request) * 1000),
        }
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "ai_settings.models.error",
            payload.user_id,
            target=target,
            provider=provider,
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


_OCR_TEST_FONT = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)


def _write_ocr_test_png(path: Path, text: str = "OCR TEST 527") -> None:
    scale = 10
    gap = 2
    glyph_w = 5
    glyph_h = 7
    margin = 18
    width = margin * 2 + len(text) * glyph_w * scale + max(0, len(text) - 1) * gap * scale
    height = margin * 2 + glyph_h * scale
    pixels = bytearray([255] * (width * height * 3))

    def set_pixel(x: int, y: int) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        index = (y * width + x) * 3
        pixels[index : index + 3] = b"\x00\x00\x00"

    cursor_x = margin
    for char in text.upper():
        glyph = _OCR_TEST_FONT.get(char, _OCR_TEST_FONT[" "])
        for row_index, row in enumerate(glyph):
            for col_index, bit in enumerate(row):
                if bit != "1":
                    continue
                start_x = cursor_x + col_index * scale
                start_y = margin + row_index * scale
                for y in range(start_y, start_y + scale):
                    for x in range(start_x, start_x + scale):
                        set_pixel(x, y)
        cursor_x += (glyph_w + gap) * scale

    raw = bytearray()
    row_bytes = width * 3
    for y in range(height):
        raw.append(0)
        start = y * row_bytes
        raw.extend(pixels[start : start + row_bytes])
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _test_ai_settings_with_payload(config, payload: AISettingsTestPayload, target: str, started: float, log_event) -> dict:
    try:
        if target == "vision":
            settings = _effective_ai_provider_settings(config, "vision", payload.vision)
            if not all(str(settings.get(key, "") or "").strip() for key in ("provider", "base_url", "model", "api_key")):
                raise RuntimeError("Vision model is not fully configured.")
            provider = str(settings.get("provider", "") or "").strip().lower()
            if provider == "openai-compatible":
                vision_client = VisionClient(
                    str(settings.get("base_url", "") or ""),
                    str(settings.get("api_key", "") or ""),
                    str(settings.get("model", "") or ""),
                    provider_name=provider,
                )
            elif provider in {"anthropic-compatible", "minimax-anthropic"}:
                vision_client = AnthropicVisionClient(
                    str(settings.get("base_url", "") or ""),
                    str(settings.get("api_key", "") or ""),
                    str(settings.get("model", "") or ""),
                    provider_name=provider,
                    auth_scheme="x-api-key" if provider == "minimax-anthropic" else "bearer",
                )
            else:
                raise RuntimeError("Vision provider must be openai-compatible or anthropic-compatible.")
            test_image = config.debug_dir / "ai_settings_vision_test.png"
            _write_ocr_test_png(test_image)
            reply = vision_client.extract_text(
                "Read the text in this image. Reply only with the visible text.",
                [test_image],
            )
        elif target == "text":
            settings = _effective_ai_provider_settings(config, "text", payload.text)
            if not all(str(settings.get(key, "") or "").strip() for key in ("provider", "base_url", "model", "api_key")):
                raise RuntimeError("Text model is not fully configured.")
            provider = str(settings.get("provider", "") or "").strip().lower()
            if provider == "minimax-anthropic":
                text_client = MiniMaxAnthropicClient(
                    str(settings.get("base_url", "") or ""),
                    str(settings.get("api_key", "") or ""),
                    str(settings.get("model", "") or ""),
                )
            elif provider == "anthropic-compatible":
                text_client = MiniMaxAnthropicClient(
                    str(settings.get("base_url", "") or ""),
                    str(settings.get("api_key", "") or ""),
                    str(settings.get("model", "") or ""),
                    auth_scheme="bearer",
                )
            elif provider == "openai-compatible":
                text_client = OpenAICompatibleTextClient(
                    str(settings.get("base_url", "") or ""),
                    str(settings.get("api_key", "") or ""),
                    str(settings.get("model", "") or ""),
                )
            else:
                raise RuntimeError("Text provider must be minimax-anthropic, anthropic-compatible, or openai-compatible.")
            reply = text_client.complete_text(
                "You are a health check assistant. Reply with OK only.",
                "Reply with OK only.",
                max_tokens=12,
            )
        else:
            raise RuntimeError("target must be text or vision.")
        if not str(reply or "").strip():
            raise RuntimeError("Model request succeeded but returned empty content.")
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event("ai_settings.test.ok", payload.user_id, target=target, duration_ms=duration_ms)
        return {"ok": True, "target": target, "reply": str(reply or "").strip(), "duration_ms": duration_ms}
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event("ai_settings.test.error", payload.user_id, target=target, duration_ms=duration_ms, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _recommended_boss_capture_rounds(limit: int, *, page_size: int = 15, max_rounds: int = 7) -> int:
    normalized_limit = max(1, int(limit or 1))
    normalized_page_size = max(1, int(page_size or 1))
    estimated_pages = max(1, math.ceil(normalized_limit / normalized_page_size))
    return max(0, min(estimated_pages - 1, max_rounds))


def _boss_stoploss_enabled() -> bool:
    return True


def _boss_stoploss_reason() -> str:
    return "BOSS 主线已临时停用：当前网页点击抓取会把结果页带回首页，存在账号风险。"


def _apply_boss_stoploss(items: list[dict]) -> list[dict]:
    if not _boss_stoploss_enabled():
        return items
    updated_items: list[dict] = []
    for item in items:
        if str(item.get("id", "") or "") != "boss":
            updated_items.append(item)
            continue
        updated = dict(item)
        updated["disabled"] = True
        updated["default_checked"] = False
        reason = _boss_stoploss_reason()
        existing_reason = str(updated.get("disabled_reason", "") or "").strip()
        updated["disabled_reason"] = reason if not existing_reason else f"{existing_reason} {reason}".strip()
        updated_items.append(updated)
    return updated_items


def _remove_disabled_selected_sources(selected_source_groups: list[str]) -> list[str]:
    if not _boss_stoploss_enabled():
        return selected_source_groups
    return [item for item in selected_source_groups if item != "boss"]


def _profile_ready(profile_dir: Path) -> bool:
    return profile_dir.exists() and (profile_dir / PROFILE_READY_MARKER).exists()


def _state_ready(state_path: Path) -> bool:
    return state_path.exists() and state_path.is_file() and state_path.stat().st_size > 2


def _candidate_windows_browser_paths(browser: str) -> list[Path]:
    home = Path.home()
    if browser == "chrome":
        return [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            home / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    return [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        home / "AppData" / "Local" / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]


def _preferred_boss_login_browser() -> tuple[str, str, str]:
    for browser, label in (("chrome", "Chrome"), ("edge", "Edge")):
        for candidate in _candidate_windows_browser_paths(browser):
            if candidate.exists():
                return browser, label, str(candidate)
    fallback = _candidate_windows_browser_paths("chrome")[0]
    return "chrome", "Chrome", str(fallback)


def _snapshot_from_target_payload(target: dict | None) -> dict:
    payload = target or {}
    url = str(payload.get("url", "") or "")
    title = str(payload.get("title", "") or "")
    body_text = ""
    lowered_url = url.lower()
    is_boss_domain = "zhipin.com" in lowered_url
    is_blank = (not url or url == "about:blank") and not title and not body_text.strip()
    is_security_verify = is_security_verify_page(url, title, body_text)
    has_body_text = bool(body_text.strip())
    is_loading = is_loading_page(url, title, body_text) if has_body_text else False
    if is_blank:
        page_state = "blank_page"
    elif not is_boss_domain:
        page_state = "unexpected_domain"
    elif is_security_verify:
        page_state = "security_verify"
    elif is_loading:
        page_state = "loading"
    elif looks_like_login_page(url, title, body_text):
        page_state = "login_required"
    else:
        page_state = "ready"
    return {
        "url": url,
        "title": title,
        "body_excerpt": "",
        "is_boss_domain": is_boss_domain,
        "is_blank": is_blank,
        "is_security_verify": is_security_verify,
        "is_loading": is_loading,
        "page_state": page_state,
        "logged_in": page_state == "ready",
    }


def _read_cdp_targets(base_url: str, timeout_seconds: float = 1.5) -> list[dict]:
    if not base_url or not base_url.startswith("http"):
        return []
    list_url = base_url.rstrip("/") + "/json/list"
    try:
        with urlopen(list_url, timeout=timeout_seconds) as response:
            if response.status != 200:
                return []
            payload = json.load(response)
    except URLError:
        return []
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _pick_boss_target_snapshot(targets: list[dict]) -> tuple[dict | None, dict | None]:
    fallback_boss: dict | None = None
    fallback_general: dict | None = None
    for target in reversed(targets):
        if str(target.get("type", "") or "") != "page":
            continue
        snapshot = _snapshot_from_target_payload(target)
        url = str(snapshot.get("url", "") or "")
        lowered_url = url.lower()
        if lowered_url.startswith("devtools://") or lowered_url.startswith("edge://") or lowered_url.startswith("chrome://"):
            continue
        if (
            "/web/geek/jobs" in url
            or "/web/geek/job" in url
            or snapshot.get("is_security_verify")
            or snapshot.get("page_state") == "login_required"
        ):
            return snapshot, fallback_general
        if snapshot.get("is_boss_domain") and fallback_boss is None:
            fallback_boss = snapshot
        if fallback_general is None:
            fallback_general = snapshot
    return fallback_boss, fallback_general


def _find_existing_boss_page(context):
    pages = list(getattr(context, "pages", []) or [])
    fallback_boss_page = None
    fallback_general_page = None
    for page in reversed(pages):
        snapshot = extract_page_snapshot(page)
        url = str(snapshot.get("url", "") or "")
        lowered_url = url.lower()
        if lowered_url.startswith("devtools://") or lowered_url.startswith("edge://") or lowered_url.startswith("chrome://"):
            continue
        if "/web/geek/jobs" in url or "/web/geek/job" in url or snapshot.get("is_security_verify"):
            return page
        if snapshot.get("is_boss_domain") and fallback_boss_page is None:
            fallback_boss_page = page
        if fallback_general_page is None:
            fallback_general_page = page
    return fallback_boss_page or fallback_general_page


def _inspect_boss_page_via_cdp(config) -> dict:
    result = {
        "snapshot": None,
        "cookie_authenticated": None,
        "cookie_names": [],
        "inspection_error": "",
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["inspection_error"] = "playwright_not_installed"
        return result
    cdp_endpoint = resolve_cdp_endpoint(
        config.boss_browser_cdp_port,
        config.boss_browser_cdp_url,
        timeout_seconds=1.0,
    )
    websocket_url = resolve_cdp_websocket_url(
        config.boss_browser_cdp_port,
        config.boss_browser_cdp_url,
        timeout_seconds=1.0,
    )
    endpoint = cdp_endpoint or websocket_url
    if not endpoint:
        result["inspection_error"] = "cdp_websocket_unavailable"
        return result
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            for context in list(getattr(browser, "contexts", []) or []):
                page = _find_existing_boss_page(context)
                if page is None:
                    continue
                snapshot = extract_page_snapshot(page)
                result["snapshot"] = snapshot
                try:
                    cookies = context.cookies([snapshot.get("url", "")] if snapshot.get("url") else None)
                except Exception:
                    cookies = []
                cookie_names = sorted(
                    {
                        str(item.get("name", "") or "").strip()
                        for item in cookies
                        if isinstance(item, dict) and str(item.get("name", "") or "").strip()
                    }
                )
                result["cookie_names"] = [name for name in cookie_names if name in {"wt2", "__zp_stoken__"}]
                if cookies:
                    names = set(cookie_names)
                    result["cookie_authenticated"] = "wt2" in names and "__zp_stoken__" in names
                return result
    except Exception as exc:
        result["inspection_error"] = str(exc)
    return result


def _classify_boss_gate_status(probe: dict) -> str:
    if not probe.get("browser_connected"):
        return "no_browser"
    if not probe.get("has_boss_page"):
        return "boss_page_missing"
    page_state = str(probe.get("page_state", "") or "")
    if page_state == "security_verify":
        return "security_verify"
    if page_state == "login_required":
        return "login_required"
    if page_state == "unexpected_domain":
        return "boss_page_missing"
    if page_state in {"loading", "blank_page"}:
        return "uncertain"
    if page_state == "ready":
        page_url = str(probe.get("page_url", "") or "")
        if "/web/geek/jobs" not in page_url and "/web/geek/job" not in page_url:
            return "results_page_required"
        if probe.get("cookie_authenticated") is False:
            return "uncertain"
        return "ready"
    return "uncertain"


def _boss_launch_command(config) -> tuple[list[str], str, str]:
    python_executable = sys.executable or "python"
    script_path = config.project_root / "scripts" / "capture_boss_session.py"
    browser, browser_label, _browser_path = _preferred_boss_login_browser()
    command = [
        python_executable,
        str(script_path),
        "launch-windows-browser",
        "--browser",
        browser,
    ]
    return command, subprocess.list2cmdline(command), browser_label


def _apply_boss_gate_copy(payload: dict, *, status: str, login_browser_label: str, probe: dict) -> dict:
    updated = dict(payload or {})
    updated["action_kind"] = updated.get("action_kind", "")
    updated["action_label"] = updated.get("action_label", "")
    if status == "ready":
        updated.update(
            {
                "can_start": True,
                "badge": "可以开始",
                "title": "可以开始抓取",
                "message": "BOSS 状态正常，可以开始抓取。",
                "action_hint": "现在可以点“导入/抓取并推荐”。",
            }
        )
    elif status == "no_browser":
        updated.update(
            {
                "can_start": False,
                "badge": "未连登录浏览器",
                "title": "没有连接到登录浏览器",
                "message": f"还没连到登录浏览器。这里认的不是你平时普通打开的 {login_browser_label}。",
                "action_hint": f"先点“打开登录浏览器”，在弹出的 {login_browser_label} 里打开并登录 BOSS，然后再点“重新检查”。",
                "action_kind": "launch_browser",
                "action_label": "打开登录浏览器",
            }
        )
    elif status == "boss_page_missing":
        updated.update(
            {
                "can_start": False,
                "badge": "未开 BOSS",
                "title": "没有打开 BOSS 页面",
                "message": "登录浏览器已经连上了，但当前不是 BOSS 页面。先切到 BOSS 页面。",
                "action_hint": "把登录浏览器切到 BOSS 页面后，再点“重新检查”。",
            }
        )
    elif status == "results_page_required":
        updated.update(
            {
                "can_start": False,
                "badge": "Logged In",
                "title": "BOSS 已登录",
                "message": "当前不是职位结果页，但这不会阻止新工作台采集，也不会阻止 JD 补抓。",
                "action_hint": "如果你只是要继续用新工作台采集或补抓 JD，可以停在当前已登录页面；只有手动浏览结果时才需要自己打开结果页。",
            }
        )
    elif status == "login_required":
        updated.update(
            {
                "can_start": False,
                "badge": "需要登录",
                "title": "需要先登录",
                "message": "已经打开 BOSS 了，但你还没登录。先登录，再回来。",
                "action_hint": "登录完成后点“重新检查”，显示“可以开始抓取”再点抓取。",
            }
        )
    elif status == "security_verify":
        updated.update(
            {
                "can_start": False,
                "badge": "安全验证",
                "title": "命中安全验证",
                "message": "BOSS 触发了安全验证。现在先不要继续抓。",
                "action_hint": "先不要继续抓，稍后再试；如果页面要求人工验证，先在浏览器里处理。",
            }
        )
    else:
        message = "当前状态还不够确定，先不要抓。建议刷新页面后重新检查。"
        if probe.get("page_state") == "loading":
            message = "BOSS 页面还没稳定下来，先不要抓。等页面稳定后再重新检查。"
        elif probe.get("page_state") == "blank_page":
            message = "当前页面是空白页，先不要抓。建议重新打开页面后再试。"
        updated.update(
            {
                "can_start": False,
                "badge": "先别抓",
                "title": "状态不明确，先不要抓",
                "message": message,
                "action_hint": "先点“重新检查”；只有显示“可以开始抓取”后再点抓取。",
            }
        )
    updated["summary"] = f"{updated['title']}：{updated['message']}"
    return updated


def _boss_gate_presenter(status: str, probe: dict) -> dict:
    checked_at = probe.get("checked_at") or datetime.now().isoformat(timespec="seconds")
    login_browser_label = str(probe.get("login_browser_label", "") or "Chrome")
    details = {
        "checked_at": checked_at,
        "browser_connected": bool(probe.get("browser_connected")),
        "cdp_url": probe.get("cdp_url", ""),
        "has_boss_page": bool(probe.get("has_boss_page")),
        "page_state": probe.get("page_state", ""),
        "page_url": probe.get("page_url", ""),
        "page_title": probe.get("page_title", ""),
        "cookie_authenticated": probe.get("cookie_authenticated"),
        "cookie_names": probe.get("cookie_names", []),
        "page_source": probe.get("page_source", ""),
        "inspection_error": probe.get("inspection_error", ""),
        "browser_profile_ready": bool(probe.get("browser_profile_ready")),
        "browser_state_ready": bool(probe.get("browser_state_ready")),
        "boss_cli_available": bool(probe.get("boss_cli_available")),
        "boss_cli_command": probe.get("boss_cli_command", ""),
        "login_command": probe.get("login_command", ""),
        "login_browser_label": login_browser_label,
    }
    payload = {
        "status": status,
        "checked_at": checked_at,
        "details": details,
        "can_start": False,
        "badge": "先别抓",
        "title": "状态不明确，先不要抓",
        "message": "当前状态不够确定，先不要抓。建议刷新页面后重新检查。",
        "action_hint": "点“重新检查”确认状态；只有显示“可以开始抓取”后再点抓取。",
        "action_kind": "",
        "action_label": "",
    }
    if status == "ready":
        payload.update(
            {
                "can_start": True,
                "badge": "可以开始",
                "title": "可以开始抓取",
                "message": "BOSS 状态正常，可以开始抓取。",
                "action_hint": "现在可以点“导入/抓取并推荐”。",
            }
        )
    elif status == "no_browser":
        payload.update(
            {
                "badge": "未连登录浏览器",
                "title": "没有连接到登录浏览器",
                "message": "还没连到登录浏览器。这里认的不是你平时普通打开的 Edge。",
                "action_hint": "先点“打开登录浏览器”，在弹出的浏览器里打开并登录 BOSS，然后再点“重新检查”。",
                "action_kind": "launch_browser",
                "action_label": "打开登录浏览器",
            }
        )
    elif status == "boss_page_missing":
        payload.update(
            {
                "badge": "未开 BOSS",
                "title": "没有打开 BOSS 页面",
                "message": "登录浏览器已经连上了，但当前不是 BOSS 页面。先切到 BOSS 页面。",
                "action_hint": "把登录浏览器切到 BOSS 页面后，再点“重新检查”。",
            }
        )
    elif status == "login_required":
        payload.update(
            {
                "badge": "需要登录",
                "title": "需要先登录",
                "message": "已经打开 BOSS 了，但你还没登录。先登录，再回来。",
                "action_hint": "登录完成后点“重新检查”，显示“可以开始抓取”再点抓取。",
            }
        )
    elif status == "security_verify":
        payload.update(
            {
                "badge": "安全验证",
                "title": "命中安全验证",
                "message": "BOSS 触发了安全验证。现在先不要继续抓。",
                "action_hint": "先不要继续抓，稍后再试；如果页面要求人工验证，先在浏览器里处理。",
            }
        )
    elif status == "uncertain":
        if probe.get("page_state") == "loading":
            payload["message"] = "BOSS 页面还没稳定下来，先不要抓。等页面稳定后再重新检查。"
        elif probe.get("page_state") == "blank_page":
            payload["message"] = "当前页面是空白页，先不要抓。建议重新打开页面后再试。"
    payload["summary"] = f"{payload['title']}：{payload['message']}"
    return _apply_boss_gate_copy(payload, status=status, login_browser_label=login_browser_label, probe=probe)


def _boss_gate_status(config) -> dict:
    command = resolve_boss_cli_command(config)
    available = bool(command)
    command_text = format_boss_cli_command(command) if command else ""
    _launch_command, launch_command_text, login_browser_label = _boss_launch_command(config)
    cdp_url = resolve_cdp_endpoint(
        config.boss_browser_cdp_port,
        config.boss_browser_cdp_url,
        timeout_seconds=1.0,
    )
    profile_ready = _profile_ready(config.boss_browser_profile_dir)
    state_ready = _state_ready(config.boss_browser_state_path)
    targets = _read_cdp_targets(cdp_url, timeout_seconds=1.0)
    boss_snapshot, general_snapshot = _pick_boss_target_snapshot(targets)
    probe = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "browser_connected": bool(cdp_url),
        "cdp_url": cdp_url,
        "browser_profile_ready": profile_ready,
        "browser_state_ready": state_ready,
        "boss_cli_available": available,
        "boss_cli_command": command_text,
        "login_command": launch_command_text,
        "login_browser_label": login_browser_label,
        "has_boss_page": bool(boss_snapshot and boss_snapshot.get("is_boss_domain")),
        "page_state": "",
        "page_url": "",
        "page_title": "",
        "page_source": "",
        "cookie_authenticated": None,
        "cookie_names": [],
        "inspection_error": "",
    }
    selected_snapshot = boss_snapshot or general_snapshot
    if selected_snapshot:
        probe.update(
            {
                "page_state": selected_snapshot.get("page_state", ""),
                "page_url": selected_snapshot.get("url", ""),
                "page_title": selected_snapshot.get("title", ""),
                "page_source": "cdp_target",
            }
        )
    if cdp_url and not selected_snapshot:
        cdp_inspection = _inspect_boss_page_via_cdp(config)
        inspected_snapshot = cdp_inspection.get("snapshot")
        if inspected_snapshot:
            probe.update(
                {
                    "has_boss_page": bool(inspected_snapshot.get("is_boss_domain")),
                    "page_state": inspected_snapshot.get("page_state", ""),
                    "page_url": inspected_snapshot.get("url", ""),
                    "page_title": inspected_snapshot.get("title", ""),
                    "page_source": "playwright_cdp",
                }
            )
        if cdp_inspection.get("cookie_authenticated") is not None:
            probe["cookie_authenticated"] = cdp_inspection.get("cookie_authenticated")
        if cdp_inspection.get("cookie_names"):
            probe["cookie_names"] = cdp_inspection.get("cookie_names", [])
        if cdp_inspection.get("inspection_error"):
            probe["inspection_error"] = cdp_inspection.get("inspection_error", "")
    gate = _boss_gate_presenter(_classify_boss_gate_status(probe), probe)
    return {
        "ready": gate["can_start"],
        "summary": gate["summary"],
        "badge": gate["badge"],
        "login_command": probe["login_command"],
        "available": available,
        "browser_profile_ready": profile_ready,
        "browser_state_ready": state_ready,
        "browser_cdp_ready": bool(cdp_url),
        "browser_cdp_url": cdp_url,
        "gate": gate,
    }


def _match_to_view_model(pipeline: ResumeBotPipeline, user_id: str, item) -> dict:
    payload = item.to_dict()
    payload["last_action"] = pipeline.store.last_action_for_job(user_id, item.job.fingerprint)
    payload["decision_status"] = "hit"
    payload["skip_reason"] = ""
    payload.update(ResumeBotPipeline._application_status_metadata(item.job))
    return payload


SOURCE_LABELS = {
    "nowcoder_direct": "牛客",
    "nowcoder_schedule": "牛客校招日程",
    "nowcoder_tavily": "牛客",
    "boss_cli": "BOSS",
    "boss_browser": "BOSS",
    "company_watchlist": "公司关注",
}


def _source_label(source_name: str) -> str:
    return SOURCE_LABELS.get(source_name, source_name)


def _application_status_label(status: str) -> str:
    normalized = str(status or "").lower()
    if normalized == "open":
        return "可投递"
    if normalized == "closed":
        return "已结束"
    if normalized == "pending":
        return "待上线"
    return "状态未确认"


def _employment_mode_label(value: str) -> str:
    normalized = str(value or "").lower()
    if normalized == "intern":
        return "实习"
    if normalized == "full_time":
        return "正职"
    return ""


def _compact_summary(text: str, limit: int = 120) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _explain_job_decision(pipeline: ResumeBotPipeline, user_id: str, job, ranked_by_fingerprint: dict, settings, profile) -> dict:
    payload = job.to_dict()
    payload["last_action"] = pipeline.store.last_action_for_job(user_id, job.fingerprint)
    payload["source_label"] = _source_label(job.source)
    payload["application_status_label"] = _application_status_label(job.application_status)
    payload["employment_mode_label"] = _employment_mode_label(job.employment_mode)
    match = ranked_by_fingerprint.get(job.fingerprint)
    if match:
        payload["recommended"] = True
        payload["recommendation_score"] = match.score
        payload["recommendation_reasons"] = match.reasons
        payload["skip_reason"] = ""
        return payload

    last_action = payload["last_action"]
    skip, reason = should_skip_job(job, settings, last_action=last_action)
    candidate_score = 0.0
    candidate_reasons: list[str] = []
    if not skip:
        heuristic = heuristic_match(job, profile, settings)
        if heuristic:
            candidate_score = heuristic.score
            candidate_reasons = heuristic.reasons
            reason = "已进入候选但未进入当前展示结果"
        elif pipeline.store.was_pushed(user_id, job.fingerprint, job.content_hash) and not settings.allow_repush_when_updated:
            reason = "岗位已推送且不允许重复发送"
        else:
            reason = "规则评分未通过"
    payload["recommended"] = False
    payload["recommendation_score"] = candidate_score
    payload["recommendation_reasons"] = candidate_reasons
    payload["skip_reason"] = reason
    return payload


def _decorate_job_collection(pipeline: ResumeBotPipeline, user_id: str, jobs: list, ranked: list, settings, profile) -> list[dict]:
    ranked_by_fingerprint = {item.job.fingerprint: item for item in ranked}
    return [
        _explain_job_decision(pipeline, user_id, job, ranked_by_fingerprint, settings, profile)
        for job in jobs
    ]


def _build_extraction_info(profile) -> dict:
    if not profile:
        return {}
    raw = profile.raw_sections or {}
    return {
        "file_type": raw.get("_extraction_file_type", ""),
        "extraction_method": raw.get("_extraction_method", ""),
        "parser_backend": raw.get("_extraction_backend", ""),
        "route_name": raw.get("_extraction_route_name", ""),
        "route_summary": raw.get("_extraction_route_summary", ""),
        "route_reason": raw.get("_extraction_route_reason", ""),
        "provider_used": raw.get("_extraction_provider_used", ""),
        "quality_score": raw.get("_extraction_quality_score", ""),
        "quality_flags": raw.get("_extraction_quality_flags", ""),
        "parse_method": raw.get("_parse_method", ""),
        "parse_warning": raw.get("_parse_warning", ""),
    }


def _parse_source_run_detail(run: dict) -> dict:
    raw_detail = run.get("detail_json", {})
    if isinstance(raw_detail, dict):
        return raw_detail
    if not raw_detail:
        return {}
    try:
        payload = json.loads(str(raw_detail))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pick_first_nonempty(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_subprocess_json_output(output_text: str) -> dict:
    text = str(output_text or "").strip()
    if not text:
        raise ValueError("BOSS 采集子程序没有返回结果。请先关闭黑窗口，重新双击 start_resume_bot.cmd，等依赖安装完成后再试。")
    try:
        payload = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            excerpt = " ".join(text.split())[:500]
            detail = f" 原始输出：{excerpt}" if excerpt else ""
            raise ValueError(
                "BOSS 采集子程序启动失败，网页拿不到可解析结果。"
                "常见原因是依赖没有安装完整，或登录浏览器没有正常连上。"
                "请先重新运行 start_resume_bot.cmd；如果仍失败，把这个错误截图发给维护者。"
                + detail
            ) from None
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("BOSS 采集子程序返回格式不正确。请重新启动后再试。")
    return payload


def _build_boss_workbench_capture_defaults(settings, recent_runs: list[dict] | None = None) -> dict:
    runs = list(recent_runs or [])
    recent_city = _pick_first_nonempty(*(str(item.get("city", "") or "") for item in runs))
    recent_keyword = _pick_first_nonempty(*(str(item.get("keyword", "") or "") for item in runs))
    recent_degree_filter = _pick_first_nonempty(
        *(str((item.get("quick_filters") or {}).get("degree_filter", "") or "") for item in runs)
    )
    recent_employment_mode_filter = _pick_first_nonempty(
        *(str((item.get("quick_filters") or {}).get("employment_mode_filter", "") or "") for item in runs)
    )
    preferred_roles = list(getattr(settings, "preferred_roles", []) or [])
    preferred_keywords = list(getattr(settings, "preferred_keywords", []) or [])
    preferred_cities = list(getattr(settings, "preferred_cities", []) or [])
    city = _pick_first_nonempty(recent_city, *(preferred_cities or []), "深圳")
    keyword = _pick_first_nonempty(
        recent_keyword,
        *(preferred_roles or []),
        *(preferred_keywords or []),
        "运营",
    )
    settings_employment_filter = getattr(settings, "campus_role_mode", "") or ""
    if settings_employment_filter not in {"full_time", "intern"}:
        settings_employment_filter = ""
    return {
        "city": city,
        "keyword": keyword,
        "limit": 45,
        "rounds": 2,
        "degree_filter": _pick_first_nonempty(recent_degree_filter, getattr(settings, "max_degree_requirement", "")),
        "employment_mode_filter": _pick_first_nonempty(recent_employment_mode_filter, settings_employment_filter),
    }


def _boss_workbench_capture_block_reason(gate: dict) -> str:
    status = str((gate or {}).get("status", "") or "")
    if status in {"ready", "results_page_required"}:
        return ""
    title = str((gate or {}).get("title", "") or "BOSS 状态不允许工作台采集")
    message = str((gate or {}).get("message", "") or "")
    return f"{title}：{message}".strip("：")


def _run_boss_workbench_capture(
    config,
    pipeline: ResumeBotPipeline,
    user_id: str,
    *,
    city: str,
    keyword: str,
    limit: int,
    rounds: int,
    degree_filter: str = "",
    employment_mode_filter: str = "",
) -> dict:
    python_executable = sys.executable or "python"
    script_path = config.project_root / "scripts" / "boss_cdp_list_probe.py"
    fetch_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    quick_filters = normalize_boss_quick_filters(
        {
            "degree_filter": degree_filter,
            "employment_mode_filter": employment_mode_filter,
        }
    )
    command = [
        python_executable,
        str(script_path),
        "--city",
        city,
        "--keyword",
        keyword,
        "--limit",
        str(limit),
        "--rounds",
        str(rounds),
        "--timeout",
        "20",
        "--fetch-session-id",
        fetch_session_id,
        "--pretty",
    ]
    if quick_filters["degree_filter"]:
        command.extend(["--degree-filter", quick_filters["degree_filter"]])
    if quick_filters["employment_mode_filter"]:
        command.extend(["--employment-mode-filter", quick_filters["employment_mode_filter"]])
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(config.project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(60, 25 + max(rounds, 0) * 15),
        check=False,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout_text = str(completed.stdout or "").strip()
    stderr_text = str(completed.stderr or "").strip()
    payload = _parse_subprocess_json_output(stdout_text or stderr_text)
    if completed.returncode != 0 or not payload.get("ok"):
        error_text = _pick_first_nonempty(
            str(payload.get("error", "") or ""),
            stderr_text,
            stdout_text,
            f"boss_cdp_list_probe exited with {completed.returncode}",
        )
        raise RuntimeError(error_text)
    artifact_path_text = str(payload.get("output_path", "") or "").strip()
    if not artifact_path_text:
        raise RuntimeError("BOSS queue capture finished without an output artifact.")
    artifact_path = Path(artifact_path_text)
    import_result = pipeline.import_boss_queue_artifact(user_id, artifact_path, quick_filters=quick_filters)
    return {
        "capture": payload,
        "import": import_result,
        "duration_ms": duration_ms,
        "fetch_session_id": str(import_result.get("fetch_session_id", "") or fetch_session_id),
        "quick_filters": quick_filters,
        "command": subprocess.list2cmdline(command),
    }


def _build_boss_workbench_summary(
    pipeline: ResumeBotPipeline,
    user_id: str,
    *,
    preferred_fetch_session_id: str | None = None,
) -> dict:
    settings = pipeline.store.get_settings(user_id)
    source_runs = pipeline.store.list_recent_source_runs(limit=8, source_names=["boss_browser"])
    recent_runs: list[dict] = []
    candidate_fetch_session_ids: list[str] = []
    seen_fetch_session_ids: set[str] = set()
    explicit_fetch_session_id = str(preferred_fetch_session_id or "").strip()
    imported_fetch_session_ids: list[str] = []
    other_fetch_session_ids: list[str] = []

    def _push_candidate(fetch_session_id: str) -> None:
        value = str(fetch_session_id or "").strip()
        if not value or value in seen_fetch_session_ids:
            return
        seen_fetch_session_ids.add(value)
        candidate_fetch_session_ids.append(value)

    for run in source_runs:
        detail = _parse_source_run_detail(run)
        status = str(run.get("status", "") or "")
        fetch_session_id = str(detail.get("fetch_session_id", "") or "").strip()
        quick_filters = normalize_boss_quick_filters(detail.get("quick_filters") or {})
        if fetch_session_id:
            if status == "imported":
                imported_fetch_session_ids.append(fetch_session_id)
            else:
                other_fetch_session_ids.append(fetch_session_id)
        recent_runs.append(
            {
                "source_name": str(run.get("source_name", "") or ""),
                "status": status,
                "started_at": str(run.get("started_at", "") or ""),
                "finished_at": str(run.get("finished_at", "") or ""),
                "fetch_session_id": fetch_session_id,
                "job_count": int(
                    detail.get("job_count", detail.get("count", detail.get("discovered_job_count", 0))) or 0
                ),
                "city": str(detail.get("city", "") or ""),
                "keyword": str(detail.get("keyword", "") or ""),
                "stop_reason": str(detail.get("stop_reason", "") or ""),
                "raw_job_count": int(detail.get("raw_job_count", 0) or 0),
                "quick_filters": quick_filters,
                "url_filter_params": detail.get("url_filter_params", {}) if isinstance(detail.get("url_filter_params"), dict) else {},
                "url_filter_applied": bool(detail.get("url_filter_applied", False)),
                "local_filter": detail.get("local_filter", {}) if isinstance(detail.get("local_filter"), dict) else {},
            }
        )

    _push_candidate(explicit_fetch_session_id)
    for fetch_session_id in imported_fetch_session_ids:
        _push_candidate(fetch_session_id)
    for fetch_session_id in other_fetch_session_ids:
        _push_candidate(fetch_session_id)

    latest_fetch_session_id = candidate_fetch_session_ids[0] if candidate_fetch_session_ids else ""
    available_review_profiles: list[dict] = []
    if latest_fetch_session_id:
        profiles = pipeline.list_review_profiles(
            user_id,
            selected_source_names=["boss_browser"],
            fetch_session_id=latest_fetch_session_id,
        )
        available_review_profiles = profiles

    capture_defaults = _build_boss_workbench_capture_defaults(settings, recent_runs)

    return {
        "available": bool(latest_fetch_session_id),
        "source_name": "boss_browser",
        "latest_fetch_session_id": latest_fetch_session_id,
        "available_review_profiles": available_review_profiles,
        "recent_source_runs": recent_runs,
        "capture_defaults": capture_defaults,
    }


def _decorate_recent_jobs(pipeline: ResumeBotPipeline, user_id: str, jobs: list, ranked: list, settings, profile) -> list[dict]:
    return _decorate_job_collection(pipeline, user_id, jobs[:20], ranked, settings, profile)


def _job_export_row(item: dict) -> list[object]:
    city = "/".join(item.get("city_list") or ([] if not item.get("city") else [item["city"]]))
    return [
        "推荐" if item.get("recommended") else "未推荐",
        item.get("source_label", ""),
        item.get("company_name", ""),
        item.get("title", ""),
        city,
        item.get("job_type", ""),
        item.get("employment_mode_label", ""),
        item.get("salary_text", ""),
        item.get("degree_requirement") or item.get("degree_preference") or "",
        item.get("application_status_label", ""),
        item.get("deadline", ""),
        round(float(item.get("recommendation_score") or 0), 1) if item.get("recommendation_score") else "",
        "；".join(item.get("recommendation_reasons") or []),
        item.get("skip_reason", ""),
        item.get("apply_url") or item.get("url") or "",
        _compact_summary(item.get("description", "")),
    ]


def _job_export_column_widths() -> list[float]:
    return [8, 8, 12, 18, 9, 8, 8, 10, 8, 10, 10, 7, 14, 14, 18, 20]


def _build_dashboard_payload(pipeline: ResumeBotPipeline, user_id: str) -> dict:
    return _build_dashboard_payload_with_state(
        pipeline,
        user_id,
        last_job_refresh=None,
        selected_source_names=None,
        selected_source_groups=None,
        selected_fetch_session_id=None,
        current_fetch_limit=40,
    )


def _build_dashboard_payload_with_state(
    pipeline: ResumeBotPipeline,
    user_id: str,
    *,
    last_job_refresh: dict | None,
    selected_source_names: list[str] | None,
    selected_source_groups: list[str] | None,
    selected_fetch_session_id: str | None,
    current_fetch_limit: int,
) -> dict:
    config = pipeline.config
    frontend_log_path = config.debug_dir / "frontend_action_log.jsonl"
    profile, _raw_text = pipeline.store.get_active_resume(user_id)
    settings = pipeline.store.get_settings(user_id)
    ranked, ranking_debug = pipeline._rank_jobs_with_debug(
        user_id,
        selected_source_names=selected_source_names,
        fetch_session_id=selected_fetch_session_id,
        allow_llm_rerank=False,
    )
    recommendations = [_match_to_view_model(pipeline, user_id, item) for item in ranked[:30]]
    extraction_info = _build_extraction_info(profile)
    active_sources = pipeline.active_source_names(user_id, selected_source_names)
    source_runs = pipeline.store.list_recent_source_runs(limit=10, source_names=active_sources)
    interactions = pipeline.store.list_recent_interactions(user_id, limit=20)
    delivery_history = pipeline.store.list_delivery_history(user_id, limit=20)
    resume_record = pipeline.store.get_active_resume_record(user_id)
    all_jobs = [
        job
        for job in pipeline.load_active_jobs(user_id, selected_source_names, fetch_session_id=selected_fetch_session_id)
        if job.application_status == "open"
    ]
    latest_source_report = source_runs[0] if source_runs else {}
    recent_frontend_logs = read_recent_action_logs(frontend_log_path, limit=12)
    recent_jobs = _decorate_recent_jobs(pipeline, user_id, all_jobs, ranked, settings, profile)
    available_fetch_sources = _apply_boss_stoploss(
        decorate_fetch_sources_for_settings(pipeline.available_fetch_sources(), settings)
    )
    if selected_source_groups is None:
        selected_fetch_sources = [item["id"] for item in available_fetch_sources if item.get("default_checked")]
    else:
        selected_fetch_sources, _dropped_sources = sanitize_selected_source_groups(selected_source_groups, settings)
        selected_fetch_sources = _remove_disabled_selected_sources(selected_fetch_sources)

    llm_ready = all(
        [
            _bool_ready(config.llm_api_key),
            _bool_ready(config.llm_base_url),
            _bool_ready(config.llm_model),
            _bool_ready(config.llm_provider),
        ]
    )
    vision_ready = all(
        [
            _bool_ready(config.vision_api_key),
            _bool_ready(config.vision_base_url),
            _bool_ready(config.vision_model),
            _bool_ready(config.vision_provider),
        ]
    )
    llm_warning = llm_runtime_warning(config)
    boss_status = _boss_gate_status(config)
    last_refresh_fetch_report = (last_job_refresh or {}).get("fetch_report", {}) if isinstance(last_job_refresh, dict) else {}
    last_refresh_ranking_debug = (last_job_refresh or {}).get("ranking_debug", {}) if isinstance(last_job_refresh, dict) else {}
    last_refresh_recommendation_count = int((last_job_refresh or {}).get("recommendation_count", 0) or 0) if isinstance(last_job_refresh, dict) else 0
    fetch_funnel = (
        build_fetch_funnel(last_refresh_fetch_report, last_refresh_ranking_debug, last_refresh_recommendation_count)
        if last_job_refresh
        else build_fetch_funnel({}, ranking_debug, len(recommendations))
    )
    boss_workbench = _build_boss_workbench_summary(
        pipeline,
        user_id,
        preferred_fetch_session_id=selected_fetch_session_id,
    )
    return {
        "has_resume": profile is not None,
        "resume_file_name": resume_record.get("file_name", "") if resume_record else "",
        "resume_created_at": resume_record.get("created_at", "") if resume_record else "",
        "profile": profile.to_dict() if profile else {},
        "profile_text": render_profile_summary(profile) if profile else "还没有解析结果。",
        "extraction_info": extraction_info,
        "settings": settings.to_dict(),
        "settings_text": settings_summary(settings),
        "recommendations": recommendations,
        "ranking_debug": ranking_debug,
        "source_runs": source_runs,
        "interactions": interactions,
        "delivery_history": delivery_history,
        "frontend_logs": recent_frontend_logs,
        "job_count": len(all_jobs),
        "last_fetch_report": latest_source_report,
        "last_job_refresh": last_job_refresh or {},
        "recent_jobs": recent_jobs,
        "available_fetch_sources": available_fetch_sources,
        "selected_fetch_sources": selected_fetch_sources,
        "selected_fetch_session_id": selected_fetch_session_id or "",
        "current_fetch_limit": current_fetch_limit,
        "fetch_funnel": fetch_funnel,
        "boss_workbench": boss_workbench,
        "status": {
            "llm_ready": llm_ready,
            "llm_summary": config.llm_model or "未配置",
            "llm_warning": llm_warning,
            "vision_ready": vision_ready,
            "vision_summary": config.vision_model or "未配置",
            "tavily_ready": _bool_ready(config.tavily_api_key),
            "boss_ready": boss_status["ready"],
            "boss_summary": boss_status["summary"],
            "boss_badge": boss_status["badge"],
            "boss_cli_available": boss_status["available"],
            "boss_profile_ready": boss_status["browser_profile_ready"],
            "boss_state_ready": boss_status["browser_state_ready"],
            "boss_cdp_ready": boss_status["browser_cdp_ready"],
            "boss_cdp_url": boss_status["browser_cdp_url"],
            "data_dir": str(config.data_dir),
            "db_path": str(config.db_path),
            "source_registry_path": str(config.source_registry_path),
            "boss_login_command": boss_status["login_command"],
            "boss_gate": boss_status["gate"],
            "boss_gate_status": boss_status["gate"]["status"],
            "boss_gate_title": boss_status["gate"]["title"],
            "boss_gate_message": boss_status["gate"]["message"],
            "boss_gate_badge": boss_status["gate"]["badge"],
            "boss_gate_can_start": boss_status["gate"]["can_start"],
            "boss_gate_action_hint": boss_status["gate"]["action_hint"],
            "boss_gate_details": boss_status["gate"]["details"],
            "boss_gate_source_groups": ["boss"],
            "web_command": "python scripts/run_local_web.py",
            "frontend_log_path": str(frontend_log_path),
        },
    }


def _build_job_refresh_summary(
    *,
    fetch_jobs: bool,
    fetch_report: dict,
    ranking_debug: dict,
    recommendation_count: int,
    selected_source_labels: list[str],
    fetch_limit: int,
) -> str:
    source_label_text = "、".join(selected_source_labels) if selected_source_labels else "默认来源"
    if not fetch_jobs:
        return (
            f"推荐重算完成（{source_label_text}）：当前入库 {ranking_debug.get('total_jobs', 0)} 条，"
            f"命中 {ranking_debug.get('matched_after_rerank', 0)} 条，"
            f"页面展示 {recommendation_count} 条。"
        )
    upsert = fetch_report.get("upsert", {}) if isinstance(fetch_report, dict) else {}
    source_count = len(fetch_report.get("sources", [])) if isinstance(fetch_report, dict) else 0
    total_jobs = int(fetch_report.get("total_jobs", 0)) if isinstance(fetch_report, dict) else 0
    funnel = build_fetch_funnel(fetch_report, ranking_debug, recommendation_count)
    return (
        f"抓取完成（{source_label_text}，本轮目标 {fetch_limit or total_jobs} 条）：来源 {source_count} 个，原始岗位 {total_jobs} 条，"
        f"新增 {int(upsert.get('inserted', 0))} 条，更新 {int(upsert.get('updated', 0))} 条，"
        f"发现企业 {funnel['enterprise_count']} 个，发现岗位 {funnel['discovered_job_count']} 条，"
        f"规则通过 {funnel['rules_passed_count']} 条，最终推荐 {funnel['final_recommendation_count']} 条。"
    )


def _boss_fetch_halt_error(
    fetch_report: dict,
    *,
    selected_source_groups: list[str],
    selected_source_names: list[str],
) -> str:
    if not isinstance(fetch_report, dict):
        return ""
    if int(fetch_report.get("total_jobs", 0) or 0) > 0:
        return ""
    if "boss" not in selected_source_groups and not any(name.startswith("boss_") for name in selected_source_names):
        return ""
    source_reports = fetch_report.get("sources", [])
    if not isinstance(source_reports, list) or not source_reports:
        return ""
    halted_reports = [
        item
        for item in source_reports
        if isinstance(item, dict) and bool(item.get("halted"))
    ]
    if len(halted_reports) != len(source_reports):
        return ""
    for item in halted_reports:
        message = str(item.get("error", "") or "").strip()
        if message:
            return message
    return ""


def create_app() -> FastAPI:
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    upload_dir = config.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    frontend_log_path = config.debug_dir / "frontend_action_log.jsonl"
    operation_lock = threading.Lock()
    operation_state_by_user: dict[str, dict] = {}
    last_job_refresh_by_user: dict[str, dict] = {}
    selected_fetch_sources_by_user: dict[str, list[str]] = {}
    selected_fetch_session_by_user: dict[str, str] = {}
    fetch_limit_by_user: dict[str, int] = {}

    def log_event(event: str, user_id: str = "me", **payload) -> None:
        try:
            append_action_log(frontend_log_path, event, {"user_id": user_id, **payload})
        except Exception:
            return

    def build_dashboard(user_id: str) -> dict:
        selected_source_groups = selected_fetch_sources_by_user.get(user_id)
        selected_source_names = (
            pipeline.resolve_selected_source_names(selected_source_groups)
            if selected_source_groups is not None
            else None
        )
        return _build_dashboard_payload_with_state(
            pipeline,
            user_id,
            last_job_refresh=last_job_refresh_by_user.get(user_id),
            selected_source_names=selected_source_names,
            selected_source_groups=selected_source_groups,
            selected_fetch_session_id=selected_fetch_session_by_user.get(user_id),
            current_fetch_limit=int(fetch_limit_by_user.get(user_id, 40) or 40),
        )

    def build_boss_assistant_state(
        user_id: str,
        *,
        fetch_session_id: str = "",
        review_limit: int = 5,
        review_profile: str = "",
    ) -> dict:
        preferred_fetch_session_id = (
            str(fetch_session_id or "").strip()
            or selected_fetch_session_by_user.get(user_id, "")
        )
        normalized_limit = max(1, min(int(review_limit or 5), 120))
        normalized_profile = str(review_profile or "").strip() or None
        summary = _build_boss_workbench_summary(
            pipeline,
            user_id,
            preferred_fetch_session_id=preferred_fetch_session_id or None,
        )
        target_fetch_session_id = preferred_fetch_session_id or str(summary.get("latest_fetch_session_id", "") or "").strip()
        if target_fetch_session_id:
            selected_fetch_session_by_user[user_id] = target_fetch_session_id

        recent_runs = summary.get("recent_source_runs") if isinstance(summary.get("recent_source_runs"), list) else []
        latest_run = next(
            (
                item
                for item in recent_runs
                if str(item.get("fetch_session_id", "") or "").strip() == target_fetch_session_id
            ),
            recent_runs[0] if recent_runs else {},
        )
        detail_status = {
            "fetch_session_id": target_fetch_session_id,
            "session_job_count": int(latest_run.get("job_count", 0) or 0),
            "detail_fetched_count": 0,
            "pending_job_count": 0,
        }
        recommendation: dict = {}
        if target_fetch_session_id:
            detail_status = pipeline.boss_session_detail_status(target_fetch_session_id)
            recommendation = pipeline.load_boss_session_recommendation(
                target_fetch_session_id,
                limit=normalized_limit,
                review_profile=normalized_profile,
            )
            if not recommendation:
                recommendation = pipeline.empty_boss_session_recommendation(
                    target_fetch_session_id,
                    message="这轮只完成了列表采集。下一步补抓并推荐。",
                )

        has_session = bool(target_fetch_session_id)
        recommendation_ready = bool(recommendation.get("ok")) if recommendation else False
        session_job_count = int(detail_status.get("session_job_count", 0) or 0)
        detail_fetched_count = int(detail_status.get("detail_fetched_count", 0) or 0)
        pending_job_count = int(detail_status.get("pending_job_count", 0) or 0)
        if not has_session:
            stage = "no_session"
            message = "还没有 active BOSS session。下一步开始采集。"
            next_action = "start_capture"
        elif recommendation_ready:
            stage = "recommendation_ready"
            message = f"推荐已生成，当前展示 {int(recommendation.get('displayed_count', 0) or 0)} 条。"
            next_action = "refresh_status"
        elif session_job_count <= 0:
            stage = "empty_session"
            message = "当前 session 没有可用岗位。下一步重新采集。"
            next_action = "start_capture"
        elif detail_fetched_count > 0 and pending_job_count <= 0:
            stage = "details_ready"
            message = "当前 session 已有完整 JD。下一步补抓并推荐。"
            next_action = "supplement_and_recommend"
        else:
            stage = "list_imported"
            message = "列表已入库。下一步补抓完整 JD 并生成推荐。"
            next_action = "supplement_and_recommend"

        active_session = {
            "fetch_session_id": target_fetch_session_id,
            "city": str(latest_run.get("city", "") or summary.get("capture_defaults", {}).get("city", "") or ""),
            "keyword": str(latest_run.get("keyword", "") or summary.get("capture_defaults", {}).get("keyword", "") or ""),
            "quick_filters": latest_run.get("quick_filters", {}) if isinstance(latest_run.get("quick_filters"), dict) else {},
            "job_count": int(latest_run.get("job_count", session_job_count) or session_job_count),
            "raw_job_count": int(latest_run.get("raw_job_count", 0) or 0),
            "session_job_count": session_job_count,
            "detail_fetched_count": detail_fetched_count,
            "pending_job_count": pending_job_count,
            "recommendation_base_count": int(recommendation.get("recommendation_base_count", detail_fetched_count) or 0),
            "matched_count": int(recommendation.get("matched_count", 0) or 0),
            "displayed_count": int(recommendation.get("displayed_count", len(recommendation.get("items", []) or [])) or 0),
            "local_filter": latest_run.get("local_filter", {}) if isinstance(latest_run.get("local_filter"), dict) else {},
            "url_filter_applied": bool(latest_run.get("url_filter_applied", False)),
        }
        available_actions = [
            {"id": "current_status", "method": "GET", "path": "/api/assistant/boss/status"},
            {"id": "start_capture", "method": "POST", "path": "/api/assistant/boss/start-capture"},
            {"id": "supplement_and_recommend", "method": "POST", "path": "/api/assistant/boss/supplement-and-recommend"},
            {"id": "refresh_status", "method": "POST", "path": "/api/assistant/boss/refresh"},
        ]
        return {
            "flow": "boss_active_session",
            "current_status": {
                "stage": stage,
                "message": message,
                "next_action": next((item for item in available_actions if item["id"] == next_action), available_actions[0]),
            },
            "active_session": active_session,
            "capture_defaults": summary.get("capture_defaults", {}),
            "available_review_profiles": summary.get("available_review_profiles", []),
            "available_actions": available_actions,
            "workbench": summary,
            "review": recommendation,
        }

    def get_operation_state(user_id: str) -> dict:
        with operation_lock:
            current = copy.deepcopy(operation_state_by_user.get(user_id, {}))
        if not current:
            return {
                "user_id": user_id,
                "kind": "",
                "active": False,
                "status": "idle",
                "message": "",
            }
        started_perf = current.pop("_started_perf", None)
        if current.get("active") and isinstance(started_perf, (int, float)):
            current["elapsed_ms"] = int((time.perf_counter() - started_perf) * 1000)
        return current

    def start_operation(user_id: str, *, kind: str, message: str, **extra) -> None:
        now = time.perf_counter()
        payload = {
            "user_id": user_id,
            "kind": kind,
            "active": True,
            "status": "running",
            "message": message,
            "error": "",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "_started_perf": now,
            **extra,
        }
        with operation_lock:
            operation_state_by_user[user_id] = payload

    def update_operation(user_id: str, **extra) -> None:
        with operation_lock:
            state = operation_state_by_user.get(user_id)
            if not state:
                return
            state.update(extra)
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def finish_operation(user_id: str, *, status: str, message: str, error: str = "", **extra) -> None:
        with operation_lock:
            state = operation_state_by_user.get(user_id, {"user_id": user_id, "kind": "jobs_refresh"})
            started_perf = state.get("_started_perf", time.perf_counter())
            state.update(extra)
            state["active"] = False
            state["status"] = status
            state["message"] = message
            state["error"] = error
            state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            state["updated_at"] = state["finished_at"]
            state["elapsed_ms"] = int((time.perf_counter() - started_perf) * 1000)
            state.pop("_started_perf", None)
            operation_state_by_user[user_id] = state

    app = FastAPI(title="Resume Bot Local", version="0.1.0")
    static_dir = config.project_root / "src" / "resume_bot" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request, exc: Exception):
        trace = traceback.format_exc()
        payload = {
            "summary": [
                f"type={exc.__class__.__name__}",
                f"message={str(exc)}",
            ],
            "traceback": trace,
        }
        report = {"json": "", "md": "", "latest_json": "", "latest_md": ""}
        try:
            report = write_debug_report(config.debug_dir, "local-web-error", "me", payload)
        except Exception:
            report = {"json": "", "md": "", "latest_json": "", "latest_md": ""}
        log_event("server.exception", "me", error=str(exc), report=report)
        return JSONResponse(
            status_code=500,
            content={
                "detail": str(exc) or exc.__class__.__name__,
                "error_type": exc.__class__.__name__,
                "debug_report": report,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/dashboard")
    def dashboard(user_id: str = "me") -> dict:
        return build_dashboard(user_id)

    @app.get("/api/assistant/boss/status")
    def assistant_boss_status(
        user_id: str = "me",
        fetch_session_id: str = "",
        review_limit: int = 5,
        review_profile: str = "",
    ) -> dict:
        state = build_boss_assistant_state(
            user_id,
            fetch_session_id=fetch_session_id,
            review_limit=review_limit,
            review_profile=review_profile,
        )
        log_event(
            "assistant_boss_status.ok",
            user_id,
            fetch_session_id=state["active_session"].get("fetch_session_id", ""),
            stage=state["current_status"]["stage"],
        )
        return {
            "ok": True,
            "action": "current_status",
            "state": state,
            "next_action": state["current_status"]["next_action"],
        }

    @app.post("/api/assistant/boss/refresh")
    def assistant_boss_refresh(payload: AssistantBossStatusPayload) -> dict:
        state = build_boss_assistant_state(
            payload.user_id,
            fetch_session_id=payload.fetch_session_id,
            review_limit=payload.review_limit,
            review_profile=payload.review_profile,
        )
        log_event(
            "assistant_boss_refresh.ok",
            payload.user_id,
            fetch_session_id=state["active_session"].get("fetch_session_id", ""),
            stage=state["current_status"]["stage"],
        )
        return {
            "ok": True,
            "action": "refresh_status",
            "state": state,
            "next_action": state["current_status"]["next_action"],
        }

    @app.post("/api/assistant/boss/start-capture")
    def assistant_boss_start_capture(payload: AssistantBossCapturePayload) -> dict:
        result = boss_workbench_capture(payload)
        target_fetch_session_id = str(
            result.get("import_result", {}).get("fetch_session_id")
            or result.get("capture", {}).get("fetch_session_id")
            or result.get("fetch_session_id")
            or ""
        ).strip()
        state = build_boss_assistant_state(
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            review_limit=payload.review_limit,
        )
        log_event(
            "assistant_boss_start_capture.ok",
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            stage=state["current_status"]["stage"],
        )
        return {
            "ok": True,
            "action": "start_capture",
            "result": result,
            "state": state,
            "next_action": state["current_status"]["next_action"],
        }

    @app.post("/api/assistant/boss/supplement-and-recommend")
    def assistant_boss_supplement_and_recommend(payload: AssistantBossSupplementPayload) -> dict:
        result = boss_workbench_supplement(
            BossWorkbenchSupplementPayload(
                user_id=payload.user_id,
                fetch_session_id=payload.fetch_session_id,
                review_limit=payload.review_limit,
                review_profile=payload.review_profile,
            )
        )
        target_fetch_session_id = str(
            result.get("recommendation", {}).get("fetch_session_id")
            or result.get("supplement", {}).get("fetch_session_id")
            or payload.fetch_session_id
            or ""
        ).strip()
        state = build_boss_assistant_state(
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            review_limit=payload.review_limit,
            review_profile=payload.review_profile,
        )
        log_event(
            "assistant_boss_supplement.ok",
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            stage=state["current_status"]["stage"],
        )
        return {
            "ok": True,
            "action": "supplement_and_recommend",
            "result": result,
            "state": state,
            "next_action": state["current_status"]["next_action"],
        }

    @app.get("/api/boss/workbench/review")
    def boss_workbench_review(
        user_id: str = "me",
        fetch_session_id: str = "",
        review_profile: str = "",
        limit: int = 5,
    ) -> dict:
        normalized_limit = max(1, min(int(limit or 12), 120))
        summary = _build_boss_workbench_summary(
            pipeline,
            user_id,
            preferred_fetch_session_id=(fetch_session_id or "").strip() or None,
        )
        target_fetch_session_id = (fetch_session_id or "").strip() or summary.get("latest_fetch_session_id", "")
        if not target_fetch_session_id:
            raise HTTPException(status_code=404, detail="还没有可审阅的 BOSS 队列。先完成一次列表队列导入。")
        review = pipeline.load_boss_session_recommendation(
            target_fetch_session_id,
            limit=normalized_limit,
            review_profile=(review_profile or "").strip() or None,
        )
        if review and not isinstance(review.get("review_items"), list):
            detail_status = pipeline.boss_session_detail_status(target_fetch_session_id)
            if int(detail_status.get("detail_fetched_count", 0) or 0) > 0:
                review = pipeline.recommend_boss_session(
                    user_id,
                    target_fetch_session_id,
                    limit=normalized_limit,
                    review_profile=(review_profile or "").strip() or None,
                )
        if not review:
            detail_status = pipeline.boss_session_detail_status(target_fetch_session_id)
            if int(detail_status.get("detail_fetched_count", 0) or 0) > 0:
                review = pipeline.recommend_boss_session(
                    user_id,
                    target_fetch_session_id,
                    limit=normalized_limit,
                    review_profile=(review_profile or "").strip() or None,
                )
            else:
                review = pipeline.empty_boss_session_recommendation(
                    target_fetch_session_id,
                    message="这轮只完成了列表采集。请先点“补抓并推荐”，再查看推荐结果。",
                )
        return {
            "ok": True,
            "workbench": summary,
            "review": review,
        }

    @app.post("/api/boss/workbench/capture")
    def boss_workbench_capture(payload: BossWorkbenchCapturePayload) -> dict:
        city = str(payload.city or "").strip()
        keyword = str(payload.keyword or "").strip()
        if not city:
            raise HTTPException(status_code=400, detail="请先填写采集城市。")
        if not keyword:
            raise HTTPException(status_code=400, detail="请先填写采集关键词。")
        limit = max(1, min(int(payload.limit or 45), 120))
        requested_rounds = int(payload.rounds or 0)
        rounds = (
            max(0, min(requested_rounds, 7))
            if requested_rounds > 0
            else _recommended_boss_capture_rounds(limit)
        )
        gate = _boss_gate_status(config)["gate"]
        blocked_reason = _boss_workbench_capture_block_reason(gate)
        if blocked_reason:
            raise HTTPException(status_code=400, detail=blocked_reason)
        quick_filters = normalize_boss_quick_filters(
            {
                "degree_filter": payload.degree_filter,
                "employment_mode_filter": payload.employment_mode_filter,
            }
        )

        started = time.perf_counter()
        log_event(
            "boss_workbench_capture.start",
            payload.user_id,
            city=city,
            keyword=keyword,
            limit=limit,
            rounds=rounds,
            quick_filters=quick_filters,
        )
        try:
            result = _run_boss_workbench_capture(
                config,
                pipeline,
                payload.user_id,
                city=city,
                keyword=keyword,
                limit=limit,
                rounds=rounds,
                degree_filter=quick_filters["degree_filter"],
                employment_mode_filter=quick_filters["employment_mode_filter"],
            )
            selected_fetch_session_by_user[payload.user_id] = result["fetch_session_id"]
            summary = _build_boss_workbench_summary(
                pipeline,
                payload.user_id,
                preferred_fetch_session_id=result["fetch_session_id"],
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "boss_workbench_capture.error",
                payload.user_id,
                city=city,
                keyword=keyword,
                limit=limit,
                rounds=rounds,
                quick_filters=quick_filters,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "boss_workbench_capture.ok",
            payload.user_id,
            city=city,
            keyword=keyword,
            limit=limit,
            rounds=rounds,
            quick_filters=quick_filters,
            duration_ms=duration_ms,
            fetch_session_id=result["fetch_session_id"],
            jobs_count=result["capture"].get("jobs_count", 0),
            imported_job_count=result["import"].get("job_count", 0),
            quick_filter_dropped=result["import"].get("local_filter", {}).get("dropped_count", 0),
        )
        return {
            "ok": True,
            "capture": result["capture"],
            "import_result": result["import"],
            "quick_filters": quick_filters,
            "workbench": summary,
            "review": None,
            "next_step": {
                "id": "supplement_and_recommend",
                "label": "补抓并推荐",
                "message": "这轮只完成了列表采集，下一步对当前 session 补抓完整 JD 并生成推荐。",
            },
        }

    @app.post("/api/boss/workbench/supplement")
    def boss_workbench_supplement(payload: BossWorkbenchSupplementPayload) -> dict:
        target_fetch_session_id = (
            str(payload.fetch_session_id or "").strip()
            or selected_fetch_session_by_user.get(payload.user_id, "")
        )
        if not target_fetch_session_id:
            raise HTTPException(status_code=404, detail="还没有可补抓 JD 的 BOSS session。先完成一次列表采集。")
        review_profile = (payload.review_profile or "").strip() or None
        recommendation_limit = max(1, min(int(payload.review_limit or 5), 120))
        started = time.perf_counter()
        log_event(
            "boss_workbench_supplement.start",
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            review_profile=review_profile or "default",
        )
        try:
            session_flow = pipeline.supplement_boss_session_and_recommend(
                payload.user_id,
                target_fetch_session_id,
                recommendation_limit=recommendation_limit,
                review_profile=review_profile,
            )
            supplement = session_flow.get("supplement", {})
            recommendation = session_flow.get("recommendation", {})
            if not recommendation.get("ok"):
                first_failure = next(
                    (
                        item
                        for item in (supplement.get("results") or [])
                        if isinstance(item, dict) and not item.get("ok")
                    ),
                    {},
                )
                quality_issues = [
                    str(item).strip()
                    for item in (first_failure.get("quality_issues") or [])
                    if str(item).strip()
                ]
                detail = str(supplement.get("error") or first_failure.get("error") or "").strip()
                if not detail and quality_issues:
                    detail = "质量判定未通过：" + " / ".join(quality_issues[:3])
                detail = detail or str(recommendation.get("message") or "").strip()
                raise HTTPException(status_code=409, detail=detail or "这次没有可用于推荐的完整 JD。")
            summary = _build_boss_workbench_summary(
                pipeline,
                payload.user_id,
                preferred_fetch_session_id=target_fetch_session_id,
            )
        except HTTPException as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "boss_workbench_supplement.error",
                payload.user_id,
                fetch_session_id=target_fetch_session_id,
                duration_ms=duration_ms,
                error=str(exc.detail or exc),
            )
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "boss_workbench_supplement.error",
                payload.user_id,
                fetch_session_id=target_fetch_session_id,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "boss_workbench_supplement.ok",
            payload.user_id,
            fetch_session_id=target_fetch_session_id,
            duration_ms=duration_ms,
            updated_count=supplement.get("updated_count", 0),
            attempted_count=supplement.get("attempted_count", 0),
            success_count=supplement.get("success_count", 0),
            recommendation_count=recommendation.get("matched_count", 0),
        )
        return {
            "ok": True,
            "supplement": supplement,
            "recommendation": recommendation,
            "workbench": summary,
            "review": recommendation,
        }

    @app.get("/api/jobs/export")
    def export_jobs(user_id: str = "me", selected_source: list[str] = Query(default=[])) -> Response:
        selected_source_groups = selected_source or selected_fetch_sources_by_user.get(user_id) or pipeline.default_fetch_source_groups()
        selected_source_names = pipeline.resolve_selected_source_names(selected_source_groups) or []
        selected_fetch_session_id = selected_fetch_session_by_user.get(user_id)
        if not selected_source_names:
            raise HTTPException(status_code=400, detail="至少勾选一个抓取来源。")
        settings = pipeline.store.get_settings(user_id)
        profile, _raw_text = pipeline.store.get_active_resume(user_id)
        ranked, _ranking_debug = pipeline._rank_jobs_with_debug(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=selected_fetch_session_id,
        )
        jobs = [
            job
            for job in pipeline.load_active_jobs(user_id, selected_source_names, fetch_session_id=selected_fetch_session_id)
            if job.application_status == "open"
        ]
        decorated_jobs = _decorate_job_collection(pipeline, user_id, jobs, ranked, settings, profile)
        if not decorated_jobs:
            raise HTTPException(status_code=400, detail="当前没有可导出的岗位。")
        header = [
            "推荐结果",
            "来源",
            "公司",
            "岗位",
            "城市",
            "招聘范围",
            "岗位性质",
            "薪资",
            "学历",
            "投递状态",
            "截止时间",
            "推荐分",
            "推荐理由",
            "未推荐原因",
            "链接",
            "JD摘要",
        ]
        all_rows = [header, *[_job_export_row(item) for item in decorated_jobs]]
        recommended_rows = [header, *[_job_export_row(item) for item in decorated_jobs if item.get("recommended")]]
        skipped_rows = [header, *[_job_export_row(item) for item in decorated_jobs if not item.get("recommended")]]
        workbook = build_xlsx_workbook(
            [
                {"name": "全部岗位", "rows": all_rows, "column_widths": _job_export_column_widths()},
                {"name": "已推荐", "rows": recommended_rows or [header], "column_widths": _job_export_column_widths()},
                {"name": "未推荐", "rows": skipped_rows or [header], "column_widths": _job_export_column_widths()},
            ]
        )
        filename = f"resume-bot-jobs-{time.strftime('%Y%m%d-%H%M%S')}.xlsx"
        log_event(
            "jobs_export.ok",
            user_id,
            selected_sources=selected_source_groups,
            job_count=len(decorated_jobs),
            recommended_count=sum(1 for item in decorated_jobs if item.get("recommended")),
        )
        return Response(
            content=workbook,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/ops/status")
    def operation_status(user_id: str = "me") -> dict:
        return get_operation_state(user_id)

    @app.post("/api/frontend-log")
    def frontend_log(payload: FrontendLogPayload) -> dict:
        log_event("frontend." + payload.event, payload.user_id, detail=payload.detail)
        return {"ok": True}

    @app.post("/api/boss/launch-browser")
    def launch_boss_browser() -> dict:
        command, command_text, browser_label = _boss_launch_command(config)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=str(config.project_root),
                check=True,
                capture_output=True,
                text=True,
                timeout=40,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "boss_browser_launch.error",
                "me",
                duration_ms=duration_ms,
                command=command_text,
                error="timeout",
            )
            raise HTTPException(status_code=500, detail="打开登录浏览器超时了。请重试一次。") from exc
        except FileNotFoundError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "boss_browser_launch.error",
                "me",
                duration_ms=duration_ms,
                command=command_text,
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail="找不到启动登录浏览器需要的程序。") from exc
        except subprocess.CalledProcessError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            raw_detail = (exc.stderr or exc.stdout or str(exc)).strip()
            log_event(
                "boss_browser_launch.error",
                "me",
                duration_ms=duration_ms,
                command=command_text,
                error=raw_detail,
            )
            raise HTTPException(
                status_code=500,
                detail=f"打开登录浏览器失败。请确认 {browser_label} 可用后重试。",
            ) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        output = (completed.stdout or completed.stderr or "").strip()
        log_event(
            "boss_browser_launch.ok",
            "me",
            duration_ms=duration_ms,
            command=command_text,
            output=output,
        )
        return {
            "ok": True,
            "message": f"登录浏览器已尝试打开。请在弹出的 {browser_label} 里登录 BOSS，然后点“重新检查”。",
            "command": command_text,
            "output": output,
            "duration_ms": duration_ms,
            "browser_label": browser_label,
        }

    @app.post("/api/llm-check")
    def llm_check(user_id: str = "me") -> dict:
        started = time.perf_counter()
        try:
            reply = pipeline.text_client.complete_text(
                "You are a health check assistant. Reply with OK only.",
                "Reply with OK only.",
                max_tokens=12,
            )
            if not (reply or "").strip():
                raise RuntimeError("模型请求成功返回了空文本，这说明当前 MiniMax 响应解析可能不对。")
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event("llm_check.ok", user_id, duration_ms=duration_ms, reply=reply)
            return {"ok": True, "reply": reply, "duration_ms": duration_ms}
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event("llm_check.error", user_id, duration_ms=duration_ms, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/ai-settings")
    def get_ai_settings() -> dict:
        return _safe_ai_settings_response(config)

    @app.post("/api/ai-settings/save")
    def save_ai_settings(payload: AISettingsSavePayload) -> dict:
        nonlocal config
        started = time.perf_counter()
        existing = _read_ai_settings_file(config.ai_settings_path)
        saved_payload = {
            "text": _merge_ai_provider_settings(
                existing.get("text", {}) if isinstance(existing.get("text"), dict) else {},
                payload.text,
            ),
            "vision": _merge_ai_provider_settings(
                existing.get("vision", {}) if isinstance(existing.get("vision"), dict) else {},
                payload.vision,
            ),
        }
        _write_ai_settings_file(config.ai_settings_path, saved_payload)
        config = load_config()
        pipeline.reload_ai_clients(config)
        duration_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "ai_settings.save",
            payload.user_id,
            duration_ms=duration_ms,
            text_provider=config.llm_provider,
            text_model=config.llm_model,
            vision_provider=config.vision_provider,
            vision_model=config.vision_model,
        )
        response = _safe_ai_settings_response(config)
        response["duration_ms"] = duration_ms
        return response

    @app.post("/api/ai-settings/test")
    def test_ai_settings(payload: AISettingsTestPayload) -> dict:
        started = time.perf_counter()
        target = str(payload.target or "text").strip().lower()
        if target not in {"text", "vision"}:
            raise HTTPException(status_code=400, detail="target must be text or vision.")
        return _test_ai_settings_with_payload(config, payload, target, started, log_event)

    @app.post("/api/ai-settings/models")
    def list_ai_models(payload: AISettingsModelsPayload) -> dict:
        started = time.perf_counter()
        target = str(payload.target or "text").strip().lower()
        if target not in {"text", "vision"}:
            raise HTTPException(status_code=400, detail="target must be text or vision.")
        return _list_ai_models_with_payload(config, payload, target, started, log_event)
        try:
            if target == "vision":
                if not pipeline.vision_client:
                    raise RuntimeError("视觉模型还没有配置完整。")
                test_image = config.debug_dir / "ai_settings_vision_test.png"
                if not test_image.exists():
                    test_image.write_bytes(
                        base64.b64decode(
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
                        )
                    )
                reply = pipeline.vision_client.extract_text(
                    "This is a connectivity test. Reply with OK only.",
                    [test_image],
                )
            else:
                reply = pipeline.text_client.complete_text(
                    "You are a health check assistant. Reply with OK only.",
                    "Reply with OK only.",
                    max_tokens=12,
                )
            if not str(reply or "").strip():
                raise RuntimeError("模型请求成功但返回了空内容。")
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "ai_settings.test.ok",
                payload.user_id,
                target=target,
                duration_ms=duration_ms,
            )
            return {"ok": True, "target": target, "reply": str(reply or "").strip(), "duration_ms": duration_ms}
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "ai_settings.test.error",
                payload.user_id,
                target=target,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/resume/upload")
    async def upload_resume(
        file: UploadFile = File(...),
        user_id: str = Form("me"),
    ) -> dict:
        if not file.filename:
            raise HTTPException(status_code=400, detail="没有选中文件。")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".txt", ".md"}:
            raise HTTPException(status_code=400, detail="暂不支持这个文件类型。")
        safe_name = Path(file.filename).name
        target_path = upload_dir / safe_name
        with target_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        started = time.perf_counter()
        log_event("resume_upload.start", user_id, file_name=safe_name)
        try:
            result = pipeline.ingest_resume(user_id, target_path)
        except Exception as exc:
            log_event(
                "resume_upload.error",
                user_id,
                file_name=safe_name,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        log_event(
            "resume_upload.ok",
            user_id,
            file_name=safe_name,
            duration_ms=int((time.perf_counter() - started) * 1000),
            extraction_method=result["extraction"]["extraction_method"],
            parse_method=result["profile"].raw_sections.get("_parse_method", "unknown"),
        )
        return {
            "ok": True,
            "profile_summary": result["profile_summary"],
            "settings_summary": result["settings_summary"],
            "extraction": result["extraction"],
            "parse_method": result["profile"].raw_sections.get("_parse_method", ""),
            "debug_report": result["debug_report"],
            "dashboard": build_dashboard(user_id),
        }

    @app.post("/api/settings/save")
    def save_settings(payload: ManualSettingsPayload) -> dict:
        started = time.perf_counter()
        log_event(
            "settings_save.start",
            payload.user_id,
            job_scope=payload.job_scope,
            campus_role_mode=payload.campus_role_mode,
        )
        scope_map = {
            "campus_social": ["校招", "社招"],
            "campus_only": ["校招"],
            "social_only": ["社招"],
        }
        if payload.job_scope not in scope_map:
            raise HTTPException(status_code=400, detail="不支持这个招聘范围。")
        if payload.campus_role_mode not in {"full_time", "intern", "both"}:
            raise HTTPException(status_code=400, detail="不支持这个岗位性质。")
        try:
            settings = pipeline.store.get_settings(payload.user_id)
            settings = apply_manual_settings(
                settings,
                preferred_roles=payload.preferred_roles,
                preferred_cities=payload.preferred_cities,
                preferred_keywords=payload.preferred_keywords,
                excluded_keywords=payload.excluded_keywords,
                job_types=scope_map[payload.job_scope],
                campus_role_mode=payload.campus_role_mode,
                salary_min=payload.salary_min,
                salary_max=payload.salary_max,
                max_degree_requirement=payload.max_degree_requirement,
            )
            pipeline.store.save_settings(settings)
        except Exception as exc:
            log_event(
                "settings_save.error",
                payload.user_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event(
            "settings_save.ok",
            payload.user_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            preferred_roles=len(settings.preferred_roles),
            preferred_cities=len(settings.preferred_cities),
            preferred_keywords=len(settings.preferred_keywords),
            excluded_keywords=len(settings.excluded_keywords),
        )
        return {
            "ok": True,
            "summary": settings_summary(settings),
            "dashboard": build_dashboard(payload.user_id),
        }

    @app.post("/api/preferences/remove-item")
    def remove_preference_item(payload: RemoveSettingItemPayload) -> dict:
        started = time.perf_counter()
        log_event("preferences_remove.start", payload.user_id, field=payload.field, value=payload.value)
        try:
            settings = pipeline.store.get_settings(payload.user_id)
            settings = remove_setting_value(settings, payload.field, payload.value)
            pipeline.store.save_settings(settings)
        except Exception as exc:
            log_event(
                "preferences_remove.error",
                payload.user_id,
                field=payload.field,
                value=payload.value,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event(
            "preferences_remove.ok",
            payload.user_id,
            field=payload.field,
            value=payload.value,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {
            "ok": True,
            "summary": settings_summary(settings),
            "dashboard": build_dashboard(payload.user_id),
        }

    @app.post("/api/jobs/refresh")
    def refresh_jobs(payload: RefreshPayload) -> dict:
        profile, _raw_text = pipeline.store.get_active_resume(payload.user_id)
        if not profile:
            raise HTTPException(status_code=400, detail="先上传简历，再抓岗位。")
        settings = pipeline.store.get_settings(payload.user_id)
        fetch_limit = max(1, min(int(payload.fetch_limit or 40), 200))
        available_fetch_sources = decorate_fetch_sources_for_settings(pipeline.available_fetch_sources(), settings)
        default_source_groups = [item["id"] for item in available_fetch_sources if item.get("default_checked")]
        requested_source_groups = payload.selected_sources or default_source_groups
        selected_source_groups, dropped_source_groups = sanitize_selected_source_groups(requested_source_groups, settings)
        if _boss_stoploss_enabled() and "boss" in selected_source_groups:
            raise HTTPException(status_code=409, detail=_boss_stoploss_reason())
        selected_source_names = pipeline.resolve_selected_source_names(selected_source_groups) or []
        fetch_limit_by_user[payload.user_id] = fetch_limit
        selected_source_lookup = {
            item["id"]: item["label"]
            for item in available_fetch_sources
        }
        selected_source_labels = [
            selected_source_lookup[source_id]
            for source_id in selected_source_groups
            if source_id in selected_source_lookup
        ]
        if dropped_source_groups and not selected_source_groups:
            raise HTTPException(
                status_code=400,
                detail="当前设置只看社招，牛客校招日程暂不可用；请改成“只看校招”或“校招+社招”后再勾选。",
            )
        if not selected_source_names:
            raise HTTPException(status_code=400, detail="至少勾选一个抓取来源。")
        if payload.fetch_jobs and ("boss" in selected_source_groups or any(name.startswith("boss_") for name in selected_source_names)):
            boss_gate = _boss_gate_status(config)
            if not boss_gate["gate"]["can_start"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{boss_gate['gate']['title']}：{boss_gate['gate']['message']}",
                )
        selected_fetch_sources_by_user[payload.user_id] = selected_source_groups
        current_fetch_session_id = selected_fetch_session_by_user.get(payload.user_id, "")
        effective_fetch_session_id = (
            (payload.fetch_session_id or "").strip()
            if payload.fetch_jobs
            else current_fetch_session_id
        )
        if payload.fetch_jobs and not effective_fetch_session_id:
            effective_fetch_session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        started = time.perf_counter()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        log_event(
            "jobs_refresh.start",
            payload.user_id,
            fetch_jobs=payload.fetch_jobs,
            selected_sources=selected_source_groups,
            fetch_limit=fetch_limit,
            fetch_session_id=effective_fetch_session_id,
        )
        start_operation(
            payload.user_id,
            kind="jobs_refresh",
            message="准备开始抓岗位：" + "、".join(selected_source_labels or ["默认来源"]),
            fetch_jobs=payload.fetch_jobs,
            selected_sources=selected_source_groups,
            selected_source_labels=selected_source_labels,
            fetch_limit=fetch_limit,
            fetch_session_id=effective_fetch_session_id,
            stage="start",
            progress_current=0,
            progress_total=0,
            source_reports=[],
            fetch_report={},
            ranking_debug={},
            recommendation_count=0,
        )

        def progress_callback(event: dict) -> None:
            update_operation(
                payload.user_id,
                stage=event.get("stage", ""),
                message=event.get("message", ""),
                progress_current=int(event.get("current", event.get("processed", 0)) or 0),
                progress_total=int(event.get("total", event.get("total_jobs", 0)) or 0),
                current_source=event.get("source", ""),
                source_index=int(event.get("source_index", 0) or 0),
                total_jobs=int(event.get("total_jobs", 0) or 0),
                matched=int(event.get("matched", event.get("matched_after_rerank", 0)) or 0),
                source_reports=event.get("source_reports", []),
                upsert=event.get("upsert", {}),
                latest_event=event,
                error=event.get("error", ""),
            )
        try:
            fetch_report = (
                pipeline.fetch_jobs(
                    payload.user_id,
                    progress_callback=progress_callback,
                    selected_source_names=selected_source_names,
                    runtime_fetch_limit=fetch_limit,
                    fetch_session_id=effective_fetch_session_id,
                )
                if payload.fetch_jobs
                else {"skipped": True, "fetch_limit": fetch_limit, "fetch_session_id": effective_fetch_session_id}
            )
            if payload.fetch_jobs:
                selected_fetch_session_by_user[payload.user_id] = fetch_report.get("fetch_session_id", effective_fetch_session_id)
            halted_error = (
                _boss_fetch_halt_error(
                    fetch_report,
                    selected_source_groups=selected_source_groups,
                    selected_source_names=selected_source_names,
                )
                if payload.fetch_jobs
                else ""
            )
            if halted_error:
                duration_ms = int((time.perf_counter() - started) * 1000)
                log_event(
                    "jobs_refresh.halted",
                    payload.user_id,
                    fetch_jobs=payload.fetch_jobs,
                    selected_sources=selected_source_groups,
                    fetch_limit=fetch_limit,
                    duration_ms=duration_ms,
                    error=halted_error,
                )
                finish_operation(
                    payload.user_id,
                    status="error",
                    message=f"鎶撳彇宸叉鎹燂細{halted_error}",
                    error=halted_error,
                    fetch_report=fetch_report,
                    source_reports=fetch_report.get("sources", []),
                    selected_sources=selected_source_groups,
                    selected_source_labels=selected_source_labels,
                    fetch_limit=fetch_limit,
                    fetch_session_id=selected_fetch_session_by_user.get(payload.user_id) or effective_fetch_session_id,
                )
                raise HTTPException(status_code=409, detail=halted_error)
            update_operation(
                payload.user_id,
                stage="rank.prepare",
                message="抓取阶段结束，准备计算推荐。",
                fetch_report=fetch_report,
                source_reports=fetch_report.get("sources", []),
            )
            ranked, ranking_debug = pipeline._rank_jobs_with_debug(
                payload.user_id,
                progress_callback=progress_callback,
                selected_source_names=selected_source_names,
                fetch_session_id=selected_fetch_session_by_user.get(payload.user_id) or effective_fetch_session_id or None,
            )
        except HTTPException:
            raise
        except Exception as exc:
            log_event(
                "jobs_refresh.error",
                payload.user_id,
                fetch_jobs=payload.fetch_jobs,
                selected_sources=selected_source_groups,
                fetch_limit=fetch_limit,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
            )
            finish_operation(
                payload.user_id,
                status="error",
                message=f"抓取失败：{str(exc)}",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        recommendations = [_match_to_view_model(pipeline, payload.user_id, item) for item in ranked[:30]]
        duration_ms = int((time.perf_counter() - started) * 1000)
        refresh_summary = {
            "started_at": started_at,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_ms": duration_ms,
            "fetch_jobs": payload.fetch_jobs,
            "selected_sources": selected_source_groups,
            "selected_source_labels": selected_source_labels,
            "fetch_limit": fetch_limit,
            "fetch_session_id": selected_fetch_session_by_user.get(payload.user_id) or effective_fetch_session_id,
            "fetch_report": fetch_report,
            "ranking_debug": ranking_debug,
            "recommendation_count": len(recommendations),
            "fetch_funnel": build_fetch_funnel(fetch_report, ranking_debug, len(recommendations)),
            "summary": _build_job_refresh_summary(
                fetch_jobs=payload.fetch_jobs,
                fetch_report=fetch_report,
                ranking_debug=ranking_debug,
                recommendation_count=len(recommendations),
                selected_source_labels=selected_source_labels,
                fetch_limit=fetch_limit,
            ),
        }
        last_job_refresh_by_user[payload.user_id] = refresh_summary
        log_event(
            "jobs_refresh.ok",
            payload.user_id,
            fetch_jobs=payload.fetch_jobs,
            selected_sources=selected_source_groups,
            fetch_limit=fetch_limit,
            duration_ms=duration_ms,
            recommendation_count=len(recommendations),
            matched_after_rerank=ranking_debug.get("matched_after_rerank", 0),
        )
        finish_operation(
            payload.user_id,
            status="ok",
            message=refresh_summary["summary"],
            fetch_report=fetch_report,
            ranking_debug=ranking_debug,
            source_reports=fetch_report.get("sources", []),
            recommendation_count=len(recommendations),
            selected_sources=selected_source_groups,
            selected_source_labels=selected_source_labels,
            fetch_limit=fetch_limit,
            fetch_session_id=refresh_summary["fetch_session_id"],
        )
        return {
            "ok": True,
            "fetch_report": fetch_report,
            "ranking_debug": ranking_debug,
            "recommendations": recommendations,
            "summary": refresh_summary["summary"],
            "dashboard": build_dashboard(payload.user_id),
        }

    @app.post("/api/jobs/{fingerprint}/action")
    def mark_job(fingerprint: str, payload: JobActionPayload) -> dict:
        if payload.action not in {"saved", "applied", "disliked", "deferred"}:
            raise HTTPException(status_code=400, detail="action 只能是 saved/applied/disliked/deferred。")
        pipeline.mark_job(payload.user_id, fingerprint, payload.action, notes=payload.notes)
        log_event("job_action.ok", payload.user_id, fingerprint=fingerprint, action=payload.action, notes=payload.notes)
        return {"ok": True, "dashboard": build_dashboard(payload.user_id)}

    return app
