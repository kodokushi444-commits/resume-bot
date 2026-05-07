#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from collections import deque
from pathlib import Path
import shutil
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from websocket import WebSocketTimeoutException, create_connection

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config
from resume_bot.job_sources.boss_browser import (
    build_boss_quick_filter_url_params,
    build_boss_search_probe_card,
    build_boss_search_url,
    extract_boss_joblist_payload,
    resolve_boss_city_code,
)
from resume_bot.job_sources.boss_common import resolve_cdp_endpoint
from resume_bot.normalization import normalize_job_fields
from resume_bot.types import utcnow_iso


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._ws = create_connection(websocket_url, timeout=1.0, suppress_origin=True)
        self._next_id = 0
        self._queue: deque[dict[str, Any]] = deque()

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def _recv_message(self, timeout: float = 0.25) -> dict[str, Any] | None:
        self._ws.settimeout(timeout)
        try:
            raw = self._ws.recv()
        except WebSocketTimeoutException:
            return None
        if not raw:
            return None
        return json.loads(raw)

    def _pop_message(self, timeout: float = 0.25) -> dict[str, Any] | None:
        if self._queue:
            return self._queue.popleft()
        return self._recv_message(timeout=timeout)

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        deferred: list[dict[str, Any]] = []
        self._ws.send(
            json.dumps(
                {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        while True:
            message = self._pop_message(timeout=1.0)
            if not message:
                continue
            if message.get("id") == message_id:
                if deferred:
                    self._queue.extendleft(reversed(deferred))
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return dict(message.get("result") or {})
            deferred.append(message)

    def iter_events(self, timeout_seconds: float):
        deadline = time.monotonic() + max(timeout_seconds, 0.1)
        while time.monotonic() < deadline:
            message = self._pop_message(timeout=0.2)
            if message:
                yield message


def _open_target(cdp_endpoint: str, url: str) -> dict[str, Any]:
    endpoint = cdp_endpoint.rstrip("/") + "/json/new?" + quote(url, safe="")
    try:
        request = Request(endpoint, method="PUT")
        with urlopen(request, timeout=5.0) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code not in {404, 405, 501}:
            raise
        with urlopen(endpoint, timeout=5.0) as response:
            return json.load(response)


def _list_targets(cdp_endpoint: str) -> list[dict[str, Any]]:
    with urlopen(cdp_endpoint.rstrip("/") + "/json/list", timeout=5.0) as response:
        return list(json.load(response) or [])


def _resolve_target_ws(cdp_endpoint: str, target_info: dict[str, Any]) -> str:
    websocket_url = str(target_info.get("webSocketDebuggerUrl") or "").strip()
    if websocket_url:
        return websocket_url
    target_id = str(target_info.get("id") or "").strip()
    target_url = str(target_info.get("url") or "").strip()
    for item in _list_targets(cdp_endpoint):
        if target_id and str(item.get("id") or "").strip() == target_id:
            websocket_url = str(item.get("webSocketDebuggerUrl") or "").strip()
            if websocket_url:
                return websocket_url
        if target_url and str(item.get("url") or "").strip() == target_url:
            websocket_url = str(item.get("webSocketDebuggerUrl") or "").strip()
            if websocket_url:
                return websocket_url
    raise RuntimeError("Failed to resolve dedicated target websocket URL.")


def _decode_response_body(result: dict[str, Any]) -> str:
    body = str(result.get("body") or "")
    if result.get("base64Encoded"):
        return base64.b64decode(body.encode("utf-8")).decode("utf-8", errors="replace")
    return body


def _get_response_body_text(client: CdpClient, request_id: str) -> str:
    for attempt in range(2):
        try:
            body_result = client.send("Network.getResponseBody", {"requestId": request_id})
            return _decode_response_body(body_result)
        except Exception as exc:
            if "No data found for resource" not in str(exc) or attempt == 1:
                raise
            time.sleep(0.25)
    raise RuntimeError("Failed to read response body.")


def _summarize_trace(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return ""
    segments = []
    for item in trace[:10]:
        event = str(item.get("event") or "").strip()
        dt_ms = int(item.get("dt_ms") or 0)
        url = str(item.get("url") or "").strip()
        if url:
            segments.append(f"{event}@{dt_ms}ms:{url}")
        else:
            segments.append(f"{event}@{dt_ms}ms")
    if len(trace) > 10:
        segments.append("...")
    return " -> ".join(segments)


def _sanitize_slug(value: str) -> str:
    cleaned = []
    for char in str(value or "").strip():
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "queue"


def _card_key(card: dict[str, Any]) -> str:
    job_id = str(card.get("job_id") or "").strip()
    if job_id:
        return f"id:{job_id}"
    job_url = str(card.get("job_url") or "").strip()
    if job_url:
        return f"url:{job_url}"
    security_id = str(card.get("security_id") or "").strip()
    if security_id:
        return f"sec:{security_id}"
    title = str(card.get("title") or "").strip()
    company_name = str(card.get("company_name") or "").strip()
    salary_text = str(card.get("salary_text") or "").strip()
    return f"fallback:{title}|{company_name}|{salary_text}"


def _merge_cards(existing_cards: list[dict[str, Any]], incoming_cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged = [dict(card) for card in existing_cards]
    index_by_key = {_card_key(card): index for index, card in enumerate(merged)}
    added = 0
    for card in incoming_cards:
        key = _card_key(card)
        payload = dict(card)
        if key in index_by_key:
            merged[index_by_key[key]].update(payload)
            continue
        index_by_key[key] = len(merged)
        merged.append(payload)
        added += 1
    return merged, added


def _build_queue_jobs(cards: list[dict[str, Any]], *, fetch_session_id: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for card in cards:
        raw_payload = dict(card)
        raw_payload["fetch_session_id"] = fetch_session_id
        raw_payload["source_name"] = "boss_browser"
        raw_payload["capture_engine"] = "boss_cdp_list_probe"
        job = normalize_job_fields(raw_payload, source="boss_browser")
        jobs.append(job.to_dict())
    return jobs


def _resolve_output_paths(
    *,
    data_dir: Path,
    city: str,
    keyword: str,
    fetch_session_id: str,
    explicit_output: str,
) -> tuple[Path, Path]:
    if explicit_output:
        output_path = Path(explicit_output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path, output_path
    queue_dir = data_dir / "queue_artifacts"
    queue_dir.mkdir(parents=True, exist_ok=True)
    session_slug = _sanitize_slug(fetch_session_id)
    city_slug = _sanitize_slug(city)
    keyword_slug = _sanitize_slug(keyword)
    output_path = queue_dir / f"{session_slug}-boss-queue-{city_slug}-{keyword_slug}.json"
    latest_path = queue_dir / f"latest-boss-queue-{city_slug}-{keyword_slug}.json"
    return output_path, latest_path


def _write_queue_artifact(artifact: dict[str, Any], *, output_path: Path, latest_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    if latest_path != output_path:
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, latest_path)


def _is_results_url(url: str) -> bool:
    normalized = str(url or "").strip()
    return "zhipin.com/web/geek/jobs" in normalized


def _flush_idle_messages(client: CdpClient, *, idle_seconds: float = 0.2, max_seconds: float = 1.0) -> int:
    deadline = time.monotonic() + max(max_seconds, idle_seconds)
    drained = 0
    while time.monotonic() < deadline:
        message = client._pop_message(timeout=idle_seconds)
        if not message:
            break
        drained += 1
    return drained


def _read_surface(client: CdpClient) -> dict[str, Any]:
    result = client.send(
        "Runtime.evaluate",
        {
            "expression": """
              (() => ({
                url: location.href,
                title: document.title,
                readyState: document.readyState,
                scrollY: window.scrollY || document.documentElement.scrollTop || 0,
                viewport: window.innerHeight || 0,
                documentHeight: document.documentElement.scrollHeight || 0
              }))()
            """,
            "returnByValue": True,
        },
    )
    return dict(result.get("result", {}).get("value") or {})


def _inject_scroll_round(client: CdpClient, *, settle_ms: int = 2600) -> dict[str, Any]:
    result = client.send(
        "Runtime.evaluate",
        {
            "expression": f"""
              (async () => {{
                const viewport = window.innerHeight || 800;
                const startY = window.scrollY || document.documentElement.scrollTop || 0;
                const maxScroll = Math.max(document.documentElement.scrollHeight - viewport, 0);
                const checkpoints = [0.55, 0.72, 0.86, 0.94, 0.985]
                  .map((ratio) => Math.max(startY, Math.floor(maxScroll * ratio)));
                let lastY = startY;
                let steps = 0;

                for (const nextY of checkpoints) {{
                  if (nextY <= lastY + 24) continue;
                  const deltaY = Math.max(nextY - lastY, 120);
                  window.dispatchEvent(new WheelEvent('wheel', {{ deltaY, bubbles: true, cancelable: true }}));
                  window.scrollTo({{ top: nextY, behavior: 'smooth' }});
                  window.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
                  await new Promise((resolve) => setTimeout(resolve, 260 + Math.floor(Math.random() * 180)));
                  lastY = window.scrollY || document.documentElement.scrollTop || nextY;
                  steps += 1;
                }}

                await new Promise((resolve) => setTimeout(resolve, {int(settle_ms)}));

                return {{
                  startY,
                  endY: window.scrollY || document.documentElement.scrollTop || 0,
                  viewport,
                  documentHeight: document.documentElement.scrollHeight || 0,
                  maxScroll,
                  steps,
                  url: location.href,
                  title: document.title
                }};
              }})()
            """,
            "awaitPromise": True,
            "returnByValue": True,
        },
    )
    return dict(result.get("result", {}).get("value") or {})


def _should_stop_after_round(
    *,
    cards_count: int,
    total_count: int | None,
    limit: int | None,
    consecutive_empty_rounds: int,
    max_empty_rounds: int,
) -> str | None:
    if limit and cards_count >= limit:
        return "reached_limit"
    if total_count and cards_count >= total_count:
        return "reached_total_count"
    if max_empty_rounds > 0 and consecutive_empty_rounds >= max_empty_rounds:
        return "max_empty_rounds"
    return None


def _wait_for_joblist(client: CdpClient, *, timeout_seconds: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start = time.monotonic()
    current_url = ""
    last_error = ""
    trace: list[dict[str, Any]] = []
    for message in client.iter_events(timeout_seconds):
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            if not frame.get("parentId"):
                current_url = str(frame.get("url") or current_url)
                trace.append({"event": "nav", "url": current_url, "dt_ms": int((time.monotonic() - start) * 1000)})
            continue
        if method == "Page.domContentEventFired":
            trace.append({"event": "domcontentloaded", "url": current_url, "dt_ms": int((time.monotonic() - start) * 1000)})
            continue
        if method == "Page.loadEventFired":
            trace.append({"event": "load", "url": current_url, "dt_ms": int((time.monotonic() - start) * 1000)})
            continue
        if method != "Network.responseReceived":
            continue
        response = params.get("response") or {}
        response_url = str(response.get("url") or "")
        if "/wapi/zpgeek/search/joblist.json" not in response_url:
            continue
        if int(response.get("status") or 0) != 200:
            last_error = f"joblist HTTP {response.get('status')} {response_url}"
            continue
        try:
            body_text = _get_response_body_text(client, str(params.get("requestId") or "")).strip()
            if not body_text:
                last_error = "joblist body was empty"
                continue
            payload = json.loads(body_text)
            extracted = extract_boss_joblist_payload(payload)
            return extracted, trace
        except Exception as exc:
            last_error = f"joblist parse failed: {exc}"
            continue
    trace_text = _summarize_trace(trace)
    detail = last_error or "joblist timeout"
    if trace_text:
        detail = f"{detail}. path={trace_text}"
    raise RuntimeError(detail)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct CDP first-page BOSS list probe.")
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--keyword", default="运营")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--open-url", default="about:blank")
    parser.add_argument("--rounds", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-empty-rounds", type=int, default=2)
    parser.add_argument("--delay-ms", type=int, default=5000)
    parser.add_argument("--settle-ms", type=int, default=2600)
    parser.add_argument("--fetch-session-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--degree-filter", default="")
    parser.add_argument("--employment-mode-filter", default="")
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _build_parser().parse_args()
    config = load_config()
    fetch_session_id = (args.fetch_session_id or "").strip() or time.strftime("%Y%m%d-%H%M%S")
    city_code = resolve_boss_city_code(args.city)
    if not city_code:
        payload = {"ok": False, "city": args.city, "keyword": args.keyword, "error": "Unknown city"}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    quick_filters = {
        "degree_filter": str(args.degree_filter or "").strip(),
        "employment_mode_filter": str(args.employment_mode_filter or "").strip(),
    }
    url_filter_params = build_boss_quick_filter_url_params(
        degree_filter=quick_filters["degree_filter"],
        employment_mode_filter=quick_filters["employment_mode_filter"],
    )
    search_url = build_boss_search_url(
        "https://www.zhipin.com/web/geek/jobs",
        keyword=args.keyword,
        city_code=city_code,
        extra_params=url_filter_params,
    )
    cdp_endpoint = resolve_cdp_endpoint(config.boss_browser_cdp_port, config.boss_browser_cdp_url, timeout_seconds=1.5)
    if not cdp_endpoint:
        payload = {
            "ok": False,
            "city": args.city,
            "keyword": args.keyword,
            "quick_filters": quick_filters,
            "url_filter_params": url_filter_params,
            "url_filter_applied": bool(url_filter_params),
            "error": "CDP endpoint unavailable",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    target_info = _open_target(cdp_endpoint, args.open_url)
    websocket_url = _resolve_target_ws(cdp_endpoint, target_info)
    client = CdpClient(websocket_url)
    try:
        client.send("Network.enable")
        client.send("Page.enable")
        client.send("Page.navigate", {"url": search_url})
        payload, trace = _wait_for_joblist(client, timeout_seconds=args.timeout)
        cards = [
            build_boss_search_probe_card(raw)
            for raw in payload.get("jobs", [])
            if isinstance(raw, dict)
        ]
        cards = [card for card in cards if card.get("job_url")]
        initial_cards = list(cards)
        rounds: list[dict[str, Any]] = []
        target_limit = max(int(args.limit or 0), 0) or None
        max_empty_rounds = max(int(args.max_empty_rounds or 0), 0)
        consecutive_empty_rounds = 0
        stop_reason = _should_stop_after_round(
            cards_count=len(cards),
            total_count=int(payload.get("total_count", 0) or 0) or None,
            limit=target_limit,
            consecutive_empty_rounds=consecutive_empty_rounds,
            max_empty_rounds=max_empty_rounds,
        )
        for round_index in range(max(args.rounds, 0)):
            if stop_reason:
                break
            drained = _flush_idle_messages(client)
            before_surface = _read_surface(client)
            if not _is_results_url(str(before_surface.get("url") or "")):
                raise RuntimeError(
                    f"scroll round {round_index + 1} lost results surface before action: {before_surface.get('url')}"
                )
            time.sleep(max(args.delay_ms, 0) / 1000.0)
            scroll_info = _inject_scroll_round(client, settle_ms=max(args.settle_ms, 0))
            after_surface = _read_surface(client)
            if not _is_results_url(str(after_surface.get("url") or "")):
                raise RuntimeError(f"scroll round {round_index + 1} left results surface: {after_surface.get('url')}")
            next_payload, round_trace = _wait_for_joblist(client, timeout_seconds=args.timeout)
            incoming_cards = [
                build_boss_search_probe_card(raw)
                for raw in next_payload.get("jobs", [])
                if isinstance(raw, dict)
            ]
            incoming_cards = [card for card in incoming_cards if card.get("job_url")]
            cards, added_count = _merge_cards(cards, incoming_cards)
            if added_count == 0:
                consecutive_empty_rounds += 1
            else:
                consecutive_empty_rounds = 0
            current_total_count = int(next_payload.get("total_count", len(cards)) or len(cards))
            rounds.append(
                {
                    "round": round_index + 1,
                    "drained_events": drained,
                    "before_surface": before_surface,
                    "scroll": scroll_info,
                    "after_surface": after_surface,
                    "raw_cards_count": len(incoming_cards),
                    "added_count": added_count,
                    "consecutive_empty_rounds": consecutive_empty_rounds,
                    "total_cards_after_round": len(cards),
                    "trace": round_trace,
                }
            )
            payload = next_payload
            stop_reason = _should_stop_after_round(
                cards_count=len(cards),
                total_count=current_total_count or None,
                limit=target_limit,
                consecutive_empty_rounds=consecutive_empty_rounds,
                max_empty_rounds=max_empty_rounds,
            )
        if not stop_reason:
            stop_reason = "completed_requested_rounds"
        jobs = _build_queue_jobs(cards, fetch_session_id=fetch_session_id)
        output_path, latest_path = _resolve_output_paths(
            data_dir=config.data_dir,
            city=args.city,
            keyword=args.keyword,
            fetch_session_id=fetch_session_id,
            explicit_output=str(args.output or "").strip(),
        )
        artifact = {
            "artifact_type": "boss_cdp_queue",
            "artifact_version": 1,
            "created_at": utcnow_iso(),
            "fetch_session_id": fetch_session_id,
            "engine": "boss_cdp_list_probe",
            "source_name": "boss_browser",
            "city": args.city,
            "city_code": city_code,
            "keyword": args.keyword,
            "search_url": search_url,
            "quick_filters": quick_filters,
            "url_filter_params": url_filter_params,
            "url_filter_applied": bool(url_filter_params),
            "open_url": args.open_url,
            "target_id": str(target_info.get("id") or ""),
            "target_url": str(target_info.get("url") or ""),
            "limit": target_limit,
            "max_empty_rounds": max_empty_rounds,
            "rounds_requested": max(args.rounds, 0),
            "rounds_completed": len(rounds),
            "stop_reason": stop_reason,
            "initial_cards_count": len(initial_cards),
            "cards_count": len(cards),
            "jobs_count": len(jobs),
            "total_count": int(payload.get("total_count", len(cards)) or len(cards)),
            "trace": trace,
            "rounds": rounds,
            "cards": cards,
            "jobs": jobs,
        }
        _write_queue_artifact(artifact, output_path=output_path, latest_path=latest_path)
        result = {
            "ok": True,
            "city": args.city,
            "city_code": city_code,
            "keyword": args.keyword,
            "search_url": search_url,
            "quick_filters": quick_filters,
            "url_filter_params": url_filter_params,
            "url_filter_applied": bool(url_filter_params),
            "open_url": args.open_url,
            "target_id": str(target_info.get("id") or ""),
            "target_url": str(target_info.get("url") or ""),
            "limit": target_limit,
            "max_empty_rounds": max_empty_rounds,
            "fetch_session_id": fetch_session_id,
            "output_path": str(output_path),
            "latest_output_path": str(latest_path),
            "rounds_requested": max(args.rounds, 0),
            "rounds_completed": len(rounds),
            "stop_reason": stop_reason,
            "initial_cards_count": len(initial_cards),
            "cards_count": len(cards),
            "jobs_count": len(jobs),
            "total_count": int(payload.get("total_count", len(cards)) or len(cards)),
            "cards": cards[:20],
            "trace": trace,
            "rounds": rounds,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    except Exception as exc:
        result = {
            "ok": False,
            "city": args.city,
            "city_code": city_code,
            "keyword": args.keyword,
            "search_url": search_url,
            "open_url": args.open_url,
            "target_id": str(target_info.get("id") or ""),
            "target_url": str(target_info.get("url") or ""),
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
