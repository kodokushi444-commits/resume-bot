#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config


DEFAULT_PORT = 9222


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Two-step BOSS login helper: first launch a normal Windows browser, then export storage_state."
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    launch = subparsers.add_parser("launch-windows-browser")
    launch.add_argument("--browser", default="chrome", choices=["edge", "chrome"])
    launch.add_argument("--entry-url", default="https://www.zhipin.com/")
    launch.add_argument("--port", type=int, default=DEFAULT_PORT)
    launch.add_argument("--windows-browser-path", default="")
    launch.add_argument("--windows-user-data-dir", default="")

    launch = subparsers.add_parser("launch-windows-chrome")
    launch.add_argument("--entry-url", default="https://www.zhipin.com/")
    launch.add_argument("--port", type=int, default=DEFAULT_PORT)
    launch.add_argument("--windows-chrome-path", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    launch.add_argument("--windows-user-data-dir", default="")

    export = subparsers.add_parser("export-state")
    export.add_argument("--output", default="", help="Where to save the BOSS storage_state JSON")
    export.add_argument(
        "--upload-target",
        default="",
        help="Optional scp target like root@1.2.3.4:/root/.openclaw/workspace/data/resume_bot/browser_state/boss_storage_state.json",
    )
    export.add_argument("--cdp-url", default="", help="Attach to an already running browser via CDP")
    export.add_argument("--port", type=int, default=DEFAULT_PORT)

    import_cookies = subparsers.add_parser("import-cookies")
    import_cookies.add_argument("--input", required=True, help="Cookie export JSON file path")
    import_cookies.add_argument("--output", default="", help="Where to save the BOSS storage_state JSON")
    import_cookies.add_argument("--upload-target", default="")

    legacy = subparsers.add_parser("legacy-capture")
    legacy.add_argument("--output", default="", help="Where to save the BOSS storage_state JSON")
    legacy.add_argument("--upload-target", default="")
    legacy.add_argument("--entry-url", default="https://www.zhipin.com/")
    legacy.add_argument("--channel", default="chrome", choices=["chrome", "msedge", "chromium"])
    legacy.add_argument("--headless", action="store_true")

    return parser


def _detect_windows_host_ip() -> str:
    resolv_conf = Path("/etc/resolv.conf")
    if resolv_conf.exists():
        for line in resolv_conf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("nameserver "):
                return line.split(None, 1)[1].strip()
    return "127.0.0.1"


def _candidate_cdp_urls(port: int, explicit: str = "") -> list[str]:
    if explicit:
        return [explicit.rstrip("/")]
    host_ip = _detect_windows_host_ip()
    candidates = [
        f"http://127.0.0.1:{port}",
        f"http://{host_ip}:{port}",
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _wait_for_any_cdp(urls: list[str], timeout_seconds: int = 25) -> str:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        for url in urls:
            version_url = url.rstrip("/") + "/json/version"
            try:
                with urlopen(version_url, timeout=2) as response:
                    if response.status == 200:
                        return url.rstrip("/")
            except URLError as exc:
                last_error = str(exc)
            except Exception as exc:
                last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"没等到浏览器调试端口就绪：{', '.join(urls)} ({last_error or 'unknown'})")


def _to_windows_path(path: Path | str) -> str:
    text = str(path)
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", text)
    if not match:
        return text
    drive, remainder = match.groups()
    converted = remainder.replace("/", "\\")
    return f"{drive.upper()}:\\{converted}"


def _default_windows_browser_path(browser: str) -> str:
    if browser == "chrome":
        return r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    return r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _default_windows_user_data_dir(browser: str) -> str:
    config = load_config()
    return _to_windows_path(config.browser_profile_dir / f"boss_cdp_{browser}")


def _launch_windows_browser(entry_url: str, port: int, windows_browser_path: str, windows_user_data_dir: str) -> None:
    argument_list = (
        f"--remote-debugging-port={port} "
        "--remote-debugging-address=0.0.0.0 "
        f"--user-data-dir=\"{windows_user_data_dir}\" "
        f"\"{entry_url}\""
    )
    command = (
        "Start-Process "
        f"-FilePath '{windows_browser_path}' "
        f"-ArgumentList '{argument_list}'"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], check=True)


def _resolve_output_path(explicit: str) -> Path:
    config = load_config()
    output = Path(explicit).expanduser() if explicit else config.boss_browser_state_path
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _upload_if_needed(file_path: Path, upload_target: str) -> None:
    if not upload_target:
        print(f"已保存到本地：{file_path}")
        print("如果要同步到腾讯云，可以稍后手动 scp 这个 JSON 文件。")
        return
    try:
        subprocess.run(["scp", str(file_path), upload_target], check=True)
        print(f"已上传到：{upload_target}")
    except subprocess.CalledProcessError:
        print(f"自动上传失败。请手动上传这个文件：{file_path}", file=sys.stderr)
        print(f"目标位置：{upload_target}", file=sys.stderr)
        raise


def _normalize_same_site(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"strict", "lax", "none"}:
        return normalized.capitalize()
    return "Lax"


