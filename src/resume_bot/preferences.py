from __future__ import annotations

import re
from dataclasses import replace

from .llm import TextModelClient
from .types import CompanyWatchItem, ResumeProfile, UserSettings


COMMON_CITIES = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "贵阳",
    "武汉",
    "南京",
    "西安",
    "苏州",
    "重庆",
    "天津",
    "长沙",
    "厦门",
]

ROLE_KEYWORDS = [
    "运营",
    "内容运营",
    "用户运营",
    "用户增长",
    "新媒体运营",
    "活动运营",
    "社区运营",
    "产品",
    "市场",
    "品牌",
    "销售",
    "客服",
    "管培生",
    "开发",
    "测试",
    "算法",
    "设计",
]

STAGE_KEYWORDS = ["已上市", "上市公司", "IPO", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮"]
JOB_TYPE_VALUES = ["校招", "社招"]
DEGREE_KEYWORDS = ["中专", "大专", "专科", "本科", "硕士", "博士"]
ROLE_MODE_MAP = {
    "只看校招正职": "full_time",
    "只看正职": "full_time",
    "只看校招实习": "intern",
    "只看实习": "intern",
    "正职和实习都看": "both",
    "校招正职和实习都看": "both",
}

TIME_PATTERN = re.compile(r"(?P<hour>[01]?\d|2[0-3])(?:[:点](?P<minute>[0-5]?\d))?")
SALARY_VALUE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>k|K|千|万|元)?")


