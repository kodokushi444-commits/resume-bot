from __future__ import annotations

from urllib.parse import urljoin, urlparse

from ..normalization import normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource


class CompanyWatchlistSource(JobSource):
    def __init__(self):
        super().__init__("company_watchlist")

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        for item in settings.company_watchlist:
            if not item.careers_url:
                continue
            jobs.extend(self._fetch_company_jobs(item.name, item.careers_url, item.stage))
        return jobs

    def _fetch_company_jobs(self, company_name: str, careers_url: str, stage: str) -> list[JobPosting]:
        import requests
        from bs4 import BeautifulSoup

        response = requests.get(
            careers_url,
            timeout=30,
            headers={"user-agent": "Mozilla/5.0 resume-bot/1.0"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        domain = urlparse(careers_url).netloc
        jobs: list[JobPosting] = []

        for anchor in soup.find_all("a", href=True):
            text = anchor.get_text(" ", strip=True)
            href = anchor["href"]
            url = urljoin(careers_url, href)
            if urlparse(url).netloc and urlparse(url).netloc != domain:
                continue
            if not any(keyword in (text + href) for keyword in ["校招", "校园", "应届", "毕业生", "运营", "产品", "内容", "增长"]):
                continue
            jobs.append(
                normalize_job_fields(
                    {
                        "title": text or url,
                        "url": url,
                        "company_name": company_name,
                        "company_stage": stage,
                        "description": soup.get_text("\n", strip=True)[:4000],
                        "apply_url": url,
                    },
                    source=self.name,
                )
            )
        unique: dict[str, JobPosting] = {}
        for job in jobs:
            unique[job.fingerprint] = job
        return list(unique.values())
