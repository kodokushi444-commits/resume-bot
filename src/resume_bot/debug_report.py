from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def _sanitize_name(value: str) -> str:
    allowed = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "report"


def _json_block(payload: Any) -> list[str]:
    return ["```json", json.dumps(payload, ensure_ascii=False, indent=2), "```"]


def render_markdown(report_type: str, payload: dict[str, Any]) -> str:
    lines: list[str] = [f"# {report_type}", ""]
    created_at = payload.get("created_at")
    if created_at:
        lines.append(f"生成时间：{created_at}")
        lines.append("")

    summary = payload.get("summary")
    if summary:
        lines.append("## 摘要")
        if isinstance(summary, list):
            lines.extend(f"- {item}" for item in summary)
        elif isinstance(summary, dict):
            for key, value in summary.items():
                lines.append(f"- {key}: {value}")
        else:
            lines.append(str(summary))
        lines.append("")

    if payload.get("extraction"):
        lines.append("## 简历提取")
        extraction = payload["extraction"]
        for key in ["file_name", "file_type", "extraction_method", "parser_backend", "quality_score", "fallback_used"]:
            if key in extraction:
                lines.append(f"- {key}: {extraction[key]}")
        if extraction.get("quality_flags"):
            lines.append(f"- quality_flags: {', '.join(extraction['quality_flags'])}")
        lines.append("")
        preview = extraction.get("raw_text_preview", "")
        if preview:
            lines.append("### 原文预览")
            lines.append("```text")
            lines.append(preview)
            lines.append("```")
            lines.append("")

    if payload.get("profile_summary"):
        lines.append("## 简历摘要")
        lines.append("```text")
        lines.append(payload["profile_summary"])
        lines.append("```")
        lines.append("")

    if payload.get("settings_summary"):
        lines.append("## 当前设置")
        lines.append("```text")
        lines.append(payload["settings_summary"])
        lines.append("```")
        lines.append("")

    if payload.get("fetch_report"):
        lines.append("## 抓取结果")
        lines.extend(_json_block(payload["fetch_report"]))
        lines.append("")

    if payload.get("ranking_debug"):
        lines.append("## 筛选诊断")
        lines.extend(_json_block(payload["ranking_debug"]))
        lines.append("")

    if payload.get("digest_text"):
        lines.append("## Digest 预览")
        lines.append("```text")
        lines.append(payload["digest_text"])
        lines.append("```")
        lines.append("")

    lines.append("## 完整原始数据")
    lines.extend(_json_block(payload))
    lines.append("")
    return "\n".join(lines)


def write_debug_report(debug_dir: Path, report_type: str, user_id: str, payload: dict[str, Any]) -> dict[str, str]:
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _sanitize_name(f"{timestamp}-{report_type}-{user_id}")
    json_path = debug_dir / f"{slug}.json"
    md_path = debug_dir / f"{slug}.md"
    latest_json_path = debug_dir / f"latest-{_sanitize_name(report_type)}-{_sanitize_name(user_id)}.json"
    latest_md_path = debug_dir / f"latest-{_sanitize_name(report_type)}-{_sanitize_name(user_id)}.md"

    json_payload = dict(payload)
    json_payload.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    json_text = json.dumps(json_payload, ensure_ascii=False, indent=2)
    markdown_text = render_markdown(report_type, json_payload)

    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(markdown_text, encoding="utf-8")
    shutil.copyfile(json_path, latest_json_path)
    shutil.copyfile(md_path, latest_md_path)

    return {
        "json": str(json_path),
        "md": str(md_path),
        "latest_json": str(latest_json_path),
        "latest_md": str(latest_md_path),
    }