def _normalize_token(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = normalized.strip("，,、;；/|")
    return normalized


def _normalized_token_key(value: str) -> str:
    return _normalize_token(value).casefold()


def _dedupe_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_token(value)
        key = _normalized_token_key(value)
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def dedupe_text_list(values: list[str]) -> list[str]:
    return _dedupe_keep_order(values)


def normalize_settings_lists(settings: UserSettings) -> UserSettings:
    normalized = replace(settings)
    normalized.preferred_roles = _dedupe_keep_order(settings.preferred_roles)
    normalized.avoided_roles = _dedupe_keep_order(settings.avoided_roles)
    normalized.preferred_cities = _dedupe_keep_order(settings.preferred_cities)
    normalized.excluded_cities = _dedupe_keep_order(settings.excluded_cities)
    normalized.preferred_company_stages = _dedupe_keep_order(settings.preferred_company_stages)
    normalized.excluded_keywords = _dedupe_keep_order(settings.excluded_keywords)
    normalized.preferred_keywords = _dedupe_keywords_keep_order(settings.preferred_keywords)
    normalized.job_types = _dedupe_keep_order([item for item in settings.job_types if item in JOB_TYPE_VALUES]) or ["校招", "社招"]
    if normalized.salary_max and normalized.salary_min and normalized.salary_max < normalized.salary_min:
        normalized.salary_min, normalized.salary_max = normalized.salary_max, normalized.salary_min
    if normalized.max_degree_requirement == "专科":
        normalized.max_degree_requirement = "大专"
    return normalized


def _keyword_root_key(value: str) -> str:
    normalized = _normalize_token(value)
    normalized = re.split(r"[（(]", normalized, maxsplit=1)[0]
    return _normalized_token_key(normalized)


def _dedupe_keywords_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    index_by_root: dict[str, int] = {}
    for raw in values:
        normalized = _normalize_token(raw)
        if not normalized:
            continue
        root = _keyword_root_key(normalized)
        existing_index = index_by_root.get(root)
        if existing_index is None:
            index_by_root[root] = len(result)
            result.append(normalized)
            continue
        if len(normalized) > len(result[existing_index]):
            result[existing_index] = normalized
    return result


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[，,、/ ]+", value) if item.strip()]
    return [str(value).strip()] if str(value).strip() else []


def seed_settings_from_profile(settings: UserSettings, profile: ResumeProfile) -> UserSettings:
    merged = replace(settings)
    merged.preferred_roles = _dedupe_keep_order(settings.preferred_roles + profile.target_roles)
    merged.preferred_cities = _dedupe_keep_order(settings.preferred_cities + profile.target_cities)
    role_keys = {_normalized_token_key(item) for item in merged.preferred_roles}
    filtered_skills = [skill for skill in profile.skills[:8] if _normalized_token_key(skill) not in role_keys]
    merged.preferred_keywords = _dedupe_keywords_keep_order(settings.preferred_keywords + filtered_skills)
    if not merged.max_degree_requirement and profile.degree in DEGREE_KEYWORDS:
        merged.max_degree_requirement = "大专" if profile.degree == "专科" else profile.degree
    return merged


def _profile_seed_lists(profile: ResumeProfile) -> tuple[list[str], list[str], list[str]]:
    roles = _dedupe_keep_order(profile.target_roles)
    cities = _dedupe_keep_order(profile.target_cities)
    role_keys = {_normalized_token_key(item) for item in roles}
    keywords = _dedupe_keywords_keep_order(
        [skill for skill in profile.skills[:8] if _normalized_token_key(skill) not in role_keys]
    )
    return roles, cities, keywords


def reseed_settings_from_profile(
    settings: UserSettings,
    profile: ResumeProfile,
    previous_profile: ResumeProfile | None = None,
) -> UserSettings:
    updated = replace(settings)
    if previous_profile:
        old_roles, old_cities, old_keywords = _profile_seed_lists(previous_profile)
        old_role_keys = {_normalized_token_key(item) for item in old_roles}
        old_city_keys = {_normalized_token_key(item) for item in old_cities}
        old_keyword_roots = {_keyword_root_key(item) for item in old_keywords}
        updated.preferred_roles = [
            item for item in updated.preferred_roles if _normalized_token_key(item) not in old_role_keys
        ]
        updated.preferred_cities = [
            item for item in updated.preferred_cities if _normalized_token_key(item) not in old_city_keys
        ]
        updated.preferred_keywords = [
            item for item in updated.preferred_keywords if _keyword_root_key(item) not in old_keyword_roots
        ]
    return seed_settings_from_profile(updated, profile)


def apply_manual_settings(
    settings: UserSettings,
    *,
    preferred_roles: list[str],
    preferred_cities: list[str],
    preferred_keywords: list[str],
    excluded_keywords: list[str],
    job_types: list[str],
    campus_role_mode: str,
    salary_min: int,
    salary_max: int,
    max_degree_requirement: str,
) -> UserSettings:
    updated = replace(settings)
    updated.preferred_roles = _dedupe_keep_order(preferred_roles)
    updated.preferred_cities = _dedupe_keep_order(preferred_cities)
    updated.preferred_keywords = _dedupe_keywords_keep_order(preferred_keywords)
    updated.excluded_keywords = _dedupe_keep_order(excluded_keywords)
    updated.avoided_roles = []
    updated.job_types = _dedupe_keep_order([item for item in job_types if item in JOB_TYPE_VALUES]) or ["校招", "社招"]
    updated.campus_role_mode = campus_role_mode if campus_role_mode in {"full_time", "intern", "both"} else "full_time"
    updated.salary_min = max(0, int(salary_min or 0))
    updated.salary_max = max(0, int(salary_max or 0))
    if updated.salary_max and updated.salary_min and updated.salary_max < updated.salary_min:
        updated.salary_min, updated.salary_max = updated.salary_max, updated.salary_min
    degree = _normalize_token(max_degree_requirement)
    updated.max_degree_requirement = "大专" if degree == "专科" else degree
    return updated


def _apply_patch(settings: UserSettings, patch: dict) -> UserSettings:
    updated = replace(settings)
    for field in [
        "preferred_roles",
        "avoided_roles",
        "preferred_cities",
        "excluded_cities",
        "preferred_company_stages",
        "excluded_keywords",
        "preferred_keywords",
    ]:
        add_values = _as_list(patch.get(f"{field}_add", []))
        remove_values = set(_as_list(patch.get(f"{field}_remove", [])))
        if patch.get(field) is not None:
            if field == "preferred_keywords":
                setattr(updated, field, _dedupe_keywords_keep_order(_as_list(patch[field])))
            else:
                setattr(updated, field, _dedupe_keep_order(_as_list(patch[field])))
            continue
        current_values = [item for item in getattr(updated, field) if item not in remove_values]
        if field == "preferred_keywords":
            setattr(updated, field, _dedupe_keywords_keep_order(current_values + list(add_values)))
        else:
            setattr(updated, field, _dedupe_keep_order(current_values + list(add_values)))
    add_job_types = [item for item in _as_list(patch.get("job_types_add", [])) if item in JOB_TYPE_VALUES]
    remove_job_types = set(item for item in _as_list(patch.get("job_types_remove", [])) if item in JOB_TYPE_VALUES)
    if patch.get("job_types") is not None:
        updated.job_types = _dedupe_keep_order([item for item in _as_list(patch["job_types"]) if item in JOB_TYPE_VALUES])
    else:
        updated.job_types = _dedupe_keep_order(
            [item for item in updated.job_types if item not in remove_job_types] + add_job_types
        )
    if not updated.job_types:
        updated.job_types = ["校招", "社招"]
    if patch.get("push_time"):
        updated.push_time = patch["push_time"]
    if patch.get("campus_role_mode"):
        updated.campus_role_mode = patch["campus_role_mode"]
    if patch.get("salary_min") is not None:
        updated.salary_min = max(0, int(patch["salary_min"]))
    if patch.get("salary_max") is not None:
        updated.salary_max = max(0, int(patch["salary_max"]))
    if updated.salary_max and updated.salary_min and updated.salary_max < updated.salary_min:
        updated.salary_min, updated.salary_max = updated.salary_max, updated.salary_min
    if patch.get("accept_unspecified_salary") is not None:
        updated.accept_unspecified_salary = bool(patch["accept_unspecified_salary"])
    if patch.get("max_degree_requirement") is not None:
        value = str(patch["max_degree_requirement"]).strip()
        updated.max_degree_requirement = "大专" if value == "专科" else value
    if patch.get("history_backfill_limit"):
        updated.history_backfill_limit = int(patch["history_backfill_limit"])
    if patch.get("notify_when_empty") is not None:
        updated.notify_when_empty = bool(patch["notify_when_empty"])
    if patch.get("allow_repush_when_updated") is not None:
        updated.allow_repush_when_updated = bool(patch["allow_repush_when_updated"])
    return updated


def _parse_salary_value(text: str) -> int:
    match = SALARY_VALUE_PATTERN.search(text)
    if not match:
        return 0
    value = float(match.group("value"))
    unit = (match.group("unit") or "元").lower()
    if unit in {"k", "千"}:
        value *= 1000
    elif unit == "万":
        value *= 10000
    return int(round(value))


def _parse_salary_patch(text: str) -> dict:
    patch: dict = {}
    compact = text.replace(" ", "")
    if "薪资不限" in compact or "不限制薪资" in compact:
        patch["salary_min"] = 0
        patch["salary_max"] = 0
        return patch
    range_match = re.search(
        r"(?:薪资|工资|月薪|日薪)?(?:范围|区间)?[：:]?\s*(?P<min>\d+(?:\.\d+)?\s*(?:k|K|千|万|元)?)\s*[-~至]\s*(?P<max>\d+(?:\.\d+)?\s*(?:k|K|千|万|元)?)",
        compact,
    )
    if range_match:
        patch["salary_min"] = _parse_salary_value(range_match.group("min"))
        patch["salary_max"] = _parse_salary_value(range_match.group("max"))
        return patch
    min_match = re.search(r"(?:最低|不少于|起步|至少)(?P<value>\d+(?:\.\d+)?\s*(?:k|K|千|万|元)?)", compact)
    if min_match:
        patch["salary_min"] = _parse_salary_value(min_match.group("value"))
    max_match = re.search(r"(?:最高|不超过|上限)(?P<value>\d+(?:\.\d+)?\s*(?:k|K|千|万|元)?)", compact)
    if max_match:
        patch["salary_max"] = _parse_salary_value(max_match.group("value"))
    elif min_match and any(token in compact for token in ["无上限", "上限不限", "最高不限"]):
        patch["salary_max"] = 0
    if min_match and not max_match:
        patch["salary_max"] = patch.get("salary_max", 0)
    if max_match and not min_match:
        patch["salary_min"] = 0
    if "薪资未写也推" in compact or "接受薪资未写" in compact or "薪资没写也推" in compact:
        patch["accept_unspecified_salary"] = True
    if "不要薪资未写" in compact or "薪资未写的不看" in compact or "不接受薪资未写" in compact:
        patch["accept_unspecified_salary"] = False
    return patch


def _parse_degree_patch(text: str) -> dict:
    compact = text.replace(" ", "")
    patch: dict = {}
    if "学历不限" in compact or "学历不限制" in compact:
        patch["max_degree_requirement"] = ""
        return patch
    if "学历" not in compact and "要求" not in compact and "jd" not in compact.lower():
        return patch
    for degree in DEGREE_KEYWORDS:
        if degree in compact:
            patch["max_degree_requirement"] = "大专" if degree == "专科" else degree
            break
    return patch


def _parse_job_type_patch(text: str) -> dict:
    patch: dict = {}
    if "只看校招" in text and "社招" not in text:
        patch["job_types"] = ["校招"]
    elif "只看社招" in text and "校招" not in text:
        patch["job_types"] = ["社招"]
    elif "校招和社招都看" in text or "校招社招都看" in text or "校招和社招一起看" in text:
        patch["job_types"] = ["校招", "社招"]
    if "不看校招" in text or "不要校招" in text:
        patch["job_types_remove"] = _dedupe_keep_order(list(patch.get("job_types_remove", [])) + ["校招"])
    if "不看社招" in text or "不要社招" in text:
        patch["job_types_remove"] = _dedupe_keep_order(list(patch.get("job_types_remove", [])) + ["社招"])
    if "加上校招" in text or "校招也看" in text:
        patch["job_types_add"] = _dedupe_keep_order(list(patch.get("job_types_add", [])) + ["校招"])
    if "加上社招" in text or "社招也看" in text:
        patch["job_types_add"] = _dedupe_keep_order(list(patch.get("job_types_add", [])) + ["社招"])
    return patch


def _heuristic_patch(text: str) -> dict:
    patch: dict = {}
    found_cities = [city for city in COMMON_CITIES if city in text]
    found_roles = [role for role in ROLE_KEYWORDS if role in text]
    found_stages = [stage for stage in STAGE_KEYWORDS if stage in text]
    patch.update(_parse_job_type_patch(text))
    if "只看" in text and found_cities:
        patch["preferred_cities"] = found_cities
    elif ("不要" in text or "不想看" in text) and found_cities:
        patch["excluded_cities_add"] = found_cities
    elif ("城市" in text or "地点" in text) and found_cities:
        patch["preferred_cities_add"] = found_cities
    if ("不要" in text or "不想看" in text) and found_roles:
        patch["avoided_roles_add"] = found_roles
        patch["excluded_keywords_add"] = found_roles
    elif found_roles:
        patch["preferred_roles_add"] = found_roles
    if found_stages and ("只看" in text or "偏好" in text or "想看" in text):
        patch["preferred_company_stages_add"] = found_stages
    for trigger, role_mode in ROLE_MODE_MAP.items():
        if trigger in text:
            patch["campus_role_mode"] = role_mode
            break
    if "都看" in text and "实习" in text and "正职" in text:
        patch["campus_role_mode"] = "both"
    if "黑名单" in text:
        tokens = [token.strip("，,。；; ") for token in re.split(r"[，,。；; ]+", text)]
        patch["excluded_keywords_add"] = [token for token in tokens if len(token) >= 2]
    time_match = TIME_PATTERN.search(text)
    if time_match and ("推送" in text or "提醒" in text):
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or "0")
        patch["push_time"] = f"{hour:02d}:{minute:02d}"
    limit_match = re.search(r"补发(?:默认)?(\d{1,2})条", text)
    if limit_match:
        patch["history_backfill_limit"] = int(limit_match.group(1))
    patch.update(_parse_salary_patch(text))
    patch.update(_parse_degree_patch(text))
    return patch


