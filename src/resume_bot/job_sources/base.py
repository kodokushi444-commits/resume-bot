from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import JobPosting, ResumeProfile, UserSettings


class SourceHaltError(RuntimeError):
    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


class JobSource(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def fetch_jobs(self, settings: UserSettings, profile: ResumeProfile | None) -> list[JobPosting]:
        raise NotImplementedError
