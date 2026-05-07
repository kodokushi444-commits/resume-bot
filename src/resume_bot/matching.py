from __future__ import annotations

from .dates import parse_date_text
from .llm import TextModelClient
from .types import JobPosting, MatchResult, ResumeProfile, UserSettings


DEGREE_ORDER = {
    "不限": 0,
    "中专": 1,
    "大专": 2,
    "专科": 2,
    "本科": 3,
    "硕士": 4,
    "博士": 5,
}


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords if keyword)


def _matching_keywords(text: str, keywords: list[str], *, limit: int = 4) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for keyword in keywords:
        normalized = keyword.strip()
        if not normalized or normalized.lower() not in lowered:
            continue
        if any(normalized in existing and normalized != existing for existing in hits):
            continue
        hits = [existing for existing in hits if existing not in normalized or existing == normalized]
        if normalized not in hits:
            hits.append(normalized)
        if len(hits) >= limit:
            break
    return hits


def _job_cities(job: JobPosting) -> list[str]:
    return job.city_list or ([job.city] if job.city else [])


def _job_keyword_text(job: JobPosting) -> str:
    return "\n".join(
        part
        for part in [
            job.title,
            job.company_name,
            job.job_type,
            job.employment_mode,
            _format_job_city(job),
        ]
        if part
    )


def _format_job_city(job: JobPosting) -> str:
    cities = _job_cities(job)
    return "/".join(cities) if cities else ""


def _normalize_degree(degree: str) -> str:
    normalized = degree.strip()
    if normalized == "专科":
        return "大专"
    return normalized


def _degree_rank(degree: str) -> int:
    return DEGREE_ORDER.get(_normalize_degree(degree), -1)


def _salary_out_of_range(job: JobPosting, settings: UserSettings) -> bool:
    if not settings.salary_min and not settings.salary_max:
        return False
    if not job.salary_min and not job.salary_max:
        return not settings.accept_unspecified_salary
    if settings.salary_min and job.salary_max and job.salary_max < settings.salary_min:
        return True
    if settings.salary_max and job.salary_min and job.salary_min > settings.salary_max:
        return True
    return False


def should_skip_job(job: JobPosting, settings: UserSettings, last_action: str) -> tuple[bool, str]:
    text = job.text_blob()
    keyword_text = _job_keyword_text(job)
    if last_action == "disliked":
        return True, "用户已标记为不感兴趣"
    if settings.job_types and job.job_type and job.job_type not in settings.job_types:
        return True, "不在招聘范围里"
    if job.application_status == "closed":
        return True, "岗位已过投递截止时间"
    if job.application_status == "pending":
        return True, "岗位暂未开放投递"
    if job.application_status == "unknown":
        return True, "投递状态未确认"
    cities = _job_cities(job)
    if settings.preferred_cities and cities and not any(city in settings.preferred_cities for city in cities):
        return True, "不在目标城市里"
    if settings.preferred_cities and not cities and settings.skip_unknown_city_when_city_filtered:
        return True, "城市未识别，且已启用目标城市过滤"
    if settings.excluded_cities and any(city in settings.excluded_cities for city in cities):
        return True, "命中屏蔽城市"
    if _contains_any(keyword_text, settings.excluded_keywords):
        return True, "命中黑名单词"
    if _contains_any(keyword_text, settings.avoided_roles):
        return True, "命中不想看岗位"
    if settings.campus_role_mode == "full_time" and job.employment_mode == "intern":
        return True, "当前设置只看正职"
    if settings.campus_role_mode == "intern" and job.employment_mode == "full_time":
        return True, "当前设置只看实习"
    if _salary_out_of_range(job, settings):
        return True, "不在薪资范围里"
    if settings.max_degree_requirement:
        max_degree_rank = _degree_rank(settings.max_degree_requirement)
        job_requirement_rank = _degree_rank(job.degree_requirement)
        if job_requirement_rank > max_degree_rank >= 0:
            return True, "学历要求高于当前设置"
    return False, ""


