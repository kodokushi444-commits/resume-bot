from __future__ import annotations

import re
from datetime import date, datetime


DATE_PATTERNS = [
    re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
]


def parse_date_text(text: str) -> date | None:
    candidate = text.strip()
    if candidate in {"长期", "长期有效"}:
        return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(candidate)
        if not match:
            continue
        year, month, day = [int(part) for part in match.groups()]
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def to_iso_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    matches: list[date] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_date_text(match.group(0))
            if parsed:
                matches.append(parsed)
    return matches


def extract_deadline(text: str) -> tuple[str, str]:
    if not text:
        return "", "unknown"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    deadline_keywords = [
        "投递截止",
        "截止时间",
        "截止日期",
        "网申截止",
        "申请截止",
        "投递时间",
        "报名截止",
        "截至",
        "截止至",
    ]
    for line in lines:
        if not any(keyword in line for keyword in deadline_keywords):
            continue
        if "长期" in line:
            return "长期", "open"
        dates = find_dates(line)
        if dates:
            deadline = max(dates)
            return deadline.isoformat(), "open" if deadline >= date.today() else "closed"
    if any(keyword in text for keyword in ["已截止", "结束招聘", "招聘结束", "已结束"]):
        return "", "closed"
    return "", "unknown"


def is_recent_date(value: str, days: int = 2) -> bool:
    parsed = parse_date_text(value) if value else None
    if not parsed:
        return False
    return (date.today() - parsed).days <= days
