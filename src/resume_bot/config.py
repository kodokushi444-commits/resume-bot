from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _first_nonempty_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _load_dotenv_candidates(project_root: Path) -> None:
    explicit = os.getenv("RESUME_BOT_ENV_FILE", "").strip()
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            project_root / ".env",
            project_root / "config" / "resume_bot" / ".env",
            project_root.parent / ".env",
        ]
    )
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        _load_dotenv_file(candidate)


def _load_openclaw_fallbacks(project_root: Path) -> dict:
    candidates = [project_root / "openclaw.json", project_root.parent / "openclaw.json"]
    for candidate in candidates:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _load_local_ai_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _setting_value(settings: dict, section: str, key: str) -> str:
    section_payload = settings.get(section, {})
    if not isinstance(section_payload, dict):
        return ""
    value = section_payload.get(key, "")
    return str(value or "").strip()


def _can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".resume_bot_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _default_runtime_data_dir() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "ResumeBotLocal" / "data"
    app_data = os.getenv("APPDATA", "").strip()
    if app_data:
        return Path(app_data) / "ResumeBotLocal" / "data"
    return Path(tempfile.gettempdir()) / "ResumeBotLocal" / "data"


def _best_effort_copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        try:
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            else:
                if not destination.exists():
                    shutil.copy2(child, destination)
        except Exception:
            continue


def _select_runtime_data_dir(candidate: Path) -> Path:
    if _can_write_dir(candidate):
        return candidate
    fallback = _default_runtime_data_dir()
    if fallback != candidate:
        _best_effort_copy_tree(candidate, fallback)
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


@dataclass
class AppConfig:
    project_root: Path
    data_dir: Path
    debug_dir: Path
    browser_state_dir: Path
    browser_profile_dir: Path
    db_path: Path
    ai_settings_path: Path
    default_settings_path: Path
    source_registry_path: Path
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    vision_provider: str
    vision_base_url: str
    vision_api_key: str
    vision_model: str
    feishu_app_id: str
    feishu_app_secret: str
    tavily_api_key: str
    default_user_id: str
    enable_llm_rerank: bool
    llm_rerank_top_n: int
    playwright_timeout_ms: int
    boss_browser_state_path: Path
    boss_browser_profile_dir: Path
    boss_browser_cdp_url: str
    boss_browser_cdp_port: int
    boss_browser_prefer_cdp: bool
    boss_login_qr_path: Path
    boss_login_full_page_path: Path
    boss_login_status_path: Path
    boss_browser_headless_override: bool | None
    boss_cli_command: str
    boss_cli_timeout_sec: int


def _detect_project_root() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name == "resume_bot" and current.parent.parent.name == "scripts":
        return current.parent.parent.parent
    for parent in current.parents:
        if (parent / "README.md").exists() and (parent / "scripts").exists():
            return parent
        if (parent / "AGENTS.md").exists() and (parent / "skills").exists():
            return parent
    return current.parents[2]