def heuristic_match(job: JobPosting, profile: ResumeProfile | None, settings: UserSettings) -> MatchResult | None:
    score = 35.0
    reasons: list[str] = []
    text = job.text_blob()
    cities = _job_cities(job)

    if settings.job_types and job.job_type in settings.job_types:
        score += 4
        reasons.append(f"招聘范围符合：{job.job_type}")
    if cities and settings.preferred_cities and any(city in settings.preferred_cities for city in cities):
        score += 8
        reasons.append(f"城市符合偏好：{_format_job_city(job)}")
    elif settings.preferred_cities and not cities:
        reasons.append("城市未识别")
    if profile and profile.target_roles:
        role_hits = _matching_keywords(text, profile.target_roles, limit=3)
        if role_hits:
            score += 12
            reasons.append(f"匹配简历目标方向：{', '.join(role_hits)}")
    if profile and profile.skills:
        skill_hits = _matching_keywords(text, profile.skills, limit=3)
        if skill_hits:
            score += min(8, len(skill_hits) * 3)
            reasons.append(f"命中简历技能：{', '.join(skill_hits)}")
    if settings.preferred_roles:
        preferred_role_hits = _matching_keywords(text, settings.preferred_roles, limit=4)
    else:
        preferred_role_hits = []
    if preferred_role_hits:
        score += 18
        reasons.append(f"岗位命中偏好方向：{', '.join(preferred_role_hits)}")
    if settings.preferred_company_stages and job.company_stage in settings.preferred_company_stages:
        score += 6
        reasons.append(f"公司阶段符合偏好：{job.company_stage}")
    if settings.preferred_keywords:
        hits = [keyword for keyword in settings.preferred_keywords if keyword and keyword.lower() in text.lower()]
        if hits:
            score += min(12, len(hits) * 2)
            reasons.append(f"命中加分词：{', '.join(hits[:4])}")
    if job.salary_text:
        score += 4
        reasons.append(f"薪资信息：{job.salary_text}")
    elif settings.salary_min or settings.salary_max:
        reasons.append("薪资未写明")
    if job.degree_requirement:
        if job.degree_requirement == "不限":
            score += 4
            reasons.append("学历不限")
        else:
            score += 4
            reasons.append(f"学历要求：{job.degree_requirement}")
    elif job.degree_preference:
        reasons.append(f"学历偏好：{job.degree_preference}")
    if job.job_type == "社招":
        score += 3
        reasons.append("属于普通社招范围")
    if "运营" in job.title or "运营" in text:
        score += 10
        reasons.append("岗位明显偏运营方向")
    if job.deadline:
        deadline_date = parse_date_text(job.deadline)
        if deadline_date:
            reasons.append(f"投递截止：{deadline_date.isoformat()}")
        else:
            reasons.append(f"投递截止：{job.deadline}")
    if job.employment_mode == "unknown":
        reasons.append("岗位类型未识别")
    if "管培生" in text:
        score -= 10
    if "销售" in text or "客服" in text:
        score -= 20
    if score < 45:
        return None
    return MatchResult(job=job, score=min(score, 100), reasons=reasons[:4] or ["规则评分通过"])


def rerank_with_llm(
    candidates: list[MatchResult],
    profile: ResumeProfile | None,
    settings: UserSettings,
    llm_client: TextModelClient,
    top_n: int,
) -> list[MatchResult]:
    if not candidates:
        return []
    shortlisted = candidates[:top_n]
    try:
        system_prompt = (
            "你是校招岗位推荐助手。"
            "给每个岗位打 0 到 100 分，并给出 1 到 3 条简短推荐理由。"
            "只输出 JSON 数组。"
        )
        user_prompt = {
            "profile": profile.to_dict() if profile else {},
            "settings": settings.to_dict(),
            "jobs": [
                {
                    "fingerprint": item.job.fingerprint,
                    "title": item.job.title,
                    "company_name": item.job.company_name,
                    "city": item.job.city,
                    "city_list": item.job.city_list,
                    "job_type": item.job.job_type,
                    "employment_mode": item.job.employment_mode,
                    "salary_text": item.job.salary_text,
                    "degree_requirement": item.job.degree_requirement,
                    "degree_preference": item.job.degree_preference,
                    "company_stage": item.job.company_stage,
                    "description": item.job.description[:1500],
                }
                for item in shortlisted
            ],
        }
        payload = llm_client.complete_json(system_prompt, str(user_prompt), max_tokens=1800)
        by_fingerprint = {item.job.fingerprint: item for item in shortlisted}
        reranked: list[MatchResult] = []
        for entry in payload:
            item = by_fingerprint.get(entry.get("fingerprint", ""))
            if not item:
                continue
            item.score = float(entry.get("score", item.score))
            item.reasons = entry.get("reasons", item.reasons) or item.reasons
            reranked.append(item)
        if reranked:
            reranked.sort(key=lambda item: item.score, reverse=True)
            remainder = candidates[top_n:]
            return reranked + remainder
    except Exception:
        pass
    return candidates
