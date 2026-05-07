from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .boss_cli_bridge import get_boss_cli_status
from .config import AppConfig
from .debug_report import write_debug_report
from .dates import is_recent_date, parse_iso_datetime
from .digest import build_feishu_cards, build_text_digest
from .extractor import extract_resume_text, file_sha256
from .feishu import FeishuClient
from .job_sources import (
    BossBrowserSource,
    BossCliSource,
    CompanyWatchlistSource,
    JsonFeedSource,
    NowcoderDirectSource,
    NowcoderScheduleSource,
    TavilySearchSource,
)
from .job_sources.base import SourceHaltError
from .llm import build_text_client, build_vision_client
from .matching import DEGREE_ORDER, heuristic_match, rerank_with_llm, should_skip_job
from .job_sources.boss_common import PROFILE_READY_MARKER, resolve_cdp_endpoint
from .normalization import normalize_job_fields
from .preferences import (
    add_company_watch,
    interpret_preference_text,
    remove_company_watch,
    reseed_settings_from_profile,
    settings_summary,
)
from .resume_parser import ResumeParser, render_profile_summary
from .storage import ResumeBotStore
from .types import DigestBundle, JobPosting, MatchResult, ResumeProfile, UserSettings, utcnow_iso


BOSS_EMPLOYMENT_FILTER_ALIASES = {
    "full_time": "full_time",
    "full-time": "full_time",
    "fulltime": "full_time",
    "全职": "full_time",
    "正职": "full_time",
    "正式": "full_time",
    "intern": "intern",
    "internship": "intern",
    "实习": "intern",
}


