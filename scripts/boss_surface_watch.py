from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from src.resume_bot.job_sources.boss_common import resolve_cdp_websocket_url
from src.resume_bot.types import utcnow_iso


def _sample_page(page, *, label: str) -> dict:
    url = ""
    title = ""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    try:
        surface = page.evaluate(
            r"""
            () => {
              const normalize = (v) => String(v || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
              const textOf = (n) => normalize(n ? (n.innerText || n.textContent || '') : '');
              const isVisible = (node) => {
                if (!node) return false;
                const style = window.getComputedStyle(node);
                if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = node.getBoundingClientRect();
                return rect.width > 40 && rect.height > 12;
              };
              return {
                body_excerpt: textOf(document.body).slice(0, 300),
                job_link_count: document.querySelectorAll('a[href*="/job_detail"], a[href*="job_detail"]').length,
                visible_card_count: Array.from(
                  document.querySelectorAll(
                    'li.job-card-box, [class*="job-card-box"], [class*="job-card-wrap"], [class*="job-list-item"], [class*="search-job-result"]'
                  )
                ).filter(isVisible).length,
                skeleton_count: document.querySelectorAll('[class*="skeleton"], [class*="loading"], [class*="placeholder"]').length,
              };
            }
            """
        )
    except Exception as exc:
        surface = {"surface_error": str(exc)}
    return {
        "label": label,
        "url": url,
        "title": title[:120],
        **surface,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only watch of the currently attached BOSS tab.")
    parser.add_argument("--seconds", type=float, default=3.0, help="Total watch duration in seconds.")
    parser.add_argument("--interval-ms", type=int, default=1000, help="Sampling interval in milliseconds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    ws = resolve_cdp_websocket_url(9222, "", timeout_seconds=1.5)
    if not ws:
        print(json.dumps({"ok": False, "error": "no_cdp"}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(ws)
        try:
            page = None
            for context in browser.contexts:
                for candidate in context.pages:
                    try:
                        url = candidate.url or ""
                    except Exception:
                        continue
                    if "/web/geek/jobs" in url:
                        page = candidate
                        break
                if page is not None:
                    break
            if page is None:
                for context in browser.contexts:
                    for candidate in context.pages:
                        try:
                            url = candidate.url or ""
                        except Exception:
                            continue
                        if "zhipin.com" in url:
                            page = candidate
                            break
                    if page is not None:
                        break
            if page is None:
                print(json.dumps({"ok": False, "error": "no_boss_page"}, ensure_ascii=False, indent=2 if args.pretty else None))
                return 1

            interval_seconds = max(args.interval_ms, 100) / 1000.0
            total_seconds = max(args.seconds, interval_seconds)
            sample_count = max(2, int(total_seconds / interval_seconds) + 1)
            samples = []
            for index in range(sample_count):
                samples.append(_sample_page(page, label=f"t{index}"))
                if index < sample_count - 1:
                    time.sleep(interval_seconds)

            payload = {
                "ok": True,
                "captured_at": utcnow_iso(),
                "seconds": total_seconds,
                "interval_ms": int(args.interval_ms),
                "samples": samples,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