def _merge_guardrail_patch(llm_patch: dict, heuristic_patch: dict, text: str) -> dict:
    merged = dict(llm_patch or {})
    if heuristic_patch.get("campus_role_mode"):
        merged["campus_role_mode"] = heuristic_patch["campus_role_mode"]
    if heuristic_patch.get("push_time"):
        merged["push_time"] = heuristic_patch["push_time"]
    if heuristic_patch.get("history_backfill_limit"):
        merged["history_backfill_limit"] = heuristic_patch["history_backfill_limit"]
    if heuristic_patch.get("job_types") is not None:
        merged["job_types"] = heuristic_patch["job_types"]
    if heuristic_patch.get("job_types_add"):
        merged["job_types_add"] = _dedupe_keep_order(
            list(merged.get("job_types_add", [])) + list(heuristic_patch["job_types_add"])
        )
    if heuristic_patch.get("job_types_remove"):
        merged["job_types_remove"] = _dedupe_keep_order(
            list(merged.get("job_types_remove", [])) + list(heuristic_patch["job_types_remove"])
        )
    if heuristic_patch.get("salary_min") is not None:
        merged["salary_min"] = heuristic_patch["salary_min"]
    if heuristic_patch.get("salary_max") is not None:
        merged["salary_max"] = heuristic_patch["salary_max"]
    if heuristic_patch.get("accept_unspecified_salary") is not None:
        merged["accept_unspecified_salary"] = heuristic_patch["accept_unspecified_salary"]
    if heuristic_patch.get("max_degree_requirement") is not None:
        merged["max_degree_requirement"] = heuristic_patch["max_degree_requirement"]
    if "只看" in text and heuristic_patch.get("preferred_cities"):
        merged["preferred_cities"] = heuristic_patch["preferred_cities"]
    if ("不要" in text or "不想看" in text) and heuristic_patch.get("avoided_roles_add"):
        merged["avoided_roles_add"] = _dedupe_keep_order(
            list(merged.get("avoided_roles_add", [])) + list(heuristic_patch["avoided_roles_add"])
        )
    if ("不要" in text or "不想看" in text) and heuristic_patch.get("excluded_keywords_add"):
        merged["excluded_keywords_add"] = _dedupe_keep_order(
            list(merged.get("excluded_keywords_add", [])) + list(heuristic_patch["excluded_keywords_add"])
        )
    return merged