def _run_import_cookies(input_arg: str, output_arg: str, upload_target: str) -> int:
    input_path = Path(input_arg).expanduser()
    if not input_path.exists():
        print(f"找不到输入文件：{input_path}", file=sys.stderr)
        return 1
    output = _resolve_output_path(output_arg)
    payload = input_path.read_text(encoding="utf-8")
    try:
        raw = json.loads(payload)
    except Exception as exc:
        print(f"Cookie JSON 解析失败：{exc}", file=sys.stderr)
        return 1
    if isinstance(raw, dict) and "cookies" in raw:
        raw_cookies = raw.get("cookies", [])
    elif isinstance(raw, list):
        raw_cookies = raw
    else:
        print("不认识这个 Cookie 导出格式。请导出 JSON 数组，或包含 cookies 字段的 JSON。", file=sys.stderr)
        return 1
    cookies: list[dict] = []
    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", ""))
        domain = str(item.get("domain", "")).strip()
        path = str(item.get("path", "/")).strip() or "/"
        if not name or not domain:
            continue
        expires = item.get("expires", item.get("expirationDate", item.get("expiry", -1)))
        try:
            expires = int(float(expires))
        except Exception:
            expires = -1
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "expires": expires,
                "httpOnly": bool(item.get("httpOnly", False)),
                "secure": bool(item.get("secure", False)),
                "sameSite": _normalize_same_site(str(item.get("sameSite", ""))),
            }
        )
    if not cookies:
        print("没有从导出文件里读到可用 Cookie。", file=sys.stderr)
        return 1
    output.write_text(json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 storage_state：{output}")
    _upload_if_needed(output, upload_target)
    return 0


def _run_export_state(output_arg: str, upload_target: str, cdp_url: str, port: int) -> int:
    output = _resolve_output_path(output_arg)
    urls = _candidate_cdp_urls(port, cdp_url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright。先执行：pip3 install -r requirements.txt", file=sys.stderr)
        print("然后执行：python3 -m playwright install chromium", file=sys.stderr)
        return 1
    resolved_cdp_url = _wait_for_any_cdp(urls)
    print(f"已连接浏览器调试地址：{resolved_cdp_url}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(resolved_cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.storage_state(path=str(output))
    print(f"已保存 BOSS 登录态：{output}")
    _upload_if_needed(output, upload_target)
    return 0


def _run_legacy_capture(output_arg: str, upload_target: str, entry_url: str, channel: str, headless: bool) -> int:
    output = _resolve_output_path(output_arg)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright。先执行：pip3 install -r requirements.txt", file=sys.stderr)
        print("然后执行：python3 -m playwright install chromium", file=sys.stderr)
        return 1

    print("即将打开浏览器，请手动登录 BOSS。登录完成后回到终端按回车。")
    print(f"登录态将保存到：{output}")
    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": headless,
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled", "--start-maximized"],
        }
        if channel != "chromium":
            launch_kwargs["channel"] = channel
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined,
            });
            """
        )
        page.goto(entry_url, wait_until="domcontentloaded")
        input("登录完成后按回车保存登录态...")
        context.storage_state(path=str(output))
        context.close()
        browser.close()
    print(f"已保存 BOSS 登录态：{output}")
    _upload_if_needed(output, upload_target)
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    command = args.command or "legacy-capture"

    if command == "launch-windows-browser":
        windows_browser_path = args.windows_browser_path or _default_windows_browser_path(args.browser)
        windows_user_data_dir = args.windows_user_data_dir or _default_windows_user_data_dir(args.browser)
        _launch_windows_browser(args.entry_url, args.port, windows_browser_path, windows_user_data_dir)
        urls = _candidate_cdp_urls(args.port)
        print(f"Windows {args.browser} 已尝试启动。")
        print("现在请在弹出的 Windows 浏览器里手动登录 BOSS。")
        print("登录完成后，不要关浏览器。")
        print("然后执行第二步导出命令。")
        print(f"浏览器程序：{windows_browser_path}")
        print(f"浏览器数据目录：{windows_user_data_dir}")
        print("可能可用的调试地址：")
        for url in urls:
            print(f"- {url}")
        return 0
    if command == "launch-windows-chrome":
        windows_user_data_dir = args.windows_user_data_dir or _default_windows_user_data_dir("chrome")
        _launch_windows_browser(args.entry_url, args.port, args.windows_chrome_path, windows_user_data_dir)
        urls = _candidate_cdp_urls(args.port)
        print("Windows Chrome 已尝试启动。")
        print("现在请在弹出的 Windows Chrome 里手动登录 BOSS。")
        print("登录完成后，不要关浏览器。")
        print("然后执行第二步导出命令。")
        print(f"浏览器程序：{args.windows_chrome_path}")
        print(f"浏览器数据目录：{windows_user_data_dir}")
        print("可能可用的调试地址：")
        for url in urls:
            print(f"- {url}")
        return 0
    if command == "export-state":
        return _run_export_state(args.output, args.upload_target, args.cdp_url, args.port)
    if command == "import-cookies":
        return _run_import_cookies(args.input, args.output, args.upload_target)
    if command == "legacy-capture":
        return _run_legacy_capture(args.output, args.upload_target, args.entry_url, args.channel, args.headless)

    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
