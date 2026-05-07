#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from boss_cdp_list_probe import CdpClient, _open_target, _resolve_target_ws
from resume_bot.config import load_config
from resume_bot.job_sources.boss_browser import (
    build_boss_detail_card_from_job,
    build_boss_detail_page_payload,
    build_boss_detail_url,
    extract_boss_detail_api_payload,
)
from resume_bot.job_sources.boss_common import resolve_cdp_endpoint
from resume_bot.normalization import is_job_quality_acceptable, job_quality_issues, normalize_job_fields
from resume_bot.types import JobPosting, utcnow_iso


def _sanitize_slug(value: str) -> str:
    cleaned = []
    for char in str(value or "").strip():
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "detail"


def _resolve_output_paths(*, debug_dir: Path, fetch_session_id: str, explicit_output: str) -> tuple[Path, Path]:
    if explicit_output:
        output_path = Path(explicit_output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path, output_path
    debug_dir.mkdir(parents=True, exist_ok=True)
    session_slug = _sanitize_slug(fetch_session_id)
    output_path = debug_dir / f"{session_slug}-boss-cdp-detail-probe.json"
    latest_path = debug_dir / "latest-boss-cdp-detail-probe.json"
    return output_path, latest_path


def _write_artifact(artifact: dict[str, Any], *, output_path: Path, latest_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    if latest_path != output_path:
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, latest_path)


def _close_target(cdp_endpoint: str, target_id: str) -> None:
    normalized_id = str(target_id or "").strip()
    if not normalized_id:
        return
    endpoint = cdp_endpoint.rstrip("/") + "/json/close/" + quote(normalized_id, safe="")
    try:
        request = Request(endpoint, method="PUT")
        with urlopen(request, timeout=5.0):
            return
    except HTTPError as exc:
        if exc.code not in {404, 405, 501}:
            return
    except Exception:
        return
    try:
        with urlopen(endpoint, timeout=5.0):
            return
    except Exception:
        return


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"jobs": payload}
    raise ValueError("补抓输入文件必须是对象或数组。")


def _load_jobs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("补抓输入缺少 jobs 列表。")
    return [dict(item) for item in jobs if isinstance(item, dict)]


def _navigate_to_page(client: CdpClient, url: str, *, wait_ms: int) -> None:
    client.send("Page.navigate", {"url": str(url or "").strip()})
    time.sleep(max(wait_ms, 0) / 1000.0)


def _inject_page_activity(client: CdpClient, *, wait_ms: int) -> dict[str, Any]:
    result = client.send(
        "Runtime.evaluate",
        {
            "expression": f"""
              (async () => {{
                const body = document.body;
                const root = document.scrollingElement || document.documentElement;
                const viewportHeight = window.innerHeight || 800;
                const maxScroll = Math.max(root.scrollHeight - viewportHeight, 0);
                const steps = [
                  Math.floor(maxScroll * 0.12),
                  Math.floor(maxScroll * 0.26),
                  Math.floor(maxScroll * 0.18)
                ];

                window.focus();
                document.dispatchEvent(new Event('visibilitychange', {{ bubbles: true }}));
                body?.dispatchEvent(new MouseEvent('mousemove', {{ clientX: 180, clientY: 220, bubbles: true }}));
                body?.dispatchEvent(new MouseEvent('mousemove', {{ clientX: 420, clientY: 260, bubbles: true }}));
                body?.dispatchEvent(new MouseEvent('mouseover', {{ clientX: 420, clientY: 260, bubbles: true }}));

                const selection = window.getSelection?.();
                if (selection) selection.removeAllRanges();

                for (const nextY of steps) {{
                  const deltaY = Math.max(120, nextY - (window.scrollY || 0));
                  window.dispatchEvent(new WheelEvent('wheel', {{ deltaY, bubbles: true, cancelable: true }}));
                  window.scrollTo({{ top: Math.max(0, Math.min(nextY, maxScroll)), behavior: 'smooth' }});
                  window.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
                  await new Promise((resolve) => setTimeout(resolve, 180));
                }}

                const backtrack = Math.max(0, Math.floor(maxScroll * 0.08));
                window.dispatchEvent(new WheelEvent('wheel', {{ deltaY: -160, bubbles: true, cancelable: true }}));
                window.scrollTo({{ top: backtrack, behavior: 'smooth' }});
                window.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
                await new Promise((resolve) => setTimeout(resolve, {max(wait_ms, 0)}));

                return {{
                  href: location.href,
                  finalY: window.scrollY || 0,
                  maxScroll
                }};
              }})()
            """,
            "awaitPromise": True,
            "returnByValue": True,
        },
    )
    return dict(((result.get("result") or {}).get("value")) or {})


