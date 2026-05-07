from __future__ import annotations

import json
import random
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

from ..normalization import CITIES, is_job_quality_acceptable, job_quality_issues, normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource, SourceHaltError
from .boss_common import (
    PROFILE_READY_MARKER,
    build_boss_context_kwargs,
    build_boss_launch_kwargs,
    candidate_cdp_urls,
    extract_page_snapshot,
    install_boss_stealth,
    is_security_verify_page,
    looks_like_login_page,
    resolve_cdp_endpoint,
    resolve_cdp_websocket_url,
)


RECOMMENDATION_PAGE_MARKERS = (
    "推荐",
    "为你推荐",
    "推荐职位",
    "推荐:",
    "职位推荐",
    "你可能感兴趣",
    "猜你喜欢",
)
RECOMMENDATION_LINK_MARKERS = (
    "cpc_job_index_",
    "ka=rcmd",
    "seorefer=index",
)
QUERY_INTENT_TOKENS = (
    "运营",
    "产品运营",
    "内容运营",
    "用户运营",
    "增长",
    "产品",
    "AI",
    "ai",
    "agent",
)
BOSS_CITY_CODES = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "贵阳": "101260100",
    "武汉": "101200100",
    "南京": "101190100",
    "西安": "101110100",
    "苏州": "101190400",
    "重庆": "101040100",
    "天津": "101030100",
    "长沙": "101250100",
    "厦门": "101230200",
    "青岛": "101120200",
    "宁波": "101210400",
}
BOSS_DEGREE_FILTER_CODES = {
    "高中": "206",
    "大专": "204",
    "专科": "204",
    "本科": "203",
    "硕士": "202",
    "博士": "201",
}


def resolve_boss_city_code(city: str) -> str:
    return BOSS_CITY_CODES.get((city or "").strip(), "")


def extract_boss_city_code_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    params = parse_qs(parsed.query)
    return str((params.get("city") or [""])[0] or "").strip()


def extract_boss_query_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    params = parse_qs(parsed.query)
    return unquote(str((params.get("query") or [""])[0] or "").strip())


def extract_boss_job_id_from_url(url: str) -> str:
    matched = re.search(r"/job_detail/([^/?#]+)\.html", str(url or "").strip(), flags=re.IGNORECASE)
    return str(matched.group(1) if matched else "").strip()


def resolve_boss_city_name(city_code: str) -> str:
    normalized_city_code = str(city_code or "").strip()
    for city_name, mapped_city_code in BOSS_CITY_CODES.items():
        if mapped_city_code == normalized_city_code:
            return city_name
    return ""


def find_boss_joblist_resource_url(resource_urls: list[str], *, city_code: str, keyword: str) -> str:
    normalized_city_code = str(city_code or "").strip()
    normalized_keyword = " ".join((keyword or "").split()).strip()
    encoded_keyword = quote(normalized_keyword)
    candidates: list[str] = []
    loose_candidates: list[str] = []
    for item in resource_urls or []:
        url = str(item or "").strip()
        if "/wapi/zpgeek/search/joblist.json" not in url:
            continue
        loose_candidates.append(url)
        if normalized_city_code and f"city={normalized_city_code}" not in url:
            continue
        if normalized_keyword and encoded_keyword not in url and normalized_keyword not in unquote(url):
            continue
        candidates.append(url)
    if candidates:
        return candidates[-1]
    return loose_candidates[-1] if loose_candidates else ""


def build_boss_quick_filter_url_params(*, degree_filter: str = "", employment_mode_filter: str = "") -> dict[str, str]:
    params: dict[str, str] = {}
    degree_code = BOSS_DEGREE_FILTER_CODES.get(str(degree_filter or "").strip())
    if degree_code:
        params["degree"] = degree_code
    # BOSS jobType/partTime values still need live verification before the main flow
    # can safely hardcode full-time/internship URL parameters.
    return params


def build_boss_search_url(
    base_url: str,
    *,
    keyword: str,
    city_code: str = "",
    extra_params: Mapping[str, str] | None = None,
) -> str:
    normalized_keyword = " ".join((keyword or "").split()).strip()
    params: dict[str, str] = {}
    if normalized_keyword:
        params["query"] = normalized_keyword
    if city_code:
        params["city"] = city_code
    for key, value in (extra_params or {}).items():
        normalized_key = str(key or "").strip()
        normalized_value = str(value or "").strip()
        if not normalized_key or not normalized_value:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", normalized_key):
            continue
        params[normalized_key] = normalized_value
    query_pairs = [f"{key}={quote(value)}" for key, value in params.items() if value]
    if not query_pairs:
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}?{'&'.join(query_pairs)}"


def build_boss_detail_url(job: dict) -> str:
    security_id = str(job.get("security_id") or job.get("securityId") or "").strip()
    job_id = str(job.get("job_id") or job.get("source_job_id") or job.get("encryptId") or "").strip()
    lid = str(job.get("lid") or "").strip()
    pairs: list[str] = []
    if security_id:
        pairs.append(f"securityId={quote(security_id)}")
    elif job_id:
        pairs.append(f"encryptJobId={quote(job_id)}")
    if lid:
        pairs.append(f"lid={quote(lid)}")
    return "https://www.zhipin.com/wapi/zpgeek/job/detail.json" + (f"?{'&'.join(pairs)}" if pairs else "")


def extract_boss_joblist_payload(payload: dict) -> dict:
    zp_data = payload.get("zpData", {}) if isinstance(payload.get("zpData"), dict) else {}
    data = zp_data.get("data", {}) if isinstance(zp_data.get("data"), dict) else {}
    candidates = [
        zp_data.get("jobList"),
        zp_data.get("job_list"),
        data.get("jobList"),
        data.get("job_list"),
        payload.get("jobList"),
        payload.get("job_list"),
    ]
    jobs = next((item for item in candidates if isinstance(item, list)), None)
    if jobs is None:
        raise RuntimeError("joblist 鍝嶅簲閲屾病鏈夋壘鍒拌亴浣嶅垪琛ㄣ€?")
    total_count = (
        zp_data.get("count")
        or zp_data.get("totalCount")
        or zp_data.get("total")
        or data.get("count")
        or data.get("totalCount")
        or data.get("total")
        or len(jobs)
    )
    return {"jobs": jobs, "total_count": int(total_count or len(jobs) or 0)}


