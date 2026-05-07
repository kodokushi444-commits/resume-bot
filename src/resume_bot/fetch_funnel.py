from __future__ import annotations


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_fetch_funnel(fetch_report: dict | None, ranking_debug: dict | None, recommendation_count: int) -> dict:
    source_reports = []
    if isinstance(fetch_report, dict):
        source_reports = fetch_report.get("sources", []) or []
    enterprise_count = sum(_safe_int(item.get("enterprise_count")) for item in source_reports if isinstance(item, dict))
    discovered_job_count = 0
    for item in source_reports:
        if not isinstance(item, dict):
            continue
        discovered_job_count += _safe_int(
            item.get("discovered_job_count", item.get("count", item.get("job_count", 0)))
        )
    if not discovered_job_count:
        discovered_job_count = _safe_int((fetch_report or {}).get("total_jobs")) if isinstance(fetch_report, dict) else 0
    rules_passed_count = _safe_int((ranking_debug or {}).get("matched_before_rerank"))
    final_recommendation_count = _safe_int(recommendation_count or (ranking_debug or {}).get("matched_after_rerank"))
    return {
        "enterprise_count": enterprise_count,
        "discovered_job_count": discovered_job_count,
        "rules_passed_count": rules_passed_count,
        "final_recommendation_count": final_recommendation_count,
    }