def interpret_preference_text(
    text: str,
    settings: UserSettings,
    profile: ResumeProfile | None,
    llm_client: TextModelClient,
) -> tuple[UserSettings, dict]:
    patch = {}
    heuristic_patch = _heuristic_patch(text)
    if text.strip():
        try:
            system_prompt = (
                "你是求职设置解析助手。"
                "把用户对岗位偏好的自然语言改动转成 JSON patch。"
                "不要输出解释，只输出 JSON。"
            )
            user_prompt = f"""
当前 settings:
{settings.to_dict()}

当前简历 profile:
{profile.to_dict() if profile else {}}

用户原话:
{text}

请输出 JSON，允许字段：
{{
  "preferred_roles": null,
  "preferred_roles_add": [],
  "preferred_roles_remove": [],
  "avoided_roles": null,
  "avoided_roles_add": [],
  "avoided_roles_remove": [],
  "preferred_cities": null,
  "preferred_cities_add": [],
  "preferred_cities_remove": [],
  "excluded_cities": null,
  "excluded_cities_add": [],
  "excluded_cities_remove": [],
  "preferred_company_stages": null,
  "preferred_company_stages_add": [],
  "preferred_company_stages_remove": [],
  "excluded_keywords": null,
  "excluded_keywords_add": [],
  "excluded_keywords_remove": [],
  "preferred_keywords": null,
  "preferred_keywords_add": [],
  "preferred_keywords_remove": [],
  "job_types": null,
  "job_types_add": [],
  "job_types_remove": [],
  "push_time": "",
  "campus_role_mode": "",
  "salary_min": null,
  "salary_max": null,
  "accept_unspecified_salary": null,
  "max_degree_requirement": null,
  "history_backfill_limit": 0,
  "notify_when_empty": null,
  "allow_repush_when_updated": null
}}
"""
            patch = llm_client.complete_json(system_prompt, user_prompt, max_tokens=1200)
            patch = _merge_guardrail_patch(patch, heuristic_patch, text)
        except Exception:
            patch = heuristic_patch
    updated = _apply_patch(settings, patch)
    return updated, patch