def _normalize_degree_label(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized == "专科":
        return "大专"
    return normalized


def _degree_rank(value: str) -> int:
    return DEGREE_ORDER.get(_normalize_degree_label(value), -1)


def normalize_boss_quick_filters(quick_filters: dict | None = None, **overrides: str) -> dict:
    source = dict(quick_filters or {})
    for key, value in overrides.items():
        if value is not None:
            source[key] = value
    degree_filter = _normalize_degree_label(str(source.get("degree_filter") or "").strip())
    if degree_filter in {"不限", "不限学历", "all", "none"}:
        degree_filter = ""
    if degree_filter and _degree_rank(degree_filter) < 0:
        degree_filter = ""
    employment_mode_filter = str(source.get("employment_mode_filter") or "").strip()
    employment_mode_filter = BOSS_EMPLOYMENT_FILTER_ALIASES.get(employment_mode_filter, employment_mode_filter)
    if employment_mode_filter not in {"full_time", "intern"}:
        employment_mode_filter = ""
    return {
        "degree_filter": degree_filter,
        "employment_mode_filter": employment_mode_filter,
    }


def _boss_quick_filter_match(job: JobPosting, quick_filters: dict) -> tuple[bool, str]:
    degree_filter = str(quick_filters.get("degree_filter") or "").strip()
    if degree_filter:
        requirement = _normalize_degree_label(job.degree_requirement or job.degree_preference or "")
        if not requirement:
            return True, "degree_requirement_deferred"
        requirement_rank = _degree_rank(requirement)
        max_rank = _degree_rank(degree_filter)
        if requirement_rank < 0:
            return True, "degree_requirement_deferred"
        if requirement_rank > max_rank >= 0:
            return False, "degree_requirement_too_high"

    employment_mode_filter = str(quick_filters.get("employment_mode_filter") or "").strip()
    if employment_mode_filter:
        employment_mode = str(job.employment_mode or "").strip()
        if not employment_mode or employment_mode == "unknown":
            return True, "employment_mode_deferred"
        if employment_mode != employment_mode_filter:
            return False, "employment_mode_mismatch"
    return True, ""


def filter_boss_queue_jobs_for_quick_filters(jobs: list[JobPosting], quick_filters: dict) -> tuple[list[JobPosting], dict]:
    normalized_filters = normalize_boss_quick_filters(quick_filters)
    enabled = bool(normalized_filters["degree_filter"] or normalized_filters["employment_mode_filter"])
    if not enabled:
        return list(jobs), {
            "enabled": False,
            "input_job_count": len(jobs),
            "kept_job_count": len(jobs),
            "dropped_count": 0,
            "dropped_reasons": {},
            "quick_filters": normalized_filters,
        }

    kept: list[JobPosting] = []
    dropped_reasons: dict[str, int] = {}
    for job in jobs:
        matched, reason = _boss_quick_filter_match(job, normalized_filters)
        if matched:
            kept.append(job)
            continue
        dropped_reasons[reason or "quick_filter_mismatch"] = dropped_reasons.get(reason or "quick_filter_mismatch", 0) + 1
    return kept, {
        "enabled": True,
        "input_job_count": len(jobs),
        "kept_job_count": len(kept),
        "dropped_count": len(jobs) - len(kept),
        "dropped_reasons": dropped_reasons,
        "quick_filters": normalized_filters,
    }


class ResumeBotPipeline:
    BOSS_RECOMMENDATION_SOURCE_NAME = "boss_recommendation"

    REVIEW_PROFILES = {
        "boss_all": {
            "label": "BOSS 都看",
            "description": "只在这次审阅里按 BOSS 队列全量看，不区分校招社招，也不区分正职实习。",
            "job_types": ["校招", "社招"],
            "campus_role_mode": "both",
            "boss_unknown_application_status": "open",
        },
        "boss_social": {
            "label": "BOSS 社招预览",
            "description": "只在这次审阅里按社招看 BOSS 队列，并把未知投递状态当作可投。",
            "job_types": ["社招"],
            "campus_role_mode": "both",
            "boss_unknown_application_status": "open",
        },
        "boss_campus": {
            "label": "BOSS 校招预览",
            "description": "只在这次审阅里按校招看 BOSS 队列，并把未知投递状态当作可投。",
            "job_types": ["校招"],
            "campus_role_mode": "both",
            "boss_unknown_application_status": "open",
        },
        "boss_full_time": {
            "label": "BOSS 正职预览",
            "description": "只在这次审阅里看正职岗位，并把未知投递状态当作可投。",
            "job_types": ["校招", "社招"],
            "campus_role_mode": "full_time",
            "boss_unknown_application_status": "open",
        },
        "boss_intern": {
            "label": "BOSS 实习预览",
            "description": "只在这次审阅里看实习岗位，并把未知投递状态当作可投。",
            "job_types": ["校招", "社招"],
            "campus_role_mode": "intern",
            "boss_unknown_application_status": "open",
        },
    }

    FETCH_SOURCE_GROUPS = (
        {
            "id": "nowcoder",
            "label": "牛客",
            "description": "公开职位详情页直抓，默认低风险来源。",
            "source_names": ("nowcoder_direct",),
        },
        {
            "id": "nowcoder_schedule",
            "label": "牛客校招日程",
            "description": "从牛客校招日程进入企业官网，深挖公开岗位后再严格筛选。",
            "source_names": ("nowcoder_schedule",),
        },
        {
            "id": "boss",
            "label": "BOSS",
            "description": "需要真实浏览器登录态，只有勾选时才尝试抓取。",
            "source_names": ("boss_browser",),
        },
    )

    def __init__(self, config: AppConfig):
        self.config = config
        self.store = ResumeBotStore(config)
        self.text_client = build_text_client(config)
        self.vision_client = build_vision_client(config)

    def reload_ai_clients(self, config: AppConfig | None = None) -> None:
        if config is not None:
            self.config = config
        self.text_client = build_text_client(self.config)
        self.vision_client = build_vision_client(self.config)

    def _write_debug_report(self, report_type: str, user_id: str, payload: dict) -> dict[str, str]:
        return write_debug_report(self.config.debug_dir, report_type, user_id, payload)

    def _emit_progress(self, progress_callback: Callable[[dict], None] | None, payload: dict) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(payload)
        except Exception:
            return

    def ingest_resume(self, user_id: str, file_path: Path) -> dict:
        extraction = extract_resume_text(file_path, vision_client=self.vision_client)
        raw_text = extraction.raw_text
        parser = ResumeParser(self.text_client)
        profile = parser.parse(raw_text, file_name=file_path.name)
        previous_profile, _previous_raw_text = self.store.get_active_resume(user_id)
        profile.raw_sections["_extraction_file_type"] = extraction.file_type
        profile.raw_sections["_extraction_method"] = extraction.extraction_method
        profile.raw_sections["_extraction_backend"] = extraction.parser_backend
        profile.raw_sections["_extraction_route_name"] = extraction.route_name
        profile.raw_sections["_extraction_route_summary"] = extraction.route_summary
        profile.raw_sections["_extraction_route_reason"] = extraction.route_reason
        profile.raw_sections["_extraction_provider_used"] = extraction.provider_used
        profile.raw_sections["_extraction_quality_score"] = str(extraction.quality_score)
        profile.raw_sections["_extraction_quality_flags"] = "；".join(extraction.quality_flags)
        file_hash = file_sha256(file_path)
        self.store.save_resume(
            user_id=user_id,
            file_name=file_path.name,
            file_hash=file_hash,
            source_path=str(file_path),
            raw_text=raw_text,
            profile=profile,
        )
        settings = reseed_settings_from_profile(self.store.get_settings(user_id), profile, previous_profile)
        self.store.save_settings(settings)
        debug_report = self._write_debug_report(
            "ingest-resume",
            user_id,
            {
                "summary": [
                    f"file={file_path.name}",
                    f"method={extraction.extraction_method}",
                    f"quality_score={extraction.quality_score}",
                    f"parse_method={profile.raw_sections.get('_parse_method', 'unknown')}",
                ],
                "extraction": extraction.to_dict(),
                "profile_summary": render_profile_summary(profile),
                "settings_summary": settings_summary(settings),
            },
        )
        return {
            "profile": profile,
            "profile_summary": render_profile_summary(profile),
            "settings_summary": settings_summary(settings),
            "extraction": extraction.to_dict(),
            "debug_report": debug_report,
        }

    def show_profile(self, user_id: str) -> str:
        profile, _ = self.store.get_active_resume(user_id)
        if not profile:
            return "还没有已保存的简历。"
        return render_profile_summary(profile)

    def show_settings(self, user_id: str) -> str:
        return settings_summary(self.store.get_settings(user_id))

    def list_sent_jobs(self, user_id: str, limit: int = 20, keyword: str = "") -> dict:
        rows = self.store.list_delivery_history(user_id, limit=limit, keyword=keyword)
        if not rows:
            text = "还没有历史已发送岗位。"
        else:
            lines = []
            for index, row in enumerate(rows, start=1):
                lines.extend(
                    [
                        f"{index}. [{row['delivery_kind']}] {row['title']} | {row['company_name'] or '公司未识别'} | {row['city'] or '城市未识别'}",
                        f"   来源：{row['source']}",
                        f"   投递链接：{row['apply_url'] or row['url']}",
                        f"   发送时间：{row['delivered_at']}",
                    ]
                )
            text = "\n".join(lines)
        debug_report = self._write_debug_report(
            "list-sent-jobs",
            user_id,
            {
                "summary": [
                    f"user={user_id}",
                    f"count={len(rows)}",
                    "note=dry-run 不会写入历史已发送岗位，只有真实 send-digest / run-daily 才会记录",
                ],
                "keyword": keyword,
                "limit": limit,
                "text": text,
                "rows": rows,
            },
        )
        return {"text": text, "rows": rows, "debug_report": debug_report}

    def update_preferences(self, user_id: str, text: str) -> dict:
        settings = self.store.get_settings(user_id)
        profile, _ = self.store.get_active_resume(user_id)
        updated, patch = interpret_preference_text(text, settings, profile, self.text_client)
        self.store.save_settings(updated)
        debug_report = self._write_debug_report(
            "update-preferences",
            user_id,
            {
                "summary": [
                    f"user={user_id}",
                    f"patch_keys={', '.join(sorted(patch.keys())) or 'none'}",
                ],
                "input_text": text,
                "patch": patch,
                "profile_summary": render_profile_summary(profile) if profile else "",
                "settings_summary": settings_summary(updated),
            },
        )
        return {"patch": patch, "summary": settings_summary(updated), "debug_report": debug_report}

    def bind_feishu_user(self, user_id: str, receive_id: str, receive_id_type: str) -> str:
        settings = self.store.get_settings(user_id)
        settings.feishu_receive_id = receive_id
        settings.feishu_receive_id_type = receive_id_type
        self.store.save_settings(settings)
        return settings_summary(settings)

    def add_company_watch(self, user_id: str, name: str, careers_url: str = "", domain: str = "", stage: str = "") -> str:
        settings = self.store.get_settings(user_id)
        settings = add_company_watch(settings, name=name, careers_url=careers_url, domain=domain, stage=stage)
        self.store.save_settings(settings)
        return settings_summary(settings)

    def remove_company_watch(self, user_id: str, name: str) -> str:
        settings = self.store.get_settings(user_id)
        settings = remove_company_watch(settings, name=name)
        self.store.save_settings(settings)
        return settings_summary(settings)

    def _load_source_registry(self) -> dict:
        return json.loads(self.config.source_registry_path.read_text(encoding="utf-8"))

    def available_fetch_sources(self) -> list[dict]:
        payload = self._load_source_registry()
        items_by_name = {
            str(item.get("name", "")): item
            for item in payload.get("sources", [])
            if item.get("name")
        }
        result = []
        for group in self.FETCH_SOURCE_GROUPS:
            source_names = [name for name in group["source_names"] if name in items_by_name]
            if not source_names:
                continue
            default_checked = any(bool(items_by_name[name].get("enabled", True)) for name in source_names)
            result.append(
                {
                    "id": group["id"],
                    "label": group["label"],
                    "description": group["description"],
                    "source_names": source_names,
                    "default_checked": default_checked,
                }
            )
        return result

    def default_fetch_source_groups(self) -> list[str]:
        return [item["id"] for item in self.available_fetch_sources() if item.get("default_checked")]

    def resolve_selected_source_names(self, selected_sources: list[str] | None) -> list[str] | None:
        if selected_sources is None:
            return None
        available_sources = self.available_fetch_sources()
        group_by_id = {item["id"]: item for item in available_sources}
        known_names = {
            source_name
            for item in available_sources
            for source_name in item.get("source_names", [])
        }
        resolved: list[str] = []
        for value in selected_sources:
            if value in group_by_id:
                resolved.extend(group_by_id[value].get("source_names", []))
            elif value in known_names:
                resolved.append(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for name in resolved:
            if name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        return deduped

    def _build_sources(
        self,
        settings: UserSettings,
        profile: ResumeProfile | None,
        selected_source_names: list[str] | None = None,
        runtime_fetch_limit: int = 0,
    ):
        payload = deepcopy(self._load_source_registry())
        sources = []
        boss_browser_item: dict | None = None
        boss_browser_enabled = False
        boss_cli_enabled = False
        boss_cli_item: dict | None = None
        selected_name_set = set(selected_source_names or []) if selected_source_names is not None else None
        registry_items = [
            item
            for item in payload.get("sources", [])
            if selected_name_set is None or str(item.get("name", "")) in selected_name_set
        ]
        fetch_limit = max(0, int(runtime_fetch_limit or 0))
        if fetch_limit > 0:
            for item in registry_items:
                item_type = item.get("type")
                if item_type == "nowcoder-direct":
                    item["max_detail_pages"] = fetch_limit
                    item["max_jobs"] = fetch_limit
                elif item_type == "nowcoder-schedule":
                    item["max_jobs"] = fetch_limit
                    item["max_enterprises"] = max(int(item.get("max_enterprises", 20)), min(fetch_limit, 40))

        def is_effectively_enabled(item: dict) -> bool:
            if selected_name_set is not None:
                return str(item.get("name", "")) in selected_name_set
            return bool(item.get("enabled", True))

        boss_native_enabled = any(
            is_effectively_enabled(item) and item.get("type") in {"boss-browser", "boss-cli"}
            for item in registry_items
        )
        for item in registry_items:
            if item.get("type") == "boss-browser":
                boss_browser_item = item
                boss_browser_enabled = is_effectively_enabled(item)
            if item.get("type") == "boss-cli" and is_effectively_enabled(item):
                boss_cli_enabled = True
                boss_cli_item = item
        if boss_browser_item:
            browser_state_path = Path(
                boss_browser_item.get("storage_state_path") or self.config.boss_browser_state_path
            )
            browser_profile_dir = Path(
                boss_browser_item.get("profile_dir") or self.config.boss_browser_profile_dir
            )
            browser_cdp_url = str(boss_browser_item.get("cdp_url", self.config.boss_browser_cdp_url) or "")
            browser_cdp_port = int(boss_browser_item.get("cdp_port", self.config.boss_browser_cdp_port))
        else:
            browser_state_path = self.config.boss_browser_state_path
            browser_profile_dir = self.config.boss_browser_profile_dir
            browser_cdp_url = self.config.boss_browser_cdp_url
            browser_cdp_port = self.config.boss_browser_cdp_port
        cdp_ready = bool(
            resolve_cdp_endpoint(
                browser_cdp_port,
                browser_cdp_url,
                timeout_seconds=1.0,
            )
        )
        browser_ready = cdp_ready or browser_state_path.exists() or (
            browser_profile_dir.exists() and (browser_profile_dir / PROFILE_READY_MARKER).exists()
        )
        boss_cli_status = (
            get_boss_cli_status(self.config)
            if boss_cli_enabled
            else {"available": False, "authenticated": None}
        )
        for item in registry_items:
            if not is_effectively_enabled(item):
                continue
            source_type = item.get("type")
            if source_type == "company-watchlist":
                sources.append(CompanyWatchlistSource())
            elif source_type == "nowcoder-direct":
                sources.append(
                    NowcoderDirectSource(
                        name=item["name"],
                        seed_urls=item.get("seed_urls", []),
                        max_seed_pages=int(item.get("max_seed_pages", 2)),
                        max_detail_pages=int(item.get("max_detail_pages", 10)),
                        max_jobs=int(item.get("max_jobs", item.get("max_detail_pages", 10))),
                        max_queries=int(item.get("max_queries", 4)),
                        request_timeout_sec=int(item.get("request_timeout_sec", 20)),
                        throttle_seconds=float(item.get("throttle_seconds", 1.6)),
                        max_consecutive_seed_anomalies=int(item.get("max_consecutive_seed_anomalies", 2)),
                        max_consecutive_detail_anomalies=int(item.get("max_consecutive_detail_anomalies", 2)),
                        debug_dir=self.config.debug_dir,
                    )
                )
            elif source_type == "nowcoder-schedule":
                sources.append(
                    NowcoderScheduleSource(
                        name=item["name"],
                        seed_urls=item.get("seed_urls", []),
                        max_schedule_pages=int(item.get("max_schedule_pages", 1)),
                        max_enterprises=int(item.get("max_enterprises", 20)),
                        max_official_pages_per_enterprise=int(item.get("max_official_pages_per_enterprise", 8)),
                        max_jobs=int(item.get("max_jobs", 40)),
                        request_timeout_sec=int(item.get("request_timeout_sec", 20)),
                        throttle_seconds=float(item.get("throttle_seconds", 1.6)),
                        debug_dir=self.config.debug_dir,
                    )
                )
            elif source_type == "tavily":
                if boss_native_enabled and (
                    "boss" in item.get("name", "").lower() or "zhipin.com" in item.get("domains", [])
                ):
                    continue
                sources.append(
                    TavilySearchSource(
                        name=item["name"],
                        api_key=self.config.tavily_api_key,
                        domains=item.get("domains", []),
                        query_templates=item.get("query_templates", []),
                        max_results_per_query=int(item.get("max_results_per_query", 6)),
                    )
                )
            elif source_type == "boss-cli":
                if browser_ready and (
                    not boss_cli_status["available"] or boss_cli_status["authenticated"] is False
                ):
                    continue
                sources.append(
                    BossCliSource(
                        name=item["name"],
                        config=self.config,
                        recommend_pages=int(item.get("recommend_pages", 1)),
                        max_queries=int(item.get("max_queries", 4)),
                        max_cards_per_query=int(item.get("max_cards_per_query", 6)),
                        max_detail_pages=int(item.get("max_detail_pages", 12)),
                    )
                )
            elif source_type == "boss-browser":
                state_path = Path(item.get("storage_state_path") or self.config.boss_browser_state_path)
                profile_dir = Path(item.get("profile_dir") or self.config.boss_browser_profile_dir)
                headless = (
                    self.config.boss_browser_headless_override
                    if self.config.boss_browser_headless_override is not None
                    else bool(item.get("headless", True))
                )
                sources.append(
                    BossBrowserSource(
                        name=item["name"],
                        storage_state_path=state_path,
                        profile_dir=profile_dir,
                        base_url=item.get("base_url", "https://www.zhipin.com/web/geek/jobs"),
                        max_queries=int(item.get("max_queries", 6)),
                        max_cards_per_query=int(item.get("max_cards_per_query", 8)),
                        max_detail_pages=int(item.get("max_detail_pages", 16)),
                        timeout_ms=int(item.get("timeout_ms", self.config.playwright_timeout_ms)),
                        headless=headless,
                        debug_dir=self.config.debug_dir,
                        prefer_cdp_attach=bool(item.get("prefer_cdp", self.config.boss_browser_prefer_cdp)),
                        cdp_url=str(item.get("cdp_url", self.config.boss_browser_cdp_url) or ""),
                        cdp_port=int(item.get("cdp_port", self.config.boss_browser_cdp_port)),
                    )
                )
            elif source_type == "json-feed":
                sources.append(JsonFeedSource(item["name"], Path(item["file_path"])))
        allow_boss_browser_fallback = bool((boss_cli_item or {}).get("allow_browser_fallback", False))
        if boss_browser_item and not boss_browser_enabled and browser_ready and allow_boss_browser_fallback:
            if not boss_cli_status["available"] or boss_cli_status["authenticated"] is False:
                state_path = Path(boss_browser_item.get("storage_state_path") or self.config.boss_browser_state_path)
                profile_dir = Path(boss_browser_item.get("profile_dir") or self.config.boss_browser_profile_dir)
                headless = (
                    self.config.boss_browser_headless_override
                    if self.config.boss_browser_headless_override is not None
                    else bool(boss_browser_item.get("headless", True))
                )
                sources.append(
                    BossBrowserSource(
                        name=boss_browser_item["name"],
                        storage_state_path=state_path,
                        profile_dir=profile_dir,
                        base_url=boss_browser_item.get("base_url", "https://www.zhipin.com/web/geek/jobs"),
                        max_queries=int(boss_browser_item.get("max_queries", 6)),
                        max_cards_per_query=int(boss_browser_item.get("max_cards_per_query", 8)),
                        max_detail_pages=int(boss_browser_item.get("max_detail_pages", 16)),
                        timeout_ms=int(boss_browser_item.get("timeout_ms", self.config.playwright_timeout_ms)),
                        headless=headless,
                        debug_dir=self.config.debug_dir,
                        prefer_cdp_attach=bool(boss_browser_item.get("prefer_cdp", self.config.boss_browser_prefer_cdp)),
                        cdp_url=str(boss_browser_item.get("cdp_url", self.config.boss_browser_cdp_url) or ""),
                        cdp_port=int(boss_browser_item.get("cdp_port", self.config.boss_browser_cdp_port)),
                    )
                )
        return sources

    def active_source_names(
        self,
        user_id: str,
        selected_source_names: list[str] | None = None,
        runtime_fetch_limit: int = 0,
    ) -> list[str]:
        settings = self.store.get_settings(user_id)
        profile, _ = self.store.get_active_resume(user_id)
        return [source.name for source in self._build_sources(settings, profile, selected_source_names, runtime_fetch_limit)]

    def _all_configured_source_names(self) -> list[str]:
        payload = self._load_source_registry()
        names: list[str] = []
        seen: set[str] = set()
        for item in payload.get("sources", []):
            name = str(item.get("name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def _ranking_source_names(
        self,
        user_id: str,
        selected_source_names: list[str] | None = None,
        fetch_session_id: str | None = None,
    ) -> list[str]:
        if selected_source_names is not None:
            return self.active_source_names(user_id, selected_source_names)
        if fetch_session_id:
            return self._all_configured_source_names()
        return self.active_source_names(user_id)

    def _apply_review_profile(
        self,
        settings: UserSettings,
        jobs: list[JobPosting],
        review_profile: str | None = None,
    ) -> tuple[UserSettings, list[JobPosting], dict]:
        if not review_profile:
            return settings, jobs, {}
        profile_spec = self.REVIEW_PROFILES.get(review_profile)
        if not profile_spec:
            return settings, jobs, {}
        effective_settings = deepcopy(settings)
        effective_settings.job_types = list(profile_spec.get("job_types", effective_settings.job_types))
        effective_settings.campus_role_mode = str(
            profile_spec.get("campus_role_mode", effective_settings.campus_role_mode)
        )
        normalized_unknown_count = 0
        effective_jobs: list[JobPosting] = []
        inferred_status = str(profile_spec.get("boss_unknown_application_status", "") or "").strip()
        for job in jobs:
            cloned = JobPosting.from_dict(job.to_dict())
            if inferred_status and cloned.source == "boss_browser" and cloned.application_status == "unknown":
                cloned.application_status = inferred_status
                if isinstance(cloned.raw_payload, dict):
                    cloned.raw_payload["review_application_status_inferred"] = inferred_status
                normalized_unknown_count += 1
            effective_jobs.append(cloned)
        return (
            effective_settings,
            effective_jobs,
            {
                "name": review_profile,
                "label": profile_spec.get("label", review_profile),
                "job_types": list(effective_settings.job_types),
                "campus_role_mode": effective_settings.campus_role_mode,
                "normalized_unknown_application_status_count": normalized_unknown_count,
            },
        )

    def _normalize_boss_unknown_application_status(
        self,
        jobs: list[JobPosting],
        *,
        inferred_status: str = "open",
        source: str = "boss_unknown_preview",
    ) -> tuple[list[JobPosting], int]:
        normalized_count = 0
        normalized_jobs: list[JobPosting] = []
        for job in jobs:
            cloned = JobPosting.from_dict(job.to_dict())
            if cloned.source == "boss_browser" and cloned.application_status == "unknown":
                raw_payload = dict(cloned.raw_payload or {})
                raw_payload["application_status_raw"] = "unknown"
                raw_payload["application_status_source"] = source
                raw_payload["review_application_status_inferred"] = inferred_status
                cloned.raw_payload = raw_payload
                cloned.application_status = inferred_status
                normalized_count += 1
            normalized_jobs.append(cloned)
        return normalized_jobs, normalized_count

    def _filter_jobs_by_session(self, jobs: list, fetch_session_id: str | None = None) -> list:
        if not fetch_session_id:
            return jobs
        return [job for job in jobs if getattr(job, "fetch_session_id", "") == fetch_session_id]

    def _load_review_scope_jobs(
        self,
        user_id: str,
        *,
        selected_source_names: list[str] | None = None,
        fetch_session_id: str | None = None,
        require_detail_fetched: bool = False,
    ) -> tuple[list[str], list[JobPosting]]:
        ranking_source_names = self._ranking_source_names(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
        )
        scoped_jobs = self._filter_jobs_by_session(self.store.load_jobs(ranking_source_names), fetch_session_id)
        if require_detail_fetched:
            scoped_jobs = [job for job in scoped_jobs if bool(getattr(job, "detail_fetched", False))]
        jobs = self._dedupe_jobs(
            scoped_jobs
        )
        return ranking_source_names, jobs

    def _boss_session_jobs(self, fetch_session_id: str) -> list[JobPosting]:
        normalized_session = str(fetch_session_id or "").strip()
        if not normalized_session:
            return []
        return [
            job
            for job in self.store.load_jobs(["boss_browser"])
            if str(job.fetch_session_id or "").strip() == normalized_session
        ]

    def boss_session_detail_status(self, fetch_session_id: str) -> dict:
        session_jobs = self._boss_session_jobs(fetch_session_id)
        detail_fetched_jobs = [job for job in session_jobs if bool(job.detail_fetched)]
        pending_jobs = [
            job
            for job in session_jobs
            if not job.detail_fetched and str(job.apply_url or job.url or "").strip()
        ]
        return {
            "fetch_session_id": str(fetch_session_id or "").strip(),
            "session_job_count": len(session_jobs),
            "detail_fetched_count": len(detail_fetched_jobs),
            "pending_job_count": len(pending_jobs),
        }

    def list_review_profiles(
        self,
        user_id: str,
        *,
        selected_source_names: list[str] | None = None,
        fetch_session_id: str | None = None,
    ) -> list[dict]:
        ranking_source_names, jobs = self._load_review_scope_jobs(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
        )
        items = [
            {
                "id": "default",
                "label": "当前全局设置",
                "description": "按你现在的长期偏好和招聘范围审阅这一轮岗位。",
                "applicable": True,
                "source_names_used": ranking_source_names,
                "job_count": len(jobs),
            }
        ]
        boss_jobs = [job for job in jobs if job.source == "boss_browser"]
        if not boss_jobs:
            return items
        for profile_id, profile_spec in self.REVIEW_PROFILES.items():
            job_count = self._count_jobs_for_review_profile(boss_jobs, profile_spec)
            if job_count <= 0:
                continue
            items.append(
                {
                    "id": profile_id,
                    "label": profile_spec.get("label", profile_id),
                    "description": profile_spec.get("description", ""),
                    "applicable": True,
                    "source_names_used": ranking_source_names,
                    "job_count": job_count,
                }
            )
        return items

    def _count_jobs_for_review_profile(self, jobs: list[JobPosting], profile_spec: dict) -> int:
        allowed_job_types = {
            str(value or "").strip()
            for value in profile_spec.get("job_types", [])
            if str(value or "").strip()
        }
        campus_role_mode = str(profile_spec.get("campus_role_mode", "both") or "both").strip().lower()
        count = 0
        for job in jobs:
            if job.source != "boss_browser":
                continue
            job_type = str(job.job_type or "").strip()
            if allowed_job_types and job_type and job_type not in allowed_job_types:
                continue
            employment_mode = str(job.employment_mode or "").strip().lower()
            if campus_role_mode in {"full_time", "intern"} and employment_mode and employment_mode != campus_role_mode:
                continue
            count += 1
        return count

    def _suggest_review_profile(
        self,
        available_profiles: list[dict],
        ranking_debug: dict,
        *,
        current_review_profile: str | None = None,
    ) -> str:
        if current_review_profile:
            return ""
        if int(ranking_debug.get("matched_after_rerank", 0) or 0) > 0:
            return ""
        skip_reasons = ranking_debug.get("skip_reasons", {}) or {}
        if not skip_reasons:
            return ""
        available_ids = {str(item.get("id", "")) for item in available_profiles}
        if "boss_social" not in available_ids:
            return ""
        allowed_reasons = {"不在招聘范围里", "投递状态未确认"}
        if set(skip_reasons.keys()).issubset(allowed_reasons):
            return "boss_social"
        return ""

    def load_active_jobs(
        self,
        user_id: str,
        selected_source_names: list[str] | None = None,
        fetch_session_id: str | None = None,
    ) -> list:
        jobs = self.store.load_jobs(self.active_source_names(user_id, selected_source_names))
        return self._dedupe_jobs(self._filter_jobs_by_session(jobs, fetch_session_id))

    def _should_replace_deduped_job(self, previous, candidate) -> bool:
        previous_company = previous.company_name.strip()
        candidate_company = candidate.company_name.strip()
        if not previous_company and candidate_company:
            return True
        if not (previous.city_list or previous.city) and (candidate.city_list or candidate.city):
            return True
        if not (previous.salary_text or previous.salary_min or previous.salary_max) and (
            candidate.salary_text or candidate.salary_min or candidate.salary_max
        ):
            return True
        if not (previous.degree_requirement or previous.degree_preference) and (
            candidate.degree_requirement or candidate.degree_preference
        ):
            return True
        return False

    def _dedupe_jobs(self, jobs: list) -> list:
        deduped: dict[tuple[str, str, str], object] = {}
        for job in jobs:
            key = (job.source, job.apply_url or job.url, job.title)
            previous = deduped.get(key)
            if previous is None or self._should_replace_deduped_job(previous, job):
                deduped[key] = job
        return list(deduped.values())

    def fetch_jobs(
        self,
        user_id: str,
        progress_callback: Callable[[dict], None] | None = None,
        selected_source_names: list[str] | None = None,
        runtime_fetch_limit: int = 0,
        fetch_session_id: str | None = None,
    ) -> dict:
        settings = self.store.get_settings(user_id)
        profile, _ = self.store.get_active_resume(user_id)
        all_jobs = []
        source_reports = []
        normalized_fetch_limit = max(0, int(runtime_fetch_limit or 0))
        effective_fetch_session_id = fetch_session_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        sources = self._build_sources(settings, profile, selected_source_names, normalized_fetch_limit)
        self._emit_progress(
            progress_callback,
            {
                "stage": "fetch.init",
                "message": f"准备抓取 {len(sources)} 个来源。",
                "current": 0,
                "total": len(sources),
                "source_reports": [],
                "fetch_limit": normalized_fetch_limit,
                "fetch_session_id": effective_fetch_session_id,
            },
        )
        for index, source in enumerate(sources, start=1):
            started_at = utcnow_iso()
            self._emit_progress(
                progress_callback,
                {
                    "stage": "fetch.source.start",
                    "message": f"正在抓取来源 {index}/{len(sources)}：{source.name}",
                    "source": source.name,
                    "current": index - 1,
                    "total": len(sources),
                    "source_index": index,
                    "source_reports": source_reports.copy(),
                    "fetch_limit": normalized_fetch_limit,
                    "fetch_session_id": effective_fetch_session_id,
                },
            )
            try:
                jobs = source.fetch_jobs(settings, profile)
                for job in jobs:
                    job.fetch_session_id = effective_fetch_session_id
                    if isinstance(job.raw_payload, dict):
                        job.raw_payload["fetch_session_id"] = effective_fetch_session_id
                pruned_count = self.store.prune_jobs_for_source(
                    source.name,
                    [job.fingerprint for job in jobs],
                )
                all_jobs.extend(jobs)
                report = {
                    "started_at": started_at,
                    "finished_at": utcnow_iso(),
                    "count": len(jobs),
                    "pruned": pruned_count,
                    "fetch_session_id": effective_fetch_session_id,
                }
                extra_report = getattr(source, "last_fetch_report", None)
                if isinstance(extra_report, dict):
                    report.update(extra_report)
                self.store.record_source_run(source.name, "ok", report)
                source_reports.append({"source": source.name, **report})
                self._emit_progress(
                    progress_callback,
                    {
                        "stage": "fetch.source.ok",
                        "message": (
                            f"来源 {index}/{len(sources)} 完成：{source.name}，拿到 {len(jobs)} 条，"
                            f"清理旧岗位 {pruned_count} 条。"
                        ),
                        "source": source.name,
                        "current": index,
                        "total": len(sources),
                        "source_index": index,
                        "fetched_count": len(jobs),
                        "pruned_count": pruned_count,
                        "total_jobs": len(all_jobs),
                        "source_reports": source_reports.copy(),
                        "fetch_limit": normalized_fetch_limit,
                        "fetch_session_id": effective_fetch_session_id,
                    },
                )
            except SourceHaltError as exc:
                report = {
                    "started_at": started_at,
                    "finished_at": utcnow_iso(),
                    "error": str(exc),
                    "halted": True,
                    "fetch_session_id": effective_fetch_session_id,
                }
                report.update(exc.detail or {})
                self.store.record_source_run(source.name, "halted", report)
                source_reports.append({"source": source.name, **report})
                self._emit_progress(
                    progress_callback,
                    {
                        "stage": "fetch.source.halted",
                        "message": f"来源 {index}/{len(sources)} 已止损停止：{source.name}",
                        "source": source.name,
                        "current": index,
                        "total": len(sources),
                        "source_index": index,
                        "error": str(exc),
                        "halted": True,
                        "total_jobs": len(all_jobs),
                        "source_reports": source_reports.copy(),
                        "fetch_limit": normalized_fetch_limit,
                        "fetch_session_id": effective_fetch_session_id,
                    },
                )
            except Exception as exc:
                report = {
                    "started_at": started_at,
                    "finished_at": utcnow_iso(),
                    "error": str(exc),
                    "fetch_session_id": effective_fetch_session_id,
                }
                self.store.record_source_run(source.name, "error", report)
                source_reports.append({"source": source.name, **report})
                self._emit_progress(
                    progress_callback,
                    {
                        "stage": "fetch.source.error",
                        "message": f"来源 {index}/{len(sources)} 失败：{source.name}",
                        "source": source.name,
                        "current": index,
                        "total": len(sources),
                        "source_index": index,
                        "error": str(exc),
                        "total_jobs": len(all_jobs),
                        "source_reports": source_reports.copy(),
                        "fetch_limit": normalized_fetch_limit,
                        "fetch_session_id": effective_fetch_session_id,
                    },
                )
        self._emit_progress(
            progress_callback,
            {
                "stage": "fetch.upsert",
                "message": f"正在写入岗位库，共 {len(all_jobs)} 条原始岗位。",
                "current": len(sources),
                "total": len(sources),
                "total_jobs": len(all_jobs),
                "source_reports": source_reports.copy(),
                "fetch_limit": normalized_fetch_limit,
                "fetch_session_id": effective_fetch_session_id,
            },
        )
        upsert_stats = self.store.upsert_jobs(all_jobs)
        result = {
            "sources": source_reports,
            "upsert": upsert_stats,
            "total_jobs": len(all_jobs),
            "fetch_limit": normalized_fetch_limit,
            "fetch_session_id": effective_fetch_session_id,
        }
        self._emit_progress(
            progress_callback,
            {
                "stage": "fetch.done",
                "message": (
                    f"岗位抓取完成：原始 {len(all_jobs)} 条，新增 {upsert_stats.get('inserted', 0)} 条，"
                    f"更新 {upsert_stats.get('updated', 0)} 条。"
                ),
                "current": len(sources),
                "total": len(sources),
                "total_jobs": len(all_jobs),
                "upsert": upsert_stats,
                "source_reports": source_reports.copy(),
                "fetch_limit": normalized_fetch_limit,
                "fetch_session_id": effective_fetch_session_id,
            },
        )
        return result

    def _repair_queue_job_payload(self, payload: dict, *, fetch_session_id: str) -> dict:
        fixed = dict(payload or {})
        raw_payload = fixed.get("raw_payload") if isinstance(fixed.get("raw_payload"), dict) else {}
        nested_card = raw_payload.get("card") if isinstance(raw_payload.get("card"), dict) else {}
        job_link = (
            fixed.get("apply_url")
            or fixed.get("url")
            or raw_payload.get("job_url")
            or raw_payload.get("detail_url")
            or nested_card.get("job_url")
            or nested_card.get("url")
            or ""
        )
        if job_link:
            fixed["url"] = str(fixed.get("url") or job_link).strip()
            fixed["apply_url"] = str(fixed.get("apply_url") or job_link).strip()
        degree_requirement = (
            fixed.get("degree_requirement")
            or raw_payload.get("degree_requirement")
            or raw_payload.get("jobDegree")
            or raw_payload.get("degreeName")
            or nested_card.get("degree_requirement")
            or nested_card.get("jobDegree")
            or nested_card.get("degreeName")
            or ""
        )
        if degree_requirement:
            fixed["degree_requirement"] = str(degree_requirement).strip()
        degree_preference = (
            fixed.get("degree_preference")
            or raw_payload.get("degree_preference")
            or nested_card.get("degree_preference")
            or ""
        )
        if degree_preference:
            fixed["degree_preference"] = str(degree_preference).strip()
        if not fixed.get("fetch_session_id"):
            fixed["fetch_session_id"] = fetch_session_id
        return fixed

    def _load_queue_jobs_from_artifact(self, artifact: dict, *, fetch_session_id: str) -> list[JobPosting]:
        jobs_payload = artifact.get("jobs")
        jobs: list[JobPosting] = []
        if isinstance(jobs_payload, list) and jobs_payload:
            for item in jobs_payload:
                if not isinstance(item, dict):
                    continue
                payload = self._repair_queue_job_payload(item, fetch_session_id=fetch_session_id)
                jobs.append(JobPosting.from_dict(payload))
            if jobs:
                return jobs

        cards_payload = artifact.get("cards")
        if isinstance(cards_payload, list) and cards_payload:
            for item in cards_payload:
                if not isinstance(item, dict):
                    continue
                raw_payload = dict(item)
                raw_payload["fetch_session_id"] = fetch_session_id
                raw_payload["source_name"] = artifact.get("source_name") or "boss_browser"
                raw_payload["capture_engine"] = artifact.get("engine") or "boss_cdp_list_probe"
                jobs.append(normalize_job_fields(raw_payload, source=str(raw_payload["source_name"])))
        return jobs

    def import_boss_queue_artifact(self, user_id: str, artifact_path: Path, quick_filters: dict | None = None) -> dict:
        path = Path(artifact_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"队列文件不存在：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifact_type = str(payload.get("artifact_type") or "")
        if artifact_type and artifact_type != "boss_cdp_queue":
            raise ValueError(f"不支持的队列类型：{artifact_type}")
        fetch_session_id = str(payload.get("fetch_session_id") or "").strip() or datetime.now().strftime("%Y%m%d-%H%M%S")
        jobs = self._load_queue_jobs_from_artifact(payload, fetch_session_id=fetch_session_id)
        if not jobs:
            raise ValueError("队列文件里没有可导入的岗位。")
        effective_quick_filters = normalize_boss_quick_filters(quick_filters or payload.get("quick_filters") or {})
        raw_job_count = len(jobs)
        jobs, local_filter_report = filter_boss_queue_jobs_for_quick_filters(jobs, effective_quick_filters)
        for job in jobs:
            job.fetch_session_id = job.fetch_session_id or fetch_session_id
            if isinstance(job.raw_payload, dict):
                job.raw_payload.setdefault("fetch_session_id", job.fetch_session_id)
                job.raw_payload.setdefault("imported_from_queue_artifact", str(path))
                job.raw_payload.setdefault("quick_filters", effective_quick_filters)
                job.raw_payload.setdefault("local_quick_filter_passed", True)
        upsert_stats = self.store.upsert_jobs(jobs)
        report = {
            "started_at": utcnow_iso(),
            "finished_at": utcnow_iso(),
            "artifact_path": str(path),
            "artifact_type": artifact_type or "boss_cdp_queue",
            "fetch_session_id": fetch_session_id,
            "raw_job_count": raw_job_count,
            "job_count": len(jobs),
            "upsert": upsert_stats,
            "city": payload.get("city", ""),
            "keyword": payload.get("keyword", ""),
            "engine": payload.get("engine", ""),
            "stop_reason": payload.get("stop_reason", ""),
            "quick_filters": effective_quick_filters,
            "url_filter_params": payload.get("url_filter_params", {}),
            "url_filter_applied": bool(payload.get("url_filter_applied", False)),
            "local_filter": local_filter_report,
        }
        self.store.record_source_run("boss_browser", "imported", report)
        debug_report = self._write_debug_report(
            "import-boss-queue",
            user_id,
            {
                "summary": [
                    f"artifact={path.name}",
                    f"fetch_session_id={fetch_session_id}",
                    f"raw_job_count={raw_job_count}",
                    f"job_count={len(jobs)}",
                    f"quick_filter_dropped={local_filter_report.get('dropped_count', 0)}",
                    f"inserted={upsert_stats.get('inserted', 0)}",
                    f"updated={upsert_stats.get('updated', 0)}",
                    f"touched={upsert_stats.get('touched', 0)}",
                ],
                "artifact_path": str(path),
                "artifact_type": artifact_type or "boss_cdp_queue",
                "fetch_session_id": fetch_session_id,
                "city": payload.get("city", ""),
                "keyword": payload.get("keyword", ""),
                "stop_reason": payload.get("stop_reason", ""),
                "quick_filters": effective_quick_filters,
                "url_filter_params": payload.get("url_filter_params", {}),
                "url_filter_applied": bool(payload.get("url_filter_applied", False)),
                "local_filter": local_filter_report,
                "raw_job_count": raw_job_count,
                "job_count": len(jobs),
                "upsert": upsert_stats,
                "sample_jobs": [job.to_dict() for job in jobs[:3]],
            },
        )
        return {
            "artifact_path": str(path),
            "artifact_type": artifact_type or "boss_cdp_queue",
            "fetch_session_id": fetch_session_id,
            "raw_job_count": raw_job_count,
            "job_count": len(jobs),
            "upsert": upsert_stats,
            "quick_filters": effective_quick_filters,
            "url_filter_params": payload.get("url_filter_params", {}),
            "url_filter_applied": bool(payload.get("url_filter_applied", False)),
            "local_filter": local_filter_report,
            "debug_report": debug_report,
        }

    def _build_boss_browser_runtime_source(self, *, name: str = "boss_browser") -> BossBrowserSource:
        return BossBrowserSource(
            name=name,
            storage_state_path=self.config.boss_browser_state_path,
            profile_dir=self.config.boss_browser_profile_dir,
            base_url="https://www.zhipin.com/web/geek/jobs",
            max_queries=1,
            max_cards_per_query=15,
            max_detail_pages=5,
            timeout_ms=int(self.config.playwright_timeout_ms),
            headless=False,
            debug_dir=self.config.debug_dir,
            prefer_cdp_attach=bool(self.config.boss_browser_prefer_cdp),
            cdp_url=str(self.config.boss_browser_cdp_url or ""),
            cdp_port=int(self.config.boss_browser_cdp_port),
        )

    def _parse_subprocess_json_output(self, output_text: str) -> dict:
        text = str(output_text or "").strip()
        if not text:
            raise ValueError("BOSS 补抓子程序没有返回结果。请重新启动 start_resume_bot.cmd 后再试。")
        try:
            payload = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                excerpt = " ".join(text.split())[:500]
                detail = f" 原始输出：{excerpt}" if excerpt else ""
                raise ValueError(
                    "BOSS 补抓子程序启动失败，网页拿不到可解析结果。"
                    "常见原因是依赖没有安装完整，或登录浏览器没有正常连上。"
                    "请先重新运行 start_resume_bot.cmd；如果仍失败，把这个错误截图发给维护者。"
                    + detail
                ) from None
            payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("BOSS 补抓子程序返回格式不正确。请重新启动后再试。")
        return payload

    def _run_boss_cdp_detail_probe(
        self,
        jobs: list[JobPosting],
        *,
        fetch_session_id: str,
        limit: int,
    ) -> dict:
        normalized_limit = max(1, int(limit or 1))
        input_path = self.config.debug_dir / f"{fetch_session_id}-boss-detail-probe-input.json"
        input_payload = {
            "artifact_type": "boss_detail_probe_input",
            "artifact_version": 1,
            "created_at": utcnow_iso(),
            "fetch_session_id": fetch_session_id,
            "jobs": [job.to_dict() for job in jobs],
        }
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            sys.executable or "python",
            str(self.config.project_root / "scripts" / "boss_cdp_detail_probe.py"),
            "--input",
            str(input_path),
            "--fetch-session-id",
            fetch_session_id,
            "--limit",
            str(normalized_limit),
            "--pretty",
        ]
        completed = subprocess.run(
            command,
            cwd=str(self.config.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, 20 + normalized_limit * 20),
            check=False,
        )
        stdout_text = str(completed.stdout or "").strip()
        stderr_text = str(completed.stderr or "").strip()
        payload = self._parse_subprocess_json_output(stdout_text or stderr_text)
        if completed.returncode != 0 and not int(payload.get("attempted_count", 0) or 0):
            error_text = str(payload.get("error") or stderr_text or stdout_text or "").strip()
            raise RuntimeError(error_text or f"boss_cdp_detail_probe exited with {completed.returncode}")
        return payload

    def supplement_boss_details(
        self,
        user_id: str,
        fetch_session_id: str,
        *,
        limit: int = 3,
    ) -> dict:
        normalized_session = str(fetch_session_id or "").strip()
        if not normalized_session:
            raise ValueError("fetch_session_id 不能为空。")
        normalized_limit = max(1, int(limit or 1))
        session_jobs = [
            job
            for job in self.store.load_jobs(["boss_browser"])
            if str(job.fetch_session_id or "").strip() == normalized_session
        ]
        pending_jobs = [
            job
            for job in session_jobs
            if not job.detail_fetched and str(job.apply_url or job.url or "").strip()
        ]
        if not pending_jobs:
            report = {
                "started_at": utcnow_iso(),
                "finished_at": utcnow_iso(),
                "fetch_session_id": normalized_session,
                "limit": normalized_limit,
                "session_job_count": len(session_jobs),
                "pending_job_count": 0,
                "updated_count": 0,
            }
            self.store.record_source_run("boss_browser", "detail_noop", report)
            debug_report = self._write_debug_report(
                "boss-detail-supplement",
                user_id,
                {
                    "summary": [
                        f"fetch_session_id={normalized_session}",
                        f"pending=0/{len(session_jobs)}",
                        "updated=0",
                    ],
                    "fetch_session_id": normalized_session,
                    "limit": normalized_limit,
                    "session_job_count": len(session_jobs),
                    "pending_job_count": 0,
                    "updated_count": 0,
                },
            )
            return {
                "ok": True,
                "fetch_session_id": normalized_session,
                "session_job_count": len(session_jobs),
                "pending_job_count": 0,
                "updated_count": 0,
                "upsert": {"inserted": 0, "updated": 0, "touched": 0},
                "results": [],
                "debug_report": debug_report,
            }

        result = self._run_boss_cdp_detail_probe(
            pending_jobs,
            limit=normalized_limit,
            fetch_session_id=normalized_session,
        )
        updated_jobs_payload = result.get("updated_jobs") if isinstance(result.get("updated_jobs"), list) else []
        updated_jobs = [JobPosting.from_dict(item) for item in updated_jobs_payload if isinstance(item, dict)]
        upsert_stats = self.store.upsert_jobs(updated_jobs) if updated_jobs else {"inserted": 0, "updated": 0, "touched": 0}
        status = "detail_ok"
        if result.get("halted"):
            status = "detail_halted"
        elif not result.get("ok"):
            status = "detail_partial" if updated_jobs else "detail_failed"
        report = {
            "started_at": utcnow_iso(),
            "finished_at": utcnow_iso(),
            "fetch_session_id": normalized_session,
            "limit": normalized_limit,
            "session_job_count": len(session_jobs),
            "pending_job_count": len(pending_jobs),
            "attempted_count": int(result.get("attempted_count", 0) or 0),
            "success_count": int(result.get("success_count", 0) or 0),
            "updated_count": len(updated_jobs),
            "halted": bool(result.get("halted")),
            "error": str(result.get("error") or "").strip(),
            "artifact_path": str(result.get("artifact_path") or "").strip(),
        }
        self.store.record_source_run("boss_browser", status, report)
        debug_report = self._write_debug_report(
            "boss-detail-supplement",
            user_id,
            {
                "summary": [
                    f"fetch_session_id={normalized_session}",
                    f"pending={len(pending_jobs)}/{len(session_jobs)}",
                    f"attempted={int(result.get('attempted_count', 0) or 0)}",
                    f"success={int(result.get('success_count', 0) or 0)}",
                    f"updated={len(updated_jobs)}",
                    f"status={status}",
                ],
                "fetch_session_id": normalized_session,
                "limit": normalized_limit,
                "session_job_count": len(session_jobs),
                "pending_job_count": len(pending_jobs),
                "result": result,
                "upsert": upsert_stats,
                "updated_jobs": [job.to_dict() for job in updated_jobs[:3]],
            },
        )
        return {
            "ok": bool(result.get("ok")),
            "fetch_session_id": normalized_session,
            "session_job_count": len(session_jobs),
            "pending_job_count": len(pending_jobs),
            "updated_count": len(updated_jobs),
            "upsert": upsert_stats,
            "updated_jobs": [job.to_dict() for job in updated_jobs],
            "results": result.get("results", []),
            "attempted_count": int(result.get("attempted_count", 0) or 0),
            "success_count": int(result.get("success_count", 0) or 0),
            "halted": bool(result.get("halted")),
            "error": str(result.get("error") or "").strip(),
            "artifact_path": str(result.get("artifact_path") or "").strip(),
            "debug_report": debug_report,
        }

    def _rank_jobs_with_debug(
        self,
        user_id: str,
        progress_callback: Callable[[dict], None] | None = None,
        selected_source_names: list[str] | None = None,
        fetch_session_id: str | None = None,
        review_profile: str | None = None,
        require_detail_fetched: bool = False,
        allow_llm_rerank: bool = True,
    ) -> tuple[list[MatchResult], dict]:
        settings = self.store.get_settings(user_id)
        profile, _ = self.store.get_active_resume(user_id)
        results: list[MatchResult] = []
        skip_reasons: dict[str, int] = {}
        skip_examples: dict[str, list[dict]] = {}
        ranking_source_names, jobs = self._load_review_scope_jobs(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
            require_detail_fetched=require_detail_fetched,
        )
        effective_settings, jobs, applied_review_profile = self._apply_review_profile(
            settings,
            jobs,
            review_profile=review_profile,
        )
        if not applied_review_profile and fetch_session_id:
            jobs, normalized_unknown_count = self._normalize_boss_unknown_application_status(
                jobs,
                inferred_status="open",
                source="boss_default_preview",
            )
            if normalized_unknown_count:
                applied_review_profile = {
                    "name": "default",
                    "label": "当前全局设置",
                    "job_types": list(effective_settings.job_types),
                    "campus_role_mode": effective_settings.campus_role_mode,
                    "normalized_unknown_application_status_count": normalized_unknown_count,
                    "boss_unknown_application_status": "open",
                    "boss_unknown_application_status_default": True,
                }
        total_jobs = len(jobs)
        self._emit_progress(
            progress_callback,
            {
                "stage": "rank.scan.start",
                "message": f"正在按规则筛选 {total_jobs} 条入库岗位。",
                "processed": 0,
                "total_jobs": total_jobs,
                "matched": 0,
            },
        )
        for index, job in enumerate(jobs, start=1):
            last_action = self.store.last_action_for_job(user_id, job.fingerprint)
            skip, reason = should_skip_job(job, effective_settings, last_action=last_action)
            if skip:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skip_examples.setdefault(reason, [])
                if len(skip_examples[reason]) < 3:
                    skip_examples[reason].append(
                        {
                            "title": job.title,
                            "company_name": job.company_name,
                            "city": "/".join(job.city_list or ([job.city] if job.city else [])),
                            "source": job.source,
                        }
                    )
                continue
            result = heuristic_match(job, profile, effective_settings)
            if not result:
                reason = "规则评分未通过"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skip_examples.setdefault(reason, [])
                if len(skip_examples[reason]) < 3:
                    skip_examples[reason].append(
                        {
                            "title": job.title,
                            "company_name": job.company_name,
                            "city": "/".join(job.city_list or ([job.city] if job.city else [])),
                            "source": job.source,
                        }
                    )
                continue
            if self.store.was_pushed(user_id, job.fingerprint, job.content_hash) and not effective_settings.allow_repush_when_updated:
                reason = "岗位已推送且不允许重复发送"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skip_examples.setdefault(reason, [])
                if len(skip_examples[reason]) < 3:
                    skip_examples[reason].append(
                        {
                            "title": job.title,
                            "company_name": job.company_name,
                            "city": "/".join(job.city_list or ([job.city] if job.city else [])),
                            "source": job.source,
                        }
                    )
                continue
            results.append(result)
            if index == total_jobs or index % 20 == 0:
                self._emit_progress(
                    progress_callback,
                    {
                        "stage": "rank.scan.progress",
                        "message": f"正在筛选岗位 {index}/{total_jobs}，当前命中 {len(results)} 条。",
                        "processed": index,
                        "total_jobs": total_jobs,
                        "matched": len(results),
                    },
                )
        heuristic_count = len(results)
        results.sort(key=lambda item: item.score, reverse=True)
        if allow_llm_rerank and self.config.enable_llm_rerank:
            self._emit_progress(
                progress_callback,
                {
                    "stage": "rank.rerank.start",
                    "message": f"正在调用 AI 重排 {min(len(results), self.config.llm_rerank_top_n)} 条候选岗位。",
                    "candidate_count": len(results),
                    "top_n": self.config.llm_rerank_top_n,
                },
            )
            results = rerank_with_llm(
                results,
                profile,
                effective_settings,
                self.text_client,
                top_n=self.config.llm_rerank_top_n,
            )
        results.sort(key=lambda item: item.score, reverse=True)
        ranking_debug = {
            "total_jobs": total_jobs,
            "matched_before_rerank": heuristic_count,
            "matched_after_rerank": len(results),
            "top_candidates": [
                {
                    "title": item.job.title,
                    "company_name": item.job.company_name,
                    "city": "/".join(item.job.city_list or ([item.job.city] if item.job.city else [])),
                    "score": item.score,
                }
                for item in results[:5]
            ],
            "skip_reasons": skip_reasons,
            "skip_examples": skip_examples,
            "fetch_session_id": fetch_session_id or "",
            "source_names_used": ranking_source_names,
            "review_profile": applied_review_profile,
            "require_detail_fetched": require_detail_fetched,
        }
        self._emit_progress(
            progress_callback,
            {
                "stage": "rank.done",
                "message": f"推荐排序完成，可展示 {min(len(results), 30)} 条。",
                "total_jobs": total_jobs,
                "matched_before_rerank": heuristic_count,
                "matched_after_rerank": len(results),
                "skip_reasons": skip_reasons,
            },
        )
        return results, ranking_debug

    def rank_jobs(
        self,
        user_id: str,
        fetch_session_id: str | None = None,
        selected_source_names: list[str] | None = None,
        review_profile: str | None = None,
        require_detail_fetched: bool = False,
    ) -> list[MatchResult]:
        return self._rank_jobs_with_debug(
            user_id,
            fetch_session_id=fetch_session_id,
            selected_source_names=selected_source_names,
            review_profile=review_profile,
            require_detail_fetched=require_detail_fetched,
        )[0]

    def review_fetch_session(
        self,
        user_id: str,
        fetch_session_id: str,
        *,
        selected_source_names: list[str] | None = None,
        limit: int = 30,
        review_profile: str | None = None,
    ) -> dict:
        ranked, ranking_debug = self._rank_jobs_with_debug(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
            review_profile=review_profile,
        )
        available_review_profiles = self.list_review_profiles(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
        )
        suggested_review_profile = self._suggest_review_profile(
            available_review_profiles,
            ranking_debug,
            current_review_profile=review_profile,
        )
        suggested_review_profile_detail = next(
            (
                item
                for item in available_review_profiles
                if str(item.get("id", "")) == suggested_review_profile
            ),
            None,
        )
        review_items: list[dict] = []
        for item in ranked[: max(0, int(limit or 0))]:
            payload = item.to_dict()
            payload["last_action"] = self.store.last_action_for_job(user_id, item.job.fingerprint)
            payload["decision_status"] = "hit"
            payload["skip_reason"] = ""
            payload.update(self._application_status_metadata(item.job))
            review_items.append(payload)
        workspace_items = self._review_workspace_items(
            user_id,
            fetch_session_id,
            ranked=ranked,
            review_profile=review_profile,
            selected_source_names=selected_source_names,
        )
        debug_report = self._write_debug_report(
            "review-session",
            user_id,
            {
                "summary": [
                    f"fetch_session_id={fetch_session_id}",
                    f"source_names={', '.join(ranking_debug.get('source_names_used', [])) or 'none'}",
                    f"review_profile={ranking_debug.get('review_profile', {}).get('name', '') or 'default'}",
                    f"suggested_review_profile={suggested_review_profile or 'none'}",
                    f"total_jobs={ranking_debug.get('total_jobs', 0)}",
                    f"matched={len(ranked)}",
                    f"displayed={len(review_items)}",
                ],
                "fetch_session_id": fetch_session_id,
                "limit": limit,
                "ranking_debug": ranking_debug,
                "available_review_profiles": available_review_profiles,
                "suggested_review_profile": suggested_review_profile,
                "suggested_review_profile_detail": suggested_review_profile_detail or {},
                "review_items": review_items,
                "workspace_items": workspace_items,
            },
        )
        return {
            "fetch_session_id": fetch_session_id,
            "source_names_used": ranking_debug.get("source_names_used", []),
            "total_jobs": int(ranking_debug.get("total_jobs", 0) or 0),
            "matched_count": len(ranked),
            "displayed_count": len(review_items),
            "skip_reasons": ranking_debug.get("skip_reasons", {}),
            "skip_examples": ranking_debug.get("skip_examples", {}),
            "top_candidates": ranking_debug.get("top_candidates", []),
            "review_profile": ranking_debug.get("review_profile", {}),
            "available_review_profiles": available_review_profiles,
            "suggested_review_profile": suggested_review_profile,
            "suggested_review_profile_detail": suggested_review_profile_detail or {},
            "items": review_items,
            "review_items": workspace_items,
            "debug_report": debug_report,
        }

    @staticmethod
    def _parse_source_run_detail_payload(raw_detail) -> dict:
        if isinstance(raw_detail, dict):
            return raw_detail
        if not raw_detail:
            return {}
        try:
            payload = json.loads(str(raw_detail))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def load_boss_session_recommendation(
        self,
        fetch_session_id: str,
        *,
        limit: int = 5,
        review_profile: str | None = None,
    ) -> dict:
        normalized_session = str(fetch_session_id or "").strip()
        if not normalized_session:
            return {}
        normalized_profile = str(review_profile or "").strip() or "default"
        normalized_limit = max(1, min(int(limit or 5), 120))
        runs = self.store.list_recent_source_runs(
            limit=80,
            source_names=[self.BOSS_RECOMMENDATION_SOURCE_NAME],
        )
        fallback_payload: dict = {}
        fallback_run: dict = {}
        for run in runs:
            detail = self._parse_source_run_detail_payload(run.get("detail_json", {}))
            if str(detail.get("fetch_session_id", "") or "").strip() != normalized_session:
                continue
            run_profile = str(detail.get("review_profile", {}).get("name", "") or "").strip() or "default"
            if run_profile != normalized_profile:
                continue
            fallback_payload = detail
            fallback_run = run
            break
        if not fallback_payload:
            return {}
        payload = deepcopy(fallback_payload)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        payload["items"] = items[:normalized_limit]
        payload["displayed_count"] = len(payload["items"])
        payload["source_run"] = {
            "source_name": str(fallback_run.get("source_name", "") or ""),
            "status": str(fallback_run.get("status", "") or ""),
            "started_at": str(fallback_run.get("started_at", "") or ""),
            "finished_at": str(fallback_run.get("finished_at", "") or ""),
        }
        return payload

    def empty_boss_session_recommendation(self, fetch_session_id: str, *, message: str = "") -> dict:
        status = self.boss_session_detail_status(fetch_session_id)
        return {
            "ok": False,
            "stage": "recommendation_pending",
            "fetch_session_id": str(fetch_session_id or "").strip(),
            "source_names_used": ["boss_browser"],
            "session_job_count": status["session_job_count"],
            "detail_fetched_count": status["detail_fetched_count"],
            "pending_job_count": status["pending_job_count"],
            "recommendation_base_count": status["detail_fetched_count"],
            "matched_count": 0,
            "displayed_count": 0,
            "items": [],
            "review_items": [],
            "top_candidates": [],
            "skip_reasons": {},
            "skip_examples": {},
            "review_profile": {},
            "available_review_profiles": [],
            "suggested_review_profile": "",
            "suggested_review_profile_detail": {},
            "message": message or "这轮还没有补抓后的推荐结果。",
        }

    @staticmethod
    def _application_status_metadata(job: JobPosting) -> dict:
        raw_payload = job.raw_payload if isinstance(job.raw_payload, dict) else {}
        status = str(job.application_status or "unknown").strip().lower() or "unknown"
        inferred = bool(
            raw_payload.get("review_application_status_inferred")
            or raw_payload.get("application_status_inferred")
        )
        source = str(raw_payload.get("application_status_source") or "").strip()
        if inferred and not source:
            source = "review_inferred"
        labels = {
            "open": "BOSS 状态未知" if inferred else "可投递",
            "closed": "已关闭",
            "pending": "暂未开放",
            "unknown": "状态未知",
        }
        return {
            "boss_status_label": labels.get(status, "状态未知"),
            "application_status_source": source,
            "is_application_status_inferred": inferred,
        }

    def _review_workspace_items(
        self,
        user_id: str,
        fetch_session_id: str,
        *,
        ranked: list[MatchResult],
        review_profile: str | None = None,
        selected_source_names: list[str] | None = None,
    ) -> list[dict]:
        settings = self.store.get_settings(user_id)
        profile, _ = self.store.get_active_resume(user_id)
        _source_names, jobs = self._load_review_scope_jobs(
            user_id,
            selected_source_names=selected_source_names,
            fetch_session_id=fetch_session_id,
            require_detail_fetched=False,
        )
        effective_settings, jobs, applied_review_profile = self._apply_review_profile(
            settings,
            jobs,
            review_profile=review_profile,
        )
        if not applied_review_profile and fetch_session_id:
            jobs, _normalized_count = self._normalize_boss_unknown_application_status(
                jobs,
                inferred_status="open",
                source="boss_default_preview",
            )
        ranked_by_fingerprint = {item.job.fingerprint: item for item in ranked}
        items: list[dict] = []
        for job in jobs:
            last_action = self.store.last_action_for_job(user_id, job.fingerprint)
            match = ranked_by_fingerprint.get(job.fingerprint)
            status_meta = self._application_status_metadata(job)
            score = 0.0
            reasons: list[str] = []
            skip_reason = ""
            decision_status = "miss"
            if not bool(job.detail_fetched):
                decision_status = "pending_detail"
                skip_reason = "未补 JD"
            elif match:
                decision_status = "hit"
                score = float(match.score)
                reasons = list(match.reasons or [])
            else:
                skip, reason = should_skip_job(job, effective_settings, last_action=last_action)
                skip_reason = reason
                if not skip:
                    candidate = heuristic_match(job, profile, effective_settings)
                    if candidate:
                        score = float(candidate.score)
                        reasons = list(candidate.reasons or [])
                        skip_reason = "已过规则但不在当前展示结果"
                    elif self.store.was_pushed(user_id, job.fingerprint, job.content_hash) and not effective_settings.allow_repush_when_updated:
                        skip_reason = "岗位已推送且不允许重复发送"
                    else:
                        skip_reason = "规则评分未通过"
            payload = {
                "job": job.to_dict(),
                "score": score,
                "reasons": reasons,
                "decision_status": decision_status,
                "skip_reason": skip_reason,
                "last_action": last_action,
                **status_meta,
            }
            items.append(payload)
        order = {"hit": 0, "pending_detail": 1, "miss": 2}
        return sorted(
            items,
            key=lambda item: (
                order.get(str(item.get("decision_status") or ""), 9),
                -float(item.get("score") or 0),
                str(item.get("job", {}).get("title", "")),
            ),
        )

    def recommend_boss_session(
        self,
        user_id: str,
        fetch_session_id: str,
        *,
        limit: int = 5,
        review_profile: str | None = None,
    ) -> dict:
        normalized_session = str(fetch_session_id or "").strip()
        if not normalized_session:
            raise ValueError("fetch_session_id 不能为空。")
        normalized_limit = max(1, min(int(limit or 5), 120))
        status_before = self.boss_session_detail_status(normalized_session)
        if status_before["detail_fetched_count"] <= 0:
            return self.empty_boss_session_recommendation(
                normalized_session,
                message="这轮还没有任何完整 JD，不能生成推荐。",
            )
        ranked, ranking_debug = self._rank_jobs_with_debug(
            user_id,
            selected_source_names=["boss_browser"],
            fetch_session_id=normalized_session,
            review_profile=review_profile,
            require_detail_fetched=True,
        )
        all_items: list[dict] = []
        for item in ranked[:120]:
            payload = item.to_dict()
            payload["last_action"] = self.store.last_action_for_job(user_id, item.job.fingerprint)
            payload["decision_status"] = "hit"
            payload["skip_reason"] = ""
            payload.update(self._application_status_metadata(item.job))
            all_items.append(payload)
        display_items = all_items[:normalized_limit]
        workspace_items = self._review_workspace_items(
            user_id,
            normalized_session,
            ranked=ranked,
            review_profile=review_profile,
            selected_source_names=["boss_browser"],
        )
        recommendation = {
            "ok": True,
            "stage": "recommendation_done",
            "fetch_session_id": normalized_session,
            "source_names_used": ranking_debug.get("source_names_used", []),
            "session_job_count": status_before["session_job_count"],
            "detail_fetched_count": status_before["detail_fetched_count"],
            "pending_job_count": status_before["pending_job_count"],
            "recommendation_base_count": status_before["detail_fetched_count"],
            "matched_count": len(ranked),
            "displayed_count": len(display_items),
            "items": display_items,
            "review_items": workspace_items,
            "top_candidates": ranking_debug.get("top_candidates", []),
            "skip_reasons": ranking_debug.get("skip_reasons", {}),
            "skip_examples": ranking_debug.get("skip_examples", {}),
            "review_profile": ranking_debug.get("review_profile", {}),
            "available_review_profiles": [],
            "suggested_review_profile": "",
            "suggested_review_profile_detail": {},
            "message": (
                f"当前推荐基于已完成 JD 的 {status_before['detail_fetched_count']}/"
                f"{status_before['session_job_count']} 条岗位。"
            ),
        }
        report = deepcopy(recommendation)
        report["started_at"] = utcnow_iso()
        report["finished_at"] = report["started_at"]
        report["limit"] = normalized_limit
        report["items"] = all_items
        report["ranking_debug"] = ranking_debug
        status = "recommendation_ok" if ranked else "recommendation_empty"
        self.store.record_source_run(self.BOSS_RECOMMENDATION_SOURCE_NAME, status, report)
        debug_report = self._write_debug_report(
            "boss-session-recommendation",
            user_id,
            {
                "summary": [
                    f"fetch_session_id={normalized_session}",
                    f"detail_fetched={status_before['detail_fetched_count']}/{status_before['session_job_count']}",
                    f"matched={len(ranked)}",
                    f"displayed={len(display_items)}",
                    f"status={status}",
                ],
                "recommendation": recommendation,
                "ranking_debug": ranking_debug,
            },
        )
        recommendation["debug_report"] = debug_report
        return recommendation

    def supplement_boss_session_and_recommend(
        self,
        user_id: str,
        fetch_session_id: str,
        *,
        recommendation_limit: int = 5,
        review_profile: str | None = None,
    ) -> dict:
        normalized_session = str(fetch_session_id or "").strip()
        if not normalized_session:
            raise ValueError("fetch_session_id 不能为空。")
        before = self.boss_session_detail_status(normalized_session)
        supplement_limit = max(1, int(before["pending_job_count"] or 1))
        supplement = self.supplement_boss_details(
            user_id,
            normalized_session,
            limit=supplement_limit,
        )
        after = self.boss_session_detail_status(normalized_session)
        supplement = dict(supplement)
        supplement["full_session_supplement"] = True
        supplement["target_pending_job_count"] = before["pending_job_count"]
        supplement["session_job_count"] = after["session_job_count"]
        supplement["detail_fetched_count"] = after["detail_fetched_count"]
        supplement["pending_job_count"] = after["pending_job_count"]
        if after["detail_fetched_count"] <= 0:
            recommendation = self.empty_boss_session_recommendation(
                normalized_session,
                message="这轮还没有任何完整 JD，补抓没有成功写入可推荐内容。",
            )
        else:
            recommendation = self.recommend_boss_session(
                user_id,
                normalized_session,
                limit=recommendation_limit,
                review_profile=review_profile,
            )
        return {
            "ok": bool(recommendation.get("ok")),
            "fetch_session_id": normalized_session,
            "before": before,
            "after": after,
            "supplement": supplement,
            "recommendation": recommendation,
        }

    def _history_backfill_key(self, user_id: str) -> str:
        active_resume = self.store.get_active_resume_record(user_id)
        return active_resume.get("file_hash", "") if active_resume else ""

    def _classify_delivery_kind(self, job, last_delivery_at: str, backfill_pending: bool) -> str:
        if backfill_pending:
            if job.published_at and is_recent_date(job.published_at, days=2):
                return "new"
            return "history"
        last_dt = parse_iso_datetime(last_delivery_at)
        discovered_dt = parse_iso_datetime(job.discovered_at)
        if last_dt and discovered_dt and discovered_dt > last_dt:
            return "new"
        if job.published_at and is_recent_date(job.published_at, days=2):
            return "new"
        return "history"

    def _build_digest_from_ranked(
        self,
        user_id: str,
        ranked: list[MatchResult],
        *,
        include_history: bool | None = None,
        history_limit: int | None = None,
        history_only: bool = False,
    ) -> DigestBundle:
        settings = self.store.get_settings(user_id)
        last_delivery_at = self.store.get_last_delivery_time(user_id)
        backfill_key = self._history_backfill_key(user_id)
        backfill_pending = bool(
            backfill_key and not self.store.has_scheduler_run(user_id, "history_backfill", backfill_key)
        )
        if include_history is None:
            include_history = backfill_pending
        effective_history_limit = history_limit or settings.history_backfill_limit
        new_items: list[MatchResult] = []
        history_items: list[MatchResult] = []
        for item in ranked:
            if self.store.was_pushed(user_id, item.job.fingerprint, item.job.content_hash):
                continue
            item.delivery_kind = self._classify_delivery_kind(item.job, last_delivery_at, backfill_pending)
            if item.delivery_kind == "history":
                history_items.append(item)
            else:
                new_items.append(item)
        selected_new_items = [] if history_only else new_items[: settings.max_items_per_push]
        selected_history_items = history_items[:effective_history_limit] if include_history else []
        if history_only:
            selected_history_items = history_items[:effective_history_limit]
        if not selected_new_items and not selected_history_items:
            empty_reason = "今天没有新增或更新后的符合条件岗位。"
            if history_only:
                empty_reason = "没有找到仍在投递期内且未发过的历史岗位。"
            return DigestBundle(user_id=user_id, empty_reason=empty_reason)
        return DigestBundle(
            user_id=user_id,
            new_items=selected_new_items,
            history_items=selected_history_items,
            history_included=bool(include_history),
            history_run_key=backfill_key if backfill_pending and include_history else "",
        )

    def build_digest(
        self,
        user_id: str,
        *,
        include_history: bool | None = None,
        history_limit: int | None = None,
        history_only: bool = False,
    ) -> DigestBundle:
        ranked, _ranking_debug = self._rank_jobs_with_debug(user_id)
        return self._build_digest_from_ranked(
            user_id,
            ranked,
            include_history=include_history,
            history_limit=history_limit,
            history_only=history_only,
        )

    def send_digest(self, user_id: str, bundle: DigestBundle | None = None) -> dict:
        settings = self.store.get_settings(user_id)
        if not settings.feishu_receive_id:
            raise RuntimeError("还没有绑定飞书接收 ID，先执行 bind-feishu-user")
        bundle = bundle or self.build_digest(user_id)
        client = FeishuClient(self.config.feishu_app_id, self.config.feishu_app_secret)
        result: dict = {}
        if bundle.all_items():
            cards = build_feishu_cards(bundle)
            responses = [
                client.send_interactive(settings.feishu_receive_id, settings.feishu_receive_id_type, card)
                for card in cards
            ]
            for item in bundle.all_items():
                self.store.record_push(
                    user_id,
                    item.job.fingerprint,
                    item.job.content_hash,
                    item.score,
                    item.reasons,
                    delivery_kind=item.delivery_kind,
                    job=item.job,
                )
            if bundle.history_run_key:
                self.store.record_scheduler_run(
                    user_id,
                    "history_backfill",
                    bundle.history_run_key,
                    {"new_count": len(bundle.new_items), "history_count": len(bundle.history_items)},
                )
            result = {"sent_cards": len(cards), "responses": responses}
        elif settings.notify_when_empty:
            response = client.send_text(
                settings.feishu_receive_id,
                settings.feishu_receive_id_type,
                build_text_digest(bundle),
            )
            if bundle.history_run_key:
                self.store.record_scheduler_run(
                    user_id,
                    "history_backfill",
                    bundle.history_run_key,
                    {"new_count": 0, "history_count": 0},
                )
            result = {"sent_cards": 0, "responses": [response]}
        else:
            if bundle.history_run_key:
                self.store.record_scheduler_run(
                    user_id,
                    "history_backfill",
                    bundle.history_run_key,
                    {"new_count": 0, "history_count": 0},
                )
            result = {"sent_cards": 0, "responses": []}
        debug_report = self._write_debug_report(
            "send-digest",
            user_id,
            {
                "summary": [
                    f"user={user_id}",
                    f"sent_cards={result.get('sent_cards', 0)}",
                    f"new_count={len(bundle.new_items)}",
                    f"history_count={len(bundle.history_items)}",
                ],
                "settings_summary": settings_summary(settings),
                "digest_text": build_text_digest(bundle),
                "send_report": result,
            },
        )
        result["debug_report"] = debug_report
        return result

    def run_daily(
        self,
        user_id: str,
        dry_run: bool = False,
        *,
        include_history: bool | None = None,
        history_limit: int | None = None,
        history_only: bool = False,
    ) -> dict:
        fetch_report = self.fetch_jobs(user_id)
        ranked, ranking_debug = self._rank_jobs_with_debug(user_id)
        bundle = self._build_digest_from_ranked(
            user_id,
            ranked,
            include_history=include_history,
            history_limit=history_limit,
            history_only=history_only,
        )
        response = {
            "fetch_report": fetch_report,
            "ranking_debug": ranking_debug,
            "digest_text": build_text_digest(bundle),
            "new_count": len(bundle.new_items),
            "history_count": len(bundle.history_items),
            "digest_count": len(bundle.all_items()),
        }
        if not dry_run:
            response["send_report"] = self.send_digest(user_id, bundle)
        response["debug_report"] = self._write_debug_report(
            "run-daily",
            user_id,
            {
                "summary": [
                    f"user={user_id}",
                    f"dry_run={dry_run}",
                    f"new_count={len(bundle.new_items)}",
                    f"history_count={len(bundle.history_items)}",
                ],
                "fetch_report": fetch_report,
                "ranking_debug": ranking_debug,
                "digest_text": build_text_digest(bundle),
                "new_count": len(bundle.new_items),
                "history_count": len(bundle.history_items),
                "digest_count": len(bundle.all_items()),
                "delivery_history_note": "dry-run 不会写入历史已发送岗位，只有真实发送才会记录。",
                "send_report": response.get("send_report", {}),
            },
        )
        return response

    def run_scheduler(self, user_id: str, grace_minutes: int = 15, force: bool = False) -> dict:
        settings = self.store.get_settings(user_id)
        now = datetime.now()
        scheduled_hour, scheduled_minute = [int(part) for part in settings.push_time.split(":", 1)]
        scheduled_at = now.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)
        run_key = now.strftime("%Y-%m-%d")
        if not force:
            if now < scheduled_at or now > scheduled_at + timedelta(minutes=grace_minutes):
                return {
                    "status": "skipped",
                    "reason": f"当前时间 {now.strftime('%H:%M')} 不在推送窗口 {settings.push_time} ~ {(scheduled_at + timedelta(minutes=grace_minutes)).strftime('%H:%M')}",
                }
            if self.store.has_scheduler_run(user_id, "daily_digest", run_key):
                return {"status": "skipped", "reason": f"{run_key} 已经执行过"}
        result = self.run_daily(user_id, dry_run=False)
        self.store.record_scheduler_run(
            user_id,
            "daily_digest",
            run_key,
            {"scheduled_time": settings.push_time, "digest_count": result.get("digest_count", 0)},
        )
        result["status"] = "sent"
        result["scheduled_for"] = settings.push_time
        return result

    def mark_job(self, user_id: str, fingerprint: str, action: str, notes: str = "") -> None:
        self.store.record_interaction(user_id, fingerprint, action=action, notes=notes)
