from __future__ import annotations

import json
import re
import time
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..matching import should_skip_job
from ..normalization import is_job_quality_acceptable, normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource, SourceHaltError

DEFAULT_SEED_URLS = [
    "https://www.nowcoder.com/jobs/recommend/campus",
]
CANDIDATE_PATH_PATTERNS = (
    re.compile(r"^/jobs/detail/\d+$"),
    re.compile(r"^/job/\d+$"),
    re.compile(r"^/careers/[^/]+/\d+$"),
    re.compile(r"^/jobs/hr/\d+$"),
    re.compile(r"^/jobs/company-project$"),
    re.compile(r"^/jobs/recommend/campus$"),
)
ABSOLUTE_CANDIDATE_URL_PATTERN = re.compile(
    r"https?://www\.nowcoder\.com(?:/jobs/detail/\d+|/job/\d+|/careers/[^/]+/\d+|/jobs/hr/\d+|/jobs/company-project(?:\?[^\"'\s<]+)?|/jobs/recommend/campus(?:\?[^\"'\s<]+)?)",
    re.IGNORECASE,
)
RELATIVE_CANDIDATE_URL_PATTERN = re.compile(
    r"(?:/jobs/detail/\d+|/job/\d+|/careers/[^/]+/\d+|/jobs/hr/\d+|/jobs/company-project(?:\?[^\"'\s<]+)?|/jobs/recommend/campus(?:\?[^\"'\s<]+)?)",
    re.IGNORECASE,
)
NOWCODER_NAV_TOKENS = [
    "首页",
    "题库",
    "公司真题",
    "专项练习",
    "面试题库",
    "在线编程",
    "面试",
    "面试经验",
    "简历",
    "求职",
    "学习",
    "基础学习课",
    "实战项目课",
    "求职辅导课",
    "专栏",
    "竞赛",
    "搜索",
]
NOWCODER_BLOCK_TOKENS = [
    "访问受限",
    "安全验证",
    "验证码",
    "请稍候",
    "请先登录",
    "登录牛客",
    "扫码登录",
    "异常行为",
]
NOWCODER_PENDING_TOKENS = ["待上线", "即将上线", "暂未开放", "敬请期待", "即将开放", "待开放"]
NOWCODER_CLOSED_TOKENS = ["已结束", "已截止", "招聘结束", "结束招聘", "停止投递"]
NOWCODER_OPEN_TOKENS = ["立即投递", "立即申请", "申请职位"]
NOWCODER_SALARY_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*[-~至]\s*\d+(?:\.\d+)?\s*(?:K|k|千|万|元)(?:\s*[*/xX×·]\s*\d+\s*薪)?"
)
NOWCODER_RECRUIT_TYPE_MAP = {
    1: "校招",
    2: "校招",
    3: "社招",
}
NOWCODER_CITY_PATTERN = re.compile(r"(北京|上海|广州|深圳|杭州|成都|贵阳|武汉|南京|西安|苏州|重庆|天津|长沙|厦门|青岛)")


