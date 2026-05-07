from __future__ import annotations

import json
from pathlib import Path

from ..normalization import normalize_job_fields
from ..types import JobPosting, ResumeProfile, UserSettings
from .base import JobSource


class JsonFeedSource(JobSource):
    def __init__(self, name: str, file_path: Path):
        super().__init__(name)
        self.file_path = file_path

    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        return [normalize_job_fields(item, source=self.name) for item in payload.get("jobs", [])]