def load_config() -> AppConfig:
    project_root = _detect_project_root()
    _load_dotenv_candidates(project_root)
    openclaw_payload = _load_openclaw_fallbacks(project_root)
    local_data_dir = project_root / "data"
    workspace_data_dir = project_root / "data" / "resume_bot"
    db_path_env = os.getenv("RESUME_BOT_DB_PATH", "").strip()
    if db_path_env:
        db_path = Path(db_path_env)
        data_dir = _select_runtime_data_dir(db_path.parent)
        db_path = data_dir / db_path.name
    else:
        preferred_data_dir = workspace_data_dir if workspace_data_dir.exists() else local_data_dir
        data_dir = _select_runtime_data_dir(preferred_data_dir)
        db_path = data_dir / "resume_bot.db"
    data_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = data_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    browser_state_dir = data_dir / "browser_state"
    browser_state_dir.mkdir(parents=True, exist_ok=True)
    browser_profile_dir = data_dir / "browser_profiles"
    try:
        browser_profile_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        browser_profile_dir = Path("/tmp/resume_bot_browser_profiles")
        browser_profile_dir.mkdir(parents=True, exist_ok=True)
    default_settings_default = project_root / "config" / "default_settings.json"
    workspace_default_settings = project_root / "config" / "resume_bot" / "default_settings.json"
    if workspace_default_settings.exists():
        default_settings_default = workspace_default_settings
    source_registry_default = project_root / "config" / "source_registry.example.json"
    workspace_source_registry = project_root / "config" / "resume_bot" / "source_registry.json"
    if workspace_source_registry.exists():
        source_registry_default = workspace_source_registry
    default_settings_path = Path(
        os.getenv("RESUME_BOT_DEFAULT_SETTINGS_PATH", default_settings_default)
    )
    source_registry_path = Path(
        os.getenv("RESUME_BOT_SOURCE_REGISTRY_PATH", source_registry_default)
    )
    feishu_channels = openclaw_payload.get("channels", {}).get("feishu", {})
    providers = openclaw_payload.get("models", {}).get("providers", {})
    minimax_provider = providers.get("minimax", {})
    ai_settings_path = Path(
        os.getenv(
            "RESUME_BOT_AI_SETTINGS_PATH",
            project_root / "data" / "resume_bot" / "ai_settings.local.json",
        )
    )
    local_ai_settings = _load_local_ai_settings(ai_settings_path)
    default_primary_model = (
        openclaw_payload.get("agents", {})
        .get("defaults", {})
        .get("model", {})
        .get("primary", "")
    )
    default_primary_model = default_primary_model.split("/", 1)[-1] if default_primary_model else ""
    llm_provider = (
        _setting_value(local_ai_settings, "text", "provider")
        or _first_nonempty_env("RESUME_BOT_LLM_PROVIDER")
    )
    llm_base_url = (
        _setting_value(local_ai_settings, "text", "base_url")
        or _first_nonempty_env("RESUME_BOT_LLM_BASE_URL")
        or minimax_provider.get("baseUrl", "").strip()
    )
    llm_api_key = (
        _setting_value(local_ai_settings, "text", "api_key")
        or _first_nonempty_env("RESUME_BOT_LLM_API_KEY")
        or minimax_provider.get("apiKey", "").strip()
    )
    llm_model = (
        _setting_value(local_ai_settings, "text", "model")
        or _first_nonempty_env("RESUME_BOT_LLM_MODEL")
        or default_primary_model
    )
    if not llm_provider and minimax_provider:
        llm_provider = "minimax-anthropic"
    feishu_app_id = _first_nonempty_env("RESUME_BOT_FEISHU_APP_ID", "FEISHU_APP_ID") or feishu_channels.get("appId", "").strip()
    feishu_app_secret = _first_nonempty_env("RESUME_BOT_FEISHU_APP_SECRET", "FEISHU_APP_SECRET") or feishu_channels.get("appSecret", "").strip()
    return AppConfig(
        project_root=project_root,
        data_dir=data_dir,
        debug_dir=debug_dir,
        browser_state_dir=browser_state_dir,
        browser_profile_dir=browser_profile_dir,
        db_path=db_path,
        ai_settings_path=ai_settings_path,
        default_settings_path=default_settings_path,
        source_registry_path=source_registry_path,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        vision_provider=(
            _setting_value(local_ai_settings, "vision", "provider")
            or _first_nonempty_env("RESUME_BOT_VISION_PROVIDER", "VISION_PROVIDER")
        ),
        vision_base_url=(
            _setting_value(local_ai_settings, "vision", "base_url")
            or _first_nonempty_env("RESUME_BOT_VISION_BASE_URL", "VISION_BASE_URL")
        ),
        vision_api_key=(
            _setting_value(local_ai_settings, "vision", "api_key")
            or _first_nonempty_env("RESUME_BOT_VISION_API_KEY", "VISION_API_KEY", "DASHSCOPE_API_KEY")
        ),
        vision_model=(
            _setting_value(local_ai_settings, "vision", "model")
            or _first_nonempty_env("RESUME_BOT_VISION_MODEL", "VISION_MODEL")
        ),
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        tavily_api_key=_first_nonempty_env("TAVILY_API_KEY"),
        default_user_id=os.getenv("RESUME_BOT_DEFAULT_USER_ID", "me").strip() or "me",
        enable_llm_rerank=_env_bool("RESUME_BOT_ENABLE_LLM_RERANK", True),
        llm_rerank_top_n=_env_int("RESUME_BOT_LLM_RERANK_TOP_N", 12),
        playwright_timeout_ms=_env_int("RESUME_BOT_PLAYWRIGHT_TIMEOUT_MS", 25000),
        boss_browser_state_path=Path(
            os.getenv("RESUME_BOT_BOSS_BROWSER_STATE_PATH", str(browser_state_dir / "boss_storage_state.json")).strip()
        ),
        boss_browser_profile_dir=Path(
            os.getenv(
                "RESUME_BOT_BOSS_BROWSER_PROFILE_DIR",
                str(browser_profile_dir / "boss"),
            ).strip()
        ),
        boss_browser_cdp_url=os.getenv("RESUME_BOT_BOSS_BROWSER_CDP_URL", "").strip(),
        boss_browser_cdp_port=_env_int("RESUME_BOT_BOSS_BROWSER_CDP_PORT", 9222),
        boss_browser_prefer_cdp=_env_bool("RESUME_BOT_BOSS_BROWSER_PREFER_CDP", True),
        boss_login_qr_path=Path(
            os.getenv(
                "RESUME_BOT_BOSS_LOGIN_QR_PATH",
                str(debug_dir / "boss_login_qr.png"),
            ).strip()
        ),
        boss_login_full_page_path=Path(
            os.getenv(
                "RESUME_BOT_BOSS_LOGIN_FULL_PAGE_PATH",
                str(debug_dir / "boss_login_full.png"),
            ).strip()
        ),
        boss_login_status_path=Path(
            os.getenv(
                "RESUME_BOT_BOSS_LOGIN_STATUS_PATH",
                str(debug_dir / "boss_login_status.json"),
            ).strip()
        ),
        boss_browser_headless_override=_env_optional_bool("RESUME_BOT_BOSS_BROWSER_HEADLESS"),
        boss_cli_command=os.getenv("RESUME_BOT_BOSS_CLI_CMD", "").strip(),
        boss_cli_timeout_sec=_env_int("RESUME_BOT_BOSS_CLI_TIMEOUT_SEC", 90),
    )
