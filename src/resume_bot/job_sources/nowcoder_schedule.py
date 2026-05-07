from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..matching import should_skip_job
from ..normalization import is_job_quality_acceptable, normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings, stable_hash
from .base import JobSource, SourceHaltError


SCHEDULE_SEED_URLS = [
    "https://mnowpick.nowcoder.com/jobs/school/schedule",
]
ENTERPRISE_URL_PATTERN = re.compile(r"https?://(?:api-cdn\.)?nowcoder\.com/enterprise/\d+", re.IGNORECASE)
OFFICIAL_LINK_TEXT_KEYWORDS = ["官网", "投递", "申请", "校招", "招聘", "查看官网", "立即投递"]
OFFICIAL_LINK_URL_KEYWORDS = ["campus", "career", "job", "jobs", "join", "recruit", "zhaopin", "xyzp", "talent"]
OPEN_HINTS = ["投递", "申请", "岗位职责", "任职要求", "职位描述", "job description"]
CLOSED_HINTS = ["截止", "结束", "已结束", "停止投递", "关闭", "closed"]
PENDING_HINTS = ["待上线", "即将开启", "敬请期待", "待开放"]
STRICT_EXACT_HOST_FAMILIES = {
    "mokahr.com",
    "hotjob.cn",
    "zhiye.com",
    "51job.com",
    "feishu.cn",
}
CITY_PATTERN = re.compile(r"(北京|上海|广州|深圳|杭州|成都|贵阳|武汉|南京|西安|苏州|重庆|天津|长沙|厦门|青岛)")
JOB_TITLE_HINT = re.compile(r"[岗位师员理生家专]|工程师|经理|运营|产品|设计|算法|开发|分析|市场|销售|职能")
BAIDU_JOB_LINE_PATTERN = re.compile(r"^(?P<city>[\u4e00-\u9fa5/]+)-(?P<title>.+?)\((?P<job_id>J\d+)\)$")
SPDB_ROW_PATTERN = re.compile(
    r"^(?P<org>.+?)\s+(?P<title>.+?)\s+"
    r"(?P<degree>博士及以上|硕士及以上|本科及以上|大专及以上|本科|硕士|博士|不限)\s+"
    r"(?P<count>\d+|若干)\s+(?P<city>[\u4e00-\u9fa5/、，,]+)$"
)
BAD_JOB_TITLE_TOKENS = [
    "校园招聘",
    "招聘官网",
    "招聘主页",
    "官网投递",
    "立即投递",
    "查看官网",
    "申请入口",
    "浦发招聘",
    "百度校园招聘",
    "职位列表",
    "全部职位",
    "招聘信息",
    "校招日程",
]
BAD_JOB_TITLE_PREFIXES = [
    "岗位职责",
    "工作职责",
    "职责描述",
    "任职要求",
    "任职资格",
    "岗位要求",
    "工作地点",
    "学历要求",
    "招聘人数",
    "所属机构",
    "立即投递",
    "立即申请",
    "投递入口",
    "要求",
]
PLATFORM_MARKETING_HINTS = [
    "招聘系统",
    "人力资源管理系统",
    "hr saas",
    "招聘管理系统",
    "校园招聘平台",
    "招聘软件",
    "解决方案",
    "演示",
    "试用",
]
RECRUITMENT_PAGE_HINTS = [
    "招聘",
    "校招",
    "校园招聘",
    "职位",
    "岗位",
    "加入我们",
    "应聘",
    "投递",
    "申请",
    "career",
    "campus",
    "job",
    "jobs",
    "talent",
]
JSON_LD_JOB_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
INITIAL_STATE_PATTERN = re.compile(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)\{var s;", re.DOTALL)
JSON_ASSIGNMENT_PATTERN = re.compile(
    r"(?:window\.)?[A-Za-z_$][\w.$]*\s*=\s*(?P<body>\{.*\}|\[.*\])\s*;?\s*$",
    re.DOTALL,
)
ZHAOPIN_XIAOZHAO_ID_PATTERN = re.compile(r'xiaozhaoId:"(?P<value>[^"]+)"')
ZHAOPIN_SCENE_PATTERN = re.compile(r'scene:"(?P<value>[^"]+)"')
ZHAOPIN_DEPARTMENT_PATTERN = re.compile(r"orgDepartmentIds:(?P<value>\d+)")
ZHAOPIN_CUSTOM_TAGS_PATTERN = re.compile(r'customTags:"(?P<value>[^"]*)"')


@dataclass
class _FetchedPage:
    requested_url: str
    final_url: str
    status_code: int
    text: str
    title: str
    content_type: str


class NowcoderScheduleSource(JobSource):
    def __init__(
        self,
        name: str,
        *,
        seed_urls: list[str] | None = None,
        max_schedule_pages: int = 1,
        max_enterprises: int = 20,
        max_official_pages_per_enterprise: int = 8,
        max_jobs: int = 40,
        request_timeout_sec: int = 20,
        throttle_seconds: float = 1.6,
        debug_dir: Path | None = None,
    ):
        super().__init__(name)
        self.seed_urls = [url.strip() for url in (seed_urls or SCHEDULE_SEED_URLS) if url.strip()]
        self.max_schedule_pages = max(1, int(max_schedule_pages))
        self.max_enterprises = max(1, int(max_enterprises))
        self.max_official_pages_per_enterprise = max(1, int(max_official_pages_per_enterprise))
        self.max_jobs = max(1, int(max_jobs))
        self.request_timeout_sec = max(5, int(request_timeout_sec))
        self.throttle_seconds = max(0.8, float(throttle_seconds))
        self.debug_dir = debug_dir
        self._session = requests.Session()
        self._session.headers.update(
            {
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self._last_request_at = 0.0
        self.last_fetch_report: dict = {}

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        enterprises: dict[str, dict] = {}
        schedule_reports: list[dict] = []
        enterprise_reports: list[dict] = []
        official_reports: list[dict] = []
        jobs_by_key: dict[str, JobPosting] = {}
        discovered_job_count = 0
        kept_job_count = 0
        official_target_count = 0
        official_page_count = 0

        for seed_url in self.seed_urls[: self.max_schedule_pages]:
            page = self._fetch_page(seed_url)
            if self._looks_blocked(page):
                detail = {"stage": "schedule", "url": seed_url, "title": page.title}
                self._write_debug_snapshot("halt", detail)
                raise SourceHaltError("牛客校招日程页返回了受限页面，已止损停止。", detail=detail)
            candidates = self._extract_enterprise_candidates(page.text, page.final_url)
            for candidate in candidates:
                enterprises.setdefault(candidate["enterprise_url"], candidate)
            schedule_reports.append(
                {
                    "url": seed_url,
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "enterprise_count": len(candidates),
                    "title": page.title,
                }
            )

        selected_enterprises = list(enterprises.values())[: self.max_enterprises]
        role_tokens = self._role_tokens(settings, profile)
        kept_enterprises = 0

        for enterprise in selected_enterprises:
            try:
                enterprise_page = self._fetch_page(enterprise["enterprise_url"])
                official_targets = self._extract_official_targets(enterprise_page.text, enterprise_page.final_url, enterprise)
                official_target_count += len(official_targets)
                enterprise_reports.append(
                    {
                        "enterprise_url": enterprise["enterprise_url"],
                        "company_name": enterprise.get("company_name", ""),
                        "official_target_count": len(official_targets),
                        "title": enterprise_page.title,
                    }
                )
            except Exception as exc:
                enterprise_reports.append(
                    {
                        "enterprise_url": enterprise["enterprise_url"],
                        "company_name": enterprise.get("company_name", ""),
                        "error": str(exc),
                    }
                )
                continue

            enterprise_jobs: list[JobPosting] = []
            for target in official_targets:
                try:
                    crawled = self._crawl_official_site(target, enterprise, settings, role_tokens)
                    enterprise_jobs.extend(crawled["jobs"])
                    official_reports.extend(crawled["reports"])
                    discovered_job_count += int(crawled.get("discovered_job_count", 0) or 0)
                    kept_job_count += int(crawled.get("kept_job_count", 0) or 0)
                    official_page_count += int(crawled.get("official_page_count", 0) or 0)
                except Exception as exc:
                    official_reports.append(
                        {
                            "official_url": target["url"],
                            "company_name": enterprise.get("company_name", ""),
                            "error": str(exc),
                        }
                    )
            if not enterprise_jobs:
                continue
            kept_enterprises += 1
            for job in enterprise_jobs:
                self._merge_job_candidate(jobs_by_key, job)
                if len(jobs_by_key) >= self.max_jobs:
                    break
            if len(jobs_by_key) >= self.max_jobs:
                break

        summary = {
            "schedule_reports": schedule_reports,
            "enterprise_reports": enterprise_reports,
            "official_reports": official_reports,
            "enterprise_count": len(selected_enterprises),
            "kept_enterprise_count": kept_enterprises,
            "official_target_count": official_target_count,
            "official_page_count": official_page_count,
            "discovered_job_count": discovered_job_count,
            "kept_job_count": kept_job_count,
            "job_count": min(len(jobs_by_key), self.max_jobs),
        }
        self.last_fetch_report = {
            "enterprise_count": len(selected_enterprises),
            "kept_enterprise_count": kept_enterprises,
            "official_target_count": official_target_count,
            "official_page_count": official_page_count,
            "discovered_job_count": discovered_job_count,
            "kept_job_count": kept_job_count,
            "official_report_count": len(official_reports),
        }
        self._write_debug_snapshot("fetch", summary)
        return list(jobs_by_key.values())[: self.max_jobs]

    def _fetch_page(self, url: str) -> _FetchedPage:
        self._throttle()
        response = self._session.get(url, timeout=self.request_timeout_sec, allow_redirects=True)
        response.raise_for_status()
        response.encoding = self._best_response_encoding(response)
        text = response.text
        return _FetchedPage(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            text=text,
            title=self._extract_title(text),
            content_type=response.headers.get("content-type", ""),
        )

    def _post_json(self, url: str, payload: dict) -> dict:
        self._throttle()
        response = self._session.post(url, json=payload, timeout=self.request_timeout_sec)
        response.raise_for_status()
        return response.json()

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _extract_enterprise_candidates(self, html: str, base_url: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: dict[str, dict] = {}
        for anchor in soup.find_all("a", href=True):
            enterprise_url = self._normalize_enterprise_url(anchor.get("href", ""), base_url)
            if not enterprise_url:
                continue
            container = anchor
            for _ in range(3):
                if container.parent is None:
                    break
                container = container.parent
            context = self._compact_text(container.get_text(" ", strip=True))
            company_name = self._infer_company_name_from_schedule(context, anchor.get_text(" ", strip=True))
            candidates[enterprise_url] = {
                "enterprise_url": enterprise_url,
                "company_name": company_name,
                "schedule_context": context[:600],
                "cities": self._find_cities(context),
                "batch_text": self._infer_batch_text(context),
            }
        return list(candidates.values())

    def _extract_official_targets(self, html: str, base_url: str, enterprise: dict) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        targets: dict[str, dict] = {}
        initial_state = self._extract_initial_state(html)
        enterprise_info = (((initial_state.get("store") or {}).get("enterprise") or {}).get("enterpriseInfo") or {})
        simple_name = self._compact_text(enterprise_info.get("simpleName") or enterprise_info.get("name") or "")
        if simple_name:
            enterprise["company_name"] = simple_name
        for raw_url in [
            enterprise_info.get("buttonInfo", {}).get("url", ""),
            enterprise_info.get("officalEncodeUrl", ""),
            *[item.get("url", "") for item in enterprise_info.get("schedules", []) if isinstance(item, dict)],
            *[item.get("encodeUrl", "") for item in enterprise_info.get("schedules", []) if isinstance(item, dict)],
        ]:
            href = self._unwrap_target_url(str(raw_url or ""), base_url)
            normalized = self._normalize_official_url(href)
            if not normalized:
                continue
            targets[normalized] = {
                "url": normalized,
                "label": "官网投递",
                "company_name": enterprise.get("company_name", ""),
            }
        for anchor in soup.find_all("a", href=True):
            href = self._unwrap_target_url(anchor.get("href", ""), base_url)
            normalized = self._normalize_official_url(href)
            if not normalized:
                continue
            text = self._compact_text(anchor.get_text(" ", strip=True))
            if not self._looks_like_official_entry(text, normalized):
                continue
            targets[normalized] = {
                "url": normalized,
                "label": text,
                "company_name": enterprise.get("company_name", ""),
            }
        if targets:
            return list(targets.values())
        for match in re.finditer(r"https?://[^\s\"'<>]+", html):
            url = self._normalize_official_url(match.group(0))
            if not url:
                continue
            context = html[max(0, match.start() - 80) : match.end() + 80]
            if not any(keyword in context for keyword in OFFICIAL_LINK_TEXT_KEYWORDS):
                continue
            targets.setdefault(
                url,
                {"url": url, "label": self._compact_text(context)[:120], "company_name": enterprise.get("company_name", "")},
            )
        for target in self._extract_platform_targets(html, base_url):
            targets.setdefault(
                target,
                {"url": target, "label": "平台补充入口", "company_name": enterprise.get("company_name", "")},
            )
        return list(targets.values())

    def _crawl_official_site(self, target: dict, enterprise: dict, settings: UserSettings, role_tokens: list[str]) -> dict:
        root = self._root_host(target["url"])
        queue: deque[str] = deque([target["url"]])
        visited: set[str] = set()
        jobs_by_key: dict[str, JobPosting] = {}
        reports: list[dict] = []
        discovered_job_count = 0
        kept_job_count = 0
        while queue and len(visited) < self.max_official_pages_per_enterprise:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            page = self._fetch_page(url)
            if self._looks_blocked(page):
                reports.append({"official_url": url, "status": "blocked", "title": page.title})
                continue
            if self._looks_like_platform_marketing_page(page, enterprise):
                reports.append({"official_url": page.final_url, "status": "marketing_page", "title": page.title})
                continue
            page_jobs = self._extract_jobs_from_official_page(page, enterprise) if self._looks_like_recruitment_page(page) else []
            discovered_job_count += len(page_jobs)
            kept = 0
            for job in page_jobs:
                skip, _reason = should_skip_job(job, settings, last_action="")
                if skip:
                    continue
                if role_tokens and not self._matches_role_tokens(job, role_tokens):
                    continue
                if not is_job_quality_acceptable(job):
                    continue
                self._merge_job_candidate(jobs_by_key, job)
                kept += 1
            kept_job_count += kept
            next_links = self._extract_official_follow_links(page.text, page.final_url, root)
            for link in self._extract_platform_targets(page.text, page.final_url):
                if link not in next_links:
                    next_links.append(link)
            for link in next_links:
                if link not in visited and link not in queue:
                    queue.append(link)
            reports.append(
                {
                    "official_url": page.final_url,
                    "title": page.title,
                    "raw_job_count": len(page_jobs),
                    "kept_job_count": kept,
                    "follow_link_count": len(next_links),
                }
            )
        return {
            "jobs": list(jobs_by_key.values()),
            "reports": reports,
            "discovered_job_count": discovered_job_count,
            "kept_job_count": kept_job_count,
            "official_page_count": len(visited),
        }

    def _extract_jobs_from_official_page(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        soup = BeautifulSoup(page.text, "html.parser")
        jobs_by_key: dict[str, JobPosting] = {}
        for job in self._extract_jobs_from_host_specific_patterns(page, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        for job in self._extract_jobs_from_embedded_json(page, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        for job in self._extract_jobs_from_json_ld(page, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        for job in self._extract_jobs_from_tables(soup, page, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        page_job = self._build_page_level_job(page, soup, enterprise)
        if page_job:
            self._merge_job_candidate(jobs_by_key, page_job)
        for job in self._extract_jobs_from_text_blocks(page, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        for job in self._extract_jobs_from_anchors(soup, page.final_url, enterprise):
            self._merge_job_candidate(jobs_by_key, job)
        return list(jobs_by_key.values())

    def _extract_jobs_from_embedded_json(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        soup = BeautifulSoup(page.text, "html.parser")
        jobs_by_key: dict[str, JobPosting] = {}
        for payload in self._iter_embedded_json_payloads(soup):
            for item in self._iter_job_like_items(payload):
                job = self._build_job_from_embedded_item(item, page, enterprise)
                if not job:
                    continue
                self._merge_job_candidate(jobs_by_key, job)
        return list(jobs_by_key.values())

    def _iter_embedded_json_payloads(self, soup: BeautifulSoup) -> list[dict | list]:
        payloads: list[dict | list] = []
        for script in soup.find_all("script"):
            body = (script.string or script.get_text() or "").strip()
            if not body:
                continue
            parsed = self._parse_script_json_payload(body)
            if parsed is None:
                continue
            payloads.append(parsed)
        return payloads

    def _parse_script_json_payload(self, body: str):
        candidates = [body.strip()]
        match = JSON_ASSIGNMENT_PATTERN.search(body.strip())
        if match:
            candidates.append(match.group("body").strip())
        for candidate in candidates:
            if not candidate or candidate in {"{}", "[]"}:
                continue
            if not candidate.startswith("{") and not candidate.startswith("["):
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _iter_job_like_items(self, payload, depth: int = 0):
        if depth > 8:
            return
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_job_like_items(item, depth + 1)
            return
        if not isinstance(payload, dict):
            return
        if self._looks_like_job_dict(payload):
            yield payload
        for value in payload.values():
            yield from self._iter_job_like_items(value, depth + 1)

    def _looks_like_job_dict(self, item: dict) -> bool:
        title = self._first_text(
            item,
            [
                "jobName",
                "positionName",
                "postName",
                "title",
                "name",
                "jobTitle",
                "recruitName",
            ],
        )
        if not title or not self._is_candidate_job_title(title):
            return False
        signals = [
            self._first_text(item, ["jobCity", "city", "cityName", "workCity", "location"]),
            self._first_text(item, ["description", "jobDescription", "responsibility", "jd", "content"]),
            self._first_text(item, ["requirement", "jobRequire", "qualification", "qualifications"]),
            self._first_text(item, ["applyUrl", "positionUrl", "detailUrl", "pcUrl", "url"]),
        ]
        return any(bool(value) for value in signals)

    def _build_job_from_embedded_item(self, item: dict, page: _FetchedPage, enterprise: dict) -> JobPosting | None:
        title = self._first_text(
            item,
            [
                "jobName",
                "positionName",
                "postName",
                "title",
                "name",
                "jobTitle",
                "recruitName",
            ],
        )
        if not title or not self._is_candidate_job_title(title):
            return None
        city = self._first_text(item, ["jobCity", "city", "cityName", "workCity", "location"])
        description = self._join_text_values(
            [
                self._first_text(item, ["description", "jobDescription", "jd", "content", "responsibility"]),
                self._first_text(item, ["requirement", "jobRequire", "qualification", "qualifications"]),
                self._first_text(item, ["degree", "degreeRequirement", "education", "educationName"]),
            ]
        )
        if not description:
            return None
        raw_url = self._first_text(item, ["applyUrl", "positionUrl", "detailUrl", "pcUrl", "url", "linkUrl"])
        normalized_url = self._normalize_official_url(urljoin(page.final_url, raw_url)) if raw_url else page.final_url
        status_text = self._join_text_values(
            [
                description,
                self._first_text(item, ["status", "jobStatus", "statusName", "recruitStatus"]),
            ]
        )
        raw = {
            "url": normalized_url or page.final_url,
            "apply_url": normalized_url or page.final_url,
            "title": title,
            "company_name": enterprise.get("company_name", ""),
            "city": city,
            "source_job_id": self._first_text(item, ["jobId", "positionId", "postId", "id", "recruitId"]),
            "description": description,
            "application_status": self._infer_page_status(status_text),
            "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
            "detail_fetched": True,
            "raw_payload": {
                "enterprise_url": enterprise.get("enterprise_url", ""),
                "batch_text": enterprise.get("batch_text", ""),
                "official_pattern": "embedded-json",
            },
        }
        job = normalize_job_fields(raw, source=self.name)
        if not job.city and enterprise.get("cities"):
            job.city_list = enterprise["cities"][:]
            job.city = job.city_list[0]
            job.ensure_ids()
        return job

    def _extract_jobs_from_tables(self, soup: BeautifulSoup, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for row in soup.find_all("tr"):
            cells = [self._compact_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
            cells = [cell for cell in cells if cell]
            if len(cells) < 2:
                continue
            title = next((cell for cell in cells if self._is_candidate_job_title(cell)), "")
            if not title:
                continue
            description = "\n".join(cells[:10])
            raw = {
                "url": page.final_url,
                "apply_url": page.final_url,
                "title": title,
                "company_name": enterprise.get("company_name", ""),
                "city": self._find_cities(description)[0] if self._find_cities(description) else "",
                "description": description,
                "application_status": self._infer_page_status(description),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "official_pattern": "table-row",
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if not job.city and enterprise.get("cities"):
                job.city_list = enterprise["cities"][:]
                job.city = job.city_list[0]
                job.ensure_ids()
            jobs.append(job)
        return jobs

    def _extract_jobs_from_host_specific_patterns(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        host = self._root_host(page.final_url)
        jobs: dict[str, JobPosting] = {}
        if host.endswith("spdb.com.cn"):
            for job in self._extract_jobs_from_spdb_rows(page, enterprise):
                jobs[job.fingerprint] = job
        if host.endswith("talent.baidu.com") or host.endswith("baidu.com"):
            for job in self._extract_jobs_from_baidu_list(page, enterprise):
                jobs[job.fingerprint] = job
        if host.endswith("zhaopin.com"):
            for job in self._extract_jobs_from_zhaopin_site(page, enterprise):
                jobs[job.fingerprint] = job
        return list(jobs.values())

    def _extract_jobs_from_zhaopin_site(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        bundles: list[_FetchedPage] = []
        if page.final_url.lower().endswith(".js") or "javascript" in page.content_type.lower():
            bundles.append(page)
        else:
            for asset_url in self._extract_same_site_script_assets(page.text, page.final_url, self._root_host(page.final_url))[:4]:
                try:
                    bundles.append(self._fetch_page(asset_url))
                except Exception:
                    continue
        jobs_by_key: dict[str, JobPosting] = {}
        for bundle in bundles:
            for job in self._extract_jobs_from_zhaopin_bundle(bundle, enterprise):
                self._merge_job_candidate(jobs_by_key, job)
        return list(jobs_by_key.values())

    def _extract_jobs_from_zhaopin_bundle(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        text = page.text
        xiaozhao_id_match = ZHAOPIN_XIAOZHAO_ID_PATTERN.search(text)
        if not xiaozhao_id_match:
            return []
        xiaozhao_id = self._compact_text(xiaozhao_id_match.group("value"))
        scene_match = ZHAOPIN_SCENE_PATTERN.search(text)
        scene = self._compact_text(scene_match.group("value")) if scene_match else "cam"
        department_match = ZHAOPIN_DEPARTMENT_PATTERN.search(text)
        custom_tags_match = ZHAOPIN_CUSTOM_TAGS_PATTERN.search(text)
        payload = {
            "orgNumbers": [xiaozhao_id],
            "jobSource": 2 if scene == "cam" else 1,
            "pageIndex": 1,
            "pageSize": 100,
            "workRegionIds": "",
            "jobTypes": "",
            "priorityMajors": "",
            "keyword": "",
        }
        if department_match:
            payload["orgDepartmentIds"] = int(department_match.group("value"))
        if custom_tags_match and custom_tags_match.group("value").strip():
            payload["customTags"] = custom_tags_match.group("value").strip()
        try:
            response = self._post_json("https://fe.zhaopin.com/grace/api/dsc/search-job-list", payload)
        except Exception:
            return []
        if int(response.get("code", 0) or 0) != 200:
            return []
        job_list = ((response.get("data") or {}).get("jobList") or [])
        jobs: list[JobPosting] = []
        for item in job_list:
            if not isinstance(item, dict):
                continue
            company = item.get("company") or {}
            job = item.get("job") or {}
            title = self._compact_text(job.get("title", ""))
            if not self._is_candidate_job_title(title):
                continue
            description = self._compact_text(
                BeautifulSoup(str(job.get("detail", "")), "html.parser").get_text("\n", strip=True)
            )
            description = self._join_text_values(
                [
                    title,
                    f"工作地点：{self._compact_text(job.get('cityName', ''))}{self._compact_text(job.get('districtName', ''))}",
                    description,
                ]
            )
            raw = {
                "url": self._compact_text(job.get("deliveryPath", "")) or page.requested_url,
                "apply_url": self._compact_text(job.get("deliveryPath", "")) or page.requested_url,
                "title": title,
                "company_name": self._compact_text(
                    company.get("campusOrgShortName")
                    or company.get("campusOrgName")
                    or company.get("slaveDisplayOrgName")
                    or enterprise.get("company_name", "")
                ),
                "city": self._compact_text(job.get("cityName", "")),
                "source_job_id": str(job.get("id", "")).strip(),
                "description": description,
                "application_status": self._infer_page_status(description),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "official_pattern": "zhaopin-api",
                    "xiaozhao_id": xiaozhao_id,
                    "scene": scene,
                },
            }
            normalized = normalize_job_fields(raw, source=self.name)
            if not normalized.city and enterprise.get("cities"):
                normalized.city_list = enterprise["cities"][:]
                normalized.city = normalized.city_list[0]
                normalized.ensure_ids()
            jobs.append(normalized)
        return jobs

    def _extract_jobs_from_spdb_rows(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for line in self._visible_lines(page.text):
            match = SPDB_ROW_PATTERN.match(line)
            if not match:
                continue
            title = self._compact_text(match.group("title"))
            if not self._is_candidate_job_title(title):
                continue
            raw = {
                "url": page.final_url,
                "apply_url": page.final_url,
                "title": title,
                "company_name": enterprise.get("company_name", ""),
                "city": self._compact_text(match.group("city").replace("，", "/").replace("、", "/").replace(",", "/")),
                "description": "\n".join(
                    [
                        title,
                        f"所属机构：{self._compact_text(match.group('org'))}",
                        f"学历要求：{self._compact_text(match.group('degree'))}",
                        f"招聘人数：{self._compact_text(match.group('count'))}",
                        f"工作地点：{self._compact_text(match.group('city'))}",
                        "公开官网岗位列表",
                    ]
                ),
                "application_status": self._infer_page_status(line),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "official_pattern": "spdb-row",
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if not job.city and enterprise.get("cities"):
                job.city_list = enterprise["cities"][:]
                job.city = job.city_list[0]
                job.ensure_ids()
            jobs.append(job)
        return jobs

    def _extract_jobs_from_baidu_list(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        lines = self._visible_lines(page.text)
        jobs: list[JobPosting] = []
        current_title = ""
        current_city = ""
        current_job_id = ""
        block: list[str] = []

        def flush() -> None:
            nonlocal current_title, current_city, current_job_id, block
            if not current_title:
                return
            description = "\n".join(block[:10]).strip()
            if not description:
                description = current_title
            raw = {
                "url": page.final_url,
                "apply_url": page.final_url,
                "title": current_title,
                "company_name": enterprise.get("company_name", ""),
                "city": current_city,
                "source_job_id": current_job_id,
                "description": description,
                "application_status": self._infer_page_status(description),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "official_pattern": "baidu-list",
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if not job.city and enterprise.get("cities"):
                job.city_list = enterprise["cities"][:]
                job.city = job.city_list[0]
                job.ensure_ids()
            jobs.append(job)
            current_title = ""
            current_city = ""
            current_job_id = ""
            block = []

        for line in lines:
            match = BAIDU_JOB_LINE_PATTERN.match(line)
            if match:
                flush()
                current_city = self._compact_text(match.group("city").split("/")[0])
                current_title = self._compact_text(match.group("title"))
                current_job_id = self._compact_text(match.group("job_id"))
                block = [line]
                continue
            if not current_title:
                continue
            if len(block) < 12:
                block.append(line)
        flush()
        return [job for job in jobs if self._is_candidate_job_title(job.title)]

    def _extract_jobs_from_json_ld(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for match in JSON_LD_JOB_PATTERN.finditer(page.text):
            body = match.group("body").strip()
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("@type", "")).lower() != "jobposting":
                    continue
                title = self._compact_text(item.get("title", ""))
                description = self._compact_text(BeautifulSoup(str(item.get("description", "")), "html.parser").get_text(" ", strip=True))
                if not title or not description:
                    continue
                raw = {
                    "url": self._normalize_official_url(item.get("url") or page.final_url) or page.final_url,
                    "apply_url": self._normalize_official_url(item.get("url") or page.final_url) or page.final_url,
                    "title": title,
                    "company_name": enterprise.get("company_name", ""),
                    "city": self._compact_text(item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")),
                    "description": description,
                    "deadline": self._compact_text(item.get("validThrough", "")),
                    "application_status": "open",
                    "detail_fetched": True,
                    "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                    "raw_payload": {
                        "enterprise_url": enterprise.get("enterprise_url", ""),
                        "schedule_context": enterprise.get("schedule_context", ""),
                        "batch_text": enterprise.get("batch_text", ""),
                    },
                }
                job = normalize_job_fields(raw, source=self.name)
                if not job.city and enterprise.get("cities"):
                    job.city_list = enterprise["cities"][:]
                    job.city = job.city_list[0]
                    job.ensure_ids()
                jobs.append(job)
        return jobs

    def _build_page_level_job(self, page: _FetchedPage, soup: BeautifulSoup, enterprise: dict) -> JobPosting | None:
        title = self._best_title(soup, page.title)
        description = self._best_description(soup)
        if not title or not description:
            return None
        if not JOB_TITLE_HINT.search(title):
            return None
        if not any(token in description for token in OPEN_HINTS):
            return None
        lowered = f"{title}\n{description}".lower()
        if any(token in lowered for token in ["首页", "关于我们", "公司简介"]) and not any(token in lowered for token in ["岗位职责", "职位描述", "任职要求"]):
            return None
        raw = {
            "url": page.final_url,
            "apply_url": page.final_url,
            "title": title,
            "company_name": enterprise.get("company_name", ""),
            "city": self._find_cities(description)[0] if self._find_cities(description) else "",
            "description": description,
            "application_status": self._infer_page_status(description),
            "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
            "detail_fetched": True,
            "raw_payload": {
                "enterprise_url": enterprise.get("enterprise_url", ""),
                "official_origin_url": page.requested_url,
                "batch_text": enterprise.get("batch_text", ""),
            },
        }
        job = normalize_job_fields(raw, source=self.name)
        if not job.city and enterprise.get("cities"):
            job.city_list = enterprise["cities"][:]
            job.city = job.city_list[0]
            job.ensure_ids()
        return job

    def _extract_jobs_from_anchors(self, soup: BeautifulSoup, base_url: str, enterprise: dict) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for anchor in soup.find_all("a", href=True):
            title = self._compact_text(anchor.get_text(" ", strip=True))
            if len(title) < 4 or len(title) > 80:
                continue
            if not self._is_candidate_job_title(title):
                continue
            href = self._normalize_official_url(urljoin(base_url, anchor.get("href", "")))
            if not href:
                continue
            container = anchor
            for _ in range(2):
                if container.parent is None:
                    break
                container = container.parent
            context = self._compact_text(container.get_text(" ", strip=True))
            if not any(token in context for token in ["工作地点", "岗位职责", "任职要求", "职位描述", "申请", "投递", "招聘", "职位"]):
                continue
            description = context if len(context) >= len(title) + 12 else f"{title}\n{context}"
            raw = {
                "url": href,
                "apply_url": href,
                "title": title,
                "company_name": enterprise.get("company_name", ""),
                "city": self._find_cities(context)[0] if self._find_cities(context) else "",
                "description": description,
                "application_status": self._infer_page_status(context),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "schedule_context": enterprise.get("schedule_context", ""),
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if not job.city and enterprise.get("cities"):
                job.city_list = enterprise["cities"][:]
                job.city = job.city_list[0]
                job.ensure_ids()
            jobs.append(job)
        return jobs

    def _extract_jobs_from_text_blocks(self, page: _FetchedPage, enterprise: dict) -> list[JobPosting]:
        lines = self._visible_lines(page.text)
        if len(lines) < 4:
            return []
        jobs: list[JobPosting] = []
        current_title = ""
        block: list[str] = []

        def flush() -> None:
            nonlocal current_title, block
            if not current_title or not block:
                current_title = ""
                block = []
                return
            description = "\n".join(block[:16]).strip()
            if len(description) < 24:
                current_title = ""
                block = []
                return
            if not any(token in description for token in OPEN_HINTS + ["工作地点", "学历", "专业", "职责", "要求"]):
                current_title = ""
                block = []
                return
            raw = {
                "url": page.final_url,
                "apply_url": page.final_url,
                "title": current_title,
                "company_name": enterprise.get("company_name", ""),
                "city": self._find_cities(description)[0] if self._find_cities(description) else "",
                "description": description,
                "application_status": self._infer_page_status(description),
                "job_type": self._infer_job_type_from_batch(enterprise.get("batch_text", "")),
                "detail_fetched": True,
                "raw_payload": {
                    "enterprise_url": enterprise.get("enterprise_url", ""),
                    "batch_text": enterprise.get("batch_text", ""),
                    "official_pattern": "text-block",
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if not job.city and enterprise.get("cities"):
                job.city_list = enterprise["cities"][:]
                job.city = job.city_list[0]
                job.ensure_ids()
            jobs.append(job)
            current_title = ""
            block = []

        for line in lines:
            if self._is_candidate_job_title(line):
                flush()
                current_title = line
                block = [line]
                continue
            if current_title and len(block) < 18:
                block.append(line)
        flush()
        return jobs

    def _extract_official_follow_links(self, html: str, base_url: str, root_host: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        scored: list[tuple[int, str]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = self._normalize_official_url(urljoin(base_url, anchor.get("href", "")))
            if not href:
                continue
            if not self._same_site_family(self._root_host(href), root_host):
                continue
            text = self._compact_text(anchor.get_text(" ", strip=True))
            score = self._score_follow_link(href, text, root_host)
            if score <= 0:
                continue
            if href in seen:
                continue
            seen.add(href)
            scored.append((score, href))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [href for _score, href in scored[:8]]

    def _extract_platform_targets(self, html: str, base_url: str) -> list[str]:
        host = self._root_host(base_url)
        targets: list[str] = []
        if host.endswith("hotjob.cn"):
            hotjob_target = self._resolve_hotjob_entry(base_url)
            if hotjob_target:
                targets.append(hotjob_target)
        if host.endswith("jobs.feishu.cn") or host.endswith("feishu.cn"):
            targets.extend(self._extract_feishu_internal_targets(html, base_url))
        return self._dedupe_urls(targets)

    def _resolve_hotjob_entry(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        root_url = f"{parsed.scheme}://{parsed.netloc}/"
        try:
            payload = self._post_json(
                f"{parsed.scheme}://{parsed.netloc}/wecruit/common/getSLD",
                {"sld": parsed.netloc},
            )
        except Exception:
            return ""
        link = self._compact_text((((payload.get("data") or {}).get("linkData") or {}).get("link")) or "")
        return self._normalize_official_url(link or root_url) or ""

    def _extract_feishu_internal_targets(self, html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", id="js-websiteInfo")
        if not script:
            return []
        body = (script.string or script.get_text() or "").strip()
        if not body:
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return []
        website_info = payload.get("website_info") or {}
        path = self._compact_text(website_info.get("path", ""))
        targets: list[str] = []
        if path:
            targets.append(urljoin(base_url, f"/{path}/position/list"))
            targets.append(urljoin(base_url, f"/{path}/campus/position/list"))
        targets.append(urljoin(base_url, "/index/position/list"))
        for child in website_info.get("children_website_info") or []:
            if not isinstance(child, dict):
                continue
            child_path = self._compact_text(child.get("website_path", ""))
            if child_path:
                targets.append(urljoin(base_url, f"/{child_path}/position/list"))
                targets.append(urljoin(base_url, f"/{child_path}/campus/position/list"))
        return self._dedupe_urls(self._normalize_official_url(target) or "" for target in targets)

    def _extract_same_site_script_assets(self, html: str, base_url: str, root_host: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        assets: list[str] = []
        for script in soup.find_all("script", src=True):
            href = self._normalize_official_url(urljoin(base_url, script.get("src", "")))
            if not href:
                continue
            if not self._same_site_family(self._root_host(href), root_host):
                continue
            if not href.lower().endswith(".js"):
                continue
            assets.append(href)
        for link in soup.find_all("link", href=True):
            href = self._normalize_official_url(urljoin(base_url, link.get("href", "")))
            if not href:
                continue
            if not self._same_site_family(self._root_host(href), root_host):
                continue
            if not href.lower().endswith(".js"):
                continue
            assets.append(href)
        return self._dedupe_urls(assets)

    def _looks_blocked(self, page: _FetchedPage) -> bool:
        lowered = f"{page.title}\n{page.text[:2000]}".lower()
        return page.status_code in {401, 403, 429} or "验证" in lowered or "访问受限" in lowered

    def _normalize_enterprise_url(self, href: str, base_url: str) -> str:
        if not href:
            return ""
        absolute = self._unwrap_target_url(href, base_url)
        parsed = urlparse(absolute)
        path_match = re.search(r"/enterprise/(?P<enterprise_id>\d+)", parsed.path)
        if path_match and parsed.netloc.endswith("nowcoder.com"):
            return f"https://api-cdn.nowcoder.com/enterprise/{path_match.group('enterprise_id')}"
        match = ENTERPRISE_URL_PATTERN.search(absolute)
        if not match:
            return ""
        return match.group(0)

    def _unwrap_target_url(self, href: str, base_url: str) -> str:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc.endswith("nowcoder.com") and parsed.path == "/jump":
            target = parse_qs(parsed.query).get("url", [""])[0].strip()
            if target:
                return unquote(target)
        return absolute

    def _normalize_official_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        host = parsed.netloc.lower()
        if not host:
            return ""
        if "nowcoder.com" in host:
            return ""
        if host.endswith("mp.weixin.qq.com"):
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}" + (f"?{parsed.query}" if parsed.query else "")
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}" + (f"?{parsed.query}" if parsed.query else "")

    def _looks_like_official_entry(self, text: str, url: str) -> bool:
        lowered = f"{text} {url}".lower()
        return any(keyword.lower() in lowered for keyword in OFFICIAL_LINK_TEXT_KEYWORDS + OFFICIAL_LINK_URL_KEYWORDS)

    def _infer_company_name_from_schedule(self, context: str, anchor_text: str) -> str:
        for line in [anchor_text, *(segment.strip() for segment in re.split(r"\s+", context) if segment.strip())]:
            if line and len(line) <= 30 and not any(token in line for token in ["官网", "投递", "查看", "立即"]):
                if line in {"收藏", "关注", "查看详情"}:
                    continue
                return line
        return anchor_text.strip()

    def _infer_batch_text(self, context: str) -> str:
        for token in ["春招", "秋招", "实习", "校招", "校园招聘"]:
            match = re.search(rf"[0-9]{{2,4}}届?[^\n ]*{token}|{token}[^\n ]*", context)
            if match:
                return self._compact_text(match.group(0))
        return ""

    def _infer_job_type_from_batch(self, batch_text: str) -> str:
        lowered = batch_text.lower()
        if "社招" in batch_text or "社会招聘" in batch_text:
            return "社招"
        if any(token in lowered for token in ["实习", "校招", "春招", "秋招"]):
            return "校招"
        return "校招"

    def _infer_page_status(self, text: str) -> str:
        if any(token in text for token in PENDING_HINTS):
            return "pending"
        if any(token in text for token in CLOSED_HINTS):
            return "closed"
        return "open"

    def _role_tokens(self, settings: UserSettings, profile: ResumeProfile | None) -> list[str]:
        tokens = settings.preferred_roles[:6]
        if not tokens and profile:
            tokens = profile.target_roles[:6]
        return [token.strip() for token in tokens if token.strip()]

    def _matches_role_tokens(self, job: JobPosting, role_tokens: list[str]) -> bool:
        if not role_tokens:
            return True
        text = "\n".join([job.title, job.description]).lower()
        return any(token.lower() in text for token in role_tokens if token)

    def _best_title(self, soup: BeautifulSoup, fallback: str) -> str:
        for selector in ["h1", "title", "h2"]:
            node = soup.select_one(selector)
            if not node:
                continue
            value = self._compact_text(node.get_text(" ", strip=True))
            if value:
                return value
        return self._compact_text(fallback)

    def _best_description(self, soup: BeautifulSoup) -> str:
        for tag_name in ["script", "style", "noscript", "svg"]:
            for node in soup.find_all(tag_name):
                node.decompose()
        lines = []
        seen = set()
        for raw in soup.get_text("\n", strip=True).splitlines():
            line = self._compact_text(raw)
            if not line or line in seen:
                continue
            seen.add(line)
            lines.append(line)
        markers = ["职位描述", "岗位职责", "工作职责", "岗位要求", "任职要求", "职责描述", "任职资格"]
        start = 0
        for index, line in enumerate(lines):
            if any(marker in line for marker in markers):
                start = index
                break
        selected = lines[start : start + 80] if lines else []
        return "\n".join(selected)[:5000].strip()

    def _visible_lines(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        for tag_name in ["script", "style", "noscript", "svg"]:
            for node in soup.find_all(tag_name):
                node.decompose()
        lines: list[str] = []
        seen: set[str] = set()
        for raw in soup.get_text("\n", strip=True).splitlines():
            line = self._compact_text(raw)
            if not line or line in seen:
                continue
            seen.add(line)
            lines.append(line)
        return lines

    def _is_candidate_job_title(self, text: str) -> bool:
        value = self._compact_text(text)
        if len(value) < 4 or len(value) > 80:
            return False
        if any(token in value for token in BAD_JOB_TITLE_TOKENS):
            return False
        if any(value.startswith(prefix) for prefix in BAD_JOB_TITLE_PREFIXES):
            return False
        if any(token in value for token in ["学历", "专业不限", "优先考虑", "点击相应城市投递简历"]):
            return False
        if len(value) > 18 and any(symbol in value for symbol in "，。；："):
            return False
        if value.isdigit():
            return False
        return bool(JOB_TITLE_HINT.search(value))

    def _same_site_family(self, host: str, root_host: str) -> bool:
        def normalize(value: str) -> str:
            value = value.lower().strip()
            return value[4:] if value.startswith("www.") else value

        def family(value: str) -> str:
            parts = normalize(value).split(".")
            if len(parts) <= 2:
                return ".".join(parts)
            return ".".join(parts[-2:])

        normalized_host = normalize(host)
        normalized_root = normalize(root_host)
        if normalized_host == normalized_root:
            return True
        host_family = family(normalized_host)
        root_family = family(normalized_root)
        if host_family != root_family:
            return False
        if root_family in STRICT_EXACT_HOST_FAMILIES:
            return False
        return True

    def _score_follow_link(self, href: str, text: str, root_host: str) -> int:
        lowered = f"{href} {text}".lower()
        score = 0
        for keyword in OFFICIAL_LINK_URL_KEYWORDS:
            if keyword in lowered:
                score += 2
        if any(keyword in text for keyword in ["职位", "岗位", "校招", "实习", "招聘"]):
            score += 3
        path = urlparse(href).path.lower()
        if root_host.endswith("51job.com"):
            if path.endswith("job.html") or "/job" in path:
                score += 6
            if "about" in path:
                score += 1
        if root_host.endswith("zhiye.com"):
            if any(token in path for token in ["jobs", "detail", "position", "zpdetail"]):
                score += 6
        if root_host.endswith("spdb.com.cn"):
            if any(token in path for token in ["campusjob", "internjob", "socialjob"]):
                score += 5
        if root_host.endswith("baidu.com"):
            if "/jobs/" in path:
                score += 4
        return score

    def _best_response_encoding(self, response: requests.Response) -> str:
        encoding = (response.encoding or "").strip()
        apparent = (getattr(response, "apparent_encoding", "") or "").strip()
        if not encoding:
            return apparent or "utf-8"
        sample = response.content[:1200].decode(encoding, errors="ignore")
        if "å" in sample or "Ã" in sample:
            return apparent or encoding
        return encoding

    def _merge_job_candidate(self, jobs_by_key: dict[str, JobPosting], job: JobPosting) -> None:
        key = stable_hash(job.url, job.company_name, job.title, job.city or "/".join(job.city_list))
        existing = jobs_by_key.get(key)
        if existing is None or len(job.description) > len(existing.description):
            jobs_by_key[key] = job

    def _looks_like_platform_marketing_page(self, page: _FetchedPage, enterprise: dict) -> bool:
        host = self._root_host(page.final_url)
        if host not in {"mokahr.com", "www.mokahr.com"}:
            return False
        text = self._compact_text(f"{page.title}\n{page.text[:3000]}").lower()
        company_name = self._compact_text(enterprise.get("company_name", "")).lower()
        if company_name and company_name in text:
            return False
        return any(token in text for token in PLATFORM_MARKETING_HINTS)

    def _looks_like_recruitment_page(self, page: _FetchedPage) -> bool:
        lowered = self._compact_text(f"{page.final_url}\n{page.title}\n{page.text[:3000]}").lower()
        return any(token in lowered for token in RECRUITMENT_PAGE_HINTS)

    def _find_cities(self, text: str) -> list[str]:
        seen: list[str] = []
        for value in CITY_PATTERN.findall(text or ""):
            if value not in seen:
                seen.append(value)
        return seen

    def _root_host(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        return self._compact_text(match.group(1)) if match else ""

    def _first_text(self, payload: dict, keys: list[str]) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str):
                compact = self._compact_text(value)
                if compact:
                    return compact
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, dict):
                compact = self._join_text_values(value.values())
                if compact:
                    return compact
        return ""

    def _join_text_values(self, values) -> str:
        parts: list[str] = []
        for value in values:
            if isinstance(value, str):
                compact = self._compact_text(value)
                if compact:
                    parts.append(compact)
            elif isinstance(value, (int, float)):
                parts.append(str(value))
            elif isinstance(value, dict):
                nested = self._join_text_values(value.values())
                if nested:
                    parts.append(nested)
            elif isinstance(value, list):
                nested = self._join_text_values(value)
                if nested:
                    parts.append(nested)
        deduped: list[str] = []
        seen: set[str] = set()
        for part in parts:
            if part in seen:
                continue
            seen.add(part)
            deduped.append(part)
        return "\n".join(deduped)

    def _extract_initial_state(self, html: str) -> dict:
        match = INITIAL_STATE_PATTERN.search(html)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def _dedupe_urls(self, urls) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in urls:
            url = self._compact_text(str(value or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            result.append(url)
        return result

    def _write_debug_snapshot(self, kind: str, payload: dict) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.debug_dir / f"nowcoder_schedule_{kind}_{timestamp}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            latest_path = self.debug_dir / f"latest-nowcoder-schedule-{kind}.json"
            latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return
