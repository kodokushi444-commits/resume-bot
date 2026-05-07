from __future__ import annotations

from datetime import datetime

from ..boss_cli_bridge import get_boss_cli_status, run_boss_cli_json, unwrap_boss_cli_data
from ..config import AppConfig
from ..normalization import is_job_quality_acceptable, normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource


def _stringify_list(values) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            normalized.append(value.strip())
        elif isinstance(value, dict):
            for key in ["name", "label", "text", "value"]:
                field = value.get(key)
                if isinstance(field, str) and field.strip():
                    normalized.append(field.strip())
                    break
    return normalized


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class BossCliSource(JobSource):
    def __init__(
        self,
        name: str,
        config: AppConfig,
        *,
        recommend_pages: int = 1,
        max_queries: int = 4,
        max_cards_per_query: int = 6,
        max_detail_pages: int = 12,
    ):
        super().__init__(name)
        self.config = config
        self.recommend_pages = max(recommend_pages, 0)
        self.max_queries = max(max_queries, 0)
        self.max_cards_per_query = max(max_cards_per_query, 1)
        self.max_detail_pages = max(max_detail_pages, 1)

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        status = get_boss_cli_status(self.config)
        if not status["available"]:
            raise RuntimeError(status["error"] or "未检测到 boss-cli 命令。")
        if status["authenticated"] is False:
            raise RuntimeError(
                "boss-cli 已安装，但当前 BOSS 登录态不可用。先执行 `boss login --cookie-source edge`，确认成功后再抓岗位。"
            )

        candidates: dict[str, dict] = {}
        for item in self._load_recommended_jobs():
            key = self._job_key(item)
            if key and key not in candidates:
                candidates[key] = item
        for request in self._build_search_requests(settings, profile):
            for item in self._search_jobs(request):
                key = self._job_key(item)
                if key and key not in candidates:
                    candidates[key] = item

        jobs: dict[str, JobPosting] = {}
        for item in list(candidates.values())[: self.max_detail_pages]:
            job = self._load_job_detail(item)
            if not job:
                continue
            jobs[job.fingerprint] = job
        return list(jobs.values())

    def _load_recommended_jobs(self) -> list[dict]:
        items: list[dict] = []
        for page in range(1, self.recommend_pages + 1):
            payload = run_boss_cli_json(self.config, ["recommend", "-p", str(page), "--json"])
            data = unwrap_boss_cli_data(payload)
            page_items = data.get("jobList", [])
            if not isinstance(page_items, list):
                continue
            items.extend(page_items[: self.max_cards_per_query])
        return items

    def _build_search_requests(self, settings: UserSettings, profile: ResumeProfile | None) -> list[dict]:
        current_year = datetime.now().year
        role_candidates = settings.preferred_roles[:3] or (profile.target_roles[:3] if profile else []) or ["运营"]
        city_candidates = settings.preferred_cities[:2] or [""]
        intents: list[str] = []
        if "校招" in settings.job_types:
            intents.extend([f"{current_year}届 应届", "校园招聘"])
        if "社招" in settings.job_types:
            intents.extend(["", "社招"])
        if settings.campus_role_mode in {"intern", "both"}:
            intents.append("实习")
        deduped: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        job_type = ""
        if settings.campus_role_mode == "intern":
            job_type = "实习"
        elif settings.campus_role_mode == "full_time":
            job_type = "全职"
        for role in role_candidates:
            for intent in intents or [""]:
                keyword = " ".join(part for part in [intent, role] if part).strip() or role.strip()
                if not keyword:
                    continue
                for city in city_candidates:
                    key = (keyword, city.strip(), job_type)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append({"keyword": keyword, "city": city.strip(), "job_type": job_type})
                    if len(deduped) >= self.max_queries:
                        return deduped
        return deduped

    def _search_jobs(self, request: dict) -> list[dict]:
        args = ["search", request["keyword"], "-p", "1"]
        if request.get("city"):
            args.extend(["--city", request["city"]])
        if request.get("job_type"):
            args.extend(["--job-type", request["job_type"]])
        args.append("--json")
        payload = run_boss_cli_json(self.config, args)
        data = unwrap_boss_cli_data(payload)
        items = data.get("jobList", [])
        if not isinstance(items, list):
            return []
        return items[: self.max_cards_per_query]

    def _job_key(self, item: dict) -> str:
        return _first_text(
            item.get("securityId"),
            item.get("encryptJobId"),
            item.get("jobId"),
        )

    def _load_job_detail(self, item: dict) -> JobPosting | None:
        security_id = self._job_key(item)
        if not security_id:
            return None
        payload = run_boss_cli_json(self.config, ["detail", security_id, "--json"])
        data = unwrap_boss_cli_data(payload)
        job_info = data.get("jobInfo", {}) if isinstance(data.get("jobInfo"), dict) else {}
        boss_info = data.get("bossInfo", {}) if isinstance(data.get("bossInfo"), dict) else {}
        brand_info = data.get("brandComInfo", {}) if isinstance(data.get("brandComInfo"), dict) else {}

        title = _first_text(job_info.get("jobName"), item.get("jobName"))
        company_name = _first_text(
            brand_info.get("brandName"),
            job_info.get("brandName"),
            item.get("brandName"),
        )
        city = _first_text(
            job_info.get("locationName"),
            job_info.get("cityName"),
            item.get("cityName"),
            item.get("areaDistrict"),
        )
        salary_text = _first_text(job_info.get("salaryDesc"), item.get("salaryDesc"))
        experience = _first_text(job_info.get("experienceName"), item.get("jobExperience"))
        degree = _first_text(job_info.get("degreeName"), item.get("jobDegree"))
        stage = _first_text(
            brand_info.get("brandStageName"),
            brand_info.get("stageName"),
            item.get("brandStageName"),
        )
        industry = _first_text(
            brand_info.get("brandIndustry"),
            brand_info.get("industry"),
        )
        scale = _first_text(
            brand_info.get("brandScaleName"),
            brand_info.get("scaleName"),
        )
        recruiter = " ".join(
            part
            for part in [
                _first_text(boss_info.get("name")),
                _first_text(boss_info.get("title")),
            ]
            if part
        ).strip()
        labels = _stringify_list(job_info.get("welfareList")) + _stringify_list(job_info.get("jobLabels"))
        skills = _stringify_list(job_info.get("skills")) + _stringify_list(item.get("skills"))
        post_description = _first_text(
            job_info.get("postDescription"),
            job_info.get("description"),
            item.get("postDescription"),
        )
        company_intro = _first_text(
            brand_info.get("brandDescription"),
            brand_info.get("brandDesc"),
            brand_info.get("introduction"),
        )
        lines = [
            f"职位：{title}" if title else "",
            f"公司：{company_name}" if company_name else "",
            f"城市：{city}" if city else "",
            f"薪资：{salary_text}" if salary_text else "",
            f"经验要求：{experience}" if experience else "",
            f"学历要求：{degree}" if degree else "",
            f"融资阶段：{stage}" if stage else "",
            f"行业：{industry}" if industry else "",
            f"公司规模：{scale}" if scale else "",
            f"招聘者：{recruiter}" if recruiter else "",
            f"福利标签：{' / '.join(dict.fromkeys(labels))}" if labels else "",
            f"技能标签：{' / '.join(dict.fromkeys(skills))}" if skills else "",
            "职位描述：",
            post_description,
            f"公司介绍：{company_intro}" if company_intro else "",
        ]
        description = "\n".join(line for line in lines if line).strip()
        raw = {
            "title": title,
            "company_name": company_name,
            "city": city,
            "description": description,
            "salary_text": salary_text,
            "company_stage": stage,
            "published_at": _first_text(item.get("activeTimeDesc"), job_info.get("activeTimeDesc")),
            "apply_url": _first_text(
                item.get("jobUrl"),
                item.get("url"),
                data.get("jobUrl") if isinstance(data.get("jobUrl"), str) else "",
                f"https://www.zhipin.com/job_detail/{security_id}.html",
            ),
            "url": _first_text(
                item.get("jobUrl"),
                item.get("url"),
                data.get("jobUrl") if isinstance(data.get("jobUrl"), str) else "",
                f"https://www.zhipin.com/job_detail/{security_id}.html",
            ),
            "source_job_id": security_id,
            "detail_fetched": True,
            "raw_payload": {
                "search_item": item,
                "detail_payload": data,
            },
        }
        job = normalize_job_fields(raw, source=self.name)
        if not is_job_quality_acceptable(job):
            return None
        return job
