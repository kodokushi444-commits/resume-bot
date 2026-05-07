from __future__ import annotations

import json

from .types import DigestBundle, MatchResult


def _job_city_label(item: MatchResult) -> str:
    cities = item.job.city_list or ([item.job.city] if item.job.city else [])
    return "/".join(cities) if cities else "城市未识别"


def _job_mode_label(item: MatchResult) -> str:
    type_prefix = item.job.job_type or "岗位"
    mode_suffix = {
        "full_time": "正职",
        "intern": "实习",
        "unknown": "类型未识别",
    }.get(item.job.employment_mode, item.job.employment_mode)
    return f"{type_prefix}{mode_suffix}"


def _group_lines(label: str, items: list[MatchResult]) -> list[str]:
    lines = [label]
    for index, item in enumerate(items, start=1):
        mode_label = _job_mode_label(item)
        lines.extend(
            [
                f"{index}. {item.job.title} | {item.job.company_name or '公司未识别'} | {_job_city_label(item)} | {mode_label}",
                f"   推荐分：{item.score:.1f}",
                f"   推荐理由：{'；'.join(item.reasons) or '规则匹配通过'}",
                f"   薪资/学历：{item.job.salary_text or '薪资未写'} | {item.job.degree_requirement or item.job.degree_preference or '学历未写'}",
                f"   截止时间：{item.job.deadline or '未识别'}",
                f"   投递链接：{item.job.apply_url or item.job.url}",
            ]
        )
    return lines


def build_text_digest(bundle: DigestBundle) -> str:
    all_items = bundle.all_items()
    if not all_items:
        return bundle.empty_reason or "今天没有符合条件的新岗位。"
    lines = [f"今天准备推送 {len(all_items)} 个岗位。"]
    if bundle.new_items:
        lines.extend(_group_lines("【今日新增岗位】", bundle.new_items))
    if bundle.history_items:
        lines.extend(_group_lines("【历史仍可投递岗位】", bundle.history_items))
    return "\n".join(lines)


def build_feishu_cards(bundle: DigestBundle) -> list[dict]:
    all_items = bundle.all_items()
    if not all_items:
        return [
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "grey",
                    "title": {"tag": "plain_text", "content": "今日岗位提醒"},
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": bundle.empty_reason or "今天没有符合条件的新岗位。"},
                    }
                ],
            }
        ]

    cards: list[dict] = []
    for item in all_items:
        mode_label = _job_mode_label(item)
        delivery_label = "新增岗位" if item.delivery_kind == "new" else "历史补发"
        cards.append(
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue" if item.delivery_kind == "new" else "turquoise",
                    "title": {
                        "tag": "plain_text",
                        "content": f"[{delivery_label}] {item.job.title} | {item.job.company_name or '公司未识别'}",
                    },
                },
                "elements": [
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**城市**\n{_job_city_label(item)}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**类型**\n{mode_label}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**来源**\n{item.job.source}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**截止时间**\n{item.job.deadline or '未识别'}",
                                },
                            },
                        ],
                    },
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**公司阶段**\n{item.job.company_stage or '未识别'}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**推荐分**\n{item.score:.1f}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**薪资**\n{item.job.salary_text or '未写'}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**学历**\n{item.job.degree_requirement or item.job.degree_preference or '未写'}",
                                },
                            },
                        ],
                    },
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**推荐理由**\n{'；'.join(item.reasons) or '规则匹配通过'}",
                        },
                    },
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**岗位摘要**\n{item.job.description[:350] or '暂无摘要'}",
                        },
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "投递链接"},
                                "type": "primary",
                                "url": item.job.apply_url or item.job.url,
                            }
                        ],
                    },
                ],
            }
        )
    return cards


def cards_as_contents(cards: list[dict]) -> list[str]:
    return [json.dumps(card, ensure_ascii=False) for card in cards]
