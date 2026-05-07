from __future__ import annotations

from .config import AppConfig


def llm_runtime_warning(config: AppConfig) -> str:
    _ = config
    return ""