def _extract_page_probe_fields(client: CdpClient) -> dict[str, Any]:
    result = client.send(
        "Runtime.evaluate",
        {
            "expression": """
              (() => {
                const normalize = (value) => String(value || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                const value = window._jobInfo || null;
                if (!value || typeof value !== 'object') return {};
                return {
                  encryptId: normalize(value.encryptId || ''),
                  securityId: normalize(value.securityId || ''),
                  lid: normalize(value.lid || ''),
                  jobName: normalize(value.jobName || ''),
                  brandName: normalize(value.brandName || value.brand || ''),
                  locationName: normalize(value.locationName || value.cityName || ''),
                  salaryDesc: normalize(value.salaryDesc || ''),
                  degreeName: normalize(value.degreeName || ''),
                  experienceName: normalize(value.experienceName || ''),
                  postDescription: normalize(value.postDescription || value.postDescriptionHtml || ''),
                  positionLabels: Array.isArray(value.positionLabels)
                    ? value.positionLabels.map((item) => normalize(item)).filter(Boolean)
                    : [],
                };
              })()
            """,
            "returnByValue": True,
        },
    )
    return dict(((result.get("result") or {}).get("value")) or {})


def _fetch_detail_payload(client: CdpClient, detail_url: str) -> dict[str, Any]:
    result = client.send(
        "Runtime.evaluate",
        {
            "expression": f"""
              (async () => {{
                const response = await fetch({json.dumps(detail_url)}, {{
                  credentials: 'include',
                  headers: {{
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'zh-CN,zh;q=0.9',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                  }},
                }});
                return {{
                  status: response.status,
                  body: await response.text(),
                }};
              }})()
            """,
            "awaitPromise": True,
            "returnByValue": True,
        },
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(str((result.get("exceptionDetails") or {}).get("text") or "详情接口执行失败。"))
    payload = dict(((result.get("result") or {}).get("value")) or {})
    status = int(payload.get("status") or 0)
    if status != 200:
        raise RuntimeError(f"详情接口返回 HTTP {status}。")
    return json.loads(str(payload.get("body") or ""))


def _build_detail_result(
    *,
    card: dict[str, Any],
    page_detail: dict[str, Any],
    job_payload: dict[str, Any],
    query: str,
    detail_url: str,
    detail_api: dict[str, Any],
    fetch_error: str,
    detail_fetch_mode: str,
) -> dict[str, Any]:
    job_url = str(card.get("job_url") or "").strip()
    payload = build_boss_detail_page_payload(
        card,
        page_detail,
        detail_api,
        query=query,
        page_url=job_url,
    )
    base_payload = dict(job_payload or {})
    merged_payload = {
        **base_payload,
        **payload,
        "source": str(base_payload.get("source") or "boss_browser").strip() or "boss_browser",
        "fetch_session_id": str(base_payload.get("fetch_session_id") or payload.get("fetch_session_id") or "").strip(),
        "job_type": str(base_payload.get("job_type") or payload.get("job_type") or "").strip(),
        "employment_mode": str(base_payload.get("employment_mode") or payload.get("employment_mode") or "").strip(),
        "application_status": str(base_payload.get("application_status") or payload.get("application_status") or "").strip()
        or "unknown",
        "raw_payload": {
            **dict(base_payload.get("raw_payload") or {}),
            **dict(payload.get("raw_payload") or {}),
            "detail_supplemented": True,
            "detail_fetch_mode": detail_fetch_mode,
        },
    }
    job = normalize_job_fields(merged_payload, source=merged_payload["source"])
    result = {
        "ok": bool(job.detail_fetched and is_job_quality_acceptable(job)),
        "job_url": job.url,
        "job_id": job.source_job_id,
        "title": job.title,
        "company_name": job.company_name,
        "salary_text": job.salary_text,
        "description_len": len(job.description or ""),
        "detail_url": detail_url,
        "detail_code": detail_api.get("code"),
        "detail_message": detail_api.get("message", ""),
        "quality_issues": job_quality_issues(job),
        "job": job.to_dict(),
    }
    if fetch_error:
        result["error"] = fetch_error
    return result


def _should_retry(result: dict[str, Any], detail_api: dict[str, Any], *, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False
    if str(result.get("error") or "").strip():
        return True
    if bool(detail_api.get("retryable")):
        return True
    return False


def _supplement_one_job(
    client: CdpClient,
    *,
    job_payload: dict[str, Any],
    query: str,
    max_attempts: int,
    navigate_wait_ms: int,
    activity_wait_ms: int,
    retry_wait_ms: int,
) -> dict[str, Any]:
    card = build_boss_detail_card_from_job(job_payload)
    job_url = str(card.get("job_url") or "").strip()
    if not job_url:
        return {
            "ok": False,
            "job_url": "",
            "title": str(card.get("title") or "").strip(),
            "company_name": str(card.get("company_name") or "").strip(),
            "error": "岗位缺少详情页链接。",
            "quality_issues": ["missing_url"],
        }

    last_result: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        detail_api: dict[str, Any] = {}
        fetch_error = ""
        page_detail: dict[str, Any] = {}
        detail_url = build_boss_detail_url(dict(card or {}))
        try:
            _navigate_to_page(client, job_url, wait_ms=navigate_wait_ms)
            _inject_page_activity(client, wait_ms=activity_wait_ms)
            page_detail = _extract_page_probe_fields(client)
            detail_url = build_boss_detail_url({**dict(card or {}), **page_detail}) or detail_url
            if not detail_url:
                fetch_error = "无法拼出 detail 接口地址。"
            else:
                detail_api = extract_boss_detail_api_payload(_fetch_detail_payload(client, detail_url))
        except Exception as exc:
            fetch_error = str(exc)

        last_result = _build_detail_result(
            card={**dict(card or {}), **page_detail},
            page_detail=page_detail,
            job_payload=job_payload,
            query=query,
            detail_url=detail_url,
            detail_api=detail_api,
            fetch_error=fetch_error,
            detail_fetch_mode="cdp_dedicated_target",
        )
        last_result["attempt"] = attempt
        if last_result.get("ok"):
            return last_result
        if not _should_retry(last_result, detail_api, attempt=attempt, max_attempts=max_attempts):
            return last_result
        time.sleep(max(retry_wait_ms, 0) / 1000.0)
    return last_result or {
        "ok": False,
        "job_url": job_url,
        "title": str(card.get("title") or "").strip(),
        "company_name": str(card.get("company_name") or "").strip(),
        "error": "详情补抓未返回结果。",
        "quality_issues": ["boss_detail_not_fetched"],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dedicated CDP BOSS detail supplement probe.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--open-url", default="about:blank")
    parser.add_argument("--output", default="")
    parser.add_argument("--fetch-session-id", default="")
    parser.add_argument("--navigate-wait-ms", type=int, default=2400)
    parser.add_argument("--activity-wait-ms", type=int, default=1400)
    parser.add_argument("--retry-wait-ms", type=int, default=2200)
    parser.add_argument("--max-attempts", type=int, default=3)
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _build_parser().parse_args()
    config = load_config()
    input_path = Path(args.input).expanduser()
    payload = _read_json_file(input_path)
    jobs = _load_jobs(payload)
    fetch_session_id = (
        str(args.fetch_session_id or "").strip()
        or str(payload.get("fetch_session_id") or "").strip()
        or time.strftime("%Y%m%d-%H%M%S")
    )
    normalized_limit = max(1, int(args.limit or 1))
    selected_jobs = list(jobs)
    cdp_endpoint = resolve_cdp_endpoint(config.boss_browser_cdp_port, config.boss_browser_cdp_url, timeout_seconds=1.5)
    if not cdp_endpoint:
        result = {
            "ok": False,
            "fetch_session_id": fetch_session_id,
            "error": "CDP endpoint unavailable",
            "limit": normalized_limit,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    target_info = _open_target(cdp_endpoint, args.open_url)
    target_id = str(target_info.get("id") or "").strip()
    websocket_url = _resolve_target_ws(cdp_endpoint, target_info)
    client = CdpClient(websocket_url)
    output_path, latest_path = _resolve_output_paths(
        debug_dir=config.debug_dir,
        fetch_session_id=fetch_session_id,
        explicit_output=str(args.output or "").strip(),
    )
    try:
        client.send("Network.enable")
        client.send("Page.enable")
        results: list[dict[str, Any]] = []
        updated_jobs: list[dict[str, Any]] = []
        success_count = 0
        for item in selected_jobs:
            result = _supplement_one_job(
                client,
                job_payload=item,
                query=f"{fetch_session_id} boss_detail",
                max_attempts=max(1, int(args.max_attempts or 1)),
                navigate_wait_ms=max(0, int(args.navigate_wait_ms or 0)),
                activity_wait_ms=max(0, int(args.activity_wait_ms or 0)),
                retry_wait_ms=max(0, int(args.retry_wait_ms or 0)),
            )
            results.append(result)
            if result.get("ok") and isinstance(result.get("job"), dict):
                updated_jobs.append(dict(result["job"]))
                success_count += 1
                if success_count >= normalized_limit:
                    break
        artifact = {
            "ok": bool(success_count > 0),
            "artifact_type": "boss_cdp_detail_probe",
            "artifact_version": 1,
            "created_at": utcnow_iso(),
            "fetch_session_id": fetch_session_id,
            "engine": "boss_cdp_detail_probe",
            "surface_mode": "cdp_dedicated_target",
            "target_id": target_id,
            "target_url": str(target_info.get("url") or ""),
            "open_url": args.open_url,
            "input_path": str(input_path),
            "limit": normalized_limit,
            "candidate_count": len(jobs),
            "attempted_count": len(results),
            "success_count": success_count,
            "updated_jobs": updated_jobs,
            "results": [{key: value for key, value in item.items() if key != "job"} for item in results],
        }
        artifact["artifact_path"] = str(output_path)
        artifact["output_path"] = str(output_path)
        _write_artifact(artifact, output_path=output_path, latest_path=latest_path)
        print(json.dumps(artifact, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if artifact["ok"] or artifact["attempted_count"] > 0 else 1
    except Exception as exc:
        result = {
            "ok": False,
            "artifact_type": "boss_cdp_detail_probe",
            "fetch_session_id": fetch_session_id,
            "engine": "boss_cdp_detail_probe",
            "surface_mode": "cdp_dedicated_target",
            "target_id": target_id,
            "target_url": str(target_info.get("url") or ""),
            "error": str(exc),
            "artifact_path": str(output_path),
            "output_path": str(output_path),
        }
        _write_artifact(result, output_path=output_path, latest_path=latest_path)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    finally:
        client.close()
        _close_target(cdp_endpoint, target_id)


if __name__ == "__main__":
    raise SystemExit(main())
