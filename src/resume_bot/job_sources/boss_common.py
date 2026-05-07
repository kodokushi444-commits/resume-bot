from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from ..types import utcnow_iso


LOGIN_PAGE_HINTS = ["登录", "注册", "验证码", "安全验证", "扫码登录", "BOSS直聘注册登录"]
LOGIN_URL_HINTS = ["/login", "/register", "/security-check", "/captcha", "/verify", "/web/user"]
PROFILE_READY_MARKER = ".resume_bot_boss_profile_ready"
QR_SELECTORS = [
    "[class*=qr] canvas",
    "[class*=qr] img",
    "[class*=scan] canvas",
    "[class*=scan] img",
    "img[src*='qr']",
    "img[src*='qrcode']",
    "canvas",
]
SECURITY_VERIFY_HINTS = ["安全验证", "异常访问行为", "完成验证后即可正常使用", "点击按钮进行验证"]
LOADING_HINTS = ["加载中，请稍候"]


def build_boss_launch_kwargs(*, headless: bool) -> dict:
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--lang=zh-CN",
        "--window-size=1440,1000",
        "--start-maximized",
        "--disable-features=Translate,AcceptCHFrame,MediaRouter",
        "--disable-infobars",
        "--password-store=basic",
    ]
    return {
        "headless": headless,
        "ignore_default_args": ["--enable-automation"],
        "args": launch_args,
    }


def build_boss_context_kwargs() -> dict:
    return {
        "viewport": {"width": 1440, "height": 1000},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    }


def _detect_windows_host_ip() -> str:
    if os.name == "nt":
        return "127.0.0.1"
    resolv_conf = Path("/etc/resolv.conf")
    if resolv_conf.exists():
        for line in resolv_conf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("nameserver "):
                return line.split(None, 1)[1].strip()
    return "127.0.0.1"


def candidate_cdp_urls(port: int, explicit: str = "") -> list[str]:
    if explicit.strip():
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


def resolve_cdp_endpoint(port: int, explicit: str = "", timeout_seconds: float = 2.0) -> str:
    if explicit.strip().startswith("ws://") or explicit.strip().startswith("wss://"):
        return explicit.strip()
    for url in candidate_cdp_urls(port, explicit):
        version_url = url.rstrip("/") + "/json/version"
        try:
            with urlopen(version_url, timeout=timeout_seconds) as response:
                if response.status == 200:
                    return url.rstrip("/")
        except URLError:
            continue
        except Exception:
            continue
    return ""


def resolve_cdp_websocket_url(port: int, explicit: str = "", timeout_seconds: float = 2.0) -> str:
    if explicit.strip().startswith("ws://") or explicit.strip().startswith("wss://"):
        return explicit.strip()
    for url in candidate_cdp_urls(port, explicit):
        version_url = url.rstrip("/") + "/json/version"
        try:
            with urlopen(version_url, timeout=timeout_seconds) as response:
                if response.status != 200:
                    continue
                payload = json.load(response)
        except URLError:
            continue
        except Exception:
            continue
        websocket_url = str(payload.get("webSocketDebuggerUrl", "") or "").strip()
        if websocket_url:
            return websocket_url
    return ""


def install_boss_stealth(context) -> None:
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
        Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'plugins', {
          get: () => [{ name: 'Chrome PDF Plugin' }, { name: 'Chrome PDF Viewer' }, { name: 'Native Client' }]
        });
        window.chrome = window.chrome || { runtime: {} };
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
          window.navigator.permissions.query = (parameters) => (
            parameters && parameters.name === 'notifications'
              ? Promise.resolve({ state: Notification.permission })
              : originalQuery(parameters)
          );
        }
        """
    )


def looks_like_login_page(url: str, title: str, body_text: str) -> bool:
    lowered_url = (url or "").lower()
    if any(token in lowered_url for token in LOGIN_URL_HINTS):
        return True
    combined = f"{title or ''}\n{(body_text or '')[:2000]}"
    return any(keyword in combined for keyword in LOGIN_PAGE_HINTS)


def is_security_verify_page(url: str, title: str, body_text: str) -> bool:
    lowered_url = (url or "").lower()
    if "verify.html" in lowered_url or "code=35" in lowered_url:
        return True
    combined = f"{title or ''}\n{(body_text or '')[:2000]}"
    return any(keyword in combined for keyword in SECURITY_VERIFY_HINTS)


def looks_like_jobs_skeleton_page(url: str, title: str, body_text: str) -> bool:
    lowered_url = (url or "").lower()
    if "zhipin.com" not in lowered_url:
        return False
    if "/web/geek/jobs" not in lowered_url and "/web/geek/job" not in lowered_url:
        return False
    normalized_title = (title or "").strip()
    normalized_body = (body_text or "").strip()
    if normalized_body:
        return False
    return not looks_like_login_page(url, normalized_title, normalized_body) and not is_security_verify_page(
        url,
        normalized_title,
        normalized_body,
    )


def is_loading_page(url: str, title: str, body_text: str) -> bool:
    lowered_url = (url or "").lower()
    if "zhipin.com" not in lowered_url:
        return False
    combined = f"{title or ''}\n{(body_text or '')[:2000]}"
    return any(keyword in combined for keyword in LOADING_HINTS) or looks_like_jobs_skeleton_page(
        url,
        title,
        body_text,
    )


def extract_page_snapshot(page) -> dict:
    title = ""
    body_text = ""
    url = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        body_text = page.locator("body").inner_text()
    except Exception:
        body_text = ""
    try:
        url = page.url
    except Exception:
        url = ""
    is_boss_domain = "zhipin.com" in (url or "").lower()
    is_blank = (not url or url == "about:blank") and not title and not body_text.strip()
    is_security_verify = is_security_verify_page(url, title, body_text)
    is_loading = is_loading_page(url, title, body_text)
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
        "body_excerpt": body_text[:2000],
        "is_boss_domain": is_boss_domain,
        "is_blank": is_blank,
        "is_security_verify": is_security_verify,
        "is_loading": is_loading,
        "page_state": page_state,
        "logged_in": page_state == "ready",
    }


def save_login_artifacts(
    page,
    *,
    qr_path: Path,
    full_page_path: Path,
    status_path: Path,
    state: str,
    note: str = "",
    extra: dict | None = None,
) -> dict:
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    full_page_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = extract_page_snapshot(page)
    qr_selector = ""

    try:
        page.screenshot(path=str(full_page_path), full_page=True)
    except Exception:
        pass

    for selector in QR_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() <= 0:
                continue
            locator.screenshot(path=str(qr_path))
            qr_selector = selector
            break
        except Exception:
            continue

    if not qr_selector and full_page_path.exists():
        try:
            shutil.copyfile(full_page_path, qr_path)
        except Exception:
            pass

    payload = {
        **snapshot,
        "state": state,
        "note": note,
        "qr_path": str(qr_path),
        "full_page_path": str(full_page_path),
        "qr_selector": qr_selector,
        "generated_at": utcnow_iso(),
    }
    if extra:
        payload.update(extra)
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