class NowcoderDirectSource(JobSource):
    def __init__(
        self,
        name: str,
        *,
        seed_urls: list[str] | None = None,
        max_seed_pages: int = 2,
        max_detail_pages: int = 40,
        max_jobs: int = 40,
        max_queries: int = 6,
        request_timeout_sec: int = 20,
        throttle_seconds: float = 1.6,
        max_consecutive_seed_anomalies: int = 2,
        max_consecutive_detail_anomalies: int = 2,
        search_endpoint: str = "https://www.bing.com/search",
        search_enabled: bool = False,
        debug_dir: Path | None = None,
    ):
        super().__init__(name)
        self.seed_urls = [url.strip() for url in (seed_urls or DEFAULT_SEED_URLS) if url.strip()]
        self.max_seed_pages = max(1, max_seed_pages)
        self.max_detail_pages = max(1, max_detail_pages)
        self.max_jobs = max(1, max_jobs)
        self.max_queries = max(1, max_queries)
        self.request_timeout_sec = max(5, request_timeout_sec)
        self.throttle_seconds = max(0.8, throttle_seconds)
        self.max_consecutive_seed_anomalies = max(1, max_consecutive_seed_anomalies)
        self.max_consecutive_detail_anomalies = max(1, max_consecutive_detail_anomalies)
        self.search_endpoint = search_endpoint.strip() or "https://www.bing.com/search"
        self.search_enabled = bool(search_enabled)
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
        role_tokens = self._role_tokens(settings, profile)
        city_tokens = self._city_tokens(settings, profile)
        detail_candidates: dict[str, dict] = {}
        seed_reports: list[dict] = []
        detail_reports: list[dict] = []
        consecutive_seed_anomalies = 0
        prefilter_report = {
            "candidate_rejected": {},
            "candidate_rejected_examples": {},
            "job_rejected": {},
            "job_rejected_examples": {},
        }

        if self.search_enabled:
            for query in self._build_queries(settings, profile)[: self.max_queries]:
                try:
                    page = self._fetch_search_page(query)
                    candidates = self._extract_detail_candidates(page.text, page.final_url)
                    accepted = 0
                    for candidate in candidates:
                        reject, reason = self._should_reject_candidate(candidate, settings)
                        if reject:
                            self._bump_prefilter(prefilter_report, "candidate", reason, candidate["context"], candidate["url"])
                            continue
                        url = candidate["url"]
                        previous = detail_candidates.get(url)
                        candidate["priority"] = self._candidate_priority(candidate["context"], url, role_tokens, city_tokens)
                        if previous and previous["priority"] >= candidate["priority"]:
                            continue
                        detail_candidates[url] = candidate
                        accepted += 1
                    seed_reports.append(
                        {
                            "type": "search",
                            "query": query,
                            "url": page.final_url,
                            "status_code": page.status_code,
                            "candidate_count": len(candidates),
                            "accepted_count": accepted,
                            "title": page.title,
                            "body_excerpt": self._compact_text(page.text[:400]),
                        }
                    )
                except Exception as exc:
                    seed_reports.append(
                        {
                            "type": "search",
                            "query": query,
                            "error": str(exc),
                        }
                    )

        for seed_url in self.seed_urls[: self.max_seed_pages]:
            accepted = 0
            direct_seed_url = self._normalize_detail_url(seed_url, seed_url)
            if direct_seed_url and urlparse(direct_seed_url).path != "/jobs/recommend/campus":
                detail_candidates.setdefault(
                    direct_seed_url,
                    {
                        "url": direct_seed_url,
                        "context": "direct_seed",
                        "seed_url": seed_url,
                        "priority": 100,
                    },
                )
                accepted += 1
            try:
                page = self._fetch_page(seed_url)
                summary = self._page_summary(page)
                if self._looks_like_hard_block(page):
                    detail = {
                        "seed_url": seed_url,
                        "page": summary,
                        "stage": "seed",
                    }
                    self._write_debug_snapshot("halt", detail)
                    raise SourceHaltError("牛客站点返回了登录/验证/受限页面，已止损停止。", detail=detail)
                candidates = self._extract_detail_candidates(page.text, page.final_url)
                for candidate in candidates:
                    reject, reason = self._should_reject_candidate(candidate, settings)
                    if reject:
                        self._bump_prefilter(prefilter_report, "candidate", reason, candidate["context"], candidate["url"])
                        continue
                    url = candidate["url"]
                    previous = detail_candidates.get(url)
                    candidate["priority"] = self._candidate_priority(candidate["context"], url, role_tokens, city_tokens)
                    if previous and previous["priority"] >= candidate["priority"]:
                        continue
                    detail_candidates[url] = candidate
                    accepted += 1
                seed_report = {
                    "type": "seed",
                    "url": seed_url,
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "candidate_count": len(candidates),
                    "accepted_count": accepted,
                    "title": page.title,
                    "body_excerpt": self._compact_text(page.text[:400]),
                }
                seed_reports.append(seed_report)
            except SourceHaltError:
                raise
            except Exception as exc:
                accepted = 0
                seed_reports.append(
                    {
                        "type": "seed",
                        "url": seed_url,
                        "error": str(exc),
                    }
                )
            if accepted == 0:
                consecutive_seed_anomalies += 1
                if consecutive_seed_anomalies >= self.max_consecutive_seed_anomalies:
                    detail = {
                        "stage": "seed",
                        "message": "连续多个牛客 seed 页未发现官方岗位详情链接。",
                        "seed_reports": seed_reports,
                    }
                    self._write_debug_snapshot("halt", detail)
                    raise SourceHaltError("牛客 seed 页连续异常，已止损停止。", detail=detail)
            else:
                consecutive_seed_anomalies = 0

        prioritized_candidates = sorted(
            detail_candidates.values(),
            key=lambda item: (item["priority"], self._detail_numeric_id(item["url"])),
            reverse=True,
        )

        jobs: dict[str, JobPosting] = {}
        consecutive_detail_anomalies = 0
        for candidate in prioritized_candidates[: self.max_detail_pages]:
            try:
                page = self._fetch_page(candidate["url"])
                summary = self._page_summary(page)
                if self._looks_like_hard_block(page):
                    detail = {
                        "stage": "detail",
                        "candidate": candidate,
                        "page": summary,
                        "seed_reports": seed_reports,
                        "detail_reports": detail_reports,
                    }
                    self._write_debug_snapshot("halt", detail)
                    raise SourceHaltError("牛客详情页出现登录/验证/受限页面，已止损停止。", detail=detail)
                raw_jobs = self._parse_candidate_page(page, candidate)
                parsed_jobs = self._prefilter_jobs(raw_jobs, settings, role_tokens, prefilter_report)
                detail_report = {
                    "url": candidate["url"],
                    "final_url": page.final_url,
                    "status_code": page.status_code,
                    "accepted": bool(raw_jobs),
                    "accepted_count": len(parsed_jobs),
                    "raw_count": len(raw_jobs),
                    "filtered_out_count": max(0, len(raw_jobs) - len(parsed_jobs)),
                    "title": page.title,
                    "parse_debug": self._detail_debug_summary(page),
                }
                detail_reports.append(detail_report)
            except SourceHaltError:
                raise
            except Exception as exc:
                raw_jobs = []
                parsed_jobs = []
                detail_reports.append(
                    {
                        "url": candidate["url"],
                        "accepted": False,
                        "error": str(exc),
                    }
                )
            if not raw_jobs:
                consecutive_detail_anomalies += 1
                if consecutive_detail_anomalies >= self.max_consecutive_detail_anomalies:
                    detail = {
                        "stage": "detail",
                        "message": "连续多个牛客详情页解析失败。",
                        "seed_reports": seed_reports,
                        "detail_reports": detail_reports,
                    }
                    self._write_debug_snapshot("halt", detail)
                    raise SourceHaltError("牛客详情页连续异常，已止损停止。", detail=detail)
                continue
            consecutive_detail_anomalies = 0
            if not parsed_jobs:
                continue
            for job in parsed_jobs:
                jobs[job.fingerprint] = job
            if len(jobs) >= self.max_jobs:
                break

        summary = {
            "seed_reports": seed_reports,
            "candidate_count": len(detail_candidates),
            "detail_reports": detail_reports,
            "job_count": min(len(jobs), self.max_jobs),
            "prefilter": prefilter_report,
        }
        self.last_fetch_report = {
            "candidate_count": len(detail_candidates),
            "enterprise_count": 0,
            "discovered_job_count": min(len(jobs), self.max_jobs),
            "candidate_rejected": sum(prefilter_report["candidate_rejected"].values()),
            "job_rejected": sum(prefilter_report["job_rejected"].values()),
            "prefilter": prefilter_report,
        }
        self._write_debug_snapshot("fetch", summary)
        return list(jobs.values())[: self.max_jobs]

    def _fetch_page(self, url: str) -> "_FetchedPage":
        self._throttle()
        response = self._session.get(url, timeout=self.request_timeout_sec, allow_redirects=True)
        response.raise_for_status()
        text = response.text
        title = self._extract_title(text)
        return _FetchedPage(
            requested_url=url,
            final_url=response.url,
            status_code=response.status_code,
            text=text,
            title=title,
            content_type=response.headers.get("content-type", ""),
        )

    def _fetch_search_page(self, query: str) -> "_FetchedPage":
        self._throttle()
        response = self._session.get(
            self.search_endpoint,
            params={"q": query, "format": "rss"},
            timeout=self.request_timeout_sec,
            allow_redirects=True,
        )
        response.raise_for_status()
        text = response.text
        title = self._extract_title(text)
        return _FetchedPage(
            requested_url=f"{self.search_endpoint}?{urlencode({'q': query})}",
            final_url=response.url,
            status_code=response.status_code,
            text=text,
            title=title,
            content_type=response.headers.get("content-type", ""),
        )

    def _throttle(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < self.throttle_seconds:
            time.sleep(self.throttle_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _extract_detail_candidates(self, html: str, base_url: str) -> list[dict]:
        if self._looks_like_rss_payload(html):
            candidates = self._extract_rss_candidates(html, base_url)
            if candidates:
                return candidates
        soup = BeautifulSoup(html, "html.parser")
        candidates: dict[str, dict] = {}
        for anchor in soup.find_all("a", href=True):
            raw_href = anchor.get("href", "").strip()
            detail_url = self._normalize_detail_url(self._unwrap_search_result_url(raw_href, base_url), base_url)
            if not detail_url:
                continue
            context = " ".join(
                part
                for part in [
                    anchor.get_text(" ", strip=True),
                    anchor.parent.get_text(" ", strip=True)[:220] if anchor.parent else "",
                ]
                if part
            )
            candidates[detail_url] = {
                "url": detail_url,
                "context": context[:260],
                "seed_url": base_url,
            }
        for match in ABSOLUTE_CANDIDATE_URL_PATTERN.finditer(html):
            detail_url = self._normalize_detail_url(match.group(0), base_url)
            if not detail_url:
                continue
            context = html[max(0, match.start() - 160) : min(len(html), match.end() + 160)]
            candidates.setdefault(
                detail_url,
                {
                    "url": detail_url,
                    "context": self._compact_text(context)[:260],
                    "seed_url": base_url,
                },
            )
        for match in RELATIVE_CANDIDATE_URL_PATTERN.finditer(html):
            previous_char = html[match.start() - 1] if match.start() > 0 else ""
            if previous_char and (previous_char.isalnum() or previous_char in {".", "_"}):
                continue
            detail_url = self._normalize_detail_url(match.group(0), base_url)
            if not detail_url:
                continue
            context = html[max(0, match.start() - 160) : min(len(html), match.end() + 160)]
            candidates.setdefault(
                detail_url,
                {
                    "url": detail_url,
                    "context": self._compact_text(context)[:260],
                    "seed_url": base_url,
                },
            )
        return list(candidates.values())

    def _looks_like_rss_payload(self, html: str) -> bool:
        lowered = (html or "").lstrip().lower()
        return lowered.startswith("<?xml") or "<rss" in lowered[:200]

    def _extract_rss_candidates(self, xml_text: str, base_url: str) -> list[dict]:
        soup = BeautifulSoup(xml_text, "xml")
        candidates: dict[str, dict] = {}
        for item in soup.find_all("item"):
            link_node = item.find("link")
            if not link_node or not link_node.get_text(strip=True):
                continue
            detail_url = self._normalize_detail_url(link_node.get_text(strip=True), base_url)
            if not detail_url:
                continue
            title_text = item.find("title").get_text(" ", strip=True) if item.find("title") else ""
            description_html = item.find("description").get_text(" ", strip=True) if item.find("description") else ""
            description_text = BeautifulSoup(description_html, "html.parser").get_text(" ", strip=True)
            candidates[detail_url] = {
                "url": detail_url,
                "context": self._compact_text(f"{title_text} {description_text}")[:260],
                "seed_url": base_url,
            }
        return list(candidates.values())

    def _normalize_detail_url(self, raw_url: str, base_url: str) -> str:
        if not raw_url:
            return ""
        if raw_url.startswith("javascript:") or raw_url.startswith("#"):
            return ""
        absolute = urljoin(base_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.netloc not in {"www.nowcoder.com", "nowcoder.com"}:
            return ""
        if not any(pattern.match(parsed.path) for pattern in CANDIDATE_PATH_PATTERNS):
            return ""
        if parsed.path == "/jobs/company-project":
            project_id = parse_qs(parsed.query).get("projectId", [""])[0].strip()
            if not project_id:
                return ""
            return f"https://www.nowcoder.com/jobs/company-project?projectId={project_id}"
        return f"https://www.nowcoder.com{parsed.path}"

    def _unwrap_search_result_url(self, raw_url: str, base_url: str) -> str:
        absolute = urljoin(base_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.netloc in {"www.nowcoder.com", "nowcoder.com"} and parsed.path == "/jump":
            target = parse_qs(parsed.query).get("url", [""])[0].strip()
            if target:
                return unquote(target)
        if parsed.netloc not in {"www.bing.com", "bing.com"}:
            return absolute
        query = parse_qs(parsed.query)
        wrapped_candidates = query.get("u", []) + query.get("url", []) + query.get("r", [])
        for candidate in wrapped_candidates:
            decoded = self._decode_wrapped_url(candidate)
            if decoded:
                return decoded
        return absolute

    def _decode_wrapped_url(self, candidate: str) -> str:
        value = unquote(candidate or "").strip()
        if not value:
            return ""
        if ABSOLUTE_CANDIDATE_URL_PATTERN.search(value):
            match = ABSOLUTE_CANDIDATE_URL_PATTERN.search(value)
            return match.group(0) if match else ""
        payload = value[2:] if value.startswith("a1") else value
        if not payload:
            return ""
        padding = "=" * (-len(payload) % 4)
        try:
            decoded = urlsafe_b64decode(payload + padding).decode("utf-8", errors="ignore")
        except Exception:
            decoded = ""
        match = ABSOLUTE_CANDIDATE_URL_PATTERN.search(decoded)
        return match.group(0) if match else ""

    def _parse_candidate_page(self, page: "_FetchedPage", candidate: dict) -> list[JobPosting]:
        parsed = urlparse(page.final_url)
        if parsed.netloc not in {"www.nowcoder.com", "nowcoder.com"}:
            return []
        if not any(pattern.match(parsed.path) for pattern in CANDIDATE_PATH_PATTERNS):
            return []
        if parsed.path == "/jobs/company-project":
            return self._parse_company_project_page(page, candidate)
        if re.match(r"^/jobs/hr/\d+$", parsed.path):
            return self._parse_hr_page(page, candidate)
        if "/careers/" in parsed.path:
            return self._parse_careers_page(page, candidate)
        job = self._parse_detail_page(page, candidate)
        return [job] if job else []

    def _parse_detail_page(self, page: "_FetchedPage", candidate: dict) -> JobPosting | None:
        parsed = urlparse(page.final_url)
        soup = BeautifulSoup(page.text, "html.parser")
        title = self._extract_detail_title(soup, page.title)
        lines = self._extract_visible_lines(soup)
        description = self._build_detail_description(lines)
        if not title or not description:
            return None
        raw = {
            "url": page.final_url,
            "title": title,
            "description": description,
            "apply_url": page.final_url,
            "detail_fetched": True,
            "source_job_id": parsed.path.rstrip("/").split("/")[-1],
            "published_at": self._find_first_line(lines, ["发布时间", "发布日期", "更新时间"]),
            "company_name": self._infer_company_from_nowcoder_title(title) or self._find_company_name(lines),
            "city": self._find_city_text(lines),
            "salary_text": self._find_salary_text(lines),
            "deadline": self._find_deadline_text(lines),
            "application_status": self._find_application_status(lines, description),
            "raw_payload": {
                "seed_url": candidate.get("seed_url", ""),
                "discovery_context": candidate.get("context", ""),
            },
        }
        job = normalize_job_fields(raw, source=self.name)
        if job.application_status != "open":
            return None
        if self._is_excluded_company_name(job.company_name):
            return None
        if not is_job_quality_acceptable(job):
            return None
        return job

    def _parse_careers_page(self, page: "_FetchedPage", candidate: dict) -> list[JobPosting]:
        soup = BeautifulSoup(page.text, "html.parser")
        lines = self._extract_visible_lines(soup, keep_duplicates=True)
        blocks = self._extract_career_blocks(lines)
        jobs: list[JobPosting] = []
        parsed_url = urlparse(page.final_url)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        page_id = path_parts[-1] if path_parts else page.final_url.rstrip("/").split("/")[-1]
        default_company = self._company_name_from_slug(path_parts[1] if len(path_parts) >= 2 else "")
        for index, block in enumerate(blocks, start=1):
            title = block.get("title", "").strip()
            description = block.get("description", "").strip()
            if not title or not description:
                continue
            synthetic_id = f"{page_id}:{index}:{title}"
            raw = {
                "url": f"{page.final_url}#{index}",
                "title": title,
                "description": description,
                "apply_url": page.final_url,
                "detail_fetched": True,
                "source_job_id": synthetic_id,
                "company_name": block.get("company_name", "") or default_company,
                "city": block.get("city", ""),
                "salary_text": block.get("salary_text", ""),
                "deadline": block.get("deadline", ""),
                "application_status": block.get("application_status", ""),
                "raw_payload": {
                    "seed_url": candidate.get("seed_url", ""),
                    "discovery_context": candidate.get("context", ""),
                    "page_type": "careers",
                },
            }
            job = normalize_job_fields(raw, source=self.name)
            if job.application_status != "open":
                continue
            if self._is_excluded_company_name(job.company_name):
                continue
            if is_job_quality_acceptable(job):
                jobs.append(job)
        return jobs

    def _parse_hr_page(self, page: "_FetchedPage", candidate: dict) -> list[JobPosting]:
        initial_state = self._extract_initial_state(page.text)
        page_data = (initial_state.get("prefetchData") or {}).get("1") or {}
        job_list_data = page_data.get("jobListData") or {}
        jobs_payload = list(job_list_data.get("dataL") or []) + list(job_list_data.get("dataR") or [])
        company_detail = page_data.get("companyDetail") or {}
        hr_info = page_data.get("hrInfo") or {}
        company_name = (company_detail.get("companyName") or "").strip()
        boss_uid = str(hr_info.get("id") or "").strip()
        jobs: list[JobPosting] = []
        for item in jobs_payload:
            job = self._build_job_from_structured_payload(
                item,
                candidate=candidate,
                company_name=company_name,
                seed_url=page.final_url,
                seed_kind="hr",
                boss_uid=boss_uid,
            )
            if job and is_job_quality_acceptable(job):
                jobs.append(job)
        return jobs

    def _parse_company_project_page(self, page: "_FetchedPage", candidate: dict) -> list[JobPosting]:
        initial_state = self._extract_initial_state(page.text)
        page_state = (initial_state.get("app") or {}).get("93") or {}
        jobs_payload = list(page_state.get("companyJobList") or [])
        company_detail = page_state.get("companyDetail") or {}
        company_name = (company_detail.get("companyName") or "").strip()
        jobs: list[JobPosting] = []
        for item in jobs_payload:
            job = self._build_job_from_structured_payload(
                item,
                candidate=candidate,
                company_name=company_name,
                seed_url=page.final_url,
                seed_kind="company_project",
            )
            if job and is_job_quality_acceptable(job):
                jobs.append(job)
        return jobs

    def _build_job_from_structured_payload(
        self,
        payload: dict,
        *,
        candidate: dict,
        company_name: str,
        seed_url: str,
        seed_kind: str,
        boss_uid: str = "",
    ) -> JobPosting | None:
        job_id = str(payload.get("id") or "").strip()
        if not job_id:
            return None
        status = self._structured_status(payload)
        if status != "open":
            return None
        title = (payload.get("jobName") or "").strip()
        description = self._structured_description(payload)
        if not title or not description:
            return None
        resolved_company_name = (
            company_name
            or ((payload.get("recommendInternCompany") or {}).get("companyName") or "").strip()
            or ((payload.get("user") or {}).get("identity") or [{}])[0].get("companyName", "").strip()
        )
        if self._is_excluded_company_name(resolved_company_name):
            return None
        city = (payload.get("jobCity") or "").strip()
        city_list = [item.strip() for item in (payload.get("jobCityList") or []) if str(item).strip()]
        if not city and city_list:
            city = city_list[0]
        raw = {
            "url": f"https://www.nowcoder.com/jobs/detail/{job_id}",
            "apply_url": f"https://www.nowcoder.com/jobs/detail/{job_id}",
            "title": title,
            "company_name": resolved_company_name,
            "city": city,
            "description": description,
            "detail_fetched": True,
            "source_job_id": job_id,
            "published_at": self._format_timestamp(payload.get("refreshTime") or payload.get("createTime")),
            "deadline": self._format_timestamp(payload.get("deliverEnd")),
            "application_status": status,
            "job_type": NOWCODER_RECRUIT_TYPE_MAP.get(payload.get("recruitType"), ""),
            "salary_text": self._structured_salary_text(payload),
            "raw_payload": {
                "seed_url": seed_url,
                "seed_kind": seed_kind,
                "boss_uid": boss_uid,
                "discovery_context": candidate.get("context", ""),
                "job_payload": payload,
            },
        }
        job = normalize_job_fields(raw, source=self.name)
        if city_list:
            job.city_list = city_list
            if not job.city:
                job.city = city_list[0]
            job.ensure_ids()
        return job

    def _is_excluded_company_name(self, company_name: str) -> bool:
        normalized = company_name.strip().lower()
        if not normalized:
            return False
        return "牛客" in company_name or "nowcoder" in normalized

    def _structured_description(self, payload: dict) -> str:
        parse_ext = payload.get("parseExt") or {}
        sections: list[str] = []
        infos = (parse_ext.get("infos") or "").strip()
        requirements = (parse_ext.get("requirements") or "").strip()
        strengths = (parse_ext.get("jobStrength") or "").strip()
        if infos:
            sections.append(f"岗位职责\n{infos}")
        if requirements:
            sections.append(f"岗位要求\n{requirements}")
        if strengths:
            sections.append(f"岗位亮点\n{strengths}")
        text = "\n\n".join(section for section in sections if section).strip()
        return text[:5000]

    def _structured_status(self, payload: dict) -> str:
        begin = self._timestamp_ms(payload.get("deliverBegin"))
        end = self._timestamp_ms(payload.get("deliverEnd"))
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw_status = payload.get("status")
        if isinstance(raw_status, int) and raw_status not in {0}:
            return "closed"
        if begin and now_ms < begin:
            return "pending"
        if end and now_ms > end:
            return "closed"
        return "open"

    def _structured_salary_text(self, payload: dict) -> str:
        salary_show = (payload.get("salaryShow") or "").strip()
        if salary_show:
            return salary_show
        salary_min = int(payload.get("salaryMin") or 0)
        salary_max = int(payload.get("salaryMax") or 0)
        salary_month = int(payload.get("salaryMonth") or 0)
        if salary_min <= 0 and salary_max >= 9999999:
            return "薪资面议"
        if salary_min > 0 and salary_max > 0:
            minimum = self._format_salary_value(salary_min)
            maximum = self._format_salary_value(salary_max)
            suffix = f" * {salary_month}薪" if salary_month > 0 else ""
            return f"{minimum}-{maximum}{suffix}".strip()
        return ""

    def _format_salary_value(self, value: int) -> str:
        if value >= 1000 and value % 1000 == 0:
            return f"{value // 1000}K"
        if value >= 1000:
            return f"{value / 1000:.1f}K".rstrip("0").rstrip(".")
        return f"{value}元"

    def _timestamp_ms(self, value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _format_timestamp(self, value: object) -> str:
        timestamp_ms = self._timestamp_ms(value)
        if not timestamp_ms:
            return ""
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    def _extract_initial_state(self, html: str) -> dict:
        match = re.search(r"window\.__INITIAL_STATE__=(.*?);\(function\(\)\{var s;", html, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    def _extract_career_blocks(self, lines: list[str]) -> list[dict]:
        blocks: list[dict] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if "岗位职责" not in line:
                index += 1
                continue
            title_index = self._find_career_title_index(lines, index)
            if title_index is None:
                index += 1
                continue
            next_block = self._find_next_career_marker(lines, index + 1)
            block_lines = lines[title_index:next_block]
            title = lines[title_index].replace("详情", "").replace("收起", "").strip()
            company_name = self._extract_company_from_title(title)
            city = self._find_city_text(block_lines)
            description = "\n".join(block_lines[:40]).strip()
            blocks.append(
                {
                    "title": title,
                    "company_name": company_name,
                    "city": city,
                    "salary_text": self._find_salary_text(block_lines),
                    "deadline": self._find_deadline_text(block_lines),
                    "application_status": self._find_application_status(block_lines, description),
                    "description": description,
                }
            )
            index = next_block
        return blocks

    def _find_career_title_index(self, lines: list[str], responsibility_index: int) -> int | None:
        for candidate_index in range(responsibility_index - 1, max(-1, responsibility_index - 5), -1):
            if candidate_index < 0:
                break
            line = lines[candidate_index]
            if self._looks_like_career_title(line):
                return candidate_index
        return None

    def _find_next_career_marker(self, lines: list[str], start_index: int) -> int:
        for index in range(start_index, len(lines)):
            if "岗位职责" in lines[index]:
                return index
        return len(lines)

    def _looks_like_career_title(self, line: str) -> bool:
        if not line or len(line) > 60:
            return False
        if any(token in line for token in ["岗位职责", "岗位要求", "职能部门", "输入关键词", "详情 收起", "已结束"]):
            return False
        if line in {"所有", "收起", "更多"}:
            return False
        return bool(re.search(r"[岗位师员理家专]", line))

    def _extract_company_from_title(self, title: str) -> str:
        match = re.match(r"^[〖【\[]?([^〗】\]]+)[〗】\]]", title)
        if match:
            return match.group(1).strip()
        return ""

    def _company_name_from_slug(self, slug: str) -> str:
        normalized = slug.strip().replace("-", " ")
        if not normalized:
            return ""
        mapping = {
            "nowcoder": "牛客网",
            "nowcoder1": "牛客网",
        }
        return mapping.get(normalized.lower(), normalized)

    def _infer_company_from_nowcoder_title(self, title: str) -> str:
        parts = [segment.strip() for segment in re.split(r"[_|｜]", title) if segment.strip()]
        for part in parts[1:]:
            cleaned = re.sub(r"(校招|校园招聘|实习|社招|内推|牛客网)$", "", part).strip()
            if not cleaned:
                continue
            if cleaned in {"牛客网", "校招", "社招", "实习"}:
                continue
            if any(token in cleaned for token in ["岗位职责", "岗位要求", "职位描述"]):
                continue
            return cleaned
        return ""

    def _extract_detail_title(self, soup: BeautifulSoup, fallback: str) -> str:
        meta_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "title"})
        if meta_title and meta_title.get("content", "").strip():
            return meta_title["content"].strip()
        h1 = soup.find("h1")
        if h1 and h1.get_text(" ", strip=True):
            return h1.get_text(" ", strip=True)
        if fallback.strip():
            return fallback.strip()
        return ""

    def _extract_visible_lines(self, soup: BeautifulSoup, *, keep_duplicates: bool = False) -> list[str]:
        for tag_name in ["script", "style", "noscript", "svg"]:
            for node in soup.find_all(tag_name):
                node.decompose()
        lines: list[str] = []
        seen: set[str] = set()
        for raw_line in soup.get_text("\n", strip=True).splitlines():
            line = self._compact_text(raw_line)
            if not line:
                continue
            if self._looks_like_nav_line(line):
                continue
            if not keep_duplicates and line in seen:
                continue
            if not keep_duplicates:
                seen.add(line)
            lines.append(line)
        return lines

    def _build_detail_description(self, lines: list[str]) -> str:
        if not lines:
            return ""
        preferred_start = 0
        markers = ["职位描述", "岗位职责", "工作职责", "任职要求", "岗位要求", "岗位亮点", "职位要求"]
        for index, line in enumerate(lines):
            if any(marker in line for marker in markers):
                preferred_start = index
                break
        selected = lines[preferred_start : preferred_start + 80] if preferred_start else lines[:80]
        text = "\n".join(selected)
        if len(text) > 5000:
            text = text[:5000]
        return text.strip()

    def _find_company_name(self, lines: list[str]) -> str:
        company_patterns = [
            re.compile(r"(?:招聘单位|公司名称|所属公司|公司)[:：]\s*([^\n]{2,40})"),
            re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()·&]{2,30}(?:有限公司|集团|科技|网络|软件|传媒|银行|证券|医药|智能|数据|信息))"),
        ]
        for line in lines[:40]:
            for pattern in company_patterns:
                match = pattern.search(line)
                if match:
                    return match.group(1).strip()
        return ""

    def _find_city_text(self, lines: list[str]) -> str:
        city_pattern = re.compile(r"(北京|上海|广州|深圳|杭州|成都|贵阳|武汉|南京|西安|苏州|重庆|天津|长沙|厦门|青岛)")
        for line in lines[:60]:
            match = city_pattern.search(line)
            if match:
                return match.group(1)
        return ""

    def _find_first_line(self, lines: list[str], markers: list[str]) -> str:
        for line in lines[:120]:
            if any(marker in line for marker in markers):
                return line
        return ""

    def _find_salary_text(self, lines: list[str]) -> str:
        for line in lines[:120]:
            match = NOWCODER_SALARY_PATTERN.search(line)
            if match:
                return match.group(0).strip()
        return ""

    def _find_deadline_text(self, lines: list[str]) -> str:
        return self._find_first_line(lines, ["投递时间", "投递截止", "截止时间", "截止日期", "申请截止", "网申截止"])

    def _find_application_status(self, lines: list[str], description: str) -> str:
        haystack = "\n".join(lines[:120]) + "\n" + description[:600]
        if any(token in haystack for token in NOWCODER_PENDING_TOKENS):
            return "pending"
        if any(token in haystack for token in NOWCODER_CLOSED_TOKENS):
            return "closed"
        if any(token in haystack for token in NOWCODER_OPEN_TOKENS):
            return "open"
        return "unknown"

    def _candidate_priority(self, context: str, url: str, role_tokens: list[str], city_tokens: list[str]) -> int:
        score = 0
        lowered = context.lower()
        if "/jobs/hr/" in url:
            score += 5
        if "/jobs/company-project" in url:
            score += 5
        if "/jobs/detail/" in url:
            score += 2
        for token in role_tokens[:6]:
            if token and token.lower() in lowered:
                score += 3
        for token in city_tokens[:4]:
            if token and token.lower() in lowered:
                score += 2
        return score

    def _should_reject_candidate(self, candidate: dict, settings: UserSettings) -> tuple[bool, str]:
        url = candidate.get("url", "")
        context = self._compact_text(candidate.get("context", ""))
        if not context or any(marker in url for marker in ["/jobs/hr/", "/jobs/company-project", "/careers/"]):
            return False, ""
        mentioned_cities = NOWCODER_CITY_PATTERN.findall(context)
        if settings.preferred_cities and mentioned_cities and not any(city in settings.preferred_cities for city in mentioned_cities):
            return True, "候选上下文城市不符"
        if settings.excluded_keywords and any(keyword and keyword.lower() in context.lower() for keyword in settings.excluded_keywords):
            return True, "候选上下文命中黑名单词"
        return False, ""

    def _prefilter_jobs(
        self,
        jobs: list[JobPosting],
        settings: UserSettings,
        role_tokens: list[str],
        prefilter_report: dict,
    ) -> list[JobPosting]:
        filtered: list[JobPosting] = []
        for job in jobs:
            skip, reason = should_skip_job(job, settings, last_action="")
            if skip:
                self._bump_prefilter(prefilter_report, "job", reason, job.title, job.url)
                continue
            if role_tokens and not self._job_matches_role_tokens(job, role_tokens):
                self._bump_prefilter(prefilter_report, "job", "未命中想看岗位", job.title, job.url)
                continue
            filtered.append(job)
        return filtered

    def _job_matches_role_tokens(self, job: JobPosting, role_tokens: list[str]) -> bool:
        if not role_tokens:
            return True
        text = "\n".join(part for part in [job.title, job.description] if part).lower()
        return any(token and token.lower() in text for token in role_tokens)

    def _bump_prefilter(self, report: dict, kind: str, reason: str, label: str, url: str) -> None:
        bucket_name = "candidate_rejected" if kind == "candidate" else "job_rejected"
        example_name = "candidate_rejected_examples" if kind == "candidate" else "job_rejected_examples"
        bucket = report[bucket_name]
        bucket[reason] = bucket.get(reason, 0) + 1
        examples = report[example_name].setdefault(reason, [])
        if len(examples) < 3:
            examples.append({"label": self._compact_text(label)[:120], "url": url})

    def _role_tokens(self, settings: UserSettings, profile: ResumeProfile | None) -> list[str]:
        tokens = settings.preferred_roles[:6]
        if not tokens and profile:
            tokens = profile.target_roles[:6]
        return [token.strip() for token in tokens if token.strip()]

    def _city_tokens(self, settings: UserSettings, profile: ResumeProfile | None) -> list[str]:
        tokens = settings.preferred_cities[:4]
        if not tokens and profile:
            tokens = profile.target_cities[:4]
        return [token.strip() for token in tokens if token.strip()]

    def _build_queries(self, settings: UserSettings, profile: ResumeProfile | None) -> list[str]:
        year = str(datetime.now().year)
        roles = self._role_tokens(settings, profile)[:2] or ["运营"]
        cities = self._city_tokens(settings, profile)[:2] or [""]
        intents: list[str] = []
        if "校招" in settings.job_types:
            intents.extend([f"{year} 校招", f"{year} 校园招聘"])
        if "社招" in settings.job_types:
            intents.extend(["社招", "社会招聘"])
        if settings.campus_role_mode in {"intern", "both"}:
            intents.extend([f"{year} 实习", "日常实习"])
        if not intents:
            intents = [f"{year} 校招"]
        queries: list[str] = []
        for role in roles:
            for city in cities:
                broad_queries = [
                    f"site:nowcoder.com/job {role} 牛客",
                    f"site:nowcoder.com/jobs/detail {role} 牛客",
                    f"site:nowcoder.com/job {city} {role} 牛客",
                    f"site:nowcoder.com/jobs/detail {city} {role} 牛客",
                ]
                queries.extend(self._compact_text(query) for query in broad_queries if self._compact_text(query))
                for intent in intents:
                    narrow_queries = [
                        f"site:nowcoder.com/job {intent} {city} {role} 牛客",
                        f"site:nowcoder.com/jobs/detail {intent} {city} {role}",
                    ]
                    queries.extend(self._compact_text(query) for query in narrow_queries if self._compact_text(query))
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            deduped.append(query)
        return deduped

    def _looks_like_hard_block(self, page: "_FetchedPage") -> bool:
        haystack = f"{page.title}\n{page.text[:3000]}".lower()
        return any(token.lower() in haystack for token in NOWCODER_BLOCK_TOKENS) or page.status_code in {401, 403, 429}

    def _looks_like_nav_line(self, line: str) -> bool:
        token_hits = sum(1 for token in NOWCODER_NAV_TOKENS if token in line)
        if token_hits >= 4 and len(line) <= 120:
            return True
        if line.startswith("牛客网") and len(line) <= 80:
            return True
        if line.startswith("首页") and token_hits >= 3:
            return True
        return False

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._compact_text(match.group(1))

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    def _detail_numeric_id(self, url: str) -> int:
        try:
            return int(url.rstrip("/").split("/")[-1])
        except ValueError:
            return 0

    def _page_summary(self, page: "_FetchedPage") -> dict:
        return {
            "requested_url": page.requested_url,
            "final_url": page.final_url,
            "status_code": page.status_code,
            "title": page.title,
            "body_excerpt": self._compact_text(page.text[:400]),
        }

    def _detail_debug_summary(self, page: "_FetchedPage") -> dict:
        try:
            soup = BeautifulSoup(page.text, "html.parser")
            lines = self._extract_visible_lines(soup)
            description = self._build_detail_description(lines)
            structured_job_count = 0
            parsed = urlparse(page.final_url)
            if parsed.path == "/jobs/company-project":
                initial_state = self._extract_initial_state(page.text)
                structured_job_count = len(((initial_state.get("app") or {}).get("93") or {}).get("companyJobList") or [])
            elif re.match(r"^/jobs/hr/\d+$", parsed.path):
                initial_state = self._extract_initial_state(page.text)
                job_list_data = (((initial_state.get("prefetchData") or {}).get("1") or {}).get("jobListData") or {})
                structured_job_count = len(job_list_data.get("dataL") or []) + len(job_list_data.get("dataR") or [])
            return {
                "line_count": len(lines),
                "title": self._extract_detail_title(soup, page.title),
                "company_name": self._find_company_name(lines),
                "city": self._find_city_text(lines),
                "salary_text": self._find_salary_text(lines),
                "deadline": self._find_deadline_text(lines),
                "application_status": self._find_application_status(lines, description),
                "description_length": len(description),
                "structured_job_count": structured_job_count,
                "lines_preview": lines[:24],
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _write_debug_snapshot(self, kind: str, payload: dict) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.debug_dir / f"nowcoder_direct_{kind}_{timestamp}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            latest_path = self.debug_dir / f"latest-nowcoder-direct-{kind}.json"
            latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return


class _FetchedPage:
    def __init__(
        self,
        *,
        requested_url: str,
        final_url: str,
        status_code: int,
        text: str,
        title: str,
        content_type: str,
    ):
        self.requested_url = requested_url
        self.final_url = final_url
        self.status_code = status_code
        self.text = text
        self.title = title
        self.content_type = content_type
