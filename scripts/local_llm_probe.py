#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config


def main() -> int:
    config = load_config()
    if config.llm_provider != "minimax-anthropic":
        print(json.dumps({"error": f"unsupported_provider:{config.llm_provider}"}, ensure_ascii=False, indent=2))
        return 1
    response = requests.post(
        f"{config.llm_base_url.rstrip('/')}/v1/messages",
        headers={
            "x-api-key": config.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.llm_model,
            "system": "You are a health check assistant. Reply with OK only.",
            "max_tokens": 32,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Reply with OK only.",
                        }
                    ],
                }
            ],
        },
        timeout=120,
    )
    print("status_code=", response.status_code)
    try:
        payload = response.json()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
