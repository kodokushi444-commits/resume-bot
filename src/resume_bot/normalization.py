from __future__ import annotations

import re
from urllib.parse import urlparse

from .dates import extract_deadline
from .types import JobPosting, stable_hash


JOB_CATEGORY_KEYWORDS = {
    "运营": ["运营", "内容", "增长", "社区", "活动", "用户运营", "内容运营", "新媒体"],
    "产品": ["产品经理", "产品运营", "产品策划"],
    "市场": ["市场", "品牌", "营销", "媒介", "商务"],
    "销售": ["销售", "商务拓展", "BD", "客户经理", "顾问"],
    "客服": ["客服", "售后", "热线", "呼叫中心"],
    "开发": ["开发", "工程师", "后端", "前端", "算法", "测试", "运维"],
    "设计": ["设计", "视觉", "UI", "UX", "交互"],
    "职能": ["人力", "HR", "法务", "财务", "审计", "采购"],
}

COMPANY_STAGE_KEYWORDS = ["已上市", "IPO", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮"]
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "贵阳", "武汉", "南京", "西安", "苏州", "重庆", "天津", "长沙", "厦门", "青岛"]
CITY_PATTERN = re.compile(r"(北京|上海|广州|深圳|杭州|成都|贵阳|武汉|南京|西安|苏州|重庆|天津|长沙|厦门|青岛)")
COMPANY_SUFFIX_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()·&]{2,30}"
    r"(?:有限公司|有限责任公司|集团|科技|信息|网络|软件|传媒|汽车|银行|证券|保险|研究院|事务所|控股|智能|电子|医药|数据|教育))"
)
EMPLOYMENT_MODE_KEYWORDS = {
    "intern": ["实习", "暑期实习", "日常实习", "intern"],
    "full_time": ["校招", "校园招聘", "应届", "毕业生", "春招", "秋招", "管培生", "全职", "正式", "2026届", "2027届"],
}
SOCIAL_KEYWORDS = ["社招", "社会招聘", "社会人才", "社會招聘", "有经验", "社会招聘", "社招岗位"]
SOCIAL_EXPERIENCE_PATTERN = re.compile(r"(?:经验要求|工作经验|经验)\s*[：: ]?\s*(?:\d+\s*[-~至]\s*\d+年|\d+年以上)")
CLASS_YEAR_PATTERN = re.compile(r"(?:20)?(?P<year>2[6-9])(?:\s*/\s*(?:20)?2[6-9])?届")
DEGREE_KEYWORDS = ["中专", "大专", "专科", "本科", "硕士", "博士"]
DEGREE_REQUIREMENT_PATTERNS = [
    re.compile(
        r"(?:学历要求|学历|任职要求|任职资格|岗位要求|岗位职责|招聘对象|面向)"
        r"[\s：:0-9一二三四五六七八九十、.．()（）-]{0,12}"
        r"(?P<degree>中专|大专|专科|本科|硕士|博士)(?:研究生)?(?:学历)?(?:及以上|以上)?"
    ),
    re.compile(r"(?P<degree>中专|大专|专科|本科|硕士|博士)(?:研究生)?(?:学历)?(?:及以上|以上)"),
    re.compile(r"(?P<degree>中专|大专|专科|本科|硕士|博士)(?:研究生)?学历"),
]
DEGREE_PREFERENCE_PATTERN = re.compile(r"(?P<degree>中专|大专|专科|本科|硕士|博士)(?:优先)")
PUBLISHED_KEYWORDS = ["发布时间", "发布日期", "发布时间：", "投递时间", "更新于"]
NOISY_PAGE_KEYWORDS = [
    "相关推荐",
    "全站热榜",
    "创作者周榜",
    "暂无评论",
    "大家都在搜",
    "对搜索结果是否满意",
    "热门职位 热门城市 热门企业",
]
LOGIN_GATE_KEYWORDS = ["注册登录", "请稍候", "扫码登录", "验证码", "安全验证", "点击登录", "BOSS直聘注册登录"]
BAD_URL_HINTS = ["/login", "/register", "/security-check", "/captcha", "/verify", "/web/user/"]
SALARY_RANGE_PATTERN = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*(?P<unit1>k|K|千|万|元)?\s*[-~至]\s*"
    r"(?P<max>\d+(?:\.\d+)?)\s*(?P<unit2>k|K|千|万|元)?(?:\s*(?:/|每|·)\s*(?P<period>月|天|年))?"
)
SALARY_SINGLE_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>k|K|千|万|元)\s*(?:/|每|·)?\s*(?P<period>月|天|年)"
)