def extract_boss_detail_api_payload(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    zp_data = payload.get("zpData", {}) if isinstance(payload.get("zpData"), dict) else {}
    job_info = zp_data.get("jobInfo", {}) if isinstance(zp_data.get("jobInfo"), dict) else {}
    jd = str(job_info.get("postDescription") or job_info.get("postDescriptionHtml") or "").strip()
    code = payload.get("code")
    message = str(payload.get("message") or "").strip()
    return {
        "code": code,
        "message": message,
        "jd": jd,
        "retryable": code == 37 or (code == 0 and not jd),
        "job_info": job_info,
    }


def infer_boss_application_status(*payloads: dict | str) -> dict:
    parts: list[str] = []
    for payload in payloads:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key.lower() in {
                    "postdescription",
                    "postdescriptionhtml",
                    "description",
                    "paneltext",
                    "status",
                    "statusdesc",
                    "jobstatus",
                    "jobstatusdesc",
                    "buttontext",
                    "applystatus",
                    "message",
                }:
                    parts.append(str(value or ""))
        else:
            parts.append(str(payload or ""))
    text = " ".join(" ".join(part.split()) for part in parts if str(part or "").strip())
    lowered = text.lower()
    closed_markers = [
        "停止招聘",
        "职位已关闭",
        "已关闭",
        "已下线",
        "职位不存在",
        "招聘已结束",
        "暂停招聘",
        "not found",
        "closed",
        "offline",
    ]
    pending_markers = ["暂未开放", "待开放", "即将开放", "未开放", "pending"]
    open_markers = [
        "立即沟通",
        "继续沟通",
        "投递简历",
        "立即投递",
        "我要应聘",
        "感兴趣",
        "聊一聊",
        "apply",
        "chat",
    ]
    if any(marker.lower() in lowered for marker in closed_markers):
        return {"status": "closed", "source": "boss_detail_text", "raw_text": text[:300]}
    if any(marker.lower() in lowered for marker in pending_markers):
        return {"status": "pending", "source": "boss_detail_text", "raw_text": text[:300]}
    if any(marker.lower() in lowered for marker in open_markers):
        return {"status": "open", "source": "boss_detail_text", "raw_text": text[:300]}
    return {"status": "unknown", "source": "", "raw_text": text[:300]}


def build_boss_search_probe_card(raw: dict) -> dict:
    job_id = str(raw.get("encryptJobId") or raw.get("jobId") or "").strip()
    job_url = f"https://www.zhipin.com/job_detail/{job_id}.html" if job_id else ""
    degree_requirement = str(raw.get("jobDegree") or raw.get("degreeName") or "").strip()
    experience_name = str(raw.get("jobExperience") or raw.get("experienceName") or "").strip()
    description_parts = []
    if experience_name:
        description_parts.append(f"???{experience_name}")
    if degree_requirement:
        description_parts.append(f"???{degree_requirement}")
    return {
        "job_id": job_id,
        "security_id": str(raw.get("securityId") or "").strip(),
        "lid": str(raw.get("lid") or "").strip(),
        "title": str(raw.get("jobName") or "").strip(),
        "company_name": str(raw.get("brandName") or "").strip(),
        "city": str(raw.get("cityName") or raw.get("areaDistrict") or "").strip(),
        "salary_text": str(raw.get("salaryDesc") or raw.get("salary") or "").strip(),
        "degree_requirement": degree_requirement,
        "experience_name": experience_name,
        "description": "\n".join(description_parts).strip(),
        "url": job_url,
        "apply_url": job_url,
        "job_url": job_url,
    }


def infer_boss_salary_text(text: str, current: str = "") -> str:
    normalized_current = str(current or "").strip()
    if normalized_current:
        return normalized_current
    compact_text = " ".join(str(text or "").split())
    if not compact_text:
        return ""
    patterns = [
        r"\d+(?:\.\d+)?-\d+(?:\.\d+)?K(?:·\d+薪)?",
        r"\d+(?:\.\d+)?-\d+(?:\.\d+)?千/月(?:·\d+薪)?",
        r"\d+(?:,\d{3})?-\d+(?:,\d{3})?元(?:/天|/月)?",
        r"\d+(?:\.\d+)?K·\d+薪",
    ]
    for pattern in patterns:
        matched = re.search(pattern, compact_text, flags=re.IGNORECASE)
        if matched:
            return matched.group(0)
    return ""


def build_boss_dom_probe_card(raw: dict) -> dict:
    job_url = str(raw.get("url") or "").strip()
    return {
        "job_id": extract_boss_job_id_from_url(job_url),
        "security_id": "",
        "lid": "",
        "title": str(raw.get("title") or "").strip(),
        "company_name": str(raw.get("company_name") or "").strip(),
        "city": str(raw.get("city") or "").strip(),
        "salary_text": infer_boss_salary_text(raw.get("text", ""), str(raw.get("salary_text") or "").strip()),
        "job_url": job_url,
    }


def merge_boss_dom_probe_detail(card: dict, detail: dict) -> dict:
    merged = dict(card or {})
    detail = detail or {}
    if detail.get("encryptId"):
        merged["job_id"] = str(detail.get("encryptId") or "").strip()
        if not merged.get("job_url"):
            merged["job_url"] = f"https://www.zhipin.com/job_detail/{merged['job_id']}.html"
    if detail.get("securityId"):
        merged["security_id"] = str(detail.get("securityId") or "").strip()
    if detail.get("jobName"):
        merged["title"] = str(detail.get("jobName") or "").strip()
    if detail.get("locationName"):
        merged["city"] = str(detail.get("locationName") or "").strip()
    if detail.get("salaryDesc"):
        merged["salary_text"] = str(detail.get("salaryDesc") or "").strip()
    return merged


def build_boss_query_label_from_url(url: str) -> str:
    city_code = extract_boss_city_code_from_url(url)
    city_name = resolve_boss_city_name(city_code)
    query = extract_boss_query_from_url(url)
    return " ".join(part for part in [city_name, query] if part).strip() or query or city_name or "当前结果页"


def extract_boss_passive_detail_text(detail: dict) -> str:
    text = str(
        detail.get("postDescription")
        or detail.get("panelText")
        or detail.get("description")
        or ""
    )
    return (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .strip()
    )


def is_boss_passive_detail_complete(detail: dict) -> bool:
    detail_text = extract_boss_passive_detail_text(detail)
    if not detail_text:
        return False
    compact_text = " ".join(detail_text.split())
    if len(compact_text) >= 120:
        return True
    lines = [line.strip() for line in re.split(r"[\r\n]+", detail_text) if line.strip()]
    return len(compact_text) >= 80 and len(lines) >= 3


def build_boss_list_queue_payload(card: dict, *, query: str, page_url: str) -> dict:
    card = dict(card or {})
    title = str(card.get("title") or "").strip()
    company_name = str(card.get("company_name") or "").strip()
    city = str(card.get("city") or "").strip()
    salary_text = str(card.get("salary_text") or "").strip()
    job_url = str(card.get("job_url") or page_url or "").strip()
    job_id = str(card.get("job_id") or "").strip()
    security_id = str(card.get("security_id") or "").strip()
    lid = str(card.get("lid") or "").strip()
    description_parts = ["BOSS 列表阶段岗位队列"]
    if str(query or "").strip():
        description_parts.append(f"查询：{str(query or '').strip()}")
    if city:
        description_parts.append(f"城市：{city}")
    if salary_text:
        description_parts.append(f"薪资：{salary_text}")
    if company_name:
        description_parts.append(f"公司：{company_name}")
    return {
        "url": job_url,
        "apply_url": job_url,
        "title": title,
        "company_name": company_name,
        "city": city,
        "salary_text": salary_text,
        "description": "\n".join(part for part in description_parts if part).strip(),
        "source_job_id": job_id,
        "job_id": job_id,
        "security_id": security_id,
        "lid": lid,
        "query": str(query or "").strip(),
        "detail_fetched": False,
        "application_status": "unknown",
        "raw_payload": {
            "phase": "list_queue",
            "card": card,
            "page_url": page_url,
            "application_status_source": "list_queue",
            "detail_url": build_boss_detail_url(card),
        },
    }


def build_boss_passive_job_payload(card: dict, detail: dict, *, query: str, page_url: str) -> dict:
    merged_card = merge_boss_dom_probe_detail(card, detail)
    position_labels = detail.get("positionLabels") if isinstance(detail.get("positionLabels"), list) else []
    description_parts: list[str] = []
    if position_labels:
        description_parts.append("标签：" + " / ".join(str(item).strip() for item in position_labels if str(item).strip()))
    if str(detail.get("experienceName") or "").strip():
        description_parts.append(f"经验：{str(detail.get('experienceName') or '').strip()}")
    if str(detail.get("degreeName") or "").strip():
        description_parts.append(f"学历：{str(detail.get('degreeName') or '').strip()}")
    panel_text = extract_boss_passive_detail_text(detail)
    if panel_text:
        description_parts.append(panel_text)
    job_url = str(merged_card.get("job_url") or page_url or "").strip()
    job_id = str(merged_card.get("job_id") or "").strip()
    detail_complete = is_boss_passive_detail_complete(detail)
    status_info = infer_boss_application_status(detail, panel_text)
    return {
        "url": job_url,
        "apply_url": job_url,
        "title": str(merged_card.get("title") or "").strip(),
        "company_name": str(detail.get("brandName") or merged_card.get("company_name") or "").strip(),
        "city": str(merged_card.get("city") or "").strip(),
        "salary_text": str(merged_card.get("salary_text") or "").strip(),
        "description": "\n".join(part for part in description_parts if part).strip(),
        "source_job_id": job_id,
        "job_id": job_id,
        "query": str(query or "").strip(),
        "detail_fetched": detail_complete,
        "application_status": status_info["status"],
        "raw_payload": {
            "card": dict(card or {}),
            "detail": dict(detail or {}),
            "page_url": page_url,
            "application_status_source": status_info.get("source", ""),
            "application_status_raw_text": status_info.get("raw_text", ""),
        },
    }


def build_boss_detail_page_payload(card: dict, page_detail: dict, detail_api: dict, *, query: str, page_url: str) -> dict:
    card = dict(card or {})
    page_detail = dict(page_detail or {})
    detail_api = dict(detail_api or {})
    api_job_info = detail_api.get("job_info") if isinstance(detail_api.get("job_info"), dict) else {}
    detail = {
        "encryptId": str(api_job_info.get("encryptId") or page_detail.get("encryptId") or card.get("job_id") or "").strip(),
        "securityId": str(api_job_info.get("securityId") or page_detail.get("securityId") or card.get("security_id") or "").strip(),
        "jobName": str(
            api_job_info.get("jobName")
            or page_detail.get("jobName")
            or page_detail.get("title")
            or card.get("title")
            or ""
        ).strip(),
        "brandName": str(
            api_job_info.get("brandName")
            or page_detail.get("brandName")
            or page_detail.get("company_name")
            or card.get("company_name")
            or ""
        ).strip(),
        "locationName": str(
            api_job_info.get("locationName")
            or page_detail.get("locationName")
            or page_detail.get("city")
            or card.get("city")
            or ""
        ).strip(),
        "salaryDesc": str(
            api_job_info.get("salaryDesc")
            or page_detail.get("salary_text")
            or card.get("salary_text")
            or ""
        ).strip(),
        "experienceName": str(api_job_info.get("experienceName") or page_detail.get("experienceName") or "").strip(),
        "degreeName": str(api_job_info.get("degreeName") or page_detail.get("degreeName") or "").strip(),
        "positionLabels": api_job_info.get("positionLabels") if isinstance(api_job_info.get("positionLabels"), list) else [],
        "postDescription": str(
            detail_api.get("jd")
            or api_job_info.get("postDescription")
            or page_detail.get("description")
            or page_detail.get("postDescription")
            or ""
        ).strip(),
    }
    payload = build_boss_passive_job_payload(card, detail, query=query, page_url=page_url)
    status_info = infer_boss_application_status(api_job_info, page_detail, detail_api, detail)
    payload["application_status"] = status_info["status"]
    payload["raw_payload"] = {
        **dict(payload.get("raw_payload") or {}),
        "page_detail": page_detail,
        "detail_api": detail_api,
        "application_status_source": status_info.get("source", ""),
        "application_status_raw_text": status_info.get("raw_text", ""),
    }
    return payload


def build_boss_detail_card_from_job(job: JobPosting | dict) -> dict:
    payload = job.to_dict() if isinstance(job, JobPosting) else dict(job or {})
    raw_payload = payload.get("raw_payload") if isinstance(payload.get("raw_payload"), dict) else {}
    return {
        "job_url": str(
            payload.get("apply_url")
            or payload.get("url")
            or raw_payload.get("job_url")
            or raw_payload.get("detail_url")
            or ""
        ).strip(),
        "job_id": str(
            payload.get("source_job_id")
            or payload.get("job_id")
            or raw_payload.get("job_id")
            or raw_payload.get("encryptId")
            or ""
        ).strip(),
        "security_id": str(raw_payload.get("security_id") or raw_payload.get("securityId") or "").strip(),
        "lid": str(raw_payload.get("lid") or "").strip(),
        "title": str(payload.get("title") or raw_payload.get("title") or "").strip(),
        "company_name": str(payload.get("company_name") or raw_payload.get("company_name") or "").strip(),
        "city": str(payload.get("city") or raw_payload.get("city") or "").strip(),
        "salary_text": str(payload.get("salary_text") or raw_payload.get("salary_text") or "").strip(),
        "degree_requirement": str(
            payload.get("degree_requirement")
            or raw_payload.get("degree_requirement")
            or raw_payload.get("degreeName")
            or raw_payload.get("jobDegree")
            or ""
        ).strip(),
    }


def looks_like_boss_blank_block(before_snapshot: dict, after_snapshot: dict) -> bool:
    before_url = str((before_snapshot or {}).get("url") or "").lower()
    after_url = str((after_snapshot or {}).get("url") or "").lower()
    before_is_boss = bool((before_snapshot or {}).get("is_boss_domain")) or "zhipin.com" in before_url
    after_is_blank = bool((after_snapshot or {}).get("is_blank")) or after_url == "about:blank"
    return before_is_boss and after_is_blank


def is_boss_results_page_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return "/web/geek/jobs" in lowered or "/web/geek/job" in lowered


def build_boss_surface_observation(snapshot: dict) -> dict:
    snapshot = snapshot or {}
    url = str(snapshot.get("url") or "").strip()
    city_code = extract_boss_city_code_from_url(url)
    return {
        "url": url,
        "page_state": str(snapshot.get("page_state") or "").strip(),
        "title": str(snapshot.get("title") or "").strip(),
        "is_blank": bool(snapshot.get("is_blank")),
        "is_boss_domain": bool(snapshot.get("is_boss_domain")),
        "is_results_page": is_boss_results_page_url(url),
        "city_code": city_code,
        "city_name": resolve_boss_city_name(city_code),
        "query": extract_boss_query_from_url(url),
    }


def detect_boss_surface_drift(before_snapshot: dict, after_snapshot: dict, *, expected_city_code: str = "", keyword: str = "") -> list[str]:
    before = build_boss_surface_observation(before_snapshot)
    after = build_boss_surface_observation(after_snapshot)
    issues: list[str] = []
    normalized_keyword = " ".join((keyword or "").split()).strip()
    after_query = " ".join(str(after.get("query") or "").split()).strip()

    if before.get("is_results_page") and after.get("is_blank"):
        issues.append("blank_after_action")
    if str(after.get("page_state") or "") == "security_verify":
        issues.append("security_verify_after_action")
    if str(after.get("page_state") or "") == "login_required":
        issues.append("login_required_after_action")
    if before.get("is_results_page") and not after.get("is_results_page"):
        issues.append("left_results_page")
    if expected_city_code and str(after.get("city_code") or "").strip() and str(after.get("city_code") or "").strip() != str(expected_city_code).strip():
        issues.append("city_changed")
    if normalized_keyword and after_query and normalized_keyword not in after_query:
        issues.append("query_changed")
    return list(dict.fromkeys(issues))


class BossBrowserSource(JobSource):
    def __init__(
        self,
        name: str,
        storage_state_path: Path,
        *,
        profile_dir: Path | None = None,
        base_url: str = "https://www.zhipin.com/web/geek/jobs",
        max_queries: int = 6,
        max_cards_per_query: int = 8,
        max_detail_pages: int = 16,
        timeout_ms: int = 25000,
        headless: bool = True,
        debug_dir: Path | None = None,
        prefer_cdp_attach: bool = True,
        cdp_url: str = "",
        cdp_port: int = 9222,
    ):
        super().__init__(name)
        self.storage_state_path = storage_state_path
        self.profile_dir = profile_dir
        self.base_url = base_url.rstrip("/")
        self.max_queries = max_queries
        self.max_cards_per_query = max_cards_per_query
        self.max_detail_pages = max_detail_pages
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.debug_dir = debug_dir
        self.prefer_cdp_attach = prefer_cdp_attach
        self.cdp_url = cdp_url
        self.cdp_port = cdp_port
        self.manual_verify_timeout_ms = max(timeout_ms * 4, 90000)
        self._action_audit_events: list[dict] | None = None

    def _reset_action_audit(self) -> None:
        self._action_audit_events = []

    def _record_action_audit(self, action: str, **fields) -> None:
        if self._action_audit_events is None:
            return
        event: dict[str, object] = {"action": str(action or "").strip() or "unknown"}
        for key, value in (fields or {}).items():
            if value is None:
                continue
            if isinstance(value, (bool, int, float)):
                event[str(key)] = value
                continue
            normalized = str(value).strip()
            if normalized:
                event[str(key)] = normalized
        self._action_audit_events.append(event)

    def _consume_action_audit(self) -> list[dict]:
        events = list(self._action_audit_events or [])
        self._action_audit_events = None
        return events

    def _snapshot_browser_pages(self, browser, context) -> list[dict]:
        contexts = list(getattr(browser, "contexts", []) or []) if browser is not None else []
        if not contexts and context is not None:
            contexts = [context]
        snapshots: list[dict] = []
        for context_index, item in enumerate(contexts):
            pages = list(getattr(item, "pages", []) or [])
            page_items: list[dict] = []
            for page_index, page in enumerate(pages):
                if self._page_is_closed(page):
                    page_items.append(
                        {
                            "page_index": page_index,
                            "url": "",
                            "title": "",
                            "page_state": "closed",
                            "is_boss_domain": False,
                            "is_blank": False,
                        }
                    )
                    continue
                snapshot = extract_page_snapshot(page)
                page_items.append(
                    {
                        "page_index": page_index,
                        "url": str(snapshot.get("url") or ""),
                        "title": str(snapshot.get("title") or ""),
                        "page_state": str(snapshot.get("page_state") or ""),
                        "is_boss_domain": bool(snapshot.get("is_boss_domain")),
                        "is_blank": bool(snapshot.get("is_blank")),
                    }
                )
            snapshots.append(
                {
                    "context_index": context_index,
                    "page_count": len(page_items),
                    "pages": page_items,
                }
            )
        return snapshots

    def _attach_action_audit(self, payload: dict, *, browser, context, before_tabs: list[dict]) -> dict:
        if not isinstance(payload, dict):
            return payload
        payload["audit"] = {
            "events": self._consume_action_audit(),
            "before_tabs": before_tabs,
            "after_tabs": self._snapshot_browser_pages(browser, context),
        }
        return payload

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip3 install -r requirements.txt`锛屽啀鎵ц `python3 -m playwright install chromium`銆?"
            ) from exc
        use_profile = bool(self.profile_dir and (self.profile_dir / PROFILE_READY_MARKER).exists())
        if not use_profile and not self.storage_state_path.exists():
            raise RuntimeError(
                "鏈壘鍒板彲鐢ㄧ殑 BOSS 鐧诲綍鐜銆傚厛瀵煎嚭 storage_state锛屾垨鍦ㄦ湰鏈烘墽琛?`python scripts/local_boss_login.py` 鎴愬姛鐧诲綍涓€娆°€?"
            )

        queries = self._build_queries(settings, profile)
        if not queries:
            return []
        if self.prefer_cdp_attach:
            resolved_cdp_ws_url = resolve_cdp_websocket_url(self.cdp_port, self.cdp_url, timeout_seconds=1.5)
            if not resolved_cdp_ws_url:
                raise SourceHaltError("当前没有附着到登录浏览器。BOSS 主线不再自动回退到旧搜索路线。先重新连接登录浏览器后再试。")

        headless_modes = [self.headless]
        if self.headless:
            headless_modes.append(False)
        last_error: RuntimeError | None = None
        seen_modes: set[bool] = set()
        for launch_headless in headless_modes:
            if launch_headless in seen_modes:
                continue
            seen_modes.add(launch_headless)
            jobs: dict[str, JobPosting] = {}
            detail_errors: list[str] = []
            search_errors: list[str] = []
            try:
                with sync_playwright() as playwright:
                    browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                        playwright,
                        use_profile=use_profile,
                        launch_headless=launch_headless,
                    )
                    if not attached_over_cdp:
                        install_boss_stealth(context)
                    attached_jobs = self._build_attached_fetch_jobs_or_raise(
                        context,
                        attached_over_cdp=attached_over_cdp,
                    )
                    if attached_jobs is not None:
                        jobs = {job.fingerprint: job for job in attached_jobs}
                        if not use_profile:
                            self._persist_storage_state_snapshot(context, stage="attached_fetch")
                        if owns_context:
                            context.close()
                        return list(jobs.values())
                    detail_budget = self.max_detail_pages
                    active_queries = queries[:]
                    per_query_detail_cap = max(1, min(2, self.max_cards_per_query))
                    for query in active_queries:
                        if detail_budget <= 0:
                            break
                        search_page, should_close_search_page = self._acquire_search_page(
                            context,
                            attached_over_cdp=attached_over_cdp,
                        )
                        search_page.set_default_timeout(self.timeout_ms)
                        results_url = ""
                        cards: list[dict] = []
                        try:
                            cards = self._search_cards(
                                search_page,
                                query,
                                launch_headless=launch_headless,
                                attached_over_cdp=attached_over_cdp,
                            )
                            if attached_over_cdp:
                                results_url = search_page.url
                        except RuntimeError as exc:
                            search_errors.append(str(exc))
                            continue
                        query_detail_budget = min(detail_budget, per_query_detail_cap)
                        for card in cards:
                            if detail_budget <= 0 or query_detail_budget <= 0:
                                break
                            query_detail_budget -= 1
                            try:
                                if attached_over_cdp:
                                    job = self._fetch_detail_from_search_page(
                                        search_page,
                                        card,
                                        query,
                                        results_url=results_url,
                                    )
                                else:
                                    job = self._fetch_detail(context, card["url"], query)
                            except RuntimeError as exc:
                                detail_errors.append(str(exc))
                                continue
                            if not job:
                                continue
                            jobs[job.fingerprint] = job
                            detail_budget -= 1
                        if should_close_search_page:
                            try:
                                search_page.close()
                            except Exception:
                                pass
                    if not use_profile:
                        self._persist_storage_state_snapshot(
                            context,
                            stage="search_fetch" if attached_over_cdp else "standalone_fetch",
                        )
                    if owns_context:
                        context.close()
                    if browser is not None and not attached_over_cdp:
                        browser.close()
            except PlaywrightError as exc:
                last_error = RuntimeError(f"BOSS 娴忚鍣ㄩ噰闆嗗け璐ワ細{exc}")
                if launch_headless and self._should_retry_headed([str(last_error)]):
                    continue
                raise last_error from exc
            if jobs:
                return list(jobs.values())
            all_errors = search_errors + detail_errors
            if all_errors:
                last_error = RuntimeError(all_errors[-1])
                if launch_headless and self._should_retry_headed(all_errors):
                    continue
                raise last_error
            return []
        if last_error is not None:
            raise last_error
        return []

    def _build_attached_fetch_jobs_or_raise(self, context, *, attached_over_cdp: bool) -> list[JobPosting] | None:
        if attached_over_cdp:
            return self._fetch_jobs_from_current_results_queue(context)
        if self.prefer_cdp_attach:
            raise SourceHaltError("当前没有附着到登录浏览器。BOSS 主线不再自动回退到旧搜索路线。先重新连接登录浏览器后再试。")
        return None

    def probe_search_landing(self, *, city: str, keyword: str) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        if not normalized_city:
            raise RuntimeError("鍩庡競涓嶈兘涓虹┖銆?")
        if not normalized_keyword:
            raise RuntimeError("鍏抽敭璇嶄笉鑳戒负绌恒€?")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"???????{normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M2銆?")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            detail_worktab = None
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M2 闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
                search_page, should_close_search_page = self._acquire_search_page(
                    context,
                    attached_over_cdp=attached_over_cdp,
                )
                search_page.set_default_timeout(self.timeout_ms)
                try:
                    return self._probe_search_landing_page(
                        search_page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        attached_over_cdp=attached_over_cdp,
                    )
                except Exception as exc:
                    return self._probe_search_failure(
                        self._resolve_live_page(
                            context,
                            preferred_page=search_page,
                            allow_general_fallback=True,
                        ),
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=build_boss_search_url(
                            self._effective_search_base_url(search_page, attached_over_cdp=attached_over_cdp),
                            keyword=normalized_keyword,
                            city_code=city_code,
                        ),
                        stage="probe",
                        reason=self._describe_probe_exception(exc),
                        snapshot=extract_page_snapshot(
                            self._resolve_live_page(
                                context,
                                preferred_page=search_page,
                                allow_general_fallback=True,
                            )
                        ),
                        query_label=query_label,
                    )
                finally:
                    if should_close_search_page:
                        try:
                            search_page.close()
                        except Exception:
                            pass
            finally:
                if detail_worktab is not None:
                    try:
                        detail_worktab.close()
                    except Exception:
                        pass
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def probe_current_search_results(self, *, city: str, keyword: str) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        if not normalized_city:
            raise RuntimeError("鍩庡競涓嶈兘涓虹┖銆?")
        if not normalized_keyword:
            raise RuntimeError("鍏抽敭璇嶄笉鑳戒负绌恒€?")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"???????{normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M2銆?")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        expected_search_url = build_boss_search_url(self.base_url, keyword=normalized_keyword, city_code=city_code)
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            detail_worktab = None
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M2 琚姩妯″紡闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
                page = self._resolve_existing_live_page(context, allow_general_fallback=False)
                if page is None:
                    raise RuntimeError("No attached BOSS results page is available for diagnosis.")
                page.set_default_timeout(self.timeout_ms)
                snapshot = extract_page_snapshot(page)
                if snapshot.get("is_blank"):
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠鐧诲綍娴忚鍣ㄩ〉绛炬槸绌虹櫧椤碉紝鐤戜技鍛戒腑鍙嶆墥銆傚厛涓嶈缁х画鎶撱€?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                if not snapshot.get("is_boss_domain"):
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠涓嶆槸 BOSS 椤甸潰銆傚厛鍦ㄧ櫥褰曟祻瑙堝櫒閲屾墜鍔ㄦ墦寮€鐩爣缁撴灉椤靛悗鍐嶈瘯銆?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                if snapshot.get("page_state") == "security_verify":
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠椤甸潰鍛戒腑瀹夊叏楠岃瘉銆傚厛涓嶈缁х画鎶撱€?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                if snapshot.get("page_state") == "login_required":
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠椤甸潰鐧诲綍鎬佸け鏁堛€傚厛閲嶆柊鐧诲綍銆?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                current_url = snapshot.get("url", "")
                if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠杩樹笉鏄亴浣嶇粨鏋滈〉銆傝鍏堟墜鍔ㄦ墦寮€鐩爣缁撴灉椤靛悗鍐嶈瘯銆?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                current_city_code = extract_boss_city_code_from_url(current_url)
                current_query = extract_boss_query_from_url(current_url)
                if current_city_code != city_code or normalized_keyword not in current_query:
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_surface",
                        reason="褰撳墠缁撴灉椤典笉鏄洰鏍囨潯浠躲€傝鍏堟墜鍔ㄦ墦寮€娣卞湷 + 杩愯惀缁撴灉椤靛悗鍐嶈瘯銆?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                snapshot, probe_cards, total_count = self._read_passive_probe_cards(
                    page,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    query_label=query_label,
                )
                if not probe_cards:
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="passive_cards",
                        reason="褰撳墠缁撴灉椤靛凡鎵撳紑锛屼絾娌℃湁璇诲埌鍩虹鍗＄墖銆?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                return {
                    "ok": True,
                    "mode": "passive",
                    "city": normalized_city,
                    "city_code": city_code,
                    "keyword": normalized_keyword,
                    "search_url": expected_search_url,
                    "final_url": current_url,
                    "page_state": snapshot.get("page_state", ""),
                    "cards_count": len(probe_cards),
                    "total_count": int(total_count or len(probe_cards)),
                    "cards": probe_cards,
                }
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def probe_current_job_queue(self, *, city: str, keyword: str, limit: int = 8, rounds: int = 0) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Run `pip install -r requirements.txt` and `python -m playwright install chromium` first."
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        normalized_limit = max(1, min(int(limit or 1), 20))
        normalized_rounds = max(0, min(int(rounds or 0), 4))
        if not normalized_city:
            raise RuntimeError("City cannot be empty.")
        if not normalized_keyword:
            raise RuntimeError("Keyword cannot be empty.")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"Unsupported city: {normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("No attached login browser is available. Pass M1 first, then run M3.")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        expected_search_url = build_boss_search_url(self.base_url, keyword=normalized_keyword, city_code=city_code)
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M3 queue probe requires attaching to the login browser, but attach failed.")
                page = self._resolve_live_page(context, allow_general_fallback=True)
                page.set_default_timeout(self.timeout_ms)
                current_snapshot = extract_page_snapshot(page)
                current_url = str(current_snapshot.get("url") or "")
                if current_snapshot.get("is_blank"):
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="queue_probe_surface",
                        reason="褰撳墠鐧诲綍娴忚鍣ㄩ〉绛炬槸绌虹櫧椤碉紝鍏堜笉瑕佺户缁€?",
                        snapshot=current_snapshot,
                        query_label=query_label,
                    )
                if not current_snapshot.get("is_boss_domain"):
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="queue_probe_surface",
                        reason="褰撳墠涓嶆槸 BOSS 椤甸潰銆傚厛鎵撳紑涓€涓?BOSS 椤甸潰鍚庡啀璇曘€?",
                        snapshot=current_snapshot,
                        query_label=query_label,
                    )
                if current_snapshot.get("page_state") == "security_verify":
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="queue_probe_surface",
                        reason="褰撳墠椤甸潰鍛戒腑瀹夊叏楠岃瘉銆傚厛涓嶈缁х画銆?",
                        snapshot=current_snapshot,
                        query_label=query_label,
                    )
                if current_snapshot.get("page_state") == "login_required":
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="queue_probe_surface",
                        reason="褰撳墠椤甸潰鐧诲綍鎬佸け鏁堛€傚厛閲嶆柊鐧诲綍銆?",
                        snapshot=current_snapshot,
                        query_label=query_label,
                    )
                if is_boss_results_page_url(current_url):
                    snapshot = self._assert_passive_results_surface(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        query_label=query_label,
                    )
                    snapshot, probe_cards, total_count = self._read_passive_probe_cards(
                        page,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                    )
                    if not probe_cards:
                        landing = self._probe_search_landing_page(
                            page,
                            city=normalized_city,
                            city_code=city_code,
                            keyword=normalized_keyword,
                            query_label=query_label,
                            attached_over_cdp=True,
                        )
                        if not landing.get("ok"):
                            return landing
                        snapshot = extract_page_snapshot(page)
                        probe_cards = list(landing.get("cards") or [])
                        total_count = int(landing.get("total_count") or len(probe_cards))
                else:
                    landing = self._probe_search_landing_page(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        attached_over_cdp=True,
                    )
                    if not landing.get("ok"):
                        return landing
                    snapshot = extract_page_snapshot(page)
                    probe_cards = list(landing.get("cards") or [])
                    total_count = int(landing.get("total_count") or len(probe_cards))
                scroll_bundle = self._collect_queue_scroll_rounds(
                    page,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    query_label=query_label,
                    search_url=expected_search_url,
                    probe_cards=probe_cards,
                    total_count=int(total_count or len(probe_cards)),
                    limit=normalized_limit,
                    rounds=normalized_rounds,
                    stage_prefix="queue_probe_scroll",
                )
                if scroll_bundle.get("failure"):
                    return self._maybe_fallback_to_worktab_queue(
                        context,
                        dict(scroll_bundle["failure"]),
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        limit=normalized_limit,
                        rounds=max(1, normalized_rounds),
                    )
                snapshot = dict(scroll_bundle["final_snapshot"])
                probe_cards = list(scroll_bundle["probe_cards"])
                total_count = int(scroll_bundle["total_count"])
                queue_bundle = self._build_queue_jobs_from_probe_cards(
                    probe_cards,
                    query_label=query_label,
                    page_url=str(snapshot.get("url") or ""),
                    limit=normalized_limit,
                )
                return {
                    "ok": bool(queue_bundle["queue_jobs"]),
                    "mode": "queue",
                    "city": normalized_city,
                    "city_code": city_code,
                    "keyword": normalized_keyword,
                    "search_url": expected_search_url,
                    "final_url": str(snapshot.get("url") or ""),
                    "page_state": snapshot.get("page_state", ""),
                    "cards_count": len(probe_cards),
                    "queue_count": len(queue_bundle["queue_jobs"]),
                    "total_count": int(total_count or len(probe_cards)),
                    "scroll_rounds_planned": normalized_rounds,
                    "scroll_rounds_completed": len(scroll_bundle.get("scroll_reports") or []),
                    "scroll_reports": list(scroll_bundle.get("scroll_reports") or []),
                    "queue_jobs": queue_bundle["queue_jobs"],
                }
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def probe_worktab_job_queue(self, *, city: str, keyword: str, limit: int = 8, rounds: int = 2) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Run `pip install -r requirements.txt` and `python -m playwright install chromium` first."
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        normalized_limit = max(1, min(int(limit or 1), 20))
        normalized_rounds = max(0, min(int(rounds or 0), 4))
        if not normalized_city:
            raise RuntimeError("City cannot be empty.")
        if not normalized_keyword:
            raise RuntimeError("Keyword cannot be empty.")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"Unsupported city: {normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("No attached login browser is available. Pass M1 first, then run M3.")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M3 worktab probe requires attaching to the login browser, but attach failed.")
                return self._probe_worktab_job_queue_page(
                    context,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    query_label=query_label,
                    limit=normalized_limit,
                    rounds=normalized_rounds,
                )
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def probe_detail_pages(self, *, city: str, keyword: str, limit: int = 3) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        normalized_limit = max(1, min(int(limit or 1), 5))
        if not normalized_city:
            raise RuntimeError("鍩庡競涓嶈兘涓虹┖銆?")
        if not normalized_keyword:
            raise RuntimeError("鍏抽敭璇嶄笉鑳戒负绌恒€?")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"???????{normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M6銆?")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        expected_search_url = build_boss_search_url(self.base_url, keyword=normalized_keyword, city_code=city_code)
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M6 闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
                page = self._resolve_live_page(context, allow_general_fallback=True)
                page.set_default_timeout(self.timeout_ms)
                self._assert_passive_results_surface(
                    page,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    search_url=expected_search_url,
                    query_label=query_label,
                )
                snapshot, probe_cards, total_count = self._read_passive_probe_cards(
                    page,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    query_label=query_label,
                )
                if not probe_cards:
                    return self._probe_search_failure(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        search_url=expected_search_url,
                        stage="detail_probe_candidates",
                        reason="褰撳墠缁撴灉椤靛凡鎵撳紑锛屼絾娌℃湁璇诲埌鍙ˉ鎶?JD 鐨勫矖浣嶅崱鐗囥€?",
                        snapshot=snapshot,
                        query_label=query_label,
                    )
                candidates = probe_cards[:normalized_limit]
                results: list[dict] = []
                halted_error = ""
                halted_detail: dict = {}
                for card in candidates:
                    try:
                        results.append(
                            self._probe_one_detail_page(
                                context,
                                card=card,
                                query=query_label,
                            )
                        )
                    except SourceHaltError as exc:
                        halted_error = str(exc)
                        halted_detail = dict(exc.detail or {})
                        break
                success_count = sum(1 for item in results if item.get("ok"))
                payload = {
                    "ok": bool(success_count > 0 and not halted_error),
                    "mode": "detail_page_probe",
                    "city": normalized_city,
                    "city_code": city_code,
                    "keyword": normalized_keyword,
                    "search_url": expected_search_url,
                    "final_url": str(snapshot.get("url") or ""),
                    "page_state": snapshot.get("page_state", ""),
                    "candidate_count": len(probe_cards),
                    "attempted_count": len(results),
                    "success_count": success_count,
                    "results": results,
                    "limit": normalized_limit,
                    "total_count": int(total_count or len(probe_cards)),
                }
                if halted_error:
                    payload["error"] = halted_error
                    payload["halted"] = True
                    if halted_detail:
                        payload["halted_detail"] = halted_detail
                        artifact_path = str(halted_detail.get("artifact_path") or "").strip()
                        if artifact_path:
                            payload["artifact_path"] = artifact_path
                if detail_worktab is not None:
                    try:
                        detail_worktab.close()
                    except Exception:
                        pass
                    detail_worktab = None
                return payload
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def _select_detail_supplement_candidates(self, jobs: list[JobPosting] | list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for item in jobs:
            payload = item.to_dict() if isinstance(item, JobPosting) else dict(item or {})
            if str(payload.get("source") or "").strip() != "boss_browser":
                continue
            if bool(payload.get("detail_fetched")):
                continue
            card = build_boss_detail_card_from_job(payload)
            if not str(card.get("job_url") or "").strip():
                continue
            candidates.append({"job": payload, "card": card})
        return candidates

    def supplement_detail_jobs(self, jobs: list[JobPosting] | list[dict], *, limit: int = 3, query: str = "") -> dict:
        normalized_limit = max(1, min(int(limit or 1), 5))
        candidates = self._select_detail_supplement_candidates(jobs)
        if not candidates:
            return {
                "ok": True,
                "mode": "detail_job_supplement",
                "candidate_count": 0,
                "attempted_count": 0,
                "success_count": 0,
                "updated_jobs": [],
                "results": [],
                "limit": normalized_limit,
            }
        query_label = str(query or "").strip() or "boss_detail"
        selected_candidates = candidates[:normalized_limit]
        storage_state_available = self.storage_state_path.exists()
        results: list[dict | None] = [None] * len(selected_candidates)
        needs_browser_fallback: list[tuple[int, dict]] = []

        for index, item in enumerate(selected_candidates):
            if storage_state_available:
                result = self._supplement_one_detail_job_via_storage_state(
                    card=item["card"],
                    job_payload=item["job"],
                    query=query_label,
                )
                results[index] = result
                detail_code = result.get("detail_code")
                try:
                    detail_code_value = int(detail_code)
                except (TypeError, ValueError):
                    detail_code_value = -1
                if result.get("ok") or detail_code_value == 0:
                    continue
                needs_browser_fallback.append((index, item))
                continue
            needs_browser_fallback.append((index, item))

        used_browser_fallback = False
        if needs_browser_fallback:
            cdp_endpoint = resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0)
            if cdp_endpoint:
                try:
                    from playwright.sync_api import sync_playwright
                except ImportError as exc:
                    raise RuntimeError(
                        "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
                    ) from exc

                with sync_playwright() as playwright:
                    browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                        playwright,
                        use_profile=False,
                        launch_headless=False,
                    )
                    if not attached_over_cdp:
                        install_boss_stealth(context)
                    try:
                        if not attached_over_cdp:
                            raise RuntimeError("M6 闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
                        used_browser_fallback = True
                        for index, item in needs_browser_fallback:
                            results[index] = self._supplement_one_detail_job_via_browser_session(
                                context,
                                card=item["card"],
                                job_payload=item["job"],
                                query=query_label,
                            )
                    finally:
                        if owns_context:
                            context.close()
                        if browser is not None and not attached_over_cdp:
                            browser.close()
            elif not storage_state_available:
                raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M6銆?")

        finalized_results = [item for item in results if isinstance(item, dict)]
        updated_jobs = [dict(item["job"]) for item in finalized_results if item.get("ok") and isinstance(item.get("job"), dict)]
        success_count = sum(1 for item in finalized_results if item.get("ok"))
        surface_mode = "browser_session_only"
        if storage_state_available and used_browser_fallback:
            surface_mode = "storage_state_then_browser_session"
        elif storage_state_available:
            surface_mode = "storage_state_only"
        payload = {
            "ok": bool(success_count > 0),
            "mode": "detail_job_supplement",
            "surface_mode": surface_mode,
            "candidate_count": len(candidates),
            "attempted_count": len(finalized_results),
            "success_count": success_count,
            "updated_jobs": updated_jobs,
            "results": [
                {key: value for key, value in item.items() if key != "job"}
                for item in finalized_results
            ],
            "limit": normalized_limit,
        }
        first_error = next(
            (
                str(item.get("error") or "").strip()
                for item in finalized_results
                if isinstance(item, dict) and not item.get("ok") and str(item.get("error") or "").strip()
            ),
            "",
        )
        if first_error and not payload["ok"]:
            payload["error"] = first_error
        return payload

    def _supplement_one_detail_job_via_storage_state(
        self,
        *,
        card: dict,
        job_payload: dict,
        query: str,
    ) -> dict:
        return self._supplement_one_detail_job_via_payload_fetch(
            card=card,
            job_payload=job_payload,
            query=query,
            detail_fetch_mode="storage_state_only",
            fetcher=self._fetch_detail_payload_via_storage_state,
        )

    def _supplement_one_detail_job_via_browser_session(
        self,
        context,
        *,
        card: dict,
        job_payload: dict,
        query: str,
    ) -> dict:
        return self._supplement_one_detail_job_via_payload_fetch(
            card=card,
            job_payload=job_payload,
            query=query,
            detail_fetch_mode="browser_session_only",
            fetcher=lambda detail_url, *, referer_url="": self._fetch_detail_payload_via_browser_context(
                context,
                detail_url,
                referer_url=referer_url,
            ),
        )

    def _supplement_one_detail_job_via_payload_fetch(
        self,
        *,
        card: dict,
        job_payload: dict,
        query: str,
        detail_fetch_mode: str,
        fetcher,
    ) -> dict:
        job_url = str(card.get("job_url") or "").strip()
        if not job_url:
            return {
                "ok": False,
                "job_url": "",
                "title": str(card.get("title") or "").strip(),
                "company_name": str(card.get("company_name") or "").strip(),
                "error": "宀椾綅缂哄皯璇︽儏椤甸摼鎺ャ€?",
            }
        detail_url = build_boss_detail_url(dict(card or {}))
        detail_api: dict = {}
        fetch_error = ""
        if detail_url:
            try:
                detail_api = extract_boss_detail_api_payload(fetcher(detail_url, referer_url=job_url))
            except Exception as exc:
                fetch_error = str(exc)
        else:
            fetch_error = "鏃犳硶鎷煎嚭 detail 鎺ュ彛鍦板潃銆?"
        return self._build_detail_supplement_result(
            card=card,
            job_payload=job_payload,
            query=query,
            detail_url=detail_url,
            detail_api=detail_api,
            fetch_error=fetch_error,
            detail_fetch_mode=detail_fetch_mode,
        )

    def _build_detail_supplement_result(
        self,
        *,
        card: dict,
        job_payload: dict,
        query: str,
        detail_url: str,
        detail_api: dict,
        fetch_error: str,
        detail_fetch_mode: str,
    ) -> dict:
        job_url = str(card.get("job_url") or "").strip()
        payload = build_boss_detail_page_payload(
            card,
            {},
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
            "employment_mode": str(
                base_payload.get("employment_mode") or payload.get("employment_mode") or ""
            ).strip(),
            "application_status": str(
                base_payload.get("application_status") or payload.get("application_status") or ""
            ).strip()
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

    def diagnose_detail_jobs(self, jobs: list[JobPosting] | list[dict], *, limit: int = 1, query: str = "") -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
            ) from exc

        normalized_limit = max(1, min(int(limit or 1), 3))
        candidates = self._select_detail_supplement_candidates(jobs)
        if not candidates:
            return {
                "ok": True,
                "mode": "detail_job_diagnose",
                "candidate_count": 0,
                "attempted_count": 0,
                "success_count": 0,
                "results": [],
                "limit": normalized_limit,
            }
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M6銆?")

        query_label = str(query or "").strip() or "boss_detail_diagnose"
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("M6 闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
                context_surfaces_before = self._snapshot_context_surfaces(context)
                current_page = self._resolve_existing_live_page(context, allow_general_fallback=False)
                if current_page is not None:
                    current_page.set_default_timeout(self.timeout_ms)
                results = [
                    self._diagnose_one_detail_job(
                        context,
                        current_page=current_page,
                        card=item["card"],
                        job_payload=item["job"],
                        query=query_label,
                    )
                    for item in candidates[:normalized_limit]
                ]
                success_count = sum(1 for item in results if item.get("ok"))
                return {
                    "ok": bool(success_count > 0),
                    "mode": "detail_job_diagnose",
                    "candidate_count": len(candidates),
                    "attempted_count": len(results),
                    "success_count": success_count,
                    "context_surfaces_before": context_surfaces_before,
                    "context_surfaces_after": self._snapshot_context_surfaces(context),
                    "resolved_current_page_url": str(getattr(current_page, "url", "") or "").strip() if current_page is not None else "",
                    "results": results,
                    "limit": normalized_limit,
                }
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def inspect_detail_surfaces(self) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "鏈畨瑁?playwright銆傚厛鎵ц `pip install -r requirements.txt`锛屽啀鎵ц `python -m playwright install chromium`銆?"
            ) from exc

        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("褰撳墠娌℃湁杩炴帴鍒扮櫥褰曟祻瑙堝櫒銆傚厛閫氳繃 M1锛屽啀鍋?M6銆?")

        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                raise RuntimeError("M6 闇€瑕佸鐢ㄧ櫥褰曟祻瑙堝櫒锛屼絾褰撳墠娌℃湁鎴愬姛闄勭潃鍒板畠銆?")
            try:
                surfaces = self._snapshot_context_surfaces(context)
                strict_page = self._resolve_existing_live_page(context, allow_general_fallback=False)
                general_page = self._resolve_existing_live_page(context, allow_general_fallback=True)
                strict_snapshot = extract_page_snapshot(strict_page) if strict_page is not None else {}
                general_snapshot = extract_page_snapshot(general_page) if general_page is not None else {}
                return {
                    "ok": True,
                    "mode": "detail_surface_inspect",
                    "context_surfaces": surfaces,
                    "strict_page_url": str(strict_snapshot.get("url") or "").strip(),
                    "strict_page_state": str(strict_snapshot.get("page_state") or "").strip(),
                    "general_page_url": str(general_snapshot.get("url") or "").strip(),
                    "general_page_state": str(general_snapshot.get("page_state") or "").strip(),
                }
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def _diagnose_one_detail_job(self, context, *, current_page, card: dict, job_payload: dict, query: str) -> dict:
        card = dict(card or {})
        job_payload = dict(job_payload or {})
        snapshot = extract_page_snapshot(current_page) if current_page is not None else {}
        current_url = str(snapshot.get("url") or "").strip()
        detail_url = build_boss_detail_url(card)
        result = {
            "ok": False,
            "job_url": str(card.get("job_url") or "").strip(),
            "job_id": str(card.get("job_id") or "").strip(),
            "security_id": str(card.get("security_id") or "").strip(),
            "lid": str(card.get("lid") or "").strip(),
            "title": str(card.get("title") or "").strip(),
            "company_name": str(card.get("company_name") or "").strip(),
            "current_page_url": current_url,
            "current_page_state": str(snapshot.get("page_state") or "").strip(),
            "detail_url": detail_url,
            "modes": [],
        }
        if current_page is None:
            result["modes"].append(
                {
                    "mode": "attached_current_cdp_fetch",
                    "ok": False,
                    "page_url": "",
                    "page_state": "",
                    "detail_url": detail_url,
                    "detail_code": None,
                    "detail_message": "",
                    "jd_len": 0,
                    "error": "褰撳墠娌℃湁宸查檮鐫€鐨勫彲鐢?BOSS 椤电銆?",
                }
            )
        else:
            result["modes"].append(
                self._probe_detail_fetch_mode(
                    current_page,
                    mode_name="attached_current_cdp_fetch",
                    card=card,
                    query=query,
                    detail_url=detail_url,
                    navigate_to_job=False,
                )
            )
        same_origin_seed = current_url if snapshot.get("is_boss_domain") else "https://www.zhipin.com/"
        result["same_origin_seed_url"] = same_origin_seed
        seeded_worktab = self._open_dedicated_worktab_page(context, same_origin_seed)
        try:
            seeded_worktab.set_default_timeout(self.timeout_ms)
            try:
                seeded_worktab.wait_for_timeout(900)
            except Exception:
                pass
            result["modes"].append(
                self._probe_detail_fetch_mode(
                    seeded_worktab,
                    mode_name="worktab_same_origin_cdp_fetch",
                    card=card,
                    query=query,
                    detail_url=detail_url,
                    navigate_to_job=False,
                )
            )
        finally:
            try:
                seeded_worktab.close()
            except Exception:
                pass
        detail_worktab = self._open_dedicated_worktab_page(context, "about:blank")
        try:
            detail_worktab.set_default_timeout(self.timeout_ms)
            result["modes"].append(
                self._probe_detail_fetch_mode(
                    detail_worktab,
                    mode_name="worktab_detail_nav_cdp_fetch",
                    card=card,
                    query=query,
                    detail_url=detail_url,
                    navigate_to_job=True,
                )
            )
        finally:
            try:
                detail_worktab.close()
            except Exception:
                pass
        result["ok"] = any(bool(mode.get("ok")) for mode in result["modes"])
        return result

    def _probe_detail_fetch_mode(
        self,
        page,
        *,
        mode_name: str,
        card: dict,
        query: str,
        detail_url: str,
        navigate_to_job: bool,
    ) -> dict:
        card = dict(card or {})
        page_detail: dict = {}
        effective_detail_url = str(detail_url or "").strip()
        snapshot: dict = {}
        result = {
            "mode": mode_name,
            "ok": False,
            "page_url": "",
            "page_state": "",
            "detail_url": effective_detail_url,
            "detail_code": None,
            "detail_message": "",
            "jd_len": 0,
        }
        try:
            try:
                page.bring_to_front()
            except Exception:
                pass
            if navigate_to_job:
                self._navigate_detail_worktab(page, str(card.get("job_url") or "").strip())
                self._inject_detail_page_activity(page)
                self._wait_for_detail_page(page)
                snapshot = extract_page_snapshot(page)
                self._raise_if_detail_page_surface_invalid(
                    page,
                    snapshot,
                    query=query,
                    card=card,
                    note=f"{mode_name}_surface_invalid",
                )
                if not self._detail_matches_target(page, {"url": str(card.get("job_url") or "").strip(), **card}):
                    self._raise_if_detail_page_surface_invalid(
                        page,
                        snapshot,
                        query=query,
                        card=card,
                        note=f"{mode_name}_target_mismatch",
                        reason="璇︽儏椤垫病鏈夌ǔ瀹氳惤鍒扮洰鏍囧矖浣嶏紝宸茬珛鍒诲仠姝€?",
                    )
                page_detail = self._extract_detail_page_probe_fields(page)
                effective_detail_url = build_boss_detail_url({**card, **page_detail}) or effective_detail_url
            else:
                try:
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                snapshot = extract_page_snapshot(page)
                if snapshot.get("is_blank"):
                    raise RuntimeError("褰撳墠椤垫槸绌虹櫧椤点€?")
                if not snapshot.get("is_boss_domain"):
                    raise RuntimeError("褰撳墠椤典笉鏄彲鐢ㄧ殑 BOSS 椤点€?")
            if not effective_detail_url:
                raise RuntimeError("鏃犳硶鎷煎嚭 detail 鎺ュ彛鍦板潃銆?")
            detail_api = extract_boss_detail_api_payload(self._fetch_detail_payload_via_cdp(page, effective_detail_url))
            result.update(
                {
                    "ok": bool(str(detail_api.get("jd") or "").strip()),
                    "page_url": str(snapshot.get("url") or "").strip(),
                    "page_state": str(snapshot.get("page_state") or "").strip(),
                    "detail_url": effective_detail_url,
                    "detail_code": detail_api.get("code"),
                    "detail_message": str(detail_api.get("message") or "").strip(),
                    "jd_len": len(str(detail_api.get("jd") or "").strip()),
                }
            )
            if page_detail:
                result["page_detail"] = {
                    "encryptId": str(page_detail.get("encryptId") or "").strip(),
                    "securityId": str(page_detail.get("securityId") or "").strip(),
                    "lid": str(page_detail.get("lid") or "").strip(),
                }
            return result
        except Exception as exc:
            try:
                snapshot = extract_page_snapshot(page)
            except Exception:
                snapshot = snapshot or {}
            result.update(
                {
                    "error": str(exc),
                    "page_url": str(snapshot.get("url") or "").strip(),
                    "page_state": str(snapshot.get("page_state") or "").strip(),
                    "detail_url": effective_detail_url,
                }
            )
            artifact = self._save_detail_artifacts(
                page,
                query=query,
                note=f"{mode_name}_failed",
                extra={
                    "mode": mode_name,
                    "card": {
                        "job_url": str(card.get("job_url") or "").strip(),
                        "title": str(card.get("title") or "").strip(),
                        "company_name": str(card.get("company_name") or "").strip(),
                    },
                    "detail_url": effective_detail_url,
                    "page_detail": page_detail,
                    "error": str(exc),
                },
            )
            if artifact:
                result["artifact_path"] = artifact["status_path"]
            return result

    """
    def diagnose_current_results_page(self, *, city: str, keyword: str, step: str = "read") -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "閺堫亜鐣ㄧ憗?playwright閵嗗倸鍘涢幍褑顢?`pip install -r requirements.txt`閿涘苯鍟€閹笛嗩攽 `python -m playwright install chromium`閵?"
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        normalized_step = str(step or "read").strip().lower()
        if normalized_step not in {"read", "scroll", "click"}:
            raise RuntimeError(f"娑撳秵鏁幐浣烘畱鐠囧﹥鏌囧銉╊€冮敍姝縮tep}")
        if not normalized_city:
            raise RuntimeError("閸╁骸绔舵稉宥堝厴娑撹櫣鈹栭妴?")
        if not normalized_keyword:
            raise RuntimeError("閸忔娊鏁拠宥勭瑝閼虫垝璐熺粚鎭掆偓?")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"閺嗗倷绗夐弨顖涘瘮閸╁骸绔堕敍姝縩ormalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("瑜版挸澧犲▽鈩冩箒鏉╃偞甯撮崚鎵瑜版洘绁荤憴鍫濇珤閵嗗倸鍘涢柅姘崇箖 M1閿涘苯鍟€閸嬫俺鐦栭弬顓溾偓?")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        expected_search_url = build_boss_search_url(self.base_url, keyword=normalized_keyword, city_code=city_code)
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("D1 鐠囧﹥鏌囧Ο鈥崇础闂団偓鐟曚礁顦查悽銊ф瑜版洘绁荤憴鍫濇珤閿涘奔绲捐ぐ鎾冲濞屸剝婀侀幋鎰闂勫嫮娼冮崚鏉跨暊閵?")
                page = self._resolve_live_page(context, allow_general_fallback=True)
                page.set_default_timeout(self.timeout_ms)
                surface = self._validate_current_results_diagnosis_surface(
                    page,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    expected_search_url=expected_search_url,
                    query_label=query_label,
                    step=normalized_step,
                )
                if surface.get("error"):
                    return surface
                if normalized_step == "read":
                    return self._diagnose_current_results_read(
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        expected_search_url=expected_search_url,
                    )
                if normalized_step == "scroll":
                    return self._diagnose_current_results_scroll(
                        context,
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        expected_search_url=expected_search_url,
                    )
                return self._diagnose_current_results_click(
                    context,
                    page,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    query_label=query_label,
                    expected_search_url=expected_search_url,
                )
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def _validate_current_results_diagnosis_surface(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        expected_search_url: str,
        query_label: str,
        step: str,
    ) -> dict:
        snapshot = extract_page_snapshot(page)
        current_url = str(snapshot.get("url") or "")
        if snapshot.get("is_blank"):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犻惂璇茬秿濞村繗顫嶉崳銊┿€夌粵鐐Ц缁岃櫣娅фい纰夌礉閻ゆ垳鎶€閸涙垝鑵戦崣宥嗗ⅴ閵嗗倸鍘涙稉宥堫洣缂佈呯敾鐠囧﹥鏌囬妴?",
                snapshot=snapshot,
            )
        if not snapshot.get("is_boss_domain"):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犳稉宥嗘Ц BOSS 妞ょ敻娼伴妴鍌氬帥閸︺劎娅ヨぐ鏇熺セ鐟欏牆娅掗柌灞惧閸斻劍澧﹀鈧惄顔界垼缂佹挻鐏夋い闈涙倵閸愬秷鐦栭弬顓溾偓?",
                snapshot=snapshot,
            )
        if snapshot.get("page_state") == "security_verify":
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犳い鐢告桨閸涙垝鑵戠€瑰鍙忔宀冪槈閵嗗倸鍘涙稉宥堫洣缂佈呯敾鐠囧﹥鏌囬妴?",
                snapshot=snapshot,
            )
        if snapshot.get("page_state") == "login_required":
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犳い鐢告桨閻ц缍嶉幀浣搞亼閺佸牄鈧倸鍘涢柌宥嗘煀閻ц缍嶉妴?",
                snapshot=snapshot,
            )
        if not is_boss_results_page_url(current_url):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犳潻妯圭瑝閺勵垵浜存担宥囩波閺嬫粓銆夐妴鍌濐嚞閸忓牊澧滈崝銊﹀ⅵ瀵偓閻╊喗鐖ｇ紒鎾寸亯妞ら潧鎮楅崘宥堢槚閺傤厹鈧?",
                snapshot=snapshot,
            )
        current_city_code = extract_boss_city_code_from_url(current_url)
        current_query = extract_boss_query_from_url(current_url)
        if current_city_code != city_code or keyword not in current_query:
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="瑜版挸澧犵紒鎾寸亯妞ゅ吀绗夐弰顖滄窗閺嶅洦娼禒韬测偓鍌濐嚞閸忓牊澧滈崝銊﹀ⅵ瀵偓鐎电懓绨查崺搴＄閸滃苯鍙ч柨顔跨槤閻ㄥ嫮绮ㄩ弸婊堛€夐妴?",
                snapshot=snapshot,
            )
        return {
            "ok": True,
            "step": step,
            "surface": build_boss_surface_observation(snapshot),
        }

    """

    def diagnose_current_results_page(self, *, city: str, keyword: str, step: str = "read") -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed. Run `pip install -r requirements.txt` and `python -m playwright install chromium` first."
            ) from exc

        normalized_city = (city or "").strip()
        normalized_keyword = " ".join((keyword or "").split()).strip()
        normalized_step = str(step or "read").strip().lower()
        if normalized_step not in {"read", "scroll", "click"}:
            raise RuntimeError(f"Unsupported diagnosis step: {step}")
        if not normalized_city:
            raise RuntimeError("City cannot be empty.")
        if not normalized_keyword:
            raise RuntimeError("Keyword cannot be empty.")
        city_code = resolve_boss_city_code(normalized_city)
        if not city_code:
            raise RuntimeError(f"Unsupported city: {normalized_city}")
        if not resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.0):
            raise RuntimeError("No attached login browser is available. Pass M1 first, then run diagnosis.")

        query_label = f"{normalized_city} {normalized_keyword}".strip()
        expected_search_url = build_boss_search_url(self.base_url, keyword=normalized_keyword, city_code=city_code)
        with sync_playwright() as playwright:
            browser, context, attached_over_cdp, owns_context = self._open_browser_context(
                playwright,
                use_profile=False,
                launch_headless=False,
            )
            if not attached_over_cdp:
                install_boss_stealth(context)
            self._reset_action_audit()
            before_tabs = self._snapshot_browser_pages(browser, context)
            try:
                if not attached_over_cdp:
                    raise RuntimeError("D1 diagnosis requires attaching to the login browser, but attach failed.")
                page = self._resolve_live_page(context, allow_general_fallback=True)
                page.set_default_timeout(self.timeout_ms)
                surface = self._validate_current_results_diagnosis_surface(
                    page,
                    city=normalized_city,
                    city_code=city_code,
                    keyword=normalized_keyword,
                    expected_search_url=expected_search_url,
                    query_label=query_label,
                    step=normalized_step,
                )
                if surface.get("error"):
                    return self._attach_action_audit(surface, browser=browser, context=context, before_tabs=before_tabs)
                if normalized_step == "read":
                    return self._attach_action_audit(
                        self._diagnose_current_results_read(
                            page,
                            city=normalized_city,
                            city_code=city_code,
                            keyword=normalized_keyword,
                            query_label=query_label,
                            expected_search_url=expected_search_url,
                        ),
                        browser=browser,
                        context=context,
                        before_tabs=before_tabs,
                    )
                if normalized_step == "scroll":
                    return self._attach_action_audit(
                        self._diagnose_current_results_scroll(
                            context,
                            page,
                            city=normalized_city,
                            city_code=city_code,
                            keyword=normalized_keyword,
                            query_label=query_label,
                            expected_search_url=expected_search_url,
                        ),
                        browser=browser,
                        context=context,
                        before_tabs=before_tabs,
                    )
                return self._attach_action_audit(
                    self._diagnose_current_results_click(
                        context,
                        page,
                        city=normalized_city,
                        city_code=city_code,
                        keyword=normalized_keyword,
                        query_label=query_label,
                        expected_search_url=expected_search_url,
                    ),
                    browser=browser,
                    context=context,
                    before_tabs=before_tabs,
                )
            finally:
                if owns_context:
                    context.close()
                if browser is not None and not attached_over_cdp:
                    browser.close()

    def _validate_current_results_diagnosis_surface(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        expected_search_url: str,
        query_label: str,
        step: str,
    ) -> dict:
        snapshot = extract_page_snapshot(page)
        current_url = str(snapshot.get("url") or "")
        if snapshot.get("is_blank"):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The attached BOSS tab is blank. This may indicate blocking, so stop here.",
                snapshot=snapshot,
            )
        if not snapshot.get("is_boss_domain"):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The attached tab is not a BOSS page. Open the target results page first.",
                snapshot=snapshot,
            )
        if snapshot.get("page_state") == "security_verify":
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The current page is in security verification state. Do not continue.",
                snapshot=snapshot,
            )
        if snapshot.get("page_state") == "login_required":
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The current page is no longer logged in. Re-login first.",
                snapshot=snapshot,
            )
        if not is_boss_results_page_url(current_url):
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The current page is not a jobs results page. Open the target results page first.",
                snapshot=snapshot,
            )
        current_city_code = extract_boss_city_code_from_url(current_url)
        current_query = extract_boss_query_from_url(current_url)
        if current_city_code != city_code or keyword not in current_query:
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step=step,
                reason="The current results page does not match the expected city or keyword.",
                snapshot=snapshot,
            )
        return {
            "ok": True,
            "step": step,
            "surface": build_boss_surface_observation(snapshot),
        }

    def _diagnose_current_results_read(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        expected_search_url: str,
    ) -> dict:
        before_snapshot = extract_page_snapshot(page)
        self._record_action_audit(
            "read_extract_search_cards_start",
            page_url=str(before_snapshot.get("url") or ""),
            query=query_label,
        )
        raw_cards = self._extract_search_cards(page, limit=max(self.max_cards_per_query, 6), query=query_label)
        after_cards_snapshot = extract_page_snapshot(page)
        self._record_action_audit(
            "read_extract_search_cards_end",
            page_url=str(after_cards_snapshot.get("url") or ""),
            cards_count=len(raw_cards),
        )
        cards = [build_boss_dom_probe_card(item) for item in raw_cards[: min(len(raw_cards), 4)]]
        self._record_action_audit(
            "read_extract_joblist_resource_start",
            page_url=str(after_cards_snapshot.get("url") or ""),
            city_code=city_code,
            keyword=keyword,
        )
        joblist_resource_url = self._extract_joblist_resource_url(page, city_code=city_code, keyword=keyword)
        after_snapshot = extract_page_snapshot(page)
        self._record_action_audit(
            "read_extract_joblist_resource_end",
            page_url=str(after_snapshot.get("url") or ""),
            found=bool(joblist_resource_url),
            resource_url=joblist_resource_url,
        )
        issues = detect_boss_surface_drift(before_snapshot, after_snapshot, expected_city_code=city_code, keyword=keyword)
        return {
            "ok": not issues,
            "mode": "diagnose",
            "step": "read",
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "expected_search_url": expected_search_url,
            "before": build_boss_surface_observation(before_snapshot),
            "after_cards": build_boss_surface_observation(after_cards_snapshot),
            "after": build_boss_surface_observation(after_snapshot),
            "cards_count": len(raw_cards),
            "cards": cards,
            "joblist_resource_url": joblist_resource_url,
            "issues": issues,
        }

    def _diagnose_current_results_scroll(
        self,
        context,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        expected_search_url: str,
    ) -> dict:
        before_snapshot = extract_page_snapshot(page)
        try:
            self._scroll_passive_results_list(page)
        except Exception as exc:
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step="scroll",
                reason=self._describe_probe_exception(exc),
                snapshot=before_snapshot,
            )
        after_page = self._resolve_existing_live_page(context, preferred_page=page, allow_general_fallback=False) or page
        after_snapshot = extract_page_snapshot(after_page)
        issues = detect_boss_surface_drift(before_snapshot, after_snapshot, expected_city_code=city_code, keyword=keyword)
        ok = not issues
        payload = {
            "ok": ok,
            "mode": "diagnose",
            "step": "scroll",
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "expected_search_url": expected_search_url,
            "before": build_boss_surface_observation(before_snapshot),
            "after": build_boss_surface_observation(after_snapshot),
            "issues": issues,
        }
        if not ok:
            artifact = self._save_search_artifacts(
                after_page,
                query=query_label,
                note="diagnose_scroll_failed",
                extra=payload,
            )
            payload["artifact_path"] = artifact["status_path"] if artifact else ""
        return payload

    """
    def _diagnose_current_results_click(
        self,
        context,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        expected_search_url: str,
    ) -> dict:
        before_snapshot = extract_page_snapshot(page)
        raw_cards = self._extract_search_cards(page, limit=max(self.max_cards_per_query, 3), query=query_label)
        if not raw_cards:
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step="click",
                reason="瑜版挸澧犵紒鎾寸亯妞ゅ吀绗傚▽鈩冩箒閸欘垯浜掗悽銊ょ艾閸楁洘顒為悙鐟板毊鐠囧﹥鏌囬惃鍕煐娴ｅ秴宕遍悧鍥モ偓?",
                snapshot=before_snapshot,
            )
        raw_card = raw_cards[0]
        clicked_card = build_boss_dom_probe_card(raw_card)
        detail = self._read_passive_card_detail(page, raw_card)
        after_page = self._resolve_existing_live_page(context, preferred_page=page, allow_general_fallback=False) or page
        after_snapshot = extract_page_snapshot(after_page)
        detail_text = extract_boss_passive_detail_text(detail)
        issues = detect_boss_surface_drift(before_snapshot, after_snapshot, expected_city_code=city_code, keyword=keyword)
        if not is_boss_passive_detail_complete(detail):
            issues.append("detail_incomplete")
        payload = {
            "ok": not issues,
            "mode": "diagnose",
            "step": "click",
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "expected_search_url": expected_search_url,
            "before": build_boss_surface_observation(before_snapshot),
            "after": build_boss_surface_observation(after_snapshot),
            "clicked_card": {
                "title": clicked_card.get("title", ""),
                "company_name": clicked_card.get("company_name", ""),
                "city": clicked_card.get("city", ""),
                "job_url": clicked_card.get("job_url", ""),
            },
            "detail_complete": is_boss_passive_detail_complete(detail),
            "detail_text_len": len(detail_text),
            "detail_preview": detail_text[:400],
            "issues": list(dict.fromkeys(issues)),
        }
        if not payload["ok"]:
            artifact = self._save_search_artifacts(
                after_page,
                query=query_label,
                note="diagnose_click_failed",
                extra=payload,
            )
            payload["artifact_path"] = artifact["status_path"] if artifact else ""
        return payload

    """

    def _diagnose_current_results_click(
        self,
        context,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        expected_search_url: str,
    ) -> dict:
        before_snapshot = extract_page_snapshot(page)
        raw_cards = self._extract_search_cards(page, limit=max(self.max_cards_per_query, 3), query=query_label)
        if not raw_cards:
            return self._diagnose_current_results_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                expected_search_url=expected_search_url,
                query_label=query_label,
                step="click",
                reason="No job card is available for the single-click diagnosis step on the current results page.",
                snapshot=before_snapshot,
            )
        raw_card = raw_cards[0]
        clicked_card = build_boss_dom_probe_card(raw_card)
        detail = self._read_passive_card_detail(page, raw_card)
        after_page = self._resolve_live_page(context, preferred_page=page, allow_general_fallback=True)
        after_snapshot = extract_page_snapshot(after_page)
        detail_text = extract_boss_passive_detail_text(detail)
        issues = detect_boss_surface_drift(before_snapshot, after_snapshot, expected_city_code=city_code, keyword=keyword)
        if not is_boss_passive_detail_complete(detail):
            issues.append("detail_incomplete")
        payload = {
            "ok": not issues,
            "mode": "diagnose",
            "step": "click",
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "expected_search_url": expected_search_url,
            "before": build_boss_surface_observation(before_snapshot),
            "after": build_boss_surface_observation(after_snapshot),
            "clicked_card": {
                "title": clicked_card.get("title", ""),
                "company_name": clicked_card.get("company_name", ""),
                "city": clicked_card.get("city", ""),
                "job_url": clicked_card.get("job_url", ""),
            },
            "detail_complete": is_boss_passive_detail_complete(detail),
            "detail_text_len": len(detail_text),
            "detail_preview": detail_text[:400],
            "issues": list(dict.fromkeys(issues)),
        }
        if not payload["ok"]:
            artifact = self._save_search_artifacts(
                after_page,
                query=query_label,
                note="diagnose_click_failed",
                extra=payload,
            )
            payload["artifact_path"] = artifact["status_path"] if artifact else ""
        return payload

    def _diagnose_current_results_failure(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        expected_search_url: str,
        query_label: str,
        step: str,
        reason: str,
        snapshot: dict,
    ) -> dict:
        payload = {
            "ok": False,
            "mode": "diagnose",
            "step": step,
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "expected_search_url": expected_search_url,
            "before": build_boss_surface_observation(snapshot),
            "after": build_boss_surface_observation(snapshot),
            "issues": ["surface_invalid"],
            "error": reason,
        }
        artifact = self._save_search_artifacts(
            page,
            query=query_label,
            note=f"diagnose_{step}_surface_invalid",
            extra=payload,
        )
        payload["artifact_path"] = artifact["status_path"] if artifact else ""
        return payload

    def _fetch_jobs_from_current_results_page(self, context) -> list[JobPosting]:
        page = self._resolve_live_page(context, allow_general_fallback=True)
        page.set_default_timeout(self.timeout_ms)
        snapshot = extract_page_snapshot(page)
        current_url = str(snapshot.get("url") or "")
        if snapshot.get("is_blank"):
            raise SourceHaltError("褰撳墠鐧诲綍娴忚鍣ㄩ〉绛炬槸绌虹櫧椤碉紝鍏堜笉瑕佺户缁姄銆?")
        if not snapshot.get("is_boss_domain"):
            raise SourceHaltError("褰撳墠涓嶆槸 BOSS 椤甸潰銆傚厛鍦ㄧ櫥褰曟祻瑙堝櫒閲屾墦寮€ BOSS 鑱屼綅缁撴灉椤点€?")
        if snapshot.get("page_state") == "security_verify":
            raise SourceHaltError("褰撳墠椤甸潰鍛戒腑瀹夊叏楠岃瘉銆傚厛涓嶈缁х画鎶撱€?")
        if snapshot.get("page_state") == "login_required":
            raise SourceHaltError("褰撳墠椤甸潰鐧诲綍鎬佸け鏁堛€傚厛閲嶆柊鐧诲綍銆?")
        if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
            raise SourceHaltError("褰撳墠杩樹笉鏄亴浣嶇粨鏋滈〉銆傚厛鍦ㄧ櫥褰曟祻瑙堝櫒閲屾墦寮€涓€涓亴浣嶇粨鏋滈〉鍐嶆姄銆?")
        current_query = extract_boss_query_from_url(current_url)
        current_city_code = extract_boss_city_code_from_url(current_url)
        if not current_query or not current_city_code:
            raise SourceHaltError("褰撳墠缁撴灉椤垫潯浠朵笉鏄庣‘銆傚厛鎵嬪姩鎵撳紑涓€涓甫鍩庡競鍜屽叧閿瘝鐨勭粨鏋滈〉锛屽啀鐐规姄鍙栥€?")
        query_label = build_boss_query_label_from_url(current_url)
        target_count = max(
            1,
            min(max(self.max_detail_pages, self.max_cards_per_query), max(self.max_cards_per_query, 1) * 2),
        )
        raw_jobs, detail_scan_report = self._collect_passive_current_result_details(
            page,
            query_label=query_label,
            target_count=target_count,
            city_code=current_city_code,
            keyword=current_query,
        )
        if not raw_jobs:
            raise SourceHaltError("褰撳墠缁撴灉椤靛凡鎵撳紑锛屼絾娌℃湁璇诲埌鍙叆搴撳矖浣嶃€?")
        jobs: dict[str, JobPosting] = {}
        for raw in raw_jobs:
            job = normalize_job_fields(raw, source=self.name)
            if not is_job_quality_acceptable(job):
                continue
            jobs[job.fingerprint] = job
        if not jobs:
            raise SourceHaltError("褰撳墠缁撴灉椤佃鍒颁簡宀椾綅鍗＄墖锛屼絾璇︽儏涓嶅瀹屾暣锛屽厛涓嶈鍏ュ簱銆?")
        self.last_fetch_report = {
            "mode": "passive_current_results_detail_scan",
            "query_label": query_label,
            "page_url": current_url,
            "raw_job_count": len(raw_jobs),
            "count": len(jobs),
            "detail_scan": detail_scan_report,
        }
        return list(jobs.values())

    def _fetch_jobs_from_current_results_queue(self, context) -> list[JobPosting]:
        page = self._resolve_live_page(context, allow_general_fallback=True)
        page.set_default_timeout(self.timeout_ms)
        snapshot = extract_page_snapshot(page)
        current_url = str(snapshot.get("url") or "")
        if snapshot.get("is_blank"):
            raise SourceHaltError("褰撳墠鐧诲綍娴忚鍣ㄩ〉绛炬槸绌虹櫧椤碉紝鍏堜笉瑕佺户缁姄銆?")
        if not snapshot.get("is_boss_domain"):
            raise SourceHaltError("褰撳墠涓嶆槸 BOSS 椤甸潰銆傚厛鍦ㄧ櫥褰曟祻瑙堝櫒閲屾墦寮€ BOSS 鑱屼綅缁撴灉椤点€?")
        if snapshot.get("page_state") == "security_verify":
            raise SourceHaltError("褰撳墠椤甸潰鍛戒腑瀹夊叏楠岃瘉銆傚厛涓嶈缁х画鎶撱€?")
        if snapshot.get("page_state") == "login_required":
            raise SourceHaltError("褰撳墠椤甸潰鐧诲綍鎬佸け鏁堛€傚厛閲嶆柊鐧诲綍銆?")
        if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
            raise SourceHaltError("褰撳墠杩樹笉鏄亴浣嶇粨鏋滈〉銆傚厛鍦ㄧ櫥褰曟祻瑙堝櫒閲屾墦寮€涓€涓亴浣嶇粨鏋滈〉鍐嶆姄銆?")
        current_query = extract_boss_query_from_url(current_url)
        current_city_code = extract_boss_city_code_from_url(current_url)
        if not current_query or not current_city_code:
            raise SourceHaltError("褰撳墠缁撴灉椤垫潯浠朵笉鏄庣‘銆傚厛鎵嬪姩鎵撳紑涓€涓甫鍩庡競鍜屽叧閿瘝鐨勭粨鏋滈〉锛屽啀鐐规姄鍙栥€?")
        query_label = build_boss_query_label_from_url(current_url)
        target_count = max(1, self.max_cards_per_query)
        surface_snapshot, probe_cards, total_count = self._read_passive_probe_cards(
            page,
            city_code=current_city_code,
            keyword=current_query,
            query_label=query_label,
        )
        if not probe_cards:
            raise SourceHaltError("褰撳墠缁撴灉椤靛凡鎵撳紑锛屼絾娌℃湁璇诲埌鍙叆搴撳矖浣嶅垪琛ㄣ€?")
        queue_bundle = self._build_queue_jobs_from_probe_cards(
            probe_cards,
            query_label=query_label,
            page_url=str(surface_snapshot.get("url") or current_url),
            limit=target_count,
        )
        jobs = {job.fingerprint: job for job in queue_bundle["jobs"]}
        if not jobs:
            raise SourceHaltError("褰撳墠缁撴灉椤佃鍒颁簡鍒楄〃鍗＄墖锛屼絾杩樹笉澶熷儚鍚堟硶鐨勫€欓€夊矖浣嶉槦鍒椼€?")
        self.last_fetch_report = {
            "mode": "passive_current_results_queue",
            "query_label": query_label,
            "page_url": current_url,
            "cards_count": len(probe_cards),
            "queue_count": int(queue_bundle["queue_count"]),
            "accepted_queue_count": int(queue_bundle["accepted_queue_count"]),
            "total_count": int(total_count or len(probe_cards)),
            "count": len(jobs),
            "page_state": str(surface_snapshot.get("page_state") or ""),
        }
        return list(jobs.values())

    def _build_queue_jobs_from_probe_cards(
        self,
        probe_cards: list[dict],
        *,
        query_label: str,
        page_url: str,
        limit: int,
    ) -> dict:
        normalized_limit = max(1, min(int(limit or 1), max(len(probe_cards or []), 1)))
        queue_payloads = [
            build_boss_list_queue_payload(
                card,
                query=query_label,
                page_url=page_url,
            )
            for card in list(probe_cards or [])[:normalized_limit]
            if isinstance(card, dict)
        ]
        queue_jobs: list[dict] = []
        jobs: dict[str, JobPosting] = {}
        accepted_count = 0
        for raw in queue_payloads:
            job = normalize_job_fields(raw, source=self.name)
            if not self._is_boss_queue_job_acceptable(job):
                continue
            if job.fingerprint in jobs:
                continue
            jobs[job.fingerprint] = job
            accepted_count += 1
            queue_jobs.append(
                {
                    "job_url": job.url,
                    "job_id": str(raw.get("job_id") or raw.get("source_job_id") or "").strip(),
                    "security_id": str(raw.get("security_id") or "").strip(),
                    "lid": str(raw.get("lid") or "").strip(),
                    "title": job.title,
                    "company_name": job.company_name,
                    "city": job.city,
                    "salary_text": job.salary_text,
                    "detail_fetched": job.detail_fetched,
                    "phase": str((job.raw_payload or {}).get("phase") or ""),
                    "detail_url": str(((job.raw_payload or {}).get("detail_url") or "")).strip(),
                }
            )
        return {
            "jobs": list(jobs.values()),
            "queue_jobs": queue_jobs,
            "queue_count": len(queue_payloads),
            "accepted_queue_count": accepted_count,
        }

    def _merge_probe_cards(
        self,
        existing_cards: list[dict],
        new_cards: list[dict],
        *,
        limit: int,
    ) -> tuple[list[dict], int]:
        normalized_limit = max(1, int(limit or 1))
        merged = list(existing_cards or [])
        seen_keys: set[str] = set()
        for item in merged:
            key = str(item.get("job_url") or item.get("job_id") or "").strip()
            if key:
                seen_keys.add(key)
        added = 0
        for item in list(new_cards or []):
            if len(merged) >= normalized_limit:
                break
            if not isinstance(item, dict):
                continue
            key = str(item.get("job_url") or item.get("job_id") or "").strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(item)
            added += 1
        return merged[:normalized_limit], added

    def _inject_worktab_joblist_scroll(self, page, *, settle_ms: int = 1400) -> dict:
        return page.evaluate(
            """
            async ({ settleMs }) => {
              const viewport = window.innerHeight || 800;
              const root = document.scrollingElement || document.documentElement;
              const maxScroll = Math.max((root?.scrollHeight || 0) - viewport, 0);
              const startY = Math.max(window.scrollY || root?.scrollTop || 0, 0);
              const softStep = Math.max(48, Math.floor(viewport * 0.09));
              const finalY = Math.max(0, Math.min(startY + softStep, maxScroll));
              const deltaY = Math.max(Math.floor(softStep * 0.5), 24);
              window.dispatchEvent(new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true }));
              window.scrollTo({ top: finalY, behavior: 'smooth' });
              window.dispatchEvent(new Event('scroll', { bubbles: true }));
              await new Promise((resolve) => setTimeout(resolve, 760 + Math.floor(Math.random() * 420)));
              await new Promise((resolve) => setTimeout(resolve, settleMs));

              return {
                startY,
                finalY,
                viewport,
                documentHeight: root?.scrollHeight || 0,
                maxScroll,
              };
            }
            """,
            {"settleMs": max(3200, int(settle_ms or 3200))},
        )

    def _collect_queue_scroll_rounds(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        search_url: str,
        probe_cards: list[dict],
        total_count: int,
        limit: int,
        rounds: int,
        stage_prefix: str,
    ) -> dict:
        final_snapshot = extract_page_snapshot(page)
        current_url = str(final_snapshot.get("url") or "")
        scroll_reports: list[dict] = []
        consecutive_no_new = 0
        planned_rounds = max(0, int(rounds or 0))
        for round_index in range(planned_rounds):
            if len(probe_cards) >= limit:
                break
            try:
                page.wait_for_timeout(4200 + round_index * 1200)
            except Exception:
                pass
            before_scroll = extract_page_snapshot(page)
            before_url = str(before_scroll.get("url") or "")
            before_city_code = extract_boss_city_code_from_url(before_url)
            before_query = extract_boss_query_from_url(before_url)
            if before_scroll.get("page_state") == "security_verify":
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍓嶅懡涓簡瀹夊叏楠岃瘉銆?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                }
            if before_scroll.get("page_state") == "login_required":
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍓嶅彂鐜扮櫥褰曟€佸け鏁堛€?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                }
            if not is_boss_results_page_url(before_url) or before_city_code != city_code or keyword not in before_query:
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍓嶅凡缁忓亸绂荤洰鏍囩粨鏋滈〉锛屽凡绔嬪嵆鍋滄銆?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                }
            try:
                before_scroll = self._assert_results_surface_quiet_window(
                    page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    query_label=query_label,
                    window_ms=2400,
                    interval_ms=400,
                    max_refresh_retries=2,
                )
            except (RuntimeError, SourceHaltError) as exc:
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason=str(exc),
                        snapshot=extract_page_snapshot(page),
                        query_label=query_label,
                    )
                }
            recovered_next_cards: list[dict] = []
            recovered_total_count = int(total_count or len(probe_cards))
            try:
                next_payload = self._wait_for_joblist_payload_after_action_v2(
                    page,
                    action=lambda: self._inject_worktab_joblist_scroll(page, settle_ms=3200),
                    timeout_ms=max(self.timeout_ms, 15000),
                )
            except RuntimeError as exc:
                exc_text = str(exc)
                normalized_exc_text = exc_text.lower()
                if "joblist" in normalized_exc_text and (
                    "超时" in exc_text
                    or "timeout" in normalized_exc_text
                    or "瓒呮椂" in exc_text
                    or "json" in normalized_exc_text
                ):
                    recovered_next_cards, recovered_total_count = self._recover_probe_cards_after_scroll_timeout(
                        page,
                        city_code=city_code,
                        keyword=keyword,
                        query_label=query_label,
                    )
                    if not recovered_next_cards:
                        return {
                            "failure": self._probe_search_failure(
                                page,
                                city=city,
                                city_code=city_code,
                                keyword=keyword,
                                search_url=search_url,
                                stage=stage_prefix,
                                reason=str(exc),
                                snapshot=extract_page_snapshot(page),
                                query_label=query_label,
                            )
                        }
                    next_payload = {"jobs": [], "total_count": recovered_total_count}
                else:
                    return {
                        "failure": self._probe_search_failure(
                            page,
                            city=city,
                            city_code=city_code,
                            keyword=keyword,
                            search_url=search_url,
                            stage=stage_prefix,
                            reason=str(exc),
                            snapshot=extract_page_snapshot(page),
                            query_label=query_label,
                        )
                    }
            after_scroll = extract_page_snapshot(page)
            after_url = str(after_scroll.get("url") or "")
            after_city_code = extract_boss_city_code_from_url(after_url)
            after_query = extract_boss_query_from_url(after_url)
            if after_scroll.get("is_blank") or not after_scroll.get("is_boss_domain"):
                reason = "婊氬姩鍚庣寮€浜嗗彲鐢ㄧ殑 BOSS 椤甸潰銆?"
                if looks_like_boss_blank_block(before_scroll, after_scroll):
                    reason = "婊氬姩鍚庤烦鎴愪簡绌虹櫧椤碉紝鐤戜技鍛戒腑鍙嶆墥銆傚厛涓嶈缁х画銆?"
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason=reason,
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                }
            if after_scroll.get("page_state") == "security_verify":
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍚庡懡涓簡瀹夊叏楠岃瘉銆?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                }
            if after_scroll.get("page_state") == "login_required":
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍚庡彂鐜扮櫥褰曟€佸け鏁堛€?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                }
            if not is_boss_results_page_url(after_url) or after_city_code != city_code or keyword not in after_query:
                return {
                    "failure": self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage=stage_prefix,
                        reason="婊氬姩鍚庡亸绂讳簡鐩爣缁撴灉椤碉紝宸茬珛鍗冲仠姝€?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                }
            if recovered_next_cards:
                next_cards = list(recovered_next_cards)
                total_count = int(recovered_total_count or total_count)
            else:
                next_cards = [
                    build_boss_search_probe_card(raw)
                    for raw in next_payload.get("jobs", [])
                    if isinstance(raw, dict)
                ]
                next_cards = [card for card in next_cards if card.get("job_url")]
                total_count = int(next_payload.get("total_count", total_count) or total_count)
            probe_cards, added = self._merge_probe_cards(probe_cards, next_cards, limit=limit)
            final_snapshot = after_scroll
            current_url = after_url
            scroll_reports.append(
                {
                    "round": round_index + 1,
                    "added": int(added),
                    "cards_count": len(probe_cards),
                    "page_state": str(after_scroll.get("page_state") or ""),
                    "final_url": after_url,
                    "recovered_from_resource": bool(recovered_next_cards),
                }
            )
            if added <= 0:
                consecutive_no_new += 1
            else:
                consecutive_no_new = 0
            if consecutive_no_new >= 2:
                break
        return {
            "failure": None,
            "probe_cards": list(probe_cards),
            "total_count": int(total_count or len(probe_cards)),
            "scroll_reports": scroll_reports,
            "final_snapshot": final_snapshot,
            "final_url": current_url,
        }

    def _is_boss_queue_job_acceptable(self, job: JobPosting) -> bool:
        title = str(job.title or "").strip()
        company_name = str(job.company_name or "").strip()
        url = str(job.url or "").strip().lower()
        description = str(job.description or "").strip()
        if len(title) < 2:
            return False
        if not company_name:
            return False
        if "zhipin.com/job_detail/" not in url:
            return False
        if any(token in url for token in ("/login", "/register", "/security-check", "/captcha", "/verify")):
            return False
        if any(token in description for token in ("注册登录", "请稍候", "安全验证", "扫码登录")):
            return False
        return True

    def _collect_passive_current_result_payloads(self, page, *, query_label: str, target_count: int) -> list[dict]:
        raw_jobs, _scan_report = self._collect_passive_current_result_details(
            page,
            query_label=query_label,
            target_count=target_count,
        )
        return raw_jobs

    def _collect_passive_current_result_details(
        self,
        page,
        *,
        query_label: str,
        target_count: int,
        city_code: str = "",
        keyword: str = "",
    ) -> tuple[list[dict], dict]:
        raw_payloads: list[dict] = []
        seen_urls: set[str] = set()
        stalled_rounds = 0
        max_rounds = max(3, min(6, target_count + 1))
        report = {
            "target_count": target_count,
            "scan_rounds": 0,
            "scroll_rounds": 0,
            "clicked_cards": 0,
            "accepted_details": 0,
        }
        for _ in range(max_rounds):
            report["scan_rounds"] = int(report.get("scan_rounds") or 0) + 1
            round_payloads, round_report, page = self._collect_visible_passive_detail_payloads(
                page,
                query_label=query_label,
                remaining_count=max(1, target_count - len(raw_payloads)),
                seen_urls=seen_urls,
                city_code=city_code,
                keyword=keyword,
            )
            raw_payloads.extend(round_payloads)
            report["clicked_cards"] = int(report.get("clicked_cards") or 0) + int(round_report.get("clicked_cards") or 0)
            report["accepted_details"] = int(report.get("accepted_details") or 0) + int(round_report.get("accepted_details") or 0)
            if len(raw_payloads) >= target_count:
                return raw_payloads[:target_count], report
            if not round_payloads:
                stalled_rounds += 1
            else:
                stalled_rounds = 0
            if stalled_rounds >= 2:
                break
            should_guard_surface = getattr(page, "context", None) is not None
            before_snapshot = extract_page_snapshot(page) if should_guard_surface else {}
            self._scroll_passive_results_list(page)
            page = self._resolve_passive_live_page(page)
            if should_guard_surface:
                self._raise_if_passive_results_surface_lost(
                    extract_page_snapshot(page),
                    city_code=city_code,
                    keyword=keyword,
                    action="婊氬姩缁撴灉鍒楄〃",
                    before_snapshot=before_snapshot,
                )
            report["scroll_rounds"] = int(report.get("scroll_rounds") or 0) + 1
        return raw_payloads, report

    def _collect_visible_passive_detail_payloads(
        self,
        page,
        *,
        query_label: str,
        remaining_count: int,
        seen_urls: set[str],
        city_code: str = "",
        keyword: str = "",
    ) -> tuple[list[dict], dict, object]:
        raw_cards = self._extract_search_cards(
            page,
            limit=max(max(remaining_count, 1) * 3, self.max_cards_per_query * 2),
            query=query_label,
        )
        raw_payloads: list[dict] = []
        report = {
            "clicked_cards": 0,
            "accepted_details": 0,
        }
        for raw_card in raw_cards:
            if len(raw_payloads) >= remaining_count:
                break
            card = build_boss_dom_probe_card(raw_card)
            job_url = str(card.get("job_url") or "").strip()
            if not job_url or job_url in seen_urls:
                continue
            report["clicked_cards"] = int(report.get("clicked_cards") or 0) + 1
            should_guard_surface = getattr(page, "context", None) is not None
            before_snapshot = extract_page_snapshot(page) if should_guard_surface else {}
            detail = self._read_passive_card_detail(page, raw_card)
            page = self._resolve_passive_live_page(page)
            if should_guard_surface:
                self._raise_if_passive_results_surface_lost(
                    extract_page_snapshot(page),
                    city_code=city_code,
                    keyword=keyword,
                    action="鐐瑰嚮宸︿晶宀椾綅鍗＄墖",
                    before_snapshot=before_snapshot,
                )
            if not is_boss_passive_detail_complete(detail):
                continue
            payload = build_boss_passive_job_payload(card, detail, query=query_label, page_url=job_url)
            if not payload.get("title") or not payload.get("company_name") or not payload.get("detail_fetched"):
                continue
            seen_urls.add(job_url)
            raw_payloads.append(payload)
            report["accepted_details"] = int(report.get("accepted_details") or 0) + 1
        return raw_payloads, report, page

    def _resolve_passive_live_page(self, page):
        context = getattr(page, "context", None)
        if context is None:
            return page
        try:
            return self._resolve_live_page(context, preferred_page=page, allow_general_fallback=True)
        except Exception:
            return page

    def _raise_if_passive_results_surface_lost(
        self,
        snapshot: dict,
        *,
        city_code: str,
        keyword: str,
        action: str,
        before_snapshot: dict | None = None,
    ) -> None:
        snapshot = snapshot or {}
        page_state = str(snapshot.get("page_state") or "").strip()
        current_url = str(snapshot.get("url") or "").strip()
        issues = detect_boss_surface_drift(
            before_snapshot or snapshot,
            snapshot,
            expected_city_code=city_code,
            keyword=keyword,
        )
        if page_state == "blank_page" or "blank_after_action" in issues:
            raise SourceHaltError(f"页面在{action}后变成了空白页，已立刻停止。")
        if page_state == "security_verify" or "security_verify_after_action" in issues:
            raise SourceHaltError(f"页面在{action}后触发了安全验证，已立刻停止。")
        if page_state == "login_required" or "login_required_after_action" in issues:
            raise SourceHaltError(f"页面在{action}后登录失效，已立刻停止。")
        if not snapshot.get("is_boss_domain"):
            raise SourceHaltError(f"页面在{action}后离开了 BOSS，已立刻停止。")
        if self._page_looks_like_recommendation_surface(snapshot):
            raise SourceHaltError(f"页面在{action}后跳回了主页，已立刻停止。")
        if not is_boss_results_page_url(current_url) or "left_results_page" in issues:
            raise SourceHaltError(f"页面在{action}后离开了当前结果页，已立刻停止。")
        if "city_changed" in issues or "query_changed" in issues:
            raise SourceHaltError(f"页面在{action}后结果条件发生了变化，已立刻停止。")

    def _scroll_passive_results_list(self, page) -> None:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        try:
            result = page.evaluate(
                """
                () => {
                  const findFirstCard = () =>
                    document.querySelector('li.job-card-box') ||
                    document.querySelector('[data-rb-job-card-key]')?.closest('li, [class*="job-card"], [class*="job-list"], [class*="search-job-result"]') ||
                    document.querySelector('a[href*="/job_detail"]')?.closest('li, [class*="job-card"], [class*="job-list"], [class*="search-job-result"]');
                  const looksScrollable = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const overflowY = String(style?.overflowY || '');
                    const scrollableHeight = Math.max((node.scrollHeight || 0) - (node.clientHeight || 0), 0);
                    return scrollableHeight > 80 && /(auto|scroll|overlay)/i.test(overflowY);
                  };
                  const firstCard = findFirstCard();
                  let container = firstCard;
                  while (container && !looksScrollable(container)) {
                    container = container.parentElement;
                  }
                  if (!container) {
                    const root = document.scrollingElement || document.documentElement;
                    const beforeTop = Math.round(window.scrollY || root.scrollTop || 0);
                    return {
                      mode: 'none',
                      moved: false,
                      delta: 0,
                      beforeTop,
                      afterTop: beforeTop,
                      maxTop: Math.max(Math.round((root.scrollHeight || 0) - (window.innerHeight || root.clientHeight || 0)), 0),
                      reason: 'no_safe_results_container',
                    };
                  }
                  const target = container;
                  const beforeTop = Math.round(target.scrollTop || 0);
                  const viewport = Math.max(1, Math.round(target.clientHeight || 0));
                  const maxTop = Math.max(Math.round((target.scrollHeight || 0) - viewport), 0);
                  const remaining = Math.max(maxTop - beforeTop, 0);
                  const delta = Math.max(0, Math.min(remaining, 48));
                  if (delta <= 0) {
                    return {
                      mode: 'container',
                      moved: false,
                      delta: 0,
                      beforeTop,
                      afterTop: beforeTop,
                      maxTop,
                    };
                  }
                  const nextTop = Math.max(0, Math.min(beforeTop + delta, maxTop));
                  target.dispatchEvent(new WheelEvent('wheel', { deltaY: delta, bubbles: true, cancelable: true }));
                  target.scrollTop = nextTop;
                  target.dispatchEvent(new Event('scroll', { bubbles: true }));
                  return {
                    mode: 'container',
                    moved: true,
                    delta,
                    beforeTop,
                    afterTop: Math.round(target.scrollTop || 0),
                    maxTop,
                  };
                }
                """
            )
        except Exception:
            result = {}
        audit_fields = {"page_url": current_url}
        if isinstance(result, dict):
            audit_fields["mode"] = str(result.get("mode") or "")
            audit_fields["delta"] = int(result.get("delta") or 0)
            audit_fields["moved"] = bool(result.get("moved"))
            audit_fields["before_top"] = int(result.get("beforeTop") or 0)
            audit_fields["after_top"] = int(result.get("afterTop") or 0)
            audit_fields["reason"] = str(result.get("reason") or "")
        self._record_action_audit("scroll_results_list", **audit_fields)
        if isinstance(result, dict) and result.get("moved"):
            page.wait_for_timeout(120)

    def _build_queries(self, settings: UserSettings, profile: ResumeProfile | None) -> list[str]:
        current_year = datetime.now().year
        role_candidates = settings.preferred_roles[:3] or (profile.target_roles[:3] if profile else []) or ["杩愯惀"]
        city_candidates = settings.preferred_cities[:2] or [""]
        intents: list[str] = []
        if "鏍℃嫑" in settings.job_types:
            intents.extend([f"{current_year}灞?搴斿眾", "鏍″洯鎷涜仒"])
        if "绀炬嫑" in settings.job_types:
            intents.extend(["绀炬嫑", "绀句細鎷涜仒"])
        if settings.campus_role_mode in {"intern", "both"}:
            intents.extend(["瀹炰範", "鏃ュ父瀹炰範"])
        if settings.campus_role_mode == "full_time":
            intents.append("姝ｈ亴")
        deduped_queries: list[str] = []
        seen: set[str] = set()
        for role in role_candidates:
            for intent in intents or [""]:
                for city in city_candidates:
                    query = " ".join(part for part in [city, intent, role] if part).strip()
                    if not query or query in seen:
                        continue
                    seen.add(query)
                    deduped_queries.append(query)
                    if len(deduped_queries) >= self.max_queries:
                        return deduped_queries
        return deduped_queries

    def _warm_up_context(self, page) -> None:
        try:
            self._goto_with_retry(page, "https://www.zhipin.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(1800)
        except Exception:
            return

    def _search_cards(self, page, query: str, *, launch_headless: bool, attached_over_cdp: bool) -> list[dict]:
        if attached_over_cdp:
            return self._search_cards_with_human_rhythm(page, query, launch_headless=launch_headless)
        return self._search_cards_with_navigation(page, query, launch_headless=launch_headless)

    def _search_cards_with_human_rhythm(self, page, query: str, *, launch_headless: bool) -> list[dict]:
        base_url = self._effective_search_base_url(page, attached_over_cdp=True)
        search_url = self._build_search_url(page, base_url=base_url, query=query)
        snapshots: list[dict] = []
        for attempt in range(2):
            try:
                page.bring_to_front()
            except Exception:
                pass
            self._reading_pause(page, short=True)
            page, snapshot = self._ensure_search_surface(page, base_url=base_url, query=query)
            snapshots.append(snapshot)
            if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
                continue
            if snapshot["is_security_verify"]:
                continue
            if not self._page_city_matches_query(page, query):
                self._goto_with_retry(page, search_url, wait_until="domcontentloaded")
                self._reading_pause(page)
                snapshot = extract_page_snapshot(page)
                snapshots.append(snapshot)
                if snapshot["is_security_verify"] and self._wait_for_manual_resume(page, query=query):
                    snapshot = extract_page_snapshot(page)
                    snapshots.append(snapshot)
                if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
                    continue
                if snapshot["is_security_verify"]:
                    continue
            self._reading_pause(page)
            previous_signature = self._current_search_signature(page, query=query)
            submitted = self._submit_query_like_user(page, query)
            if not submitted:
                if attempt == 0:
                    self._goto_with_retry(page, search_url, wait_until="domcontentloaded")
                    self._reading_pause(page)
                continue
            cards = self._wait_for_search_cards(
                page,
                query=query,
                previous_signature=previous_signature,
                search_url=search_url,
            )
            if cards:
                return cards
            snapshot = extract_page_snapshot(page)
            snapshots.append(snapshot)
            if snapshot["is_security_verify"] and self._wait_for_manual_resume(page, query=query):
                cards = self._extract_search_cards(page, query=query)
                if cards:
                    return cards
            if attempt == 0:
                self._reading_pause(page)
        snapshot = extract_page_snapshot(page)
        snapshots.append(snapshot)
        artifact = self._save_search_artifacts(
            page,
            query=query,
            note="search_page_failed",
            extra={
                "search_url": search_url,
                "headless": launch_headless,
                "attached_over_cdp": True,
                "cdp_candidates": candidate_cdp_urls(self.cdp_port, self.cdp_url),
                "snapshots": snapshots,
                "mode": "reuse_visible_page_human_rhythm",
            },
        )
        detail = (
            f"url={snapshot.get('url', '')} "
            f"state={snapshot.get('page_state', '')} "
            f"title={(snapshot.get('title', '') or '')[:40]}"
        ).strip()
        artifact_note = f" ?????{artifact['status_path']}" if artifact else ""
        if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
            raise RuntimeError(f"BOSS ??????????????{detail}{artifact_note}")
        if snapshot["is_security_verify"]:
            raise RuntimeError(f"BOSS ??????????????{detail}{artifact_note}")
        if snapshot.get("page_state") == "loading":
            raise RuntimeError(f"BOSS ????????????????{detail}{artifact_note}")
        if snapshot.get("page_state") == "login_required":
            raise RuntimeError(f"BOSS ??????????{detail}{artifact_note}")
        return []

    def _search_cards_with_navigation(self, page, query: str, *, launch_headless: bool) -> list[dict]:
        base_url = self._effective_search_base_url(page, attached_over_cdp=False)
        search_url = self._build_search_url(page, base_url=base_url, query=query)
        snapshots: list[dict] = []
        for attempt in range(2):
            self._warm_up_context(page)
            if attempt == 0:
                self._goto_with_retry(page, search_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1800)
            else:
                self._goto_with_retry(page, base_url, wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                self._goto_with_retry(page, search_url, wait_until="load")
                page.wait_for_timeout(2200)
                try:
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_timeout(1200)
                except Exception:
                    pass
            for _ in range(3):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(800)
            try:
                cards = self._extract_search_cards(page)
            except Exception as exc:
                if self._is_navigation_context_error(exc):
                    page.wait_for_timeout(1200)
                    continue
                raise
            if cards:
                return cards
            snapshot = extract_page_snapshot(page)
            snapshots.append(snapshot)
            if snapshot["page_state"] == "ready":
                if attempt == 0:
                    page.wait_for_timeout(1500)
                    continue
                return []
        snapshot = extract_page_snapshot(page)
        snapshots.append(snapshot)
        artifact = self._save_search_artifacts(
            page,
            query=query,
            note="search_page_failed",
            extra={
                "search_url": search_url,
                "headless": launch_headless,
                "attached_over_cdp": False,
                "cdp_candidates": candidate_cdp_urls(self.cdp_port, self.cdp_url),
                "snapshots": snapshots,
            },
        )
        detail = (
            f"url={snapshot.get('url', '')} "
            f"state={snapshot.get('page_state', '')} "
            f"title={(snapshot.get('title', '') or '')[:40]}"
        ).strip()
        artifact_note = f" ?????{artifact['status_path']}" if artifact else ""
        if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
            raise RuntimeError(
                f"BOSS ??????????????{detail}{artifact_note}"
            )
        if snapshot["is_security_verify"]:
            raise RuntimeError(f"BOSS ?????????{detail}{artifact_note}")
        if looks_like_login_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
            raise RuntimeError(f"BOSS ?????????{detail}{artifact_note}")
        return []

    def _probe_search_landing_page(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        attached_over_cdp: bool,
    ) -> dict:
        base_url = self._effective_search_base_url(page, attached_over_cdp=attached_over_cdp)
        search_url = build_boss_search_url(base_url, keyword=keyword, city_code=city_code)
        start_snapshot = extract_page_snapshot(page)
        page, surface_snapshot = self._ensure_search_surface(
            page,
            base_url=base_url,
            query=query_label,
            allow_manual_resume=False,
        )
        if surface_snapshot["is_blank"] or not surface_snapshot["is_boss_domain"]:
            reason = "褰撳墠涓嶆槸鍙敤鐨?BOSS 椤甸潰銆?"
            if looks_like_boss_blank_block(start_snapshot, surface_snapshot):
                reason = "椤甸潰浠庡凡鎵撳紑鐨?BOSS 椤佃烦鎴愪簡绌虹櫧椤碉紝鐤戜技鍛戒腑鍙嶆墥銆傚厛涓嶈缁х画鎶撱€?"
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="surface",
                reason=reason,
                snapshot=surface_snapshot,
                query_label=query_label,
            )
        if surface_snapshot.get("page_state") == "security_verify":
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="surface",
                reason="鍒氳繘鍏ユ悳绱㈤潰灏卞懡涓簡瀹夊叏楠岃瘉銆?",
                snapshot=surface_snapshot,
                query_label=query_label,
            )
        if surface_snapshot.get("page_state") == "login_required":
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="surface",
                reason="鐧诲綍鎬佸け鏁堬紝鏃犳硶缁х画杩涘叆鎼滅储缁撴灉椤点€?",
                snapshot=surface_snapshot,
                query_label=query_label,
            )

        recovered_cards: list[dict] | None = None
        recovered_total_count = 0
        try:
            payload = self._wait_for_joblist_payload_after_action_v2(
                page,
                action=lambda: self._goto_with_retry(page, search_url, wait_until="domcontentloaded"),
                timeout_ms=max(self.timeout_ms, 15000),
            )
        except RuntimeError as exc:
            exc_text = str(exc or "").strip()
            for recovery_round in range(3):
                recovered_cards, recovered_total_count = self._probe_passive_cards_from_joblist(
                    page,
                    city_code=city_code,
                    keyword=keyword,
                )
                if recovered_cards:
                    break
                recovery_snapshot = extract_page_snapshot(page)
                recovery_url = str(recovery_snapshot.get("url") or "")
                if recovery_snapshot.get("page_state") in {"security_verify", "login_required", "blank_page"}:
                    break
                if not is_boss_results_page_url(recovery_url):
                    break
                if recovery_snapshot.get("page_state") != "loading":
                    break
                try:
                    page.wait_for_timeout(900 + recovery_round * 500)
                except Exception:
                    break
            if (
                not recovered_cards
                and ("鍚?URL 閲嶅瀵艰埅/鍒锋柊" in exc_text or "body_length=0" in exc_text)
            ):
                try:
                    page.wait_for_timeout(2600)
                except Exception:
                    pass
                for recovery_round in range(2):
                    recovered_cards, recovered_total_count = self._probe_passive_cards_from_joblist(
                        page,
                        city_code=city_code,
                        keyword=keyword,
                    )
                    if recovered_cards:
                        break
                    try:
                        page.wait_for_timeout(1200 + recovery_round * 700)
                    except Exception:
                        break
            if not recovered_cards:
                return self._probe_search_failure(
                    page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="joblist",
                    reason=str(exc),
                    snapshot=extract_page_snapshot(page),
                    query_label=query_label,
                )
            payload = {"jobs": [], "total_count": recovered_total_count}

        final_snapshot = extract_page_snapshot(page)
        final_city_code = extract_boss_city_code_from_url(final_snapshot.get("url", ""))
        if final_snapshot.get("page_state") == "security_verify":
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="joblist",
                reason="杩涘叆缁撴灉椤靛悗鍛戒腑浜嗗畨鍏ㄩ獙璇併€?",
                snapshot=final_snapshot,
                query_label=query_label,
            )
        if final_snapshot.get("page_state") == "login_required":
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="joblist",
                reason="杩涘叆缁撴灉椤靛悗鍙戠幇鐧诲綍鎬佸け鏁堛€?",
                snapshot=final_snapshot,
                query_label=query_label,
            )
        if recovered_cards is not None:
            cards = list(recovered_cards)[: self.max_cards_per_query]
        else:
            cards = [
                build_boss_search_probe_card(raw)
                for raw in payload.get("jobs", [])
                if isinstance(raw, dict)
            ]
            cards = [card for card in cards if card.get("job_url")][: self.max_cards_per_query]
        target_city_name = resolve_boss_city_name(city_code)
        payload_city_matches = bool(
            target_city_name
            and cards
            and any(target_city_name in str(card.get("city") or "") for card in cards)
        )
        if (not final_city_code or final_city_code != city_code) and final_snapshot.get("page_state") == "loading":
            for _ in range(2):
                try:
                    page.wait_for_timeout(900)
                except Exception:
                    pass
                follow_snapshot = extract_page_snapshot(page)
                follow_city_code = extract_boss_city_code_from_url(follow_snapshot.get("url", ""))
                if follow_snapshot.get("page_state") == "security_verify":
                    return self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="joblist",
                        reason="杩涘叆缁撴灉椤靛悗鍛戒腑浜嗗畨鍏ㄩ獙璇併€?",
                        snapshot=follow_snapshot,
                        query_label=query_label,
                    )
                if follow_snapshot.get("page_state") == "login_required":
                    return self._probe_search_failure(
                        page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="joblist",
                        reason="杩涘叆缁撴灉椤靛悗鍙戠幇鐧诲綍鎬佸け鏁堛€?",
                        snapshot=follow_snapshot,
                        query_label=query_label,
                    )
                final_snapshot = follow_snapshot
                final_city_code = follow_city_code
                if final_city_code == city_code:
                    break
        if not final_city_code or final_city_code != city_code:
            if payload_city_matches and cards:
                final_city_code = city_code
            else:
                reason = "缁撴灉椤靛煄甯傛病鏈夋纭垏鍒扮洰鏍囧煄甯傘€?"
                if looks_like_boss_blank_block(surface_snapshot, final_snapshot):
                    reason = "杩涘叆缁撴灉椤垫椂椤甸潰鍙樻垚浜嗙┖鐧介〉锛岀枒浼煎懡涓弽鎵掋€傚厛涓嶈缁х画鎶撱€?"
                return self._probe_search_failure(
                    page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="joblist",
                    reason=reason,
                    snapshot=final_snapshot,
                    query_label=query_label,
                )
        if not cards:
            return self._probe_search_failure(
                page,
                city=city,
                city_code=city_code,
                keyword=keyword,
                search_url=search_url,
                stage="cards",
                reason="宸茬粡杩涘叆缁撴灉椤碉紝浣嗙涓€椤垫病鏈夋嬁鍒板熀纭€鍗＄墖銆?",
                snapshot=final_snapshot,
                query_label=query_label,
            )

        return {
            "ok": True,
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "search_url": search_url,
            "start_url": start_snapshot.get("url", ""),
            "final_url": final_snapshot.get("url", ""),
            "page_state": final_snapshot.get("page_state", ""),
            "cards_count": len(cards),
                "total_count": int(payload.get("total_count", len(cards)) or len(cards)),
                "cards": cards,
            }

    def _probe_search_failure(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        search_url: str,
        stage: str,
        reason: str,
        snapshot: dict,
        query_label: str,
    ) -> dict:
        artifact = self._save_search_artifacts(
            page,
            query=query_label,
            note=f"probe_{stage}_failed",
            extra={
                "city": city,
                "city_code": city_code,
                "keyword": keyword,
                "search_url": search_url,
                "stage": stage,
                "reason": reason,
                "snapshot": snapshot,
            },
        )
        return {
            "ok": False,
            "city": city,
            "city_code": city_code,
            "keyword": keyword,
            "search_url": search_url,
            "final_url": snapshot.get("url", ""),
            "page_state": snapshot.get("page_state", ""),
            "cards_count": 0,
            "total_count": 0,
            "cards": [],
            "error": reason,
            "artifact_path": artifact["status_path"] if artifact else "",
        }

    def _maybe_fallback_to_worktab_queue(
        self,
        context,
        failure: dict | None,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        limit: int,
        rounds: int,
    ) -> dict:
        failure = dict(failure or {})
        reason = str(failure.get("error") or "").strip()
        if not reason:
            return failure
        if "重复刷新" not in reason and "来回跳" not in reason:
            return failure
        fallback = self._probe_worktab_job_queue_page(
            context,
            city=city,
            city_code=city_code,
            keyword=keyword,
            query_label=query_label,
            limit=limit,
            rounds=max(1, int(rounds or 0)),
        )
        fallback = dict(fallback or {})
        fallback["worktab_fallback"] = True
        fallback["fallback_reason"] = reason
        return fallback

    def _extract_joblist_payload_from_response(self, response) -> dict:
        if response.status != 200:
            raise RuntimeError(f"joblist 接口返回 HTTP {response.status}。")
        try:
            payload = response.json()
        except Exception as exc:
            try:
                raw_text = response.text()
            except Exception:
                raw_text = ""
            normalized_text = str(raw_text or "").lstrip("\ufeff").strip()
            if normalized_text.startswith(")]}',"):
                normalized_text = normalized_text[5:].lstrip()
            try:
                payload = json.loads(normalized_text)
            except Exception:
                content_type = ""
                try:
                    content_type = str(response.header_value("content-type") or "").strip()
                except Exception:
                    content_type = ""
                snippet = " ".join(normalized_text.split())[:180]
                body_length = len(normalized_text)
                if snippet:
                    raise RuntimeError(
                        f"joblist 响应不是可解析的 JSON。content-type={content_type or 'unknown'} body_length={body_length} snippet={snippet}"
                    ) from exc
                raise RuntimeError(
                    f"joblist 响应不是可解析的 JSON。content-type={content_type or 'unknown'} body_length={body_length}"
                ) from exc
        return extract_boss_joblist_payload(payload)

    def _wait_for_joblist_payload_after_action(self, page, *, action, timeout_ms: int) -> dict:
        trace, trace_handler = self._start_navigation_trace(page)
        try:
            with page.expect_response(
                lambda response: "/wapi/zpgeek/search/joblist.json" in response.url,
                timeout=timeout_ms,
            ) as response_info:
                action()
            response = response_info.value
        except Exception as exc:
            trace_text = self._summarize_navigation_trace(trace)
            if trace_text:
                raise RuntimeError(f"?? joblist ???????{trace_text}") from exc
            raise RuntimeError("?? joblist ???????") from exc
        finally:
            try:
                trace.append(self._shorten_live_url(page.url or ""))
            except Exception:
                pass
            self._stop_navigation_trace(page, trace_handler)

        if response.status != 200:
            trace_text = self._summarize_navigation_trace(trace)
            if trace_text:
                raise RuntimeError(f"joblist ???? HTTP {response.status}?{trace_text}")
            raise RuntimeError(f"joblist ???? HTTP {response.status}?")
        try:
            payload = response.json()
        except Exception as exc:
            try:
                raw_text = response.text()
            except Exception:
                raw_text = ""
            normalized_text = str(raw_text or "").lstrip("\ufeff").strip()
            if normalized_text.startswith(")]}',"):
                normalized_text = normalized_text[5:].lstrip()
            try:
                payload = json.loads(normalized_text)
            except Exception:
                content_type = ""
                try:
                    content_type = str(response.header_value("content-type") or "").strip()
                except Exception:
                    content_type = ""
                snippet = " ".join(normalized_text.split())[:180]
                trace_text = self._summarize_navigation_trace(trace)
                body_length = len(normalized_text)
                if snippet:
                    detail = f"joblist 鍝嶅簲涓嶆槸鍙В鏋愮殑 JSON銆俢ontent-type={content_type or 'unknown'} body_length={body_length} snippet={snippet}"
                    if trace_text:
                        detail = f"{detail} {trace_text}"
                    raise RuntimeError(detail) from exc
                detail = f"joblist 鍝嶅簲涓嶆槸鍙В鏋愮殑 JSON銆俢ontent-type={content_type or 'unknown'} body_length={body_length}"
                if trace_text:
                    detail = f"{detail} {trace_text}"
                raise RuntimeError(detail) from exc
        return extract_boss_joblist_payload(payload)

    def _wait_for_joblist_payload_after_action_v2(self, page, *, action, timeout_ms: int) -> dict:
        trace, trace_handler = self._start_navigation_trace(page)
        state: dict[str, object] = {"payload": None, "error": None}

        def on_response(response) -> None:
            try:
                response_url = str(response.url or "")
            except Exception:
                response_url = ""
            if "/wapi/zpgeek/search/joblist.json" not in response_url:
                return
            try:
                state["payload"] = self._extract_joblist_payload_from_response(response)
                state["error"] = None
            except Exception as exc:
                state["error"] = exc

        try:
            try:
                page.on("response", on_response)
            except Exception:
                pass
            action()
            deadline = monotonic() + max(int(timeout_ms or 0), 1000) / 1000.0
            while monotonic() < deadline:
                if state.get("payload") is not None:
                    return dict(state["payload"])
                try:
                    page.wait_for_timeout(120)
                except Exception:
                    pass
        finally:
            try:
                trace.append(
                    {
                        "event": "end",
                        "url": self._shorten_live_url(page.url or ""),
                        "dt_ms": 0,
                    }
                )
            except Exception:
                pass
            try:
                page.remove_listener("response", on_response)
            except Exception:
                try:
                    page.off("response", on_response)
                except Exception:
                    pass
            self._stop_navigation_trace(page, trace_handler)

        trace_text = self._summarize_navigation_trace(trace)
        last_error = state.get("error")
        if isinstance(last_error, Exception):
            detail = str(last_error).strip()
            if trace_text:
                detail = f"{detail} {trace_text}"
            raise RuntimeError(detail) from last_error
        if trace_text:
            raise RuntimeError(f"等待 joblist 接口响应时检测到异常导航。{trace_text}")
        raise RuntimeError("等待 joblist 接口响应超时。")

    def _probe_passive_cards_from_joblist(self, page, *, city_code: str, keyword: str) -> tuple[list[dict], int]:
        last_error: RuntimeError | None = None
        for attempt in range(3):
            try:
                joblist_url = self._extract_joblist_resource_url(page, city_code=city_code, keyword=keyword)
                if not joblist_url:
                    return [], 0
                payload = self._fetch_joblist_payload_from_url(page, joblist_url)
                cards = [
                    build_boss_search_probe_card(raw)
                    for raw in payload.get("jobs", [])
                    if isinstance(raw, dict)
                ]
                cards = [card for card in cards if card.get("job_url")][: self.max_cards_per_query]
                target_city_name = resolve_boss_city_name(city_code)
                if target_city_name and cards and not any(target_city_name in str(card.get("city") or "") for card in cards):
                    return [], 0
                return cards, int(payload.get("total_count", len(cards)) or len(cards))
            except RuntimeError as exc:
                if not self._is_navigation_context_error(exc) or attempt >= 2:
                    raise
                last_error = exc
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
                except Exception:
                    pass
                try:
                    page.wait_for_timeout(900 * (attempt + 1))
                except Exception:
                    pass
        if last_error is not None:
            raise last_error
        return [], 0

    def _recover_probe_cards_after_scroll_timeout(
        self,
        page,
        *,
        city_code: str,
        keyword: str,
        query_label: str = "",
    ) -> tuple[list[dict], int]:
        try:
            page.wait_for_timeout(1800)
        except Exception:
            pass
        cards, total_count = self._probe_passive_cards_from_joblist(page, city_code=city_code, keyword=keyword)
        cards = [card for card in list(cards or []) if isinstance(card, dict) and card.get("job_url")]
        if cards:
            return cards, int(total_count or len(cards))
        raw_dom_cards = self._extract_search_cards(page, query=query_label)
        if not raw_dom_cards:
            raw_dom_cards = self._extract_search_cards(page, query="")
        dom_cards = [build_boss_dom_probe_card(raw_card) for raw_card in raw_dom_cards[: self.max_cards_per_query]]
        dom_cards = [card for card in dom_cards if card.get("job_url")]
        target_city_name = resolve_boss_city_name(city_code)
        if target_city_name and dom_cards and not any(target_city_name in str(card.get("city") or "") for card in dom_cards):
            return [], 0
        return dom_cards, int(total_count or len(dom_cards))

    def _read_passive_probe_cards(
        self,
        page,
        *,
        city_code: str,
        keyword: str,
        query_label: str,
    ) -> tuple[dict, list[dict], int]:
        last_snapshot = extract_page_snapshot(page)
        for attempt in range(5):
            last_snapshot = extract_page_snapshot(page)
            if last_snapshot.get("page_state") in {"blank_page", "security_verify", "login_required"}:
                return last_snapshot, [], 0
            probe_cards, total_count = self._probe_passive_cards_from_joblist(
                page,
                city_code=city_code,
                keyword=keyword,
            )
            if probe_cards:
                return last_snapshot, probe_cards, total_count
            raw_dom_cards = self._extract_search_cards(page, query=query_label)
            if not raw_dom_cards:
                # The page URL has already been validated. Some live result pages expose
                # visible job cards whose text is incomplete while the hrefs are still
                # correct, so a second pass without query filtering is safer than
                # escalating to heavier DOM interactions here.
                raw_dom_cards = self._extract_search_cards(page, query="")
            if raw_dom_cards:
                dom_cards = [build_boss_dom_probe_card(raw_card) for raw_card in raw_dom_cards[: self.max_cards_per_query]]
                return last_snapshot, dom_cards, len(dom_cards)
            if not self._should_wait_for_passive_probe(page, last_snapshot):
                break
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 2500))
            except Exception:
                pass
            page.wait_for_timeout(900 + attempt * 500)
        return last_snapshot, [], 0

    def _should_wait_for_passive_probe(self, page, snapshot: dict) -> bool:
        if snapshot.get("page_state") == "loading":
            return True
        current_url = str(snapshot.get("url") or "")
        if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
            return False
        try:
            surface = page.evaluate(
                """
                () => ({
                  textLength: String(document.body?.innerText || document.body?.textContent || '').trim().length,
                  jobLinkCount: document.querySelectorAll('a[href*="/job_detail"]').length,
                  visibleCardCount: Array.from(
                    document.querySelectorAll(
                      'li.job-card-box, [class*="job-card-box"], [class*="job-card-wrap"], [class*="job-list-item"], [class*="search-job-result"]'
                    )
                  ).filter((node) => {
                    const style = window.getComputedStyle(node);
                    if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 80 && rect.height > 24;
                  }).length,
                  skeletonCount: document.querySelectorAll('[class*="skeleton"], [class*="loading"], [class*="placeholder"]').length,
                })
                """
            )
        except Exception:
            return True
        if not isinstance(surface, dict):
            return True
        job_link_count = int(surface.get("jobLinkCount") or 0)
        visible_card_count = int(surface.get("visibleCardCount") or 0)
        text_length = int(surface.get("textLength") or 0)
        skeleton_count = int(surface.get("skeletonCount") or 0)
        if job_link_count > 0:
            return False
        # Some live BOSS result pages render the global shell/footer text first,
        # so body text alone is not enough to declare the surface "ready".
        # If job links are still absent but we can see placeholder cards or
        # skeleton/loading nodes, keep waiting instead of failing fast.
        if skeleton_count > 0 or visible_card_count > 0:
            return True
        return text_length <= 0

    def _enrich_passive_dom_probe_cards(self, page, raw_cards: list[dict]) -> list[dict]:
        enriched_cards: list[dict] = []
        for raw_card in raw_cards[: self.max_cards_per_query]:
            if not isinstance(raw_card, dict):
                continue
            card = build_boss_dom_probe_card(raw_card)
            detail = self._read_passive_card_detail(page, raw_card)
            if detail:
                card = merge_boss_dom_probe_detail(card, detail)
            enriched_cards.append(card)
        return [card for card in enriched_cards if card.get("job_url")]

    def _read_passive_card_detail(self, page, raw_card: dict) -> dict:
        card_key = str(raw_card.get("card_key") or "").strip()
        expected_job_id = extract_boss_job_id_from_url(raw_card.get("url") or "")
        if not expected_job_id and not card_key:
            return {}
        try:
            if expected_job_id:
                anchor = page.locator(f'a[href*="/job_detail/{expected_job_id}.html"]').first
            else:
                anchor = page.locator(f'a[data-rb-job-card-key="{card_key}"]').first
            card_box = anchor.locator("xpath=ancestor::li[1]")
            if card_box.count() > 0 and card_box.is_visible():
                self._reading_pause(page, short=True)
                self._human_click_locator(page, card_box)
            else:
                self._reading_pause(page, short=True)
                self._human_click_locator(page, anchor)
        except Exception:
            return {}
        if expected_job_id:
            try:
                page.wait_for_function(
                    "(jobId) => !!window._jobInfo && window._jobInfo.encryptId === jobId",
                    expected_job_id,
                    timeout=min(self.timeout_ms, 4000),
                )
            except Exception:
                pass
        try:
            detail = page.evaluate(
                """
                () => {
                  const normalize = (value) => String(value || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const textOf = (node) => normalize(node ? (node.innerText || node.textContent || '') : '');
                  const pickFrom = (root, selectors) => {
                    if (!root) return '';
                    for (const selector of selectors) {
                      const node = root.querySelector(selector);
                      const text = textOf(node);
                      if (text) return text;
                    }
                    return '';
                  };
                  const value = window._jobInfo || null;
                  const targetTitle = normalize(value && typeof value === 'object' ? value.jobName : '');
                  const candidates = Array.from(
                    document.querySelectorAll(
                      '[class*="job-detail"], [class*="detail-box"], [class*="detail-content"], [class*="job-sec"], [class*="right"], [class*="side"], [class*="panel"]'
                    )
                  )
                    .map((node) => {
                      const text = textOf(node);
                      if (!text || text.length < 40) return null;
                      const rect = node.getBoundingClientRect();
                      let score = 0;
                      if (rect.left >= window.innerWidth * 0.4) score += 4;
                      if (text.length >= 180) score += 2;
                      if (targetTitle && text.includes(targetTitle)) score += 4;
                      if (/鑱屼綅鎻忚堪|宀椾綅鑱岃矗|浠昏亴瑕佹眰|宸ヤ綔鍐呭/.test(text)) score += 3;
                      return { node, text, score };
                    })
                    .filter(Boolean)
                    .sort((left, right) => right.score - left.score);
                  const panelNode = candidates.length ? candidates[0].node : null;
                  const panelText = candidates.length ? candidates[0].text.slice(0, 16000) : '';
                  if (!value || typeof value !== 'object') {
                    return {
                      postDescription: panelText,
                      panelText,
                    };
                  }
                  return {
                    encryptId: normalize(value.encryptId || ''),
                    securityId: normalize(value.securityId || ''),
                    jobName: normalize(value.jobName || '') || pickFrom(panelNode, ['h1', 'h2', '[class*="job-name"]', '[class*="title"]']),
                    brandName: normalize(value.brandName || value.brand || '') || pickFrom(panelNode, ['[class*="company-name"]', '[class*="brand-name"]', '[class*="boss-name"]']),
                    locationName: normalize(value.locationName || value.cityName || '') || pickFrom(panelNode, ['[class*="location"]', '[class*="area"]', '[class*="city"]']),
                    salaryDesc: normalize(value.salaryDesc || '') || pickFrom(panelNode, ['[class*="salary"]', '[class*="pay"]']),
                    degreeName: normalize(value.degreeName || ''),
                    experienceName: normalize(value.experienceName || ''),
                    postDescription: normalize(value.postDescription || value.postDescriptionHtml || '') || panelText,
                    panelText,
                    positionLabels: Array.isArray(value.positionLabels)
                      ? value.positionLabels.map((item) => normalize(item)).filter(Boolean)
                      : [],
                  };
                }
                """
            )
        except Exception:
            return {}
        return detail if isinstance(detail, dict) else {}

    def _extract_joblist_resource_url(self, page, *, city_code: str, keyword: str) -> str:
        try:
            resource_urls = page.evaluate(
                """
                () => performance
                  .getEntriesByType('resource')
                  .map((entry) => String(entry.name || ''))
                  .filter(Boolean)
                """
            )
        except Exception:
            resource_urls = []
        if not isinstance(resource_urls, list):
            return ""
        return find_boss_joblist_resource_url(resource_urls, city_code=city_code, keyword=keyword)

    def _fetch_joblist_payload_from_url(self, page, joblist_url: str) -> dict:
        try:
            response = page.evaluate(
                """
                async (url) => {
                  const result = await fetch(url, {
                    credentials: 'include',
                    headers: {
                      'Accept': 'application/json, text/plain, */*',
                      'Accept-Language': 'zh-CN,zh;q=0.9',
                      'Sec-Fetch-Dest': 'empty',
                      'Sec-Fetch-Mode': 'cors',
                      'Sec-Fetch-Site': 'same-origin',
                    },
                  });
                  return { status: result.status, body: await result.text() };
                }
                """,
                joblist_url,
            )
        except Exception as exc:
            detail = str(exc or "").strip()
            if detail:
                raise RuntimeError(f"???????? joblist ???????{detail}") from exc
            raise RuntimeError("???????? joblist ???????") from exc
        if not isinstance(response, dict):
            raise RuntimeError("褰撳墠缁撴灉椤靛叧鑱旂殑 joblist 鎺ュ彛杩斿洖寮傚父銆?")
        status = int(response.get("status") or 0)
        if status != 200:
            raise RuntimeError(f"褰撳墠缁撴灉椤靛叧鑱旂殑 joblist 鎺ュ彛杩斿洖 HTTP {status}銆?")
        raw_body = str(response.get("body") or "")
        try:
            payload = json.loads(raw_body)
        except Exception as exc:
            snippet = " ".join(raw_body.split())[:180]
            if snippet:
                raise RuntimeError(f"褰撳墠缁撴灉椤靛叧鑱旂殑 joblist 鎺ュ彛涓嶆槸鍚堟硶 JSON銆俿nippet={snippet}") from exc
            raise RuntimeError("???????? joblist ?????? JSON?") from exc
        return extract_boss_joblist_payload(payload)

    def _assert_passive_results_surface(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        search_url: str,
        query_label: str,
    ) -> dict:
        snapshot = extract_page_snapshot(page)
        if snapshot.get("is_blank"):
            raise SourceHaltError(
                "当前登录浏览器页签是空白页，先不要继续。",
                detail={"snapshot": build_boss_surface_observation(snapshot)},
            )
        if not snapshot.get("is_boss_domain"):
            raise RuntimeError("当前不是 BOSS 页面。先在登录浏览器里打开目标结果页。")
        if snapshot.get("page_state") == "security_verify":
            raise SourceHaltError(
                "当前页面命中安全验证，先不要继续。",
                detail={"snapshot": build_boss_surface_observation(snapshot)},
            )
        if snapshot.get("page_state") == "login_required":
            raise RuntimeError("当前页面登录态失效。先重新登录。")
        current_url = str(snapshot.get("url") or "")
        if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
            raise RuntimeError("当前还不是职位结果页。先手动打开目标结果页。")
        current_city_code = extract_boss_city_code_from_url(current_url)
        current_query = extract_boss_query_from_url(current_url)
        if current_city_code != city_code or keyword not in current_query:
            raise RuntimeError(f"当前结果页不是目标条件。先打开 {city} + {keyword} 结果页。")
        # A live BOSS results tab can briefly look correct and then drift back
        # to the city homepage without any explicit action from us. Require a
        # short stable window before treating the page as safe for queue reads.
        for _ in range(2):
            try:
                page.wait_for_timeout(900)
            except Exception:
                pass
            snapshot = extract_page_snapshot(page)
            current_url = str(snapshot.get("url") or "")
            if snapshot.get("is_blank"):
                raise SourceHaltError(
                    "当前结果页在静止等待时不稳定，已经变成空白页。先不要继续。",
                    detail={"snapshot": build_boss_surface_observation(snapshot)},
                )
            if snapshot.get("page_state") == "security_verify":
                raise SourceHaltError(
                    "当前结果页在静止等待时触发了安全验证，先不要继续。",
                    detail={"snapshot": build_boss_surface_observation(snapshot)},
                )
            if snapshot.get("page_state") == "login_required":
                raise RuntimeError("当前结果页在静止等待时登录失效了。先重新登录。")
            if not snapshot.get("is_boss_domain"):
                raise RuntimeError("当前结果页在静止等待时离开了 BOSS，先不要继续。")
            if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
                raise RuntimeError("当前结果页在静止等待时自己跳回了主页，先不要继续。")
            current_city_code = extract_boss_city_code_from_url(current_url)
            current_query = extract_boss_query_from_url(current_url)
            if current_city_code != city_code or keyword not in current_query:
                raise RuntimeError(f"当前结果页在静止等待时条件变了，先重新打开 {city} + {keyword} 结果页。")
        return snapshot

    def _assert_results_surface_quiet_window(
        self,
        page,
        *,
        city: str,
        city_code: str,
        keyword: str,
        search_url: str,
        query_label: str,
        window_ms: int = 2400,
        interval_ms: int = 400,
        max_refresh_retries: int = 1,
    ) -> dict:
        last_snapshot = extract_page_snapshot(page)
        total_window_ms = max(1200, int(window_ms or 1200))
        poll_interval_ms = max(300, int(interval_ms or 300))
        checks = max(2, total_window_ms // poll_interval_ms)
        total_attempts = max(1, int(max_refresh_retries or 0) + 1)
        for attempt in range(total_attempts):
            trace, trace_handler = self._start_navigation_trace(page)
            try:
                for _ in range(checks):
                    try:
                        page.wait_for_timeout(poll_interval_ms)
                    except Exception:
                        pass
                    snapshot = extract_page_snapshot(page)
                    last_snapshot = snapshot
                    current_url = str(snapshot.get("url") or "")
                    if snapshot.get("is_blank"):
                        raise SourceHaltError(
                            "当前结果页在滚动前的静稳观察里变成了空白页，先不要继续。",
                            detail={"snapshot": build_boss_surface_observation(snapshot)},
                        )
                    if not snapshot.get("is_boss_domain"):
                        raise RuntimeError("当前结果页在滚动前离开了 BOSS，先不要继续。")
                    if snapshot.get("page_state") == "security_verify":
                        raise SourceHaltError(
                            "当前结果页在滚动前触发了安全验证，先不要继续。",
                            detail={"snapshot": build_boss_surface_observation(snapshot)},
                        )
                    if snapshot.get("page_state") == "login_required":
                        raise RuntimeError("当前结果页在滚动前登录失效了，先重新登录。")
                    if not is_boss_results_page_url(current_url):
                        raise RuntimeError("当前结果页在滚动前跳回了主页或其他页面，先不要继续。")
                    current_city_code = extract_boss_city_code_from_url(current_url)
                    current_query = extract_boss_query_from_url(current_url)
                    if current_city_code != city_code or keyword not in current_query:
                        raise RuntimeError(f"当前结果页在滚动前条件变了，先重新打开 {city} + {keyword} 结果页。")
                trace_text = self._summarize_navigation_trace(trace)
                if "同 URL 重复导航/刷新" in trace_text or "主页/搜索页来回跳" in trace_text:
                    if attempt >= total_attempts - 1:
                        raise RuntimeError(f"当前结果页还在重复刷新，先不要滚动。{trace_text}")
                    try:
                        page.wait_for_timeout(2200 + attempt * 1200)
                    except Exception:
                        pass
                    last_snapshot = extract_page_snapshot(page)
                    continue
                return last_snapshot
            finally:
                self._stop_navigation_trace(page, trace_handler)
        return last_snapshot

    def _probe_worktab_job_queue_page(
        self,
        context,
        *,
        city: str,
        city_code: str,
        keyword: str,
        query_label: str,
        limit: int,
        rounds: int,
    ) -> dict:
        search_url = build_boss_search_url(self.base_url, keyword=keyword, city_code=city_code)
        # Keep the dedicated worktab inert at first so we own the first real
        # navigation into the search results surface, instead of racing against
        # a tab that might already be auto-navigating in the background.
        work_page = self._open_dedicated_worktab_page(context, "about:blank")
        work_page.set_default_timeout(self.timeout_ms)
        start_snapshot = extract_page_snapshot(work_page)
        try:
            warmup_snapshot = extract_page_snapshot(work_page)
            warmup_url = str(warmup_snapshot.get("url") or "")
            warmup_city_code = extract_boss_city_code_from_url(warmup_url)
            warmup_query = extract_boss_query_from_url(warmup_url)
            initial_probe_cards: list[dict] = []
            initial_total_count = 0
            warmup_is_target_results = (
                warmup_snapshot.get("is_boss_domain")
                and not warmup_snapshot.get("is_blank")
                and is_boss_results_page_url(warmup_url)
                and warmup_city_code == city_code
                and keyword in warmup_query
            )
            if warmup_is_target_results:
                try:
                    initial_probe_cards, initial_total_count = self._probe_passive_cards_from_joblist(
                        work_page,
                        city_code=city_code,
                        keyword=keyword,
                    )
                except RuntimeError:
                    initial_probe_cards, initial_total_count = [], 0
            payload = None
            if not initial_probe_cards:
                joblist_error: RuntimeError | None = None
                joblist_actions = [lambda: work_page.wait_for_timeout(2400)]
                if not warmup_is_target_results:
                    joblist_actions.append(
                        lambda: self._goto_with_retry(work_page, search_url, wait_until="domcontentloaded")
                    )
                for joblist_action in joblist_actions:
                    try:
                        payload = self._wait_for_joblist_payload_after_action_v2(
                            work_page,
                            action=joblist_action,
                            timeout_ms=max(self.timeout_ms, 15000),
                        )
                        joblist_error = None
                        break
                    except RuntimeError as exc:
                        joblist_error = exc
                        if joblist_action is joblist_actions[-1]:
                            return self._probe_search_failure(
                                work_page,
                                city=city,
                                city_code=city_code,
                                keyword=keyword,
                                search_url=search_url,
                                stage="worktab_queue_joblist",
                                reason=str(exc),
                                snapshot=extract_page_snapshot(work_page),
                                query_label=query_label,
                            )
                if payload is None and joblist_error is not None:
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_joblist",
                        reason=str(joblist_error),
                        snapshot=extract_page_snapshot(work_page),
                        query_label=query_label,
                    )

            final_snapshot = extract_page_snapshot(work_page)
            current_url = str(final_snapshot.get("url") or "")
            if final_snapshot.get("is_blank") or not final_snapshot.get("is_boss_domain"):
                reason = "涓撶敤宸ヤ綔椤电娌℃湁钀藉湪鍙敤鐨?BOSS 椤甸潰銆?"
                if looks_like_boss_blank_block(start_snapshot, final_snapshot):
                    reason = "涓撶敤宸ヤ綔椤电璺虫垚浜嗙┖鐧介〉锛岀枒浼煎懡涓弽鎵掋€傚厛涓嶈缁х画銆?"
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason=reason,
                    snapshot=final_snapshot,
                    query_label=query_label,
                )
            if final_snapshot.get("page_state") == "security_verify":
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason="涓撶敤宸ヤ綔椤电鍛戒腑浜嗗畨鍏ㄩ獙璇併€?",
                    snapshot=final_snapshot,
                    query_label=query_label,
                )
            if final_snapshot.get("page_state") == "login_required":
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason="涓撶敤宸ヤ綔椤电鍙戠幇鐧诲綍鎬佸け鏁堛€?",
                    snapshot=final_snapshot,
                    query_label=query_label,
                )
            if not is_boss_results_page_url(current_url):
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason="涓撶敤宸ヤ綔椤电娌℃湁绋冲畾钀藉埌鑱屼綅缁撴灉椤点€?",
                    snapshot=final_snapshot,
                    query_label=query_label,
                )
            final_city_code = extract_boss_city_code_from_url(current_url)
            final_query = extract_boss_query_from_url(current_url)
            if final_city_code != city_code or keyword not in final_query:
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason="涓撶敤宸ヤ綔椤电缁撴灉鏉′欢鍜岀洰鏍囦笉涓€鑷淬€?",
                    snapshot=final_snapshot,
                    query_label=query_label,
                )

            if initial_probe_cards:
                probe_cards = [
                    dict(card)
                    for card in list(initial_probe_cards or [])
                    if isinstance(card, dict) and card.get("job_url")
                ][: max(1, min(int(limit or 1), 20))]
                total_count = int(initial_total_count or len(probe_cards))
            else:
                probe_cards = [
                    build_boss_search_probe_card(raw)
                    for raw in payload.get("jobs", [])
                    if isinstance(raw, dict)
                ]
                probe_cards = [card for card in probe_cards if card.get("job_url")][: max(1, min(int(limit or 1), 20))]
                total_count = int(payload.get("total_count", len(probe_cards)) or len(probe_cards))
            if not probe_cards:
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_cards",
                    reason="涓撶敤宸ヤ綔椤电宸茬粡鎷垮埌 joblist锛屼絾娌¤鍒板彲鍏ラ槦宀椾綅銆?",
                    snapshot=final_snapshot,
                    query_label=query_label,
                )

            try:
                final_snapshot = self._assert_results_surface_quiet_window(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    query_label=query_label,
                    window_ms=1200,
                    interval_ms=400,
                    max_refresh_retries=1,
                )
                current_url = str(final_snapshot.get("url") or current_url)
            except RuntimeError as exc:
                return self._probe_search_failure(
                    work_page,
                    city=city,
                    city_code=city_code,
                    keyword=keyword,
                    search_url=search_url,
                    stage="worktab_queue_surface",
                    reason=str(exc),
                    snapshot=extract_page_snapshot(work_page),
                    query_label=query_label,
                )

            scroll_reports: list[dict] = []
            consecutive_no_new = 0
            planned_rounds = max(0, int(rounds or 0))
            for round_index in range(planned_rounds):
                if len(probe_cards) >= limit:
                    break
                try:
                    work_page.wait_for_timeout((600 if round_index == 0 else 1800) + round_index * 400)
                except Exception:
                    pass
                before_scroll = extract_page_snapshot(work_page)
                before_url = str(before_scroll.get("url") or "")
                before_city_code = extract_boss_city_code_from_url(before_url)
                before_query = extract_boss_query_from_url(before_url)
                if before_scroll.get("page_state") == "security_verify":
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍓嶅懡涓簡瀹夊叏楠岃瘉銆?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                if before_scroll.get("page_state") == "login_required":
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍓嶅彂鐜扮櫥褰曟€佸け鏁堛€?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                if not is_boss_results_page_url(before_url) or before_city_code != city_code or keyword not in before_query:
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍓嶅凡缁忓亸绂荤洰鏍囩粨鏋滈〉锛屽凡绔嬪嵆鍋滄銆?",
                        snapshot=before_scroll,
                        query_label=query_label,
                    )
                try:
                    before_scroll = self._assert_results_surface_quiet_window(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        query_label=query_label,
                        window_ms=2400,
                        interval_ms=400,
                        max_refresh_retries=2,
                    )
                except (RuntimeError, SourceHaltError) as exc:
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason=str(exc),
                        snapshot=extract_page_snapshot(work_page),
                        query_label=query_label,
                    )
                try:
                    next_payload = self._wait_for_joblist_payload_after_action_v2(
                        work_page,
                        action=lambda: self._inject_worktab_joblist_scroll(work_page, settle_ms=3200),
                        timeout_ms=max(self.timeout_ms, 15000),
                    )
                except RuntimeError as exc:
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason=str(exc),
                        snapshot=extract_page_snapshot(work_page),
                        query_label=query_label,
                    )
                after_scroll = extract_page_snapshot(work_page)
                after_url = str(after_scroll.get("url") or "")
                after_city_code = extract_boss_city_code_from_url(after_url)
                after_query = extract_boss_query_from_url(after_url)
                if after_scroll.get("is_blank") or not after_scroll.get("is_boss_domain"):
                    reason = "涓撶敤宸ヤ綔椤电婊氬姩鍚庣寮€浜嗗彲鐢ㄧ殑 BOSS 椤甸潰銆?"
                    if looks_like_boss_blank_block(before_scroll, after_scroll):
                        reason = "涓撶敤宸ヤ綔椤电婊氬姩鍚庤烦鎴愪簡绌虹櫧椤碉紝鐤戜技鍛戒腑鍙嶆墥銆傚厛涓嶈缁х画銆?"
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason=reason,
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                if after_scroll.get("page_state") == "security_verify":
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍚庡懡涓簡瀹夊叏楠岃瘉銆?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                if after_scroll.get("page_state") == "login_required":
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍚庡彂鐜扮櫥褰曟€佸け鏁堛€?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                if not is_boss_results_page_url(after_url) or after_city_code != city_code or keyword not in after_query:
                    return self._probe_search_failure(
                        work_page,
                        city=city,
                        city_code=city_code,
                        keyword=keyword,
                        search_url=search_url,
                        stage="worktab_queue_scroll",
                        reason="涓撶敤宸ヤ綔椤电婊氬姩鍚庡亸绂讳簡鐩爣缁撴灉椤碉紝宸茬珛鍗冲仠姝€?",
                        snapshot=after_scroll,
                        query_label=query_label,
                    )
                final_snapshot = after_scroll
                current_url = after_url

                if recovered_next_cards:
                    next_cards = list(recovered_next_cards)
                    total_count = int(recovered_total_count or total_count)
                else:
                    next_cards = [
                        build_boss_search_probe_card(raw)
                        for raw in next_payload.get("jobs", [])
                        if isinstance(raw, dict)
                    ]
                    next_cards = [card for card in next_cards if card.get("job_url")]
                    total_count = int(next_payload.get("total_count", total_count) or total_count)
                probe_cards, added = self._merge_probe_cards(probe_cards, next_cards, limit=limit)
                scroll_reports.append(
                    {
                        "round": round_index + 1,
                        "added": int(added),
                        "cards_count": len(probe_cards),
                        "page_state": str(after_scroll.get("page_state") or ""),
                        "final_url": after_url,
                        "recovered_from_resource": bool(recovered_next_cards),
                    }
                )
                if added <= 0:
                    consecutive_no_new += 1
                else:
                    consecutive_no_new = 0
                if consecutive_no_new >= 2:
                    break

            queue_bundle = self._build_queue_jobs_from_probe_cards(
                probe_cards,
                query_label=query_label,
                page_url=current_url,
                limit=limit,
            )
            return {
                "ok": bool(queue_bundle["queue_jobs"]),
                "mode": "queue_worktab",
                "city": city,
                "city_code": city_code,
                "keyword": keyword,
                "search_url": search_url,
                "start_url": str(start_snapshot.get("url") or ""),
                "warmup_url": str(warmup_snapshot.get("url") or ""),
                "final_url": current_url,
                "page_state": str(final_snapshot.get("page_state") or ""),
                "cards_count": len(probe_cards),
                "queue_count": len(queue_bundle["queue_jobs"]),
                "total_count": int(total_count or len(probe_cards)),
                "scroll_rounds_planned": planned_rounds,
                "scroll_rounds_completed": len(scroll_reports),
                "scroll_reports": scroll_reports,
                "queue_jobs": queue_bundle["queue_jobs"],
            }
        finally:
            try:
                work_page.close()
            except Exception:
                pass

    def _probe_one_detail_page(self, context, *, card: dict, query: str) -> dict:
        detail_page = context.new_page()
        detail_page.set_default_timeout(self.timeout_ms)
        job_url = str(card.get("job_url") or "").strip()
        if not job_url:
            return {
                "ok": False,
                "job_url": "",
                "title": str(card.get("title") or "").strip(),
                "company_name": str(card.get("company_name") or "").strip(),
                "error": "宀椾綅缂哄皯璇︽儏椤甸摼鎺ャ€?",
            }
        try:
            if bring_to_front:
                try:
                    detail_page.bring_to_front()
                except Exception:
                    pass
            self._navigate_detail_worktab(detail_page, job_url)
            self._inject_detail_page_activity(detail_page)
            self._wait_for_detail_page(detail_page)
            snapshot = extract_page_snapshot(detail_page)
            self._raise_if_detail_page_surface_invalid(
                detail_page,
                snapshot,
                query=query,
                card=card,
                note="detail_probe_surface_invalid",
            )
            if not self._detail_matches_target(detail_page, {"url": job_url, **dict(card or {})}):
                self._raise_if_detail_page_surface_invalid(
                    detail_page,
                    snapshot,
                    query=query,
                    card=card,
                    note="detail_probe_target_mismatch",
                    reason="璇︽儏椤垫病鏈夌ǔ瀹氳惤鍒扮洰鏍囧矖浣嶏紝宸茬珛鍒诲仠姝€?",
                )
            page_detail = self._extract_detail_page_probe_fields(detail_page)
            detail_url = build_boss_detail_url({**dict(card or {}), **page_detail})
            detail_api: dict = {}
            fetch_error = ""
            if detail_url:
                try:
                    detail_api = extract_boss_detail_api_payload(self._fetch_detail_payload_from_url(detail_page, detail_url))
                except Exception as exc:
                    fetch_error = str(exc)
            else:
                fetch_error = "鏃犳硶鎷煎嚭 detail 鎺ュ彛鍦板潃銆?"
            payload = build_boss_detail_page_payload(
                card,
                page_detail,
                detail_api,
                query=query,
                page_url=str(snapshot.get("url") or job_url),
            )
            job = normalize_job_fields(payload, source=self.name)
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
            }
            if fetch_error:
                result["error"] = fetch_error
            if not result["ok"]:
                artifact = self._save_detail_artifacts(
                    detail_page,
                    query=query,
                    note="detail_probe_quality_failed",
                    extra={
                        "card": dict(card or {}),
                        "page_detail": page_detail,
                        "detail_api": detail_api,
                        "result": result,
                    },
                )
                if artifact:
                    result["artifact_path"] = artifact["status_path"]
            return result
        finally:
            try:
                detail_page.close()
            except Exception:
                pass

    def _supplement_one_detail_job(
        self,
        detail_page,
        *,
        card: dict,
        job_payload: dict,
        query: str,
        navigate_to_job: bool = False,
        use_cdp_primary: bool = False,
        bring_to_front: bool = True,
    ) -> dict:
        job_url = str(card.get("job_url") or "").strip()
        if not job_url:
            return {
                "ok": False,
                "job_url": "",
                "title": str(card.get("title") or "").strip(),
                "company_name": str(card.get("company_name") or "").strip(),
                "error": "宀椾綅缂哄皯璇︽儏椤甸摼鎺ャ€?",
            }
        try:
            if bring_to_front:
                try:
                    detail_page.bring_to_front()
                except Exception:
                    pass
            if navigate_to_job:
                page_detail: dict = {}
                detail_url = build_boss_detail_url(dict(card or {}))
                detail_api: dict = {}
                fetch_error = ""
                self._navigate_detail_worktab(detail_page, job_url)
                self._inject_detail_page_activity(detail_page)
                self._wait_for_detail_page(detail_page)
                snapshot = extract_page_snapshot(detail_page)
                self._raise_if_detail_page_surface_invalid(
                    detail_page,
                    snapshot,
                    query=query,
                    card=card,
                    note="detail_supplement_surface_invalid",
                )
                if not self._detail_matches_target(detail_page, {"url": job_url, **card}):
                    self._raise_if_detail_page_surface_invalid(
                        detail_page,
                        snapshot,
                        query=query,
                        card=card,
                        note="detail_supplement_target_mismatch",
                        reason="详情工作页没有稳定落到目标岗位，已立即停止。",
                    )
                page_detail = self._extract_detail_page_probe_fields(detail_page)
                detail_url = build_boss_detail_url({**dict(card or {}), **page_detail}) or detail_url
                if detail_url:
                    if use_cdp_primary:
                        try:
                            detail_api = extract_boss_detail_api_payload(
                                self._fetch_detail_payload_via_cdp(detail_page, detail_url)
                            )
                        except Exception as exc:
                            primary_error = str(exc)
                            try:
                                detail_api = extract_boss_detail_api_payload(
                                    self._fetch_detail_payload_from_url(detail_page, detail_url)
                                )
                                fetch_error = f"cdp_fetch_failed:{primary_error}"
                            except Exception as page_exc:
                                fetch_error = f"cdp_fetch_failed:{primary_error}; page_fetch_failed:{page_exc}"
                    else:
                        try:
                            detail_api = extract_boss_detail_api_payload(
                                self._fetch_detail_payload_from_url(detail_page, detail_url)
                            )
                        except Exception as exc:
                            primary_error = str(exc)
                            try:
                                detail_api = extract_boss_detail_api_payload(
                                    self._fetch_detail_payload_via_cdp(detail_page, detail_url)
                                )
                                fetch_error = f"page_fetch_failed:{primary_error}"
                            except Exception as cdp_exc:
                                fetch_error = f"page_fetch_failed:{primary_error}; cdp_fetch_failed:{cdp_exc}"
                else:
                    fetch_error = "閺冪姵纭堕幏鐓庡毉 detail 閹恒儱褰涢崷鏉挎絻閵?"
                payload = build_boss_detail_page_payload(
                    card,
                    page_detail,
                    detail_api,
                    query=query,
                    page_url=str(snapshot.get("url") or job_url),
                )
                base_payload = dict(job_payload or {})
                merged_payload = {
                    **base_payload,
                    **payload,
                    "source": str(base_payload.get("source") or "boss_browser").strip() or "boss_browser",
                    "fetch_session_id": str(base_payload.get("fetch_session_id") or payload.get("fetch_session_id") or "").strip(),
                    "job_type": str(base_payload.get("job_type") or payload.get("job_type") or "").strip(),
                    "employment_mode": str(
                        base_payload.get("employment_mode") or payload.get("employment_mode") or ""
                    ).strip(),
                    "application_status": str(
                        base_payload.get("application_status") or payload.get("application_status") or ""
                    ).strip()
                    or "unknown",
                    "raw_payload": {
                        **dict(base_payload.get("raw_payload") or {}),
                        **dict(payload.get("raw_payload") or {}),
                        "detail_supplemented": True,
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
                if not result["ok"]:
                    artifact = self._save_detail_artifacts(
                        detail_page,
                        query=query,
                        note="detail_supplement_quality_failed",
                        extra={
                            "card": dict(card or {}),
                            "job_payload": dict(job_payload or {}),
                            "page_detail": page_detail,
                            "detail_api": detail_api,
                            "result": {key: value for key, value in result.items() if key != "job"},
                        },
                    )
                    if artifact:
                        result["artifact_path"] = artifact["status_path"]
                return result
            snapshot = extract_page_snapshot(detail_page)
            if snapshot.get("is_blank"):
                self._raise_if_detail_page_surface_invalid(
                    detail_page,
                    snapshot,
                    query=query,
                    card=card,
                    note="detail_supplement_surface_blank",
                    reason="褰撳墠闄勭潃鐨?BOSS 椤靛凡缁忓彉鎴愮┖鐧介〉锛屽凡绔嬪埢鍋滄銆?",
                )
            if not snapshot.get("is_boss_domain"):
                self._raise_if_detail_page_surface_invalid(
                    detail_page,
                    snapshot,
                    query=query,
                    card=card,
                    note="detail_supplement_surface_non_boss",
                    reason="褰撳墠闄勭潃鐨勯〉宸茬粡涓嶆槸 BOSS锛屽凡绔嬪埢鍋滄銆?",
                )
            page_detail: dict = {}
            detail_url = build_boss_detail_url(dict(card or {}))
            detail_api: dict = {}
            fetch_error = ""
            if detail_url:
                try:
                    detail_api = extract_boss_detail_api_payload(
                        self._fetch_detail_payload_via_browser_session(
                            detail_page,
                            detail_url,
                            referer_url=job_url,
                        )
                    )
                except Exception as exc:
                    session_error = str(exc)
                    try:
                        detail_api = extract_boss_detail_api_payload(
                            self._fetch_detail_payload_from_url(detail_page, detail_url)
                        )
                        fetch_error = f"browser_session_fetch_failed:{session_error}"
                    except Exception as page_exc:
                        primary_error = str(page_exc)
                        try:
                            detail_api = extract_boss_detail_api_payload(
                                self._fetch_detail_payload_via_cdp(detail_page, detail_url)
                            )
                            fetch_error = (
                                f"browser_session_fetch_failed:{session_error}; "
                                f"page_fetch_failed:{primary_error}"
                            )
                        except Exception as cdp_exc:
                            fetch_error = (
                                f"browser_session_fetch_failed:{session_error}; "
                                f"page_fetch_failed:{primary_error}; "
                                f"cdp_fetch_failed:{cdp_exc}"
                            )
            else:
                fetch_error = "鏃犳硶鎷煎嚭 detail 鎺ュ彛鍦板潃銆?"
            payload = build_boss_detail_page_payload(
                card,
                page_detail,
                detail_api,
                query=query,
                page_url=str(snapshot.get("url") or job_url),
            )
            base_payload = dict(job_payload or {})
            merged_payload = {
                **base_payload,
                **payload,
                "source": str(base_payload.get("source") or "boss_browser").strip() or "boss_browser",
                "fetch_session_id": str(base_payload.get("fetch_session_id") or payload.get("fetch_session_id") or "").strip(),
                "job_type": str(base_payload.get("job_type") or payload.get("job_type") or "").strip(),
                "employment_mode": str(
                    base_payload.get("employment_mode") or payload.get("employment_mode") or ""
                ).strip(),
                "application_status": str(
                    base_payload.get("application_status") or payload.get("application_status") or ""
                ).strip()
                or "unknown",
                "raw_payload": {
                    **dict(base_payload.get("raw_payload") or {}),
                    **dict(payload.get("raw_payload") or {}),
                    "detail_supplemented": True,
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
            if not result["ok"]:
                artifact = self._save_detail_artifacts(
                    detail_page,
                    query=query,
                    note="detail_supplement_quality_failed",
                    extra={
                        "card": dict(card or {}),
                        "job_payload": dict(job_payload or {}),
                        "page_detail": page_detail,
                        "detail_api": detail_api,
                        "result": {key: value for key, value in result.items() if key != "job"},
                    },
                )
                if artifact:
                    result["artifact_path"] = artifact["status_path"]
            return result
        finally:
            try:
                detail_page.wait_for_timeout(600)
            except Exception:
                pass

    def _navigate_detail_worktab(self, page, job_url: str) -> None:
        current_url = ""
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        cdp_session = None
        try:
            cdp_session = page.context.new_cdp_session(page)
        except Exception:
            cdp_session = None
        if cdp_session is None:
            self._goto_with_retry(page, job_url, wait_until="domcontentloaded")
            return
        try:
            self._record_action_audit("cdp_detail_navigate", from_url=current_url, to_url=job_url)
            try:
                cdp_session.send("Page.enable")
            except Exception:
                pass
            cdp_session.send("Page.navigate", {"url": job_url})
            page.wait_for_timeout(random.randint(2200, 3200))
            return
        except Exception:
            self._goto_with_retry(page, job_url, wait_until="domcontentloaded")

    def _inject_detail_page_activity(self, page) -> None:
        try:
            page.evaluate(
                """
                () => {
                  const root = document.scrollingElement || document.documentElement;
                  const maxScroll = Math.max((root?.scrollHeight || 0) - (window.innerHeight || 800), 0);
                  window.focus();
                  document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
                  document.body?.dispatchEvent(new MouseEvent('mousemove', { clientX: 220, clientY: 260, bubbles: true }));
                  document.body?.dispatchEvent(new MouseEvent('mouseover', { clientX: 260, clientY: 320, bubbles: true }));
                  if (maxScroll > 0) {
                    window.scrollTo({ top: Math.min(maxScroll, Math.floor(maxScroll * 0.16)), behavior: 'auto' });
                    document.dispatchEvent(new Event('scroll', { bubbles: true }));
                  }
                  return true;
                }
                """
            )
        except Exception:
            return
        try:
            page.wait_for_timeout(random.randint(900, 1500))
        except Exception:
            pass

    def _extract_detail_page_probe_fields(self, page) -> dict:
        page_detail = self._extract_detail_payload(page)
        try:
            identity = page.evaluate(
                """
                () => {
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
                }
                """
            )
        except Exception:
            identity = {}
        merged = dict(page_detail or {})
        if isinstance(identity, dict):
            for key, value in identity.items():
                if value:
                    merged[key] = value
        return merged

    def _fetch_detail_payload_from_url(self, page, detail_url: str) -> dict:
        try:
            response = page.evaluate(
                """
                async (url) => {
                  const result = await fetch(url, {
                    credentials: 'include',
                    headers: {
                      'Accept': 'application/json, text/plain, */*',
                      'Accept-Language': 'zh-CN,zh;q=0.9',
                      'Sec-Fetch-Dest': 'empty',
                      'Sec-Fetch-Mode': 'cors',
                      'Sec-Fetch-Site': 'same-origin',
                    },
                  });
                  return { status: result.status, body: await result.text() };
                }
                """,
                detail_url,
            )
        except Exception as exc:
            raise RuntimeError("?????????") from exc
        if not isinstance(response, dict):
            raise RuntimeError("璇︽儏鎺ュ彛杩斿洖寮傚父銆?")
        status = int(response.get("status") or 0)
        if status != 200:
            raise RuntimeError(f"璇︽儏鎺ュ彛杩斿洖 HTTP {status}銆?")
        try:
            return json.loads(str(response.get("body") or ""))
        except Exception as exc:
            raise RuntimeError("???????? JSON?") from exc

    def _fetch_detail_payload_via_storage_state(self, detail_url: str, *, referer_url: str = "") -> dict:
        cookies = self._load_storage_state_cookies()
        cookie_header = self._build_storage_state_cookie_header(cookies, detail_url)
        if not cookie_header:
            raise RuntimeError("落盘登录态 Cookie 不可用。")
        return self._fetch_detail_payload_with_cookie_header(
            detail_url,
            cookie_header,
            referer_url=referer_url,
            error_prefix="落盘登录态详情接口",
        )

    def _fetch_detail_payload_via_browser_context(self, context, detail_url: str, *, referer_url: str = "") -> dict:
        if context is None:
            raise RuntimeError("无法读取浏览器上下文。")
        cookie_scope = str(referer_url or "https://www.zhipin.com/").strip()
        try:
            cookies = context.cookies([cookie_scope] if cookie_scope else None)
        except TypeError:
            cookies = context.cookies()
        except Exception as exc:
            raise RuntimeError("读取浏览器登录态失败。") from exc
        cookie_header = self._format_cookie_header(cookies)
        if not cookie_header:
            raise RuntimeError("浏览器登录 Cookie 不可用。")
        return self._fetch_detail_payload_with_cookie_header(
            detail_url,
            cookie_header,
            referer_url=referer_url,
            error_prefix="浏览器会话详情接口",
        )

    def _load_storage_state_cookies(self) -> list[dict]:
        if not self.storage_state_path.exists():
            raise RuntimeError("落盘登录态不存在。")
        try:
            payload = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("读取落盘登录态失败。") from exc
        cookies = payload.get("cookies")
        if not isinstance(cookies, list):
            raise RuntimeError("落盘登录态缺少 Cookie。")
        return [item for item in cookies if isinstance(item, dict)]

    def _persist_storage_state_snapshot(self, context, *, stage: str = "") -> bool:
        if context is None:
            return False
        try:
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.storage_state_path))
        except Exception as exc:
            self._record_action_audit(
                "persist_storage_state_failed",
                stage=stage,
                path=str(self.storage_state_path),
                error=str(exc),
            )
            return False
        self._record_action_audit(
            "persist_storage_state",
            stage=stage,
            path=str(self.storage_state_path),
        )
        return True

    def _build_storage_state_cookie_header(self, cookies: list[dict], target_url: str) -> str:
        matched = [
            item
            for item in list(cookies or [])
            if self._cookie_matches_request_url(item, target_url)
        ]
        return self._format_cookie_header(matched)

    def _format_cookie_header(self, cookies: list[dict] | tuple[dict, ...] | None) -> str:
        pairs: list[str] = []
        seen_names: set[str] = set()
        for item in list(cookies or []):
            name = str(item.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            pairs.append(f"{name}={str(item.get('value') or '').strip()}")
            seen_names.add(name)
        return "; ".join(pairs).strip()

    def _cookie_matches_request_url(self, cookie: dict, target_url: str) -> bool:
        parsed = urlparse(str(target_url or "").strip())
        host = str(parsed.hostname or "").strip().lower()
        path = str(parsed.path or "/").strip() or "/"
        scheme = str(parsed.scheme or "").strip().lower()
        if not host:
            return False
        domain = str(cookie.get("domain") or "").strip().lower()
        normalized_domain = domain.lstrip(".")
        if normalized_domain and host != normalized_domain and not host.endswith("." + normalized_domain):
            return False
        cookie_path = str(cookie.get("path") or "/").strip() or "/"
        if not path.startswith(cookie_path):
            return False
        if bool(cookie.get("secure")) and scheme != "https":
            return False
        try:
            expires = float(cookie.get("expires"))
        except (TypeError, ValueError):
            expires = 0.0
        if expires > 0 and expires < datetime.now().timestamp():
            return False
        return True

    def _fetch_detail_payload_with_cookie_header(
        self,
        detail_url: str,
        cookie_header: str,
        *,
        referer_url: str = "",
        error_prefix: str,
    ) -> dict:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Cookie": cookie_header,
            "Origin": "https://www.zhipin.com",
            "Pragma": "no-cache",
            "Referer": str(referer_url or "https://www.zhipin.com/").strip(),
            "User-Agent": str(build_boss_context_kwargs().get("user_agent") or "").strip(),
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            request = Request(detail_url, headers=headers, method="GET")
            with urlopen(request, timeout=max(5.0, min(self.timeout_ms / 1000.0, 15.0))) as response:
                status = int(getattr(response, "status", 0) or 0)
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"{error_prefix}返回 HTTP {exc.code}。") from exc
        except URLError as exc:
            raise RuntimeError(f"{error_prefix}请求失败。") from exc
        except Exception as exc:
            raise RuntimeError(f"{error_prefix}读取失败。") from exc
        if status != 200:
            raise RuntimeError(f"{error_prefix}返回 HTTP {status}。")
        try:
            return json.loads(body)
        except Exception as exc:
            raise RuntimeError(f"{error_prefix}返回的不是合法 JSON。") from exc

    def _fetch_detail_payload_via_browser_session(self, page, detail_url: str, *, referer_url: str = "") -> dict:
        context = getattr(page, "context", None)
        if context is None:
            raise RuntimeError("无法读取浏览器上下文。")
        return self._fetch_detail_payload_via_browser_context(
            context,
            detail_url,
            referer_url=str(referer_url or getattr(page, "url", "") or "").strip(),
        )

    def _fetch_detail_payload_via_cdp(self, page, detail_url: str) -> dict:
        try:
            cdp_session = page.context.new_cdp_session(page)
        except Exception as exc:
            raise RuntimeError("???? CDP ?????") from exc
        try:
            response = cdp_session.send(
                "Runtime.evaluate",
                {
                    "expression": f"""
                      (async () => {{
                        const r = await fetch({json.dumps(detail_url)}, {{
                          credentials: 'include',
                          headers: {{
                            'Accept': 'application/json, text/plain, */*',
                            'Accept-Language': 'zh-CN,zh;q=0.9',
                            'Sec-Fetch-Dest': 'empty',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Site': 'same-origin',
                          }}
                        }});
                        return JSON.stringify({{ status: r.status, body: await r.text() }});
                      }})()
                    """,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
        except Exception as exc:
            raise RuntimeError("???? CDP ?????") from exc
        if isinstance(response, dict) and response.get("exceptionDetails"):
            detail = str((response.get("exceptionDetails") or {}).get("text") or "").strip()
            if detail:
                raise RuntimeError(f"???? CDP ?????{detail}")
            raise RuntimeError("???? CDP ?????")
        try:
            raw_value = str(((response or {}).get("result") or {}).get("value") or "")
            wrapped = json.loads(raw_value)
        except Exception as exc:
            raise RuntimeError("???? CDP ?????") from exc
        status = int(wrapped.get("status") or 0)
        if status != 200:
            raise RuntimeError(f"璇︽儏鎺ュ彛杩斿洖 HTTP {status}銆?")
        try:
            return json.loads(str(wrapped.get("body") or ""))
        except Exception as exc:
            raise RuntimeError("???????? JSON?") from exc

    def _raise_if_detail_page_surface_invalid(
        self,
        page,
        snapshot: dict,
        *,
        query: str,
        card: dict,
        note: str,
        reason: str = "",
    ) -> None:
        snapshot = snapshot or {}
        current_url = str(snapshot.get("url") or "").strip()
        error = reason
        if not error:
            if snapshot.get("is_blank"):
                error = "璇︽儏椤佃烦鎴愪簡绌虹櫧椤碉紝宸茬珛鍒诲仠姝€?"
            elif snapshot.get("page_state") == "security_verify":
                error = "璇︽儏椤佃Е鍙戜簡瀹夊叏楠岃瘉锛屽凡绔嬪埢鍋滄銆?"
            elif snapshot.get("page_state") == "login_required":
                if not ("/job_detail" in current_url and self._page_looks_like_detail(page)):
                    error = "璇︽儏椤电櫥褰曞け鏁堬紝宸茬珛鍒诲仠姝€?"
            elif not snapshot.get("is_boss_domain"):
                error = "璇︽儏椤电寮€浜?BOSS锛屽凡绔嬪埢鍋滄銆?"
            elif self._page_looks_like_recommendation_surface(snapshot):
                error = "璇︽儏椤佃烦鍥炰簡涓婚〉锛屽凡绔嬪埢鍋滄銆?"
            elif "/job_detail" not in current_url and not self._page_looks_like_detail(page):
                error = "璇︽儏椤垫病鏈夋垚鍔熸墦寮€锛屽凡绔嬪埢鍋滄銆?"
        if not error:
            return
        artifact = self._save_detail_artifacts(
            page,
            query=query,
            note=note,
            extra={
                "card": {
                    "job_url": str(card.get("job_url") or "").strip(),
                    "title": str(card.get("title") or "").strip(),
                    "company_name": str(card.get("company_name") or "").strip(),
                },
                "snapshot": snapshot,
            },
        )
        raise SourceHaltError(
            error,
            detail={
                "artifact_path": str((artifact or {}).get("status_path") or ""),
                "snapshot": build_boss_surface_observation(snapshot),
                "card": {
                    "job_url": str(card.get("job_url") or "").strip(),
                    "title": str(card.get("title") or "").strip(),
                    "company_name": str(card.get("company_name") or "").strip(),
                },
            },
        )

    def _ensure_search_surface(self, page, *, base_url: str, query: str, allow_manual_resume: bool = True):
        page = self._resolve_live_page(page.context, preferred_page=page, allow_general_fallback=True)
        snapshot = extract_page_snapshot(page)
        if snapshot["is_security_verify"] and allow_manual_resume and self._wait_for_manual_resume(page, query=query):
            snapshot = extract_page_snapshot(page)
        self._apply_query_city_context(page, query)
        needs_navigation = snapshot["is_blank"] or not snapshot["is_boss_domain"]
        if not needs_navigation:
            current_url = snapshot.get("url", "")
            if "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url:
                if self._page_looks_like_recommendation_surface(snapshot):
                    if self._open_jobs_page_like_user(page):
                        self._reading_pause(page)
                        page = self._resolve_live_page(page.context, preferred_page=page, allow_general_fallback=False)
                        snapshot = extract_page_snapshot(page)
                        current_url = snapshot.get("url", "")
                needs_navigation = "/web/geek/jobs" not in current_url and "/web/geek/job" not in current_url
        if needs_navigation:
            page = self._resolve_live_page(page.context, preferred_page=page, allow_general_fallback=True)
            self._goto_with_retry(page, base_url, wait_until="domcontentloaded")
            self._reading_pause(page)
            page = self._resolve_live_page(page.context, preferred_page=page, allow_general_fallback=False)
            snapshot = extract_page_snapshot(page)
        if snapshot["is_security_verify"] and allow_manual_resume and self._wait_for_manual_resume(page, query=query):
            snapshot = extract_page_snapshot(page)
        return page, snapshot

    def _apply_query_city_context(self, page, query: str) -> None:
        target_city_code = self._query_city_code(query)
        if not target_city_code:
            return
        self._record_action_audit("set_last_city_cookie", city_code=target_city_code, query=query)
        try:
            page.context.add_cookies(
                [
                    {
                        "name": "lastCity",
                        "value": target_city_code,
                        "url": "https://www.zhipin.com",
                    }
                ]
            )
        except Exception:
            return

    def _open_jobs_page_like_user(self, page) -> bool:
        jobs_nav = self._find_jobs_nav(page)
        if jobs_nav is None:
            return False
        try:
            self._record_action_audit("open_jobs_nav_click")
            self._reading_pause(page, short=True)
            self._human_click_locator(page, jobs_nav)
            return True
        except Exception:
            return False

    def _submit_query_like_user(self, page, query: str) -> bool:
        search_input = self._find_search_input(page)
        if search_input is None:
            return False
        self._reading_pause(page, short=True)
        self._human_click_locator(page, search_input)
        try:
            self._human_pause(page, 260, 520)
            page.keyboard.press("Control+A")
            self._human_pause(page, 220, 420)
            page.keyboard.press("Backspace")
            self._human_pause(page, 260, 520)
            page.keyboard.type(query, delay=random.randint(140, 260))
            self._human_pause(page, 700, 1400)
        except Exception:
            return False
        search_button = self._find_search_button(page)
        if search_button is not None:
            self._reading_pause(page, short=True)
            self._human_click_locator(page, search_button)
        else:
            try:
                self._human_pause(page, 320, 760)
                page.keyboard.press("Enter")
            except Exception:
                return False
            self._human_pause(page, 420, 860)
        return True

    def _wait_for_search_cards(self, page, *, query: str, previous_signature: str, search_url: str) -> list[dict]:
        best_cards: list[dict] = []
        for step in range(14):
            self._human_pause(page, 820, 1650)
            snapshot = extract_page_snapshot(page)
            if snapshot["is_security_verify"]:
                if best_cards:
                    return best_cards
                if not self._wait_for_manual_resume(page, query=query):
                    return best_cards
                snapshot = extract_page_snapshot(page)
            if snapshot.get("page_state") == "loading":
                self._human_pause(page, 1800, 3200)
                continue
            if snapshot["is_blank"]:
                return best_cards
            if snapshot.get("page_state") == "login_required":
                return best_cards
            recommendation_surface = self._page_looks_like_recommendation_surface(snapshot)
            cards = self._extract_search_cards(page, query=query)
            current_signature = self._cards_signature(cards)
            query_ready = self._page_matches_query(page, query)
            clean_results_surface = self._page_is_search_results(page) and not recommendation_surface
            if cards and clean_results_surface:
                best_cards = cards
            if cards and clean_results_surface and query_ready and (current_signature != previous_signature or step >= 1):
                return cards
            if best_cards and clean_results_surface and (query_ready or step >= 2):
                return best_cards
            if step in {0, 2, 5} and (recommendation_surface or (not query_ready and not best_cards)):
                if self._page_is_search_results(page) and not recommendation_surface:
                    try:
                        self._human_pause(page, 420, 900)
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                    self._human_pause(page, 1200, 2000)
                else:
                    self._reading_pause(page, short=True)
                    self._goto_with_retry(page, search_url, wait_until="domcontentloaded")
                    self._reading_pause(page)
            if step in {1, 3, 6} and clean_results_surface:
                self._small_human_scroll(page)
        if best_cards:
            return best_cards
        if self._page_matches_query(page, query):
            return self._extract_search_cards(page, query=query)
        return []

    def _wait_for_manual_resume(self, page, *, query: str) -> bool:
        deadline = monotonic() + (self.manual_verify_timeout_ms / 1000)
        while monotonic() < deadline:
            try:
                page.bring_to_front()
            except Exception:
                pass
            self._human_pause(page, 1800, 3200)
            snapshot = extract_page_snapshot(page)
            if snapshot["is_security_verify"]:
                continue
            if snapshot["is_blank"]:
                continue
            if looks_like_login_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
                return False
            if snapshot["is_boss_domain"]:
                self._reading_pause(page)
                return True
        return False

    def _find_search_input(self, page):
        selectors = [
            'input[placeholder*="鎼滅储"]',
            'input[placeholder*="鑱屼綅"]',
            'input[placeholder*="宀椾綅"]',
            '[class*="search"] input',
            'input[type="search"]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 8)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    box = candidate.bounding_box()
                except Exception:
                    continue
                if not box:
                    continue
                if box["width"] < 140 or box["height"] < 24:
                    continue
                if box["y"] > 320:
                    continue
                return candidate
        return None

    def _find_search_button(self, page):
        selectors = [
            'button:has-text("鎼滅储")',
            '[class*="search-btn"]',
            '[class*="btn-search"]',
            'button[type="submit"]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 6)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    box = candidate.bounding_box()
                except Exception:
                    continue
                if not box:
                    continue
                if box["width"] < 48 or box["height"] < 24:
                    continue
                if box["y"] > 340:
                    continue
                return candidate
        return None

    def _find_jobs_nav(self, page):
        selectors = [
            'a:has-text("鑱屼綅")',
            'button:has-text("鑱屼綅")',
            '[class*="nav"] a:has-text("鑱屼綅")',
            '[class*="menu"] a:has-text("鑱屼綅")',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 6)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    text = (candidate.inner_text(timeout=1200) or "").strip()
                    box = candidate.bounding_box()
                except Exception:
                    continue
                if text != "鑱屼綅" or not box:
                    continue
                if box["y"] > 220 or box["width"] < 24 or box["height"] < 18:
                    continue
                return candidate
        return None

    def _human_click_locator(self, page, locator) -> None:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        self._record_action_audit("locator_click", page_url=current_url)
        try:
            locator.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            box = locator.bounding_box()
        except Exception:
            box = None
        if box:
            target_x = box["x"] + min(max(box["width"] * 0.38, 8), max(box["width"] - 8, 8))
            target_y = box["y"] + min(max(box["height"] * 0.55, 6), max(box["height"] - 6, 6))
            try:
                page.mouse.move(target_x - 24, target_y - 10, steps=random.randint(10, 18))
                page.wait_for_timeout(random.randint(140, 320))
                page.mouse.move(target_x, target_y, steps=random.randint(6, 12))
                page.wait_for_timeout(random.randint(180, 420))
                page.mouse.down()
                page.wait_for_timeout(random.randint(90, 220))
                page.mouse.up()
                page.wait_for_timeout(random.randint(320, 760))
                return
            except Exception:
                pass
        locator.click(delay=random.randint(70, 180), timeout=min(self.timeout_ms, 5000))
        page.wait_for_timeout(random.randint(320, 760))

    def _human_pause(self, page, min_ms: int, max_ms: int) -> None:
        page.wait_for_timeout(random.randint(min_ms, max_ms))

    def _reading_pause(self, page, *, short: bool = False) -> None:
        if short:
            self._human_pause(page, 900, 1800)
            return
        self._human_pause(page, 1600, 3200)

    def _small_human_scroll(self, page) -> None:
        for _ in range(random.randint(1, 2)):
            try:
                page.mouse.wheel(0, random.randint(180, 420))
            except Exception:
                return
            self._human_pause(page, 900, 1800)

    def _cards_signature(self, cards: list[dict]) -> str:
        return "|".join(card.get("url", "") for card in cards[:4])

    def _current_search_signature(self, page, *, query: str = "") -> str:
        return self._cards_signature(self._extract_search_cards(page, limit=4, query=query))

    def _build_search_url(self, page, *, base_url: str, query: str) -> str:
        params: dict[str, str] = {"query": query}
        city_value = self._query_city_code(query)
        if not city_value:
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            parsed = urlparse(current_url)
            current_params = parse_qs(parsed.query)
            city_value = str((current_params.get("city") or [""])[0] or "").strip()
        if city_value:
            params["city"] = city_value
        query_pairs = [f"{key}={quote(value)}" for key, value in params.items() if value]
        if not query_pairs:
            return base_url
        return f"{base_url}?{'&'.join(query_pairs)}"

    def _page_matches_query(self, page, query: str) -> bool:
        normalized_query = " ".join(query.split()).strip().lower()
        if not self._page_is_search_results(page):
            return False
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        parsed = urlparse(current_url)
        query_params = parse_qs(parsed.query)
        query_value = " ".join((query_params.get("query") or [""])[0].split()).strip().lower()
        if query_value and normalized_query and query_value == normalized_query:
            return True
        search_input = self._find_search_input(page)
        if search_input is None:
            return False
        try:
            input_value = " ".join((search_input.input_value() or "").split()).strip().lower()
        except Exception:
            input_value = ""
        return bool(input_value and normalized_query and input_value == normalized_query)

    def _page_is_search_results(self, page) -> bool:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        lowered = current_url.lower()
        if any(marker in lowered for marker in RECOMMENDATION_LINK_MARKERS):
            return False
        return "/web/geek/jobs" in lowered or "/web/geek/job" in lowered

    def _page_city_matches_query(self, page, query: str) -> bool:
        target_city_code = self._query_city_code(query)
        if not target_city_code:
            return True
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        parsed = urlparse(current_url)
        current_params = parse_qs(parsed.query)
        current_city_code = str((current_params.get("city") or [""])[0] or "").strip()
        return bool(current_city_code and current_city_code == target_city_code)

    def _page_looks_like_recommendation_surface(self, snapshot: dict) -> bool:
        url = (snapshot.get("url") or "").lower()
        body_excerpt = snapshot.get("body_excerpt") or ""
        title = snapshot.get("title") or ""
        if any(marker in url for marker in RECOMMENDATION_LINK_MARKERS):
            return True
        marker_hits = sum(1 for marker in RECOMMENDATION_PAGE_MARKERS if marker in body_excerpt)
        if marker_hits >= 2:
            return True
        return marker_hits >= 1 and "boss" in title.lower()

    def _cards_match_query(self, cards: list[dict], query: str) -> bool:
        if not cards:
            return False
        return any(self._card_matches_query(card, query) for card in cards[: min(len(cards), 4)])

    def _card_matches_query(self, card: dict, query: str) -> bool:
        text = "\n".join(
            part for part in [card.get("title", ""), card.get("company_name", ""), card.get("city", ""), card.get("text", "")]
            if part
        ).lower()
        role_token = self._query_role_token(query).lower()
        city_tokens = [city.lower() for city in self._query_city_tokens(query)]
        if role_token and role_token not in text:
            return False
        if city_tokens and not any(city in text for city in city_tokens):
            return False
        campus_markers = ["??", "????", "??", "???", "??", "2026?", "2027?", "2028?", "2029?"]
        if any(token in query for token in ["??", "????", "??"]) and not any(marker.lower() in text for marker in campus_markers):
            return False
        if "??" in query and "??" not in text:
            return False
        return True

    def _query_city_tokens(self, query: str) -> list[str]:
        return [city for city in CITIES if city in query]

    def _query_city_code(self, query: str) -> str:
        city_tokens = self._query_city_tokens(query)
        if not city_tokens:
            return ""
        return BOSS_CITY_CODES.get(city_tokens[0], "")

    def _query_role_token(self, query: str) -> str:
        tokens = [token.strip() for token in query.split() if token.strip()]
        role_candidates: list[str] = []
        for token in tokens:
            if token in QUERY_INTENT_TOKENS:
                continue
            if token in CITIES:
                continue
            if re.fullmatch(r"(?:20)?2[6-9]?", token):
                continue
            role_candidates.append(token)
        return role_candidates[-1] if role_candidates else (tokens[-1] if tokens else "")

    def _fetch_detail(self, context, url: str, query: str) -> JobPosting | None:
        page = context.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            page.goto(url, wait_until="domcontentloaded")
            last_error: Exception | None = None
            last_snapshot: dict | None = None
            for attempt in range(3):
                self._wait_for_detail_page(page)
                snapshot = extract_page_snapshot(page)
                last_snapshot = snapshot
                if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
                    return None
                if is_security_verify_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
                    return None
                if looks_like_login_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
                    return None
                try:
                    raw = self._extract_detail_payload(page)
                except Exception as exc:
                    last_error = exc
                    if not self._is_navigation_context_error(exc) or attempt >= 2:
                        break
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 5000))
                    except Exception:
                        pass
                    page.wait_for_timeout(900 * (attempt + 1))
                    continue
                raw.update(
                    {
                        "detail_fetched": True,
                        "apply_url": page.url,
                        "query": query,
                    }
                )
                if raw.get("salary_text") and raw.get("salary_text") not in raw.get("description", ""):
                    raw["description"] = f"{raw['salary_text']}\n{raw['description']}"
                job = normalize_job_fields(raw, source=self.name)
                if not is_job_quality_acceptable(job):
                    return None
                return job
            if last_error is not None:
                snapshot = last_snapshot or extract_page_snapshot(page)
                detail = (
                    f"url={snapshot.get('url', '')} "
                    f"state={snapshot.get('page_state', '')} "
                    f"title={(snapshot.get('title', '') or '')[:40]}"
                ).strip()
                raise RuntimeError(f"BOSS ????????{last_error}?{detail}") from last_error
            return None
        finally:
            page.close()

    def _fetch_detail_from_search_page(self, page, card: dict, query: str, *, results_url: str) -> JobPosting | None:
        if not results_url:
            try:
                results_url = page.url
            except Exception:
                results_url = self.base_url
        last_error: Exception | None = None
        last_snapshot: dict | None = None
        try:
            for attempt in range(2):
                if attempt > 0:
                    self._return_to_results_surface(page, results_url, query=query)
                try:
                    clicked = self._click_search_card(page, card)
                except Exception as exc:
                    last_error = exc
                    if self._is_navigation_context_error(exc):
                        self._human_pause(page, 900, 1400)
                        continue
                    raise
                if not clicked:
                    return None
                snapshot = self._wait_for_detail_surface(page, expected_url=card.get("url", ""), query=query)
                last_snapshot = snapshot
                if snapshot["is_blank"] or not snapshot["is_boss_domain"]:
                    continue
                if snapshot["is_security_verify"]:
                    if not self._wait_for_manual_resume(page, query=query):
                        break
                    snapshot = self._wait_for_detail_surface(page, expected_url=card.get("url", ""), query=query)
                    last_snapshot = snapshot
                if looks_like_login_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
                    return None
                if not self._detail_matches_target(page, card):
                    self._save_detail_artifacts(
                        page,
                        note="detail_target_mismatch",
                        query=query,
                        extra={
                            "card": card,
                            "snapshot": snapshot,
                        },
                    )
                    return None
                try:
                    raw = self._extract_detail_payload(page)
                except Exception as exc:
                    last_error = exc
                    if self._is_navigation_context_error(exc):
                        self._human_pause(page, 900, 1400)
                        continue
                    raise
                raw.update(
                    {
                        "detail_fetched": True,
                        "apply_url": page.url,
                        "query": query,
                        "url": card.get("url") or page.url,
                    }
                )
                if raw.get("salary_text") and raw.get("salary_text") not in raw.get("description", ""):
                    raw["description"] = f"{raw['salary_text']}\n{raw['description']}"
                if self._looks_like_listing_content(raw):
                    self._save_detail_artifacts(
                        page,
                        note="detail_listing_like",
                        query=query,
                        extra={
                            "card": card,
                            "raw": {
                                "url": raw.get("url", ""),
                                "title": raw.get("title", ""),
                                "company_name": raw.get("company_name", ""),
                                "salary_text": raw.get("salary_text", ""),
                                "description_preview": (raw.get("description", "") or "")[:2000],
                            },
                        },
                    )
                    return None
                job = normalize_job_fields(raw, source=self.name)
                if not is_job_quality_acceptable(job):
                    self._save_detail_artifacts(
                        page,
                        note="detail_quality_rejected",
                        query=query,
                        extra={
                            "card": card,
                            "job": job.to_dict(),
                        },
                    )
                    return None
                return job
            if last_error is not None:
                snapshot = last_snapshot or extract_page_snapshot(page)
                detail = (
                    f"url={snapshot.get('url', '')} "
                    f"state={snapshot.get('page_state', '')} "
                    f"title={(snapshot.get('title', '') or '')[:40]}"
                ).strip()
                raise RuntimeError(f"BOSS ????????{last_error}?{detail}") from last_error
            return None
        finally:
            self._return_to_results_surface(page, results_url, query=query)

    def _click_search_card(self, page, card: dict) -> bool:
        card_key = (card.get("card_key") or "").strip()
        if card_key:
            candidate = page.locator(f'[data-rb-job-card-key="{card_key}"]').first
            try:
                if candidate.count() and candidate.is_visible():
                    self._reading_pause(page, short=True)
                    self._human_click_locator(page, candidate)
                    return True
            except Exception as exc:
                if self._is_navigation_context_error(exc):
                    raise
        for refreshed_card in self._extract_search_cards(page, limit=self.max_cards_per_query * 2):
            if refreshed_card.get("url") != card.get("url"):
                continue
            refreshed_key = (refreshed_card.get("card_key") or "").strip()
            if not refreshed_key:
                continue
            candidate = page.locator(f'[data-rb-job-card-key="{refreshed_key}"]').first
            try:
                if candidate.count() and candidate.is_visible():
                    self._reading_pause(page, short=True)
                    self._human_click_locator(page, candidate)
                    return True
            except Exception as exc:
                if self._is_navigation_context_error(exc):
                    raise
        return False

    def _wait_for_detail_surface(self, page, *, expected_url: str, query: str) -> dict:
        last_snapshot = extract_page_snapshot(page)
        for _ in range(8):
            self._human_pause(page, 450, 900)
            snapshot = extract_page_snapshot(page)
            last_snapshot = snapshot
            if snapshot["is_security_verify"]:
                return snapshot
            if snapshot["is_blank"]:
                continue
            if self._page_looks_like_recommendation_surface(snapshot):
                return snapshot
            if expected_url and expected_url in snapshot.get("url", ""):
                return snapshot
            if self._page_looks_like_detail(page):
                return snapshot
            if looks_like_login_page(snapshot["url"], snapshot["title"], snapshot["body_excerpt"]):
                return snapshot
            if "/web/geek/jobs" not in snapshot.get("url", "") and "/job_detail" in snapshot.get("url", ""):
                return snapshot
        if last_snapshot.get("is_security_verify"):
            self._wait_for_manual_resume(page, query=query)
            return extract_page_snapshot(page)
        return last_snapshot

    def _page_looks_like_detail(self, page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const selectors = [
                        '[class*="job-detail"]',
                        '[class*="detail-content"]',
                        '[class*="job-sec"]',
                        '[class*="detail-box"]',
                        '[class*="job-info-main"]',
                        '[class*="job-banner"]',
                        '[class*="job-primary"]'
                      ];
                      return selectors.some((selector) => document.querySelector(selector));
                    }
                    """
                )
            )
        except Exception:
            return False

    def _return_to_results_surface(self, page, results_url: str, *, query: str) -> None:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        if current_url == results_url and "/web/geek/jobs" in current_url:
            return
        try:
            if "/job_detail" in current_url:
                self._record_action_audit("go_back_to_results", page_url=current_url)
                page.go_back(wait_until="domcontentloaded", timeout=min(self.timeout_ms, 10000))
            else:
                self._record_action_audit("goto_results_url", page_url=current_url, results_url=results_url)
                self._goto_with_retry(page, results_url, wait_until="domcontentloaded")
        except Exception:
            try:
                self._record_action_audit("goto_results_url", page_url=current_url, results_url=results_url)
                self._goto_with_retry(page, results_url, wait_until="domcontentloaded")
            except Exception:
                return
        self._reading_pause(page)
        snapshot = extract_page_snapshot(page)
        if snapshot["is_security_verify"]:
            self._wait_for_manual_resume(page, query=query)

    def _detail_matches_target(self, page, card: dict) -> bool:
        target_url = (card.get("url") or "").strip()
        target_job_id = self._extract_job_id(target_url)
        snapshot = extract_page_snapshot(page)
        if self._page_looks_like_recommendation_surface(snapshot):
            return False
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        current_job_id = self._extract_job_id(current_url)
        if target_job_id and current_job_id and target_job_id == current_job_id:
            return True
        if current_url and target_url and current_url.startswith(target_url.split("?", 1)[0]):
            return True
        detail_text = "\n".join(
            part
            for part in [
                snapshot.get("title", ""),
                snapshot.get("body_excerpt", ""),
            ]
            if part
        )
        target_title = (card.get("title") or "").strip()
        target_company = (card.get("company_name") or "").strip()
        if target_title and target_title in detail_text:
            if not target_company or target_company in detail_text:
                return True
        return False

    def _extract_job_id(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        if not path:
            return ""
        if "/" in path:
            path = path.rsplit("/", 1)[-1]
        return unquote(path)

    def _looks_like_listing_content(self, raw: dict) -> bool:
        description = (raw.get("description") or "")[:2000]
        company_name = (raw.get("company_name") or "").strip()
        title = (raw.get("title") or "").strip()
        markers = [
            "鐑棬鑱屼綅:",
            "鍘籄PP锛屼笌BOSS闅忔椂娌熼€?",
            "鑱屼綅绫诲瀷",
            "鍦板浘\n鎼滅储",
            "棣栭〉\n \n鑱屼綅\n \n鍏徃",
            "娴峰綊涓撳睘锛岄珮钖亴浣?",
            "鐑棬鍩庡競锛?",
        ]
        if any(marker in description for marker in markers):
            return True
        if company_name in {"鍏徃", "鍏徃鍚嶇О"}:
            return True
        if title in {"鑱屼綅", "鍏徃"}:
            return True
        return False

    def _wait_for_detail_page(self, page) -> None:
        stable_hits = 0
        last_signature = ""
        for _ in range(5):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
            except Exception:
                pass
            snapshot = extract_page_snapshot(page)
            signature = "|".join(
                [
                    snapshot.get("url", ""),
                    snapshot.get("title", ""),
                    snapshot.get("page_state", ""),
                ]
            )
            if signature and signature == last_signature:
                stable_hits += 1
            else:
                last_signature = signature
                stable_hits = 0
            if snapshot.get("page_state") in {"ready", "security_verify", "login_required"} and stable_hits >= 1:
                return
            page.wait_for_timeout(500)

    def _extract_detail_payload(self, page) -> dict:
        return page.evaluate(
            """
            () => {
              const pick = (selectors) => {
                for (const selector of selectors) {
                  const node = document.querySelector(selector);
                  if (!node) continue;
                  const text = (node.innerText || node.textContent || '').trim();
                  if (text) return text;
                }
                return '';
              };
              return {
                url: window.location.href,
                title: pick(['h1', '[class*=job-name]', '[class*=jobTitle]', '[class*=name]']) || document.title || '',
                company_name: pick(['[class*=company-name]', '[class*=companyName]', 'a[href*="/gongsi/"]']),
                city: pick(['[class*=job-location]', '[class*=jobLocation]', '[class*=job-area]', '[class*=location]', '[class*=city]']),
                salary_text: pick(['[class*=salary]', '[class*=pay]']),
                description: pick([
                  '[class*=job-detail]',
                  '[class*=detail-content]',
                  '[class*=job-sec-text]',
                  '[class*=job-detail-box]',
                  '[class*=detail-box]'
                ]) || (document.body && document.body.innerText ? document.body.innerText : '').slice(0, 15000),
              };
            }
            """
        )

    def _is_navigation_context_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return "execution context was destroyed" in message or "cannot find context with specified id" in message

    def _is_interrupted_navigation_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return "interrupted by another navigation" in message or "net::err_aborted" in message

    def _open_browser_context(self, playwright, *, use_profile: bool, launch_headless: bool):
        if self.prefer_cdp_attach:
            resolved_cdp_ws_url = resolve_cdp_websocket_url(self.cdp_port, self.cdp_url, timeout_seconds=1.5)
            if resolved_cdp_ws_url:
                browser = playwright.chromium.connect_over_cdp(resolved_cdp_ws_url)
                context = self._pick_attached_context(browser)
                return browser, context, True, False
        launch_kwargs = build_boss_launch_kwargs(headless=launch_headless)
        context_kwargs = build_boss_context_kwargs()
        if use_profile:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                **launch_kwargs,
                **context_kwargs,
            )
            return context.browser, context, False, True
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            storage_state=str(self.storage_state_path),
            **context_kwargs,
        )
        return browser, context, False, True

    def _pick_attached_context(self, browser):
        contexts = list(getattr(browser, "contexts", []) or [])
        if not contexts:
            return browser.new_context()
        fallback_context = None
        best_context = None
        best_score = -1
        for context in reversed(contexts):
            pages = list(getattr(context, "pages", []) or [])
            if fallback_context is None:
                fallback_context = context
            context_score = 0
            for page in reversed(pages):
                if self._page_is_closed(page):
                    continue
                snapshot = extract_page_snapshot(page)
                lowered_url = (snapshot.get("url", "") or "").lower()
                if lowered_url.startswith("devtools://") or lowered_url.startswith("edge://") or lowered_url.startswith("chrome://"):
                    continue
                if "/web/geek/jobs" in lowered_url or "/web/geek/job" in lowered_url:
                    context_score = max(context_score, 3)
                    continue
                if snapshot.get("is_security_verify"):
                    context_score = max(context_score, 2)
                    continue
                if snapshot.get("is_boss_domain"):
                    context_score = max(context_score, 1)
            if context_score > best_score:
                best_score = context_score
                best_context = context
            if fallback_context is None:
                fallback_context = context
        return best_context or fallback_context or contexts[0]

    def _acquire_search_page(self, context, *, attached_over_cdp: bool):
        if attached_over_cdp:
            existing_page = self._find_existing_boss_page(context, allow_general_fallback=True)
            if existing_page is not None:
                return existing_page, False
        return context.new_page(), True

    def _open_cdp_target_in_browser(self, url: str) -> dict:
        cdp_endpoint = resolve_cdp_endpoint(self.cdp_port, self.cdp_url, timeout_seconds=1.5)
        if not cdp_endpoint:
            raise RuntimeError("CDP endpoint unavailable for dedicated worktab.")
        endpoint = cdp_endpoint.rstrip("/") + "/json/new?" + quote(str(url or "").strip(), safe="")
        try:
            request = Request(endpoint, method="PUT")
            with urlopen(request, timeout=5.0) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {404, 405, 501}:
                raise
            with urlopen(endpoint, timeout=5.0) as response:
                return json.load(response)
        except URLError:
            raise

    def _open_dedicated_worktab_page(self, context, search_url: str, *, bring_to_front: bool = True):
        existing_page_ids = {id(page) for page in list(getattr(context, "pages", []) or [])}
        try:
            self._open_cdp_target_in_browser(search_url)
        except Exception:
            return context.new_page()

        deadline = monotonic() + 6.0
        fallback_new_page = None
        target_prefix = str(search_url or "").split("?", 1)[0]
        while monotonic() < deadline:
            for page in list(getattr(context, "pages", []) or []):
                if self._page_is_closed(page):
                    continue
                is_new_page = id(page) not in existing_page_ids
                page_url = ""
                try:
                    page_url = str(page.url or "")
                except Exception:
                    page_url = ""
                lowered = page_url.lower()
                if is_new_page and page_url.startswith(search_url):
                    if bring_to_front:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                    return page
                if is_new_page and target_prefix and page_url.startswith(target_prefix):
                    if bring_to_front:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                    return page
                if is_new_page and "zhipin.com" in lowered:
                    fallback_new_page = page
                if is_new_page and not page_url:
                    fallback_new_page = fallback_new_page or page
            sleep(0.25)
        if fallback_new_page is not None:
            if bring_to_front:
                try:
                    fallback_new_page.bring_to_front()
                except Exception:
                    pass
            return fallback_new_page
        return context.new_page()

    def _page_is_closed(self, page) -> bool:
        try:
            return bool(page.is_closed())
        except Exception:
            return False

    def _snapshot_context_surfaces(self, context) -> list[dict]:
        surfaces: list[dict] = []
        for index, page in enumerate(list(getattr(context, "pages", []) or [])):
            closed = self._page_is_closed(page)
            snapshot = {}
            if not closed:
                try:
                    snapshot = extract_page_snapshot(page)
                except Exception:
                    snapshot = {}
            url = str(snapshot.get("url") or getattr(page, "url", "") or "").strip()
            title = str(snapshot.get("title") or "").strip()
            if not title and not closed:
                try:
                    title = str(page.title() or "").strip()
                except Exception:
                    title = ""
            surfaces.append(
                {
                    "index": index,
                    "page_id": id(page),
                    "closed": closed,
                    "url": url,
                    "page_state": str(snapshot.get("page_state") or "").strip(),
                    "is_blank": bool(snapshot.get("is_blank")),
                    "is_boss_domain": bool(snapshot.get("is_boss_domain")),
                    "is_security_verify": bool(snapshot.get("is_security_verify")),
                    "title": title,
                }
            )
        return surfaces

    def _select_detail_supplement_page(self, context, candidates: list[dict]):
        pages = list(getattr(context, "pages", []) or [])
        target_cities: list[str] = []
        for item in list(candidates or [])[:10]:
            card = item.get("card") if isinstance(item, dict) else {}
            city = str((card or {}).get("city") or "").strip()
            if city and city not in target_cities:
                target_cities.append(city)
        best_page = None
        best_score = -1
        for page in reversed(pages):
            if self._page_is_closed(page):
                continue
            snapshot = extract_page_snapshot(page)
            url = str(snapshot.get("url") or "").strip()
            lowered_url = url.lower()
            if (
                lowered_url.startswith("devtools://")
                or lowered_url.startswith("edge://")
                or lowered_url.startswith("chrome://")
            ):
                continue
            if snapshot.get("is_blank"):
                continue
            if not snapshot.get("is_boss_domain") and not snapshot.get("is_security_verify"):
                continue
            surface_text = " ".join(
                part
                for part in [
                    url,
                    str(snapshot.get("title") or "").strip(),
                    str(snapshot.get("body_excerpt") or "").strip(),
                ]
                if part
            )
            score = 0
            if "/web/geek/jobs" in lowered_url or "/web/geek/job" in lowered_url:
                score += 40
            elif "/job_detail/" in lowered_url:
                score += 30
            elif snapshot.get("is_boss_domain"):
                score += 15
            if snapshot.get("is_security_verify"):
                score += 5
            if any(city and city in surface_text for city in target_cities):
                score += 20
            if "seorefer=index" in lowered_url:
                score -= 5
            if score > best_score:
                best_score = score
                best_page = page
        if best_page is not None:
            return best_page
        return self._resolve_existing_live_page(context, allow_general_fallback=False)

    def _find_existing_boss_page(self, context, *, allow_general_fallback: bool = True):
        pages = list(getattr(context, "pages", []) or [])
        fallback_boss_page = None
        fallback_general_page = None
        for page in reversed(pages):
            if self._page_is_closed(page):
                continue
            snapshot = extract_page_snapshot(page)
            url = snapshot.get("url", "")
            lowered_url = (url or "").lower()
            if lowered_url.startswith("devtools://") or lowered_url.startswith("edge://") or lowered_url.startswith("chrome://"):
                continue
            if "/web/geek/jobs" in url or "/web/geek/job" in url or snapshot.get("is_security_verify"):
                return page
            if snapshot.get("is_boss_domain") and fallback_boss_page is None:
                fallback_boss_page = page
            if fallback_general_page is None:
                fallback_general_page = page
        if fallback_boss_page is not None:
            return fallback_boss_page
        if allow_general_fallback:
            return fallback_general_page
        return None

    def _resolve_live_page(self, context, *, preferred_page=None, allow_general_fallback: bool = True):
        existing_page = self._resolve_existing_live_page(
            context,
            preferred_page=preferred_page,
            allow_general_fallback=allow_general_fallback,
        )
        if existing_page is not None:
            return existing_page
        return context.new_page()

    def _resolve_existing_live_page(self, context, *, preferred_page=None, allow_general_fallback: bool = True):
        if preferred_page is not None and not self._page_is_closed(preferred_page):
            snapshot = extract_page_snapshot(preferred_page)
            lowered_url = (snapshot.get("url", "") or "").lower()
            if not lowered_url.startswith("devtools://") and not lowered_url.startswith("edge://") and not lowered_url.startswith("chrome://"):
                if allow_general_fallback or snapshot.get("is_boss_domain") or snapshot.get("is_security_verify"):
                    return preferred_page
        existing_page = self._find_existing_boss_page(context, allow_general_fallback=allow_general_fallback)
        if existing_page is not None:
            return existing_page
        if preferred_page is not None:
            return preferred_page
        return None

    def _describe_probe_exception(self, exc: Exception) -> str:
        text = str(exc or "").strip()
        if "Target page, context or browser has been closed" in text or "Target closed" in text:
            return "褰撳墠鎺ョ鐨?BOSS 椤电澶辨晥浜嗐€傝鍦ㄧ櫥褰曟祻瑙堝櫒閲岄噸鏂版墦寮€涓€涓?BOSS 椤甸潰鍚庡啀璇曘€?"
        return text or exc.__class__.__name__

    def _shorten_live_url(self, url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        lowered = raw.lower()
        if lowered == "about:blank":
            return "about:blank"
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        compact = f"{parsed.netloc}{path}{query}"
        return compact[:140]

    def _start_navigation_trace(self, page):
        trace: list[dict] = []
        start_ts = monotonic()

        def record(event: str, url: str) -> None:
            shortened = self._shorten_live_url(url)
            if not shortened:
                return
            trace.append(
                {
                    "event": str(event or "").strip() or "nav",
                    "url": shortened,
                    "dt_ms": int((monotonic() - start_ts) * 1000),
                }
            )

        try:
            record("start", page.url or "")
        except Exception:
            pass

        def on_frame_navigated(frame) -> None:
            try:
                if frame != page.main_frame:
                    return
            except Exception:
                pass
            try:
                record("nav", frame.url)
            except Exception:
                return

        def on_dom_content_loaded() -> None:
            try:
                record("domcontentloaded", page.url or "")
            except Exception:
                return

        def on_load() -> None:
            try:
                record("load", page.url or "")
            except Exception:
                return

        handlers: dict[str, object] = {}
        for event_name, callback in {
            "framenavigated": on_frame_navigated,
            "domcontentloaded": on_dom_content_loaded,
            "load": on_load,
        }.items():
            try:
                page.on(event_name, callback)
                handlers[event_name] = callback
            except Exception:
                continue
        return trace, handlers

    def _stop_navigation_trace(self, page, handler) -> None:
        if not handler:
            return
        for event_name, callback in dict(handler).items():
            try:
                page.remove_listener(event_name, callback)
                continue
            except Exception:
                pass
            try:
                page.off(event_name, callback)
            except Exception:
                pass

    def _summarize_navigation_trace(self, trace: list[dict]) -> str:
        cleaned = [item for item in list(trace or []) if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if len(cleaned) <= 1:
            return ""
        segments: list[str] = []
        urls: list[str] = []
        for item in cleaned[:8]:
            event = str(item.get("event") or "").strip() or "nav"
            url = str(item.get("url") or "").strip()
            dt_ms = int(item.get("dt_ms") or 0)
            urls.append(url)
            segments.append(f"{event}@{dt_ms}ms:{url}")
        trace_text = " -> ".join(segments)
        if len(cleaned) > 6:
            trace_text += " -> ..."
        saw_results = any("/web/geek/jobs" in item for item in urls)
        saw_home = any("zhipin.com/" in item and "/web/geek/jobs" not in item and "/web/geek/job" not in item for item in urls)
        saw_same_url_repeat = any(urls[idx] == urls[idx - 1] for idx in range(1, len(urls)))
        if saw_results and saw_home:
            suffix = "；检测到主页/搜索页来回跳。"
        elif saw_same_url_repeat:
            suffix = "；检测到同 URL 重复导航/刷新。"
        else:
            suffix = ""
        return f"路径={trace_text}{suffix}"

    def _effective_search_base_url(self, page, *, attached_over_cdp: bool) -> str:
        if not attached_over_cdp:
            return self.base_url
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        if "/web/geek/jobs" in current_url:
            return current_url.split("?", 1)[0]
        if "/web/geek/job" in current_url:
            return current_url.split("?", 1)[0].replace("/web/geek/job", "/web/geek/jobs")
        return self.base_url

    def _goto_with_retry(self, page, url: str, *, wait_until: str, retries: int = 3) -> None:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""
        self._record_action_audit("goto", from_url=current_url, to_url=url, wait_until=wait_until)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                page.goto(url, wait_until=wait_until)
                try:
                    page.wait_for_timeout(random.randint(1800, 3200))
                except Exception:
                    pass
                return
            except Exception as exc:
                last_error = exc
                if not self._is_interrupted_navigation_error(exc) or attempt >= retries - 1:
                    raise
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
                except Exception:
                    pass
                page.wait_for_timeout(1400 * (attempt + 1))
        if last_error is not None:
            raise last_error

    def _should_retry_headed(self, errors: list[str]) -> bool:
        text = "\n".join(errors)
        return any(keyword in text for keyword in ["???????", "????", "unexpected_domain", "blank_page"])

    def _extract_search_cards(self, page, limit: int | None = None, *, query: str = "") -> list[dict]:
        max_cards = max(limit or self.max_cards_per_query, self.max_cards_per_query)
        raw_cards = self._extract_structured_search_cards(page, max_cards=max_cards)
        if not raw_cards:
            raw_cards = self._extract_generic_search_cards(page, max_cards=max_cards)
        cards: list[dict] = []
        seen_urls: set[str] = set()
        for card in raw_cards or []:
            absolute_href = (card.get("url") or "").strip()
            if not self._is_valid_search_card(absolute_href, card.get("text", "")):
                continue
            normalized_card = {
                **card,
                "salary_text": infer_boss_salary_text(card.get("text", ""), card.get("salary_text", "")),
            }
            if query and not self._card_matches_query(normalized_card, query):
                continue
            if not absolute_href or absolute_href in seen_urls:
                continue
            seen_urls.add(absolute_href)
            cards.append(normalized_card)
            if len(cards) >= max_cards:
                break
        return cards

    def _extract_structured_search_cards(self, page, *, max_cards: int) -> list[dict]:
        try:
            raw_cards = page.evaluate(
                """
                ({ maxCards }) => {
                  const normalize = (value) => (value || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const textOf = (node) => normalize(node ? (node.innerText || node.textContent || '') : '');
                  return Array.from(document.querySelectorAll('li.job-card-box'))
                    .slice(0, maxCards * 2)
                    .map((cardNode, index) => {
                      const anchor = cardNode.querySelector('a[href*="/job_detail"], a[href*="job_detail"]');
                      const href = normalize(anchor ? anchor.getAttribute('href') : '');
                      const absoluteHref = href ? new URL(href, window.location.href).toString() : '';
                      if (anchor) {
                        anchor.setAttribute('data-rb-job-card-key', `rb-job-card-${index}`);
                      }
                      return {
                        url: absoluteHref,
                        text: textOf(cardNode).slice(0, 320),
                        title: textOf(cardNode.querySelector('a.job-name, [class*="job-name"], [class*="job-title"]')).slice(0, 80),
                        company_name: textOf(cardNode.querySelector('.boss-name, [class*="boss-name"], [class*="brand-name"], [class*="company-name"]')).slice(0, 80),
                        city: textOf(cardNode.querySelector('.company-location, [class*="company-location"], [class*="job-area"], [class*="location"]')).slice(0, 40),
                        salary_text: textOf(cardNode.querySelector('.job-salary, [class*="salary"], [class*="pay"], [class*="red"]')).slice(0, 40),
                        visible_index: index,
                        card_key: `rb-job-card-${index}`,
                        card_top: Math.round(cardNode.getBoundingClientRect().top),
                      };
                    })
                    .filter((item) => item.url && item.title);
                }
                """,
                {"maxCards": max_cards},
            )
        except Exception:
            return []
        return raw_cards if isinstance(raw_cards, list) else []

    def _extract_generic_search_cards(self, page, *, max_cards: int) -> list[dict]:
        try:
            raw_cards = page.evaluate(
                """
                ({ maxCards, blockedTextTokens, blockedHrefTokens }) => {
                  const normalize = (value) => (value || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
                  const textOf = (node) => normalize(node ? (node.innerText || node.textContent || '') : '');
                  const pickFrom = (root, selectors) => {
                    for (const selector of selectors) {
                      const node = root.querySelector(selector);
                      const text = textOf(node);
                      if (text) return text;
                    }
                    return '';
                  };
                  const isVisible = (node) => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width >= 80 && rect.height >= 18;
                  };
                  const nearestCard = (anchor) => {
                    const selectors = [
                      '[class*="job-card"]',
                      '[class*="job-list-item"]',
                      '[class*="job-item"]',
                      '[class*="search-job-result"]',
                      '[class*="search-result"]',
                      '[class*="job-list-box"]',
                      '[class*="list-card"]',
                      'li'
                    ];
                    for (const selector of selectors) {
                      const node = anchor.closest(selector);
                      if (!node) continue;
                      if (isVisible(node)) return node;
                    }
                    return anchor;
                  };
                  const collectContext = (anchor) => {
                    const parts = [];
                    let current = anchor.parentElement;
                    for (let depth = 0; depth < 4 && current; depth += 1, current = current.parentElement) {
                      const previous = current.previousElementSibling;
                      if (previous) {
                        const previousText = textOf(previous).slice(0, 120);
                        if (previousText) parts.push(previousText);
                      }
                      const ariaLabel = normalize(current.getAttribute('aria-label') || '').slice(0, 80);
                      if (ariaLabel) parts.push(ariaLabel);
                      const className = normalize(String(current.className || '')).slice(0, 120);
                      if (className) parts.push(className);
                    }
                    return parts.join(' | ');
                  };
                  const anchors = Array.from(document.querySelectorAll('a[href*="/job_detail"], a[href*="job_detail"]'));
                  const seen = new Set();
                  const results = [];
                  let visibleIndex = 0;
                  const directCards = Array.from(document.querySelectorAll('li.job-card-box, [class*="job-card-box"], [class*="job-card"]'));
                  for (const cardNode of directCards) {
                    if (!isVisible(cardNode)) continue;
                    const anchor = cardNode.querySelector('a[href*="/job_detail"], a[href*="job_detail"]');
                    if (!anchor) continue;
                    const href = normalize(anchor.getAttribute('href'));
                    const absoluteHref = href ? new URL(href, window.location.href).toString() : '';
                    const loweredHref = absoluteHref.toLowerCase();
                    if (!loweredHref.includes('/job_detail')) continue;
                    if (blockedHrefTokens.some((token) => loweredHref.includes(token))) continue;
                    if (seen.has(absoluteHref)) continue;
                    const rect = cardNode.getBoundingClientRect();
                    if (rect.width < 120 || rect.height < 28) continue;
                    const cardText = textOf(cardNode);
                    if (!cardText || cardText.length < 16) continue;
                    const directText = cardText.toLowerCase();
                    if (blockedTextTokens.some((token) => directText.includes(token))) continue;
                    const title =
                      pickFrom(cardNode, ['a.job-name', '[class*="job-name"]', '[class*="job-title"]', '[class*="title"]', 'h3', 'h4', 'strong']) ||
                      textOf(anchor).slice(0, 80);
                    const company = pickFrom(cardNode, ['.boss-name', '[class*="boss-name"]', '[class*="brand-name"]', '[class*="company-name"]']);
                    const salary = pickFrom(cardNode, ['.job-salary', '[class*="salary"]', '[class*="pay"]', '[class*="red"]']);
                    const city = pickFrom(cardNode, ['.company-location', '[class*="company-location"]', '[class*="job-area"]', '[class*="location"]', '[class*="city"]', '[class*="area"]']);
                    const key = `rb-job-card-${visibleIndex}-${Math.random().toString(16).slice(2, 10)}`;
                    anchor.setAttribute('data-rb-job-card-key', key);
                    seen.add(absoluteHref);
                    results.push({
                      url: absoluteHref,
                      text: cardText.slice(0, 320),
                      title: title.slice(0, 80),
                      company_name: company.slice(0, 80),
                      city: city.slice(0, 40),
                      salary_text: salary.slice(0, 40),
                      visible_index: visibleIndex,
                      card_key: key,
                      card_top: Math.round(rect.top),
                    });
                    visibleIndex += 1;
                    if (results.length >= maxCards * 2) break;
                  }
                  if (results.length > 0) {
                    results.sort((left, right) => left.card_top - right.card_top);
                    return results.slice(0, maxCards * 2);
                  }
                  for (const anchor of anchors) {
                    if (!isVisible(anchor)) continue;
                    const href = normalize(anchor.getAttribute('href'));
                    const absoluteHref = href ? new URL(href, window.location.href).toString() : '';
                    const loweredHref = absoluteHref.toLowerCase();
                    if (!loweredHref.includes('/job_detail')) continue;
                    if (blockedHrefTokens.some((token) => loweredHref.includes(token))) continue;
                    if (seen.has(absoluteHref)) continue;
                    const cardNode = nearestCard(anchor);
                    const rect = cardNode.getBoundingClientRect();
                    if (rect.width < 120 || rect.height < 28) continue;
                    const cardText = textOf(cardNode);
                    if (!cardText || cardText.length < 16) continue;
                    const contextText = `${cardText} | ${collectContext(anchor)}`.toLowerCase();
                    if (blockedTextTokens.some((token) => contextText.includes(token))) continue;
                    const title =
                      pickFrom(cardNode, ['[class*="job-name"]', '[class*="job-title"]', '[class*="title"]', 'h3', 'h4', 'strong']) ||
                      textOf(anchor).slice(0, 80);
                    const company = pickFrom(cardNode, ['[class*="boss-name"]', '[class*="brand-name"]', '[class*="company-name"]']);
                    const salary = pickFrom(cardNode, ['[class*="salary"]', '[class*="pay"]', '[class*="red"]']);
                    const city = pickFrom(cardNode, ['[class*="company-location"]', '[class*="job-area"]', '[class*="location"]', '[class*="city"]', '[class*="area"]']);
                    const key = `rb-job-card-${visibleIndex}-${Math.random().toString(16).slice(2, 10)}`;
                    anchor.setAttribute('data-rb-job-card-key', key);
                    seen.add(absoluteHref);
                    results.push({
                      url: absoluteHref,
                      text: cardText.slice(0, 320),
                      title: title.slice(0, 80),
                      company_name: company.slice(0, 80),
                      city: city.slice(0, 40),
                      salary_text: salary.slice(0, 40),
                      visible_index: visibleIndex,
                      card_key: key,
                      card_top: Math.round(rect.top),
                    });
                    visibleIndex += 1;
                    if (results.length >= maxCards * 4) break;
                  }
                  results.sort((left, right) => left.card_top - right.card_top);
                  return results.slice(0, maxCards * 2);
                }
                """,
                {
                    "maxCards": max_cards,
                    "blockedTextTokens": [token.lower() for token in RECOMMENDATION_PAGE_MARKERS],
                    "blockedHrefTokens": [token.lower() for token in RECOMMENDATION_LINK_MARKERS],
                },
            )
        except Exception:
            return []
        return raw_cards if isinstance(raw_cards, list) else []

    def _is_valid_search_card(self, href: str, text: str) -> bool:
        lowered_href = href.lower()
        lowered_text = (text or "").strip().lower()
        if "/job_detail" not in lowered_href:
            return False
        if any(token in lowered_href for token in RECOMMENDATION_LINK_MARKERS):
            return False
        if any(token.lower() in lowered_text for token in RECOMMENDATION_PAGE_MARKERS):
            return False
        return True

    def _save_search_artifacts(self, page, *, query: str, note: str, extra: dict | None = None) -> dict | None:
        if not self.debug_dir:
            return None
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_query = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", query).strip("-")[:24] or "query"
        base = self.debug_dir / f"boss_browser_search_{timestamp}_{safe_query}"
        screenshot_path = base.with_suffix(".png")
        status_path = base.with_suffix(".json")
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass
        payload = {
            "note": note,
            "query": query,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "screenshot_path": str(screenshot_path),
            "snapshot": extract_page_snapshot(page),
        }
        if extra:
            payload.update(extra)
        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status_path": str(status_path),
            "screenshot_path": str(screenshot_path),
        }

    def _save_detail_artifacts(self, page, *, query: str, note: str, extra: dict | None = None) -> dict | None:
        if not self.debug_dir:
            return None
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_query = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", query).strip("-")[:24] or "query"
        base = self.debug_dir / f"boss_browser_detail_{timestamp}_{safe_query}"
        screenshot_path = base.with_suffix(".png")
        status_path = base.with_suffix(".json")
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            pass
        payload = {
            "note": note,
            "query": query,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "screenshot_path": str(screenshot_path),
            "snapshot": extract_page_snapshot(page),
        }
        if extra:
            payload.update(extra)
        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status_path": str(status_path),
            "screenshot_path": str(screenshot_path),
        }
