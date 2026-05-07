from __future__ import annotations


def schedule_source_allowed(settings) -> bool:
    return "校招" in (getattr(settings, "job_types", []) or [])


def decorate_fetch_sources_for_settings(available_sources: list[dict], settings) -> list[dict]:
    decorated: list[dict] = []
    allow_schedule = schedule_source_allowed(settings)
    for item in available_sources:
        current = dict(item)
        current["disabled"] = False
        current["disabled_reason"] = ""
        if current.get("id") == "nowcoder_schedule" and not allow_schedule:
            current["disabled"] = True
            current["disabled_reason"] = "当前设置只看社招，勾选“只看校招”或“校招+社招”后才能启用牛客校招日程。"
            current["default_checked"] = False
        decorated.append(current)
    return decorated


def sanitize_selected_source_groups(selected_source_groups: list[str], settings) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    dropped: list[str] = []
    allow_schedule = schedule_source_allowed(settings)
    for source_id in selected_source_groups:
        if source_id == "nowcoder_schedule" and not allow_schedule:
            dropped.append(source_id)
            continue
        allowed.append(source_id)
    return allowed, dropped