def infer_job_categories(title: str, description: str) -> list[str]:
    text = f"{title}\n{description}"
    matched: list[str] = []
    for category, keywords in JOB_CATEGORY_KEYWORDS.items():
        if any(keyword.lower() in text.lower() for keyword in keywords):
            matched.append(category)
    return matched or ["未分类"]


def infer_company_stage(text: str) -> str:
    for keyword in COMPANY_STAGE_KEYWORDS:
        if keyword in text:
            return keyword
    return ""


def _dedupe_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def infer_city(text: str) -> str:
    match = CITY_PATTERN.search(text)
    return match.group(1) if match else ""


def infer_city_list(raw_city: str, text: str) -> list[str]:
    values: list[str] = []
    if raw_city.strip():
        values.extend(CITY_PATTERN.findall(raw_city))
    if not values:
        values.extend(CITY_PATTERN.findall(text))
    return _dedupe_keep_order(values)


def is_campus_job(title: str, description: str) -> bool:
    text = f"{title}\n{description}".lower()
    campus_keywords = ["校招", "校园招聘", "应届", "毕业生", "campus", "graduate", "春招", "秋招"]
    if any(keyword.lower() in text for keyword in campus_keywords):
        return True
    class_year_match = CLASS_YEAR_PATTERN.search(text)
    if class_year_match and ("应届" in text or "毕业" in text or "校招" in text):
        return True
    return bool(CLASS_YEAR_PATTERN.search(text) and ("面向" in text or "招募" in text or "届" in title))


def infer_employment_mode(title: str, description: str) -> str:
    title_text = title.lower()
    body_text = description.lower()
    text = f"{title}\n{description}".lower()
    if any(keyword.lower() in title_text for keyword in EMPLOYMENT_MODE_KEYWORDS["intern"]):
        return "intern"
    if any(keyword.lower() in title_text for keyword in EMPLOYMENT_MODE_KEYWORDS["full_time"]):
        return "full_time"
    if is_campus_job(title, description):
        return "full_time"
    if any(keyword.lower() in body_text for keyword in EMPLOYMENT_MODE_KEYWORDS["intern"]):
        return "intern"
    if any(keyword.lower() in body_text for keyword in EMPLOYMENT_MODE_KEYWORDS["full_time"]):
        return "full_time"
    if title.strip() or description.strip():
        return "full_time"
    return "unknown"


def infer_job_type(title: str, description: str, employment_mode: str) -> str:
    text = f"{title}\n{description}"
    if is_campus_job(title, description):
        return "校招"
    lowered = text.lower()
    if any(keyword.lower() in lowered for keyword in SOCIAL_KEYWORDS):
        return "社招"
    if SOCIAL_EXPERIENCE_PATTERN.search(text):
        return "社招"
    if employment_mode == "intern":
        return "社招"
    return "社招"


def infer_company_name(title: str, description: str, fallback: str = "") -> str:
    if fallback.strip():
        return fallback.strip()
    for pattern in [
        re.compile(r"(招聘单位|所属公司|公司名称|公司)[:：]\s*([^\n]{2,40})"),
        COMPANY_SUFFIX_PATTERN,
    ]:
        match = pattern.search(description)
        if not match:
            continue
        if match.lastindex and match.lastindex >= 2:
            value = match.group(2).strip()
        else:
            value = match.group(1).strip()
        if value and "牛客" not in value and "BOSS" not in value:
            return value
    title_match = re.match(r"([\u4e00-\u9fa5A-Za-z0-9·]{2,20})(20\d{2}|春招|秋招|校招)", title)
    if title_match:
        return title_match.group(1).strip()
    return ""


