from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests

from ..normalization import is_job_quality_acceptable, looks_like_noise_page, normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource


class TavilySearchSource(JobSource):
    def __init__(self, name: str, api_key: str, domains: list[str], query_templates: list[str], max_results_per_query: int = 6):
        super().__init__(name)
        self.api_key = api_key
        self.domains = domains
        self.query_templates = query_templates
        self.max_results_per_query = max_results_per_query

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        if not self.api_key:
            return []
        year = str(datetime.now().year)
        role_candidates = settings.preferred_roles[:4] or (profile.target_roles[:4] if profile else []) or ["运营"]
        raw_results_by_url: dict[str, dict] = {}
        for role in role_candidates:
            for query in self._build_queries(year, role, settings):
                for raw_item in self._search(query):
                    url = raw_item.get("url", "").strip()
                    if not url or looks_like_noise_page(url, raw_item.get("title", ""), raw_item.get("description", "")):
                        continue
                    previous = raw_results_by_url.get(url, {})
                    if len(raw_item.get("description", "")) > len(previous.get("description", "")):
                        raw_results_by_url[url] = raw_item
                    elif url not in raw_results_by_url:
                        raw_results_by_url[url] = raw_item
        jobs: dict[str, JobPosting] = {}
        for raw_item in raw_results_by_url.values():
            enriched = self._enrich_result(raw_item)
            if looks_like_noise_page(enriched.get("url", ""), enriched.get("title", ""), enriched.get("description", "")):
                continue
            job = normalize_job_fields(enriched, source=self.name)
            if job.url and job.title and job.job_type and is_job_quality_acceptable(job):
                jobs[job.fingerprint] = job
        return list(jobs.values())

    def _build_queries(self, year: str, role: str, settings: UserSettings) -> list[str]:
        intents: list[str] = []
        if "校招" in settings.job_types:
            intents.extend([f"{year} 校招", f"{year} 校园招聘"])
        if "社招" in settings.job_types:
            intents.extend(["社招", "社会招聘"])
        if settings.campus_role_mode in {"intern", "both"}:
            intents.extend([f"{year} 实习", "日常实习", "暑期实习"])
        queries: list[str] = []
        templates = self.query_templates or ["{intent} {role}"]
        for intent in intents or [f"{year} 校招"]:
            for template in templates:
                try:
                    query = template.format(year=year, role=role, intent=intent)
                except KeyError:
                    query = template.format(year=year, role=role)
                queries.append(query)
            if self.domains:
                queries.append(f"{intent} {role} site:{self.domains[0]}")
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            normalized = query.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _search(self, query: str) -> list[dict]:
        response = requests.post(
            "https://api.tavily.com/search",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "query": query,
                "topic": "general",
                "search_depth": "basic",
                "max_results": self.max_results_per_query,
                "include_domains": self.domains,
                "include_raw_content": "text",
                "country": "china",
                "time_range": "month",
            },
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        normalized: list[dict] = []
        for item in results:
            normalized.append(
                {
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "description": item.get("raw_content") or item.get("content") or "",
                    "apply_url": item.get("url", ""),
                    "published_at": item.get("published_date", "") or item.get("date", ""),
                }
            )
        return normalized

    def _enrich_result(self, item: dict) -> dict:
        enriched = dict(item)
        url = enriched.get("url", "").strip()
        if not url:
            return enriched
        try:
            response = requests.get(
                url,
                timeout=30,
                headers={"user-agent": "Mozilla/5.0 resume-bot/1.0"},
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return enriched
        if "text/html" not in response.headers.get("content-type", ""):
            return enriched
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return enriched
        soup = BeautifulSoup(response.text, "html.parser")
        for tag_name in ["script", "style", "noscript"]:
            for node in soup.find_all(tag_name):
                node.decompose()
        title = enriched.get("title", "").strip()
        meta_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "title"})
        if meta_title and meta_title.get("content", "").strip():
            title = meta_title["content"].strip()
        elif soup.title and soup.title.text.strip():
            title = soup.title.text.strip()
        meta_desc = (
            soup.find("meta", attrs={"property": "og:description"})
            or soup.find("meta", attrs={"name": "description"})
        )
        visible_text = soup.get_text("\n", strip=True)
        description = enriched.get("description", "")
        if meta_desc and meta_desc.get("content", "").strip():
            description = f"{meta_desc['content'].strip()}\n{visible_text[:5000]}".strip()
        else:
            description = visible_text[:5000]
        apply_url = enriched.get("apply_url", url)
        for anchor in soup.find_all("a", href=True):
            link_text = anchor.get_text(" ", strip=True)
            if not link_text:
                continue
            if any(keyword in link_text for keyword in ["立即投递", "申请职位", "投递简历", "马上申请", "网申地址", "申请入口"]):
                apply_url = urljoin(response.url, anchor["href"])
                break
        enriched.update(
            {
                "url": response.url,
                "title": title,
                "description": description,
                "apply_url": apply_url,
                "detail_fetched": True,
            }
        )
        if not enriched.get("company_name"):
            enriched["company_name"] = self._infer_company_from_page(title, description, response.url)
        if not enriched.get("city"):
            enriched["city"] = self._infer_city_from_page(description)
        return enriched

    def _infer_city_from_page(self, text: str) -> str:
        for city in ["北京", "上海", "广州", "深圳", "杭州", "成都", "贵阳", "武汉", "南京", "西安", "苏州", "重庆", "天津", "长沙", "厦门", "青岛"]:
            if city in text:
                return city
        return ""

    def _infer_company_from_page(self, title: str, description: str, url: str) -> str:
        for pattern in [
            r"(?:招聘单位|公司名称|所属公司|公司)[:：]\s*([^\n]{2,40})",
            r"([\u4e00-\u9fa5A-Za-z0-9（）()·&]{2,30}(?:有限公司|集团|科技|网络|软件|传媒|银行|证券|医药|智能|数据|信息))",
        ]:
            import re

            match = re.search(pattern, description)
            if match:
                return match.group(1).strip()
        title_parts = [part.strip() for part in title.split("|") if part.strip()]
        if len(title_parts) >= 2 and "牛客" not in title_parts[1]:
            return title_parts[1]
        netloc = urlparse(url).netloc
        if netloc.startswith("job."):
            return netloc.split(".")[1]
        return ""