def add_company_watch(settings: UserSettings, name: str, careers_url: str = "", domain: str = "", stage: str = "") -> UserSettings:
    updated = replace(settings)
    updated.company_watchlist = [
        item for item in updated.company_watchlist if item.name.strip().lower() != name.strip().lower()
    ]
    updated.company_watchlist.append(
        CompanyWatchItem(name=name.strip(), careers_url=careers_url.strip(), domain=domain.strip(), stage=stage.strip())
    )
    return updated


def remove_company_watch(settings: UserSettings, name: str) -> UserSettings:
    updated = replace(settings)
    updated.company_watchlist = [
        item for item in updated.company_watchlist if item.name.strip().lower() != name.strip().lower()
    ]
    return updated


def remove_setting_value(settings: UserSettings, field: str, value: str) -> UserSettings:
    normalized_value = value.strip()
    updated = replace(settings)
    removable_fields = {
        "preferred_roles",
        "avoided_roles",
        "preferred_cities",
        "excluded_cities",
        "preferred_company_stages",
        "excluded_keywords",
        "preferred_keywords",
        "job_types",
    }
    if field in removable_fields:
        current_values = [item for item in getattr(updated, field) if item.strip() != normalized_value]
        setattr(updated, field, _dedupe_keep_order(current_values))
        return updated
    if field == "company_watchlist":
        updated.company_watchlist = [
            item for item in updated.company_watchlist if item.name.strip() != normalized_value
        ]
        return updated
    raise ValueError(f"不支持删除这个设置字段：{field}")


