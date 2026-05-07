#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config
from resume_bot.job_sources.boss_common import (
    PROFILE_READY_MARKER,
    build_boss_context_kwargs,
    build_boss_launch_kwargs,
    extract_page_snapshot,
    install_boss_stealth,
    save_login_artifacts,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open a local BOSS browser, log in manually, then keep the profile for later scraping.")
    parser.add_argument("--entry-url", default="https://www.zhipin.com/")
    parser.add_argument("--channel", default="auto", choices=["auto", "chrome", "msedge", "chromium"])
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    config = load_config()
    profile_dir = config.boss_browser_profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright。先执行：pip install -r requirements.txt", file=sys.stderr)
        print("然后执行：python -m playwright install chromium", file=sys.stderr)
        return 1

    launch_kwargs = build_boss_launch_kwargs(headless=False)
    if args.channel == "auto":
        if os.name == "nt":
            launch_kwargs["channel"] = "chrome"
    elif args.channel != "chromium":
        launch_kwargs["channel"] = args.channel

    context_kwargs = build_boss_context_kwargs()
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_kwargs,
                **context_kwargs,
            )
            install_boss_stealth(context)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.entry_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            print("浏览器已经打开。请在弹出的窗口里手动登录 BOSS。")
            print("登录完成后，回到这里按回车保存。")
            input()
            snapshot = extract_page_snapshot(page)
            artifact = save_login_artifacts(
                page,
                qr_path=config.boss_login_qr_path,
                full_page_path=config.boss_login_full_page_path,
                status_path=config.boss_login_status_path,
                state="local_profile_saved" if snapshot["logged_in"] else snapshot["page_state"],
                note="本地 Windows 登录完成后保存了浏览器 profile。",
                extra={"profile_dir": str(profile_dir)},
            )
            if snapshot["logged_in"]:
                (profile_dir / PROFILE_READY_MARKER).write_text("ready\n", encoding="utf-8")
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            context.close()
    except PlaywrightError as exc:
        print(f"BOSS 本地登录失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