def infer_published_at(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if any(keyword in line for keyword in PUBLISHED_KEYWORDS):
            return line
    return ""


def _to_monthly_salary(value: float, unit: str, period: str) -> int:
    amount = float(value)
    unit = unit or "元"
    period = period or "月"
    if unit in {"k", "K", "千"}:
        amount *= 1000
    elif unit == "万":
        amount *= 10000
    if period == "天":
        amount *= 21.75
    elif period == "年":
        amount /= 12
    return int(round(amount))


def infer_salary(text: str) -> tuple[str, int, int, str]:
    normalized_text = text.replace(" ", "")
    if "面议" in normalized_text:
        return "面议", 0, 0, "negotiable"
    for match in SALARY_RANGE_PATTERN.finditer(normalized_text):
        salary_text = match.group(0)
        unit1 = match.group("unit1")
        unit2 = match.group("unit2")
        if not unit1 and not unit2:
            continue
        unit = unit2 or unit1 or "元"
        period = match.group("period") or ("月" if unit in {"k", "K", "千", "万"} else "月")
        minimum = _to_monthly_salary(float(match.group("min")), unit, period)
        maximum = _to_monthly_salary(float(match.group("max")), unit, period)
        if minimum > 0 and maximum > 0:
            salary_unit = {"月": "month", "天": "day", "年": "year"}.get(period, "")
            return salary_text, minimum, maximum, salary_unit
    single = SALARY_SINGLE_PATTERN.search(normalized_text)
    if single:
        salary_text = single.group(0)
        unit = single.group("unit")
        period = single.group("period")
        value = _to_monthly_salary(float(single.group("value")), unit, period)
        salary_unit = {"月": "month", "天": "day", "年": "year"}.get(period, "")
        return salary_text, value, value, salary_unit
    return "", 0, 0, ""


def infer_degree_requirement(text: str) -> tuple[str, str]:
    compact_text = text.replace(" ", "")
    if "学历不限" in compact_text or "专业不限" in compact_text:
        requirement = "不限"
    else:
        requirement = ""
        for pattern in DEGREE_REQUIREMENT_PATTERNS:
            match = pattern.search(compact_text)
            if match:
                requirement = match.group("degree")
                break
    preference = ""
    preference_match = DEGREE_PREFERENCE_PATTERN.search(compact_text)
    if preference_match:
        preference = preference_match.group("degree")
    return requirement, preference


def looks_like_noise_page(url: str, title: str, description: str) -> bool:
    lowered_url = url.lower()
    if any(token in lowered_url for token in ["/feed/main/detail", "/discuss/", "/article/", "/forum/", "/community/"]):
        return True
    if "nowcoder.com" in lowered_url and not any(token in lowered_url for token in ["/jobs/detail/", "/job/", "/careers/"]):
        return True
    if "xiaohongshu.com" in lowered_url and "/explore/" in lowered_url:
        return True
    noise_hits = sum(1 for token in NOISY_PAGE_KEYWORDS if token in description)
    if noise_hits >= 2 and "投递" not in description and "申请" not in description:
        return True
    if not title.strip() and not description.strip():
        return True
    return False


def job_quality_issues(job: JobPosting) -> list[str]:
    issues: list[str] = []
    title = job.title.strip()
    description = job.description.strip()
    text = job.text_blob()
    lowered_url = job.url.lower()
    raw_payload = job.raw_payload if isinstance(job.raw_payload, dict) else {}
    detail_api = raw_payload.get("detail_api") if isinstance(raw_payload.get("detail_api"), dict) else {}
    if looks_like_noise_page(job.url, job.title, job.description):
        issues.append("noise_page")
    if not job.url:
        issues.append("missing_url")
    if any(token in lowered_url for token in BAD_URL_HINTS):
        issues.append("gate_or_login_url")
    if any(keyword in text for keyword in LOGIN_GATE_KEYWORDS):
        issues.append("login_or_gate_page")
    if len(title) < 4:
        issues.append("title_too_short")
    description_length = len(description)
    if description_length < 120:
        issues.append("description_too_short")
    metadata_hits = 0
    if job.company_name.strip():
        metadata_hits += 1
    if job.city_list or job.city.strip():
        metadata_hits += 1
    if job.salary_text or job.salary_min or job.salary_max:
        metadata_hits += 1
    if job.degree_requirement or job.degree_preference:
        metadata_hits += 1
    if metadata_hits == 0:
        issues.append("metadata_missing")
    if "boss" in job.source.lower():
        if not job.detail_fetched:
            issues.append("boss_detail_not_fetched")
        jd_marker_hits = sum(
            1
            for marker in ("职位描述", "岗位职责", "任职要求", "岗位要求", "福利待遇")
            if marker in description
        )
        detail_api_success = detail_api.get("code") == 0
        if description_length < 160:
            issues.append("boss_description_too_short")
        elif description_length < 240 and not detail_api_success and jd_marker_hits == 0:
            issues.append("boss_description_too_short")
        generic_detail_url = lowered_url.rstrip("/").endswith("/job_detail") or job.source_job_id == "job_detail"
        if generic_detail_url:
            issues.append("boss_generic_detail_url")
        if "对搜索结果是否满意" in description or "招聘频道介绍" in description:
            issues.append("boss_search_results_page")
    if "请稍候" in title or "登录" in title or "注册" in title:
        issues.append("title_looks_like_gate_page")
    if "职位描述" not in description and "岗位职责" not in description and "任职要求" not in description:
        if description_length < 280 and metadata_hits < 2:
            issues.append("missing_jd_markers")
    return _dedupe_keep_order(issues)


def is_job_quality_acceptable(job: JobPosting) -> bool:
    issues = set(job_quality_issues(job))
    if not issues:
        return True
    if issues & {
        "noise_page",
        "missing_url",
        "gate_or_login_url",
        "login_or_gate_page",
        "title_looks_like_gate_page",
        "boss_detail_not_fetched",
        "boss_generic_detail_url",
        "boss_search_results_page",
    }:
        return False
    if "description_too_short" in issues and "metadata_missing" in issues:
        return False
    if "boss_description_too_short" in issues:
        return False
    if "missing_jd_markers" in issues and "metadata_missing" in issues:
        return False
    return True


def normalize_job_fields(raw: dict, source: str) -> JobPosting:
    title = (raw.get("title") or raw.get("job_title") or "").strip()
    url = (raw.get("url") or raw.get("detail_url") or raw.get("apply_url") or raw.get("job_url") or "").strip()
    description = (raw.get("description") or raw.get("content") or raw.get("raw_content") or "").strip()
    company_name = infer_company_name(title, description, fallback=(raw.get("company_name") or raw.get("company") or "").strip())
    raw_city = (raw.get("city") or raw.get("location") or "").strip()
    city_list = infer_city_list(raw_city, description)
    city = city_list[0] if city_list else (raw_city or infer_city(description))
    company_stage = (raw.get("company_stage") or "").strip() or infer_company_stage(description)
    source_job_id = (raw.get("source_job_id") or raw.get("job_id") or "").strip()
    published_at = (raw.get("published_at") or raw.get("published_time") or raw.get("date") or "").strip() or infer_published_at(description)
    apply_url = (raw.get("apply_url") or raw.get("job_url") or url).strip()
    raw_deadline_text = (raw.get("deadline") or raw.get("apply_deadline") or "").strip()
    raw_application_status = (raw.get("application_status") or "").strip().lower()
    deadline, application_status = extract_deadline(raw_deadline_text or description)
    if raw_application_status:
        application_status = raw_application_status
    employment_mode = infer_employment_mode(title, description)
    job_type = (raw.get("job_type") or "").strip() or infer_job_type(title, description, employment_mode)
    raw_salary_text = (raw.get("salary_text") or raw.get("salary") or "").strip()
    if raw_salary_text:
        salary_text, salary_min, salary_max, salary_unit = infer_salary(raw_salary_text)
        if not salary_text:
            salary_text = raw_salary_text
    else:
        salary_text, salary_min, salary_max, salary_unit = infer_salary(f"{title}\n{description}")
    degree_requirement, degree_preference = infer_degree_requirement(f"{title}\n{description}")
    raw_degree_requirement = (
        raw.get("degree_requirement")
        or raw.get("jobDegree")
        or raw.get("degreeName")
        or ""
    ).strip()
    raw_degree_preference = (raw.get("degree_preference") or "").strip()
    if raw_degree_requirement:
        degree_requirement = raw_degree_requirement
    if raw_degree_preference:
        degree_preference = raw_degree_preference
    if not source_job_id and url:
        path = urlparse(url).path
        source_job_id = path.strip("/").split("/")[-1]
    job = JobPosting(
        source=source,
        url=url,
        source_job_id=source_job_id,
        title=title,
        company_name=company_name,
        city=city,
        city_list=city_list or ([city] if city else []),
        description=description,
        apply_url=apply_url,
        company_stage=company_stage,
        published_at=published_at,
        deadline=deadline,
        application_status=application_status,
        employment_mode=employment_mode,
        salary_text=salary_text,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_unit=salary_unit,
        degree_requirement=degree_requirement,
        degree_preference=degree_preference,
        job_categories=infer_job_categories(title, description),
        job_type=job_type,
        detail_fetched=bool(raw.get("detail_fetched", False)),
        fetch_session_id=(raw.get("fetch_session_id") or "").strip(),
        raw_payload=raw,
    )
    city_hash_part = ",".join(job.city_list or ([city] if city else []))
    job.fingerprint = stable_hash(source, source_job_id, company_name, title, city, url)
    job.content_hash = stable_hash(title, company_name, city_hash_part, description, apply_url)
    return job
