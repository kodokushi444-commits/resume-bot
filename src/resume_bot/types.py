from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(*parts: str) -> str:
    payload = "||".join(part.strip().lower() for part in parts if part)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ResumeProfile:
    name: str = ""
    phone: str = ""
    email: str = ""
    school: str = ""
    major: str = ""
    degree: str = ""
    graduation_year: str = ""
    target_roles: list[str] = field(default_factory=list)
    target_cities: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    experiences: list[str] = field(default_factory=list)
    summary: str = ""
    raw_sections: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResumeProfile":
        if not data:
            return cls()
        return cls(**data)


@dataclass
class ResumeExtractionResult:
    file_name: str
    file_type: str
    raw_text: str
    extraction_method: str
    parser_backend: str
    route_name: str = ""
    route_summary: str = ""
    route_reason: str = ""
    provider_used: str = ""
    quality_score: int = 0
    quality_flags: list[str] = field(default_factory=list)
    fallback_used: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    raw_text_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompanyWatchItem:
    name: str
    careers_url: str = ""
    domain: str = ""
    stage: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanyWatchItem":
        return cls(**data)


@dataclass
class UserSettings:
    user_id: str
    preferred_roles: list[str] = field(default_factory=list)
    avoided_roles: list[str] = field(default_factory=list)
    preferred_cities: list[str] = field(default_factory=list)
    excluded_cities: list[str] = field(default_factory=list)
    preferred_company_stages: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    preferred_keywords: list[str] = field(default_factory=list)
    company_watchlist: list[CompanyWatchItem] = field(default_factory=list)
    job_types: list[str] = field(default_factory=lambda: ["校招", "社招"])
    campus_role_mode: str = "full_time"
    salary_min: int = 0
    salary_max: int = 0
    accept_unspecified_salary: bool = True
    max_degree_requirement: str = ""
    history_backfill_limit: int = 10
    push_time: str = "09:00"
    notify_when_empty: bool = True
    allow_repush_when_updated: bool = True
    max_items_per_push: int = 20
    skip_unknown_city_when_city_filtered: bool = True
    feishu_receive_id: str = ""
    feishu_receive_id_type: str = "open_id"
    updated_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["company_watchlist"] = [item.to_dict() for item in self.company_watchlist]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, user_id: str) -> "UserSettings":
        if not data:
            return cls(user_id=user_id)
        payload = dict(data)
        payload["user_id"] = user_id
        watchlist = payload.get("company_watchlist", [])
        payload["company_watchlist"] = [CompanyWatchItem.from_dict(item) for item in watchlist]
        return cls(**payload)


@dataclass
class JobPosting:
    source: str
    url: str
    title: str
    company_name: str = ""
    city: str = ""
    city_list: list[str] = field(default_factory=list)
    description: str = ""
    apply_url: str = ""
    source_job_id: str = ""
    company_stage: str = ""
    job_type: str = "校招"
    employment_mode: str = "unknown"
    salary_text: str = ""
    salary_min: int = 0
    salary_max: int = 0
    salary_unit: str = ""
    degree_requirement: str = ""
    degree_preference: str = ""
    job_categories: list[str] = field(default_factory=list)
    published_at: str = ""
    deadline: str = ""
    application_status: str = "unknown"
    detail_fetched: bool = False
    fetch_session_id: str = ""
    discovered_at: str = field(default_factory=utcnow_iso)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    content_hash: str = ""

    def ensure_ids(self) -> None:
        if not self.fingerprint:
            self.fingerprint = stable_hash(
                self.source,
                self.source_job_id or "",
                self.company_name,
                self.title,
                self.city,
                self.url,
            )
        if not self.content_hash:
            self.content_hash = stable_hash(
                self.title,
                self.company_name,
                ",".join(self.city_list or ([self.city] if self.city else [])),
                self.description,
                self.apply_url or self.url,
            )

    def text_blob(self) -> str:
        return "\n".join(
            part
            for part in [
                self.title,
                self.company_name,
                self.city,
                " ".join(self.city_list),
                self.job_type,
                self.employment_mode,
                self.salary_text,
                self.degree_requirement,
                self.degree_preference,
                self.description,
            ]
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        self.ensure_ids()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobPosting":
        job = cls(**data)
        job.ensure_ids()
        return job


@dataclass
class MatchResult:
    job: JobPosting
    score: float
    reasons: list[str] = field(default_factory=list)
    delivery_kind: str = "new"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job.to_dict(),
            "score": self.score,
            "reasons": self.reasons,
            "delivery_kind": self.delivery_kind,
        }


@dataclass
class DigestBundle:
    user_id: str
    new_items: list[MatchResult] = field(default_factory=list)
    history_items: list[MatchResult] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow_iso)
    empty_reason: str = ""
    history_included: bool = False
    history_run_key: str = ""

    def all_items(self) -> list[MatchResult]:
        return [*self.new_items, *self.history_items]