def settings_summary(settings: UserSettings) -> str:
    companies = ", ".join(item.name for item in settings.company_watchlist) or "未设置"
    role_mode_label = {
        "full_time": "只看正职",
        "intern": "只看实习",
        "both": "正职和实习都看",
    }.get(settings.campus_role_mode, "未设置")
    salary_label = "不限"
    if settings.salary_min or settings.salary_max:
        lower = str(settings.salary_min) if settings.salary_min else "不限"
        upper = str(settings.salary_max) if settings.salary_max else "不限"
        salary_label = f"{lower} - {upper} 元/月"
    return "\n".join(
        [
            f"用户：{settings.user_id}",
            f"招聘范围：{', '.join(settings.job_types) or '未设置'}",
            f"岗位性质：{role_mode_label}",
            f"想看岗位：{', '.join(settings.preferred_roles) or '未设置'}",
            f"不想看岗位：{', '.join(settings.avoided_roles) or '未设置'}",
            f"想看城市：{', '.join(settings.preferred_cities) or '不限'}",
            f"屏蔽城市：{', '.join(settings.excluded_cities) or '无'}",
            f"想看公司阶段：{', '.join(settings.preferred_company_stages) or '不限'}",
            f"薪资范围：{salary_label}",
            f"薪资未写明也推：{'开' if settings.accept_unspecified_salary else '关'}",
            f"最高学历要求：{settings.max_degree_requirement or '不限'}",
            f"黑名单词：{', '.join(settings.excluded_keywords) or '无'}",
            f"加分词：{', '.join(settings.preferred_keywords) or '无'}",
            f"官网监控名单：{companies}",
            f"历史补发条数：{settings.history_backfill_limit}",
            f"推送时间：{settings.push_time}",
            f"无新岗位提醒：{'开' if settings.notify_when_empty else '关'}",
            f"岗位更新可重推：{'开' if settings.allow_repush_when_updated else '关'}",
            f"飞书接收 ID：{settings.feishu_receive_id or '未绑定'}",
        ]
    )
