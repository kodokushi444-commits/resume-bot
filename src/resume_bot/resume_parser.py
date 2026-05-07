from __future__ import annotations

import json
import re

from .llm import TextModelClient, _extract_json_block
from .types import ResumeProfile


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
YEAR_PATTERN = re.compile(r"(20\d{2})")
DATE_RANGE_PATTERN = re.compile(r"(20\d{2})[./年-]\d{1,2}(?:[./月-]\d{1,2})?\s*[-~至]\s*(20\d{2})[./年-]\d{1,2}")
EDUCATION_PATTERN = re.compile(
    r"(?P<school>[\u4e00-\u9fa5A-Za-z0-9（）()·\s]{2,40}(?:大学|学院|学校))\s*(?:\||｜|\s)+"
    r"(?P<major>[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,24})\s*(?:\||｜|\s)+"
    r"(?P<degree>博士|硕士|本科|大专|专科).*?(?P<start>20\d{2}).*?(?P<end>20\d{2})"
)
EDUCATION_INLINE_PATTERN = re.compile(
    r"(?P<school>[\u4e00-\u9fa5A-Za-z0-9（）()·\s]{2,40}(?:大学|学院|学校))\s*(?:\||｜)\s*"
    r"(?P<major>[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,24})\s*(?:\||｜)\s*"
    r"(?P<degree>博士|硕士|本科|大专|专科)"
)
DEGREE_KEYWORDS = ["博士", "硕士", "研究生", "本科", "大专", "专科"]
CITY_INLINE_PATTERN = re.compile(r"(北京|上海|广州|深圳|杭州|成都|贵阳|武汉|南京|西安|苏州|重庆|天津|长沙|厦门|青岛)")
SECTION_HEADINGS = [
    "求职意向",
    "目标岗位",
    "教育经历",
    "实习经历",
    "工作经历",
    "项目经历",
    "校园经历",
    "实践经历",
    "社团经历",
    "技能",
    "专业技能",
    "个人技能",
    "职业技能",
    "证书",
    "校园活动",
    "获奖经历",
    "荣誉奖项",
    "个人优势",
    "个人总结",
    "自我评价",
]
ROLE_KEYWORDS = [
    "AI产品运营",
    "产品运营",
    "内容运营",
    "用户运营",
    "用户增长",
    "增长运营",
    "活动运营",
    "新媒体运营",
    "社群运营",
    "社区运营",
    "电商运营",
    "策略运营",
    "商业分析",
    "数据分析",
    "产品经理",
    "市场营销",
    "运营",
    "产品",
    "市场",
    "品牌",
]
PROJECT_HINTS = ["项目", "负责人", "创业"]


class ResumeParser:
    def __init__(self, llm_client: TextModelClient):
        self.llm_client = llm_client

    def parse(self, raw_text: str, file_name: str = "") -> ResumeProfile:
        if raw_text.strip():
            heuristic = self._parse_heuristically(raw_text)
            try:
                llm_profile = self._parse_with_llm(raw_text, file_name=file_name)
                return self._merge_profiles(llm_profile, heuristic)
            except Exception as exc:
                heuristic.raw_sections["_parse_method"] = "heuristic"
                heuristic.raw_sections["_parse_warning"] = str(exc)
                return heuristic
        return ResumeProfile()

    def _parse_with_llm(self, raw_text: str, file_name: str = "") -> ResumeProfile:
        system_prompt = (
            "你是简历结构化助手。"
            "把中文简历解析成 JSON。"
            "不要输出任何解释，只输出一个 JSON 对象。"
        )
        user_prompt = f"""
文件名: {file_name}

请从下面简历里提取字段，并严格输出 JSON:
{{
  "name": "",
  "phone": "",
  "email": "",
  "school": "",
  "major": "",
  "degree": "",
  "graduation_year": "",
  "target_roles": [],
  "target_cities": [],
  "skills": [],
  "experiences": [],
  "summary": ""
}}

要求：
1. `graduation_year` 取毕业年份，不要取出生年份。
2. `major` 只能填专业，不要把技能、项目或证书填进来。
 3. `experiences` 只保留 3 到 5 条，每条都要包含“公司/项目 + 身份 + 做过什么”，不能只写“公司 + 岗位 + 日期”。
4. `target_roles` 根据简历内容推断 1 到 5 个最适合的非开发方向。
 5. `summary` 控制在 120 字以内。
 6. 只输出合法 JSON，不要输出 markdown，不要输出注释。

简历正文:
{raw_text[:24000]}
"""
        raw_reply = self.llm_client.complete_text(system_prompt, user_prompt, max_tokens=3200)
        try:
            payload = _extract_json_block(raw_reply)
        except Exception:
            repair_prompt = f"""
下面这段内容本来应该是一个合法 JSON，但现在格式坏了。
请你只做一件事：修复成一个合法 JSON。
不要解释，不要加 markdown，不要补充额外文字。

原始内容：
{raw_reply[:12000]}
"""
            repaired_reply = self.llm_client.complete_text(system_prompt, repair_prompt, max_tokens=3200)
            payload = _extract_json_block(repaired_reply)
        payload.setdefault("raw_sections", {})
        payload["raw_sections"]["_parse_method"] = "llm"
        return ResumeProfile.from_dict(payload)

    def _parse_heuristically(self, raw_text: str) -> ResumeProfile:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        email = EMAIL_PATTERN.search(raw_text)
        phone = PHONE_PATTERN.search(raw_text)
        sections = self._extract_sections(lines)
        education_lines = self._education_candidate_lines(lines, sections)
        education_line = self._find_education_line(education_lines)
        school = ""
        major = ""
        degree = ""
        graduation_year = ""
        if education_line:
            match = EDUCATION_PATTERN.search(education_line) or EDUCATION_INLINE_PATTERN.search(education_line)
            if match:
                school = match.group("school").strip()
                major = match.group("major").strip()
                degree = match.group("degree").strip()
                graduation_year = match.groupdict().get("end", "").strip()
        if not school:
            school = self._infer_school(education_lines)
        if not degree:
            for keyword in DEGREE_KEYWORDS:
                if keyword in raw_text:
                    degree = "硕士" if keyword == "研究生" else keyword
                    break
        if not graduation_year:
            graduation_year = self._infer_graduation_year(lines, education_line, sections)
        if not major:
            major = self._infer_major(lines, education_line, sections)
        target_roles = self._infer_target_roles(raw_text)
        target_cities = self._infer_target_cities(lines)
        experiences = self._infer_experiences(lines, sections)
        skills = self._infer_skills(lines, sections)
        summary = self._build_heuristic_summary(
            school=school,
            major=major,
            degree=degree,
            target_roles=target_roles,
            skills=skills,
            experiences=experiences,
        )
        return ResumeProfile(
            name=lines[0] if lines else "",
            phone=phone.group(0) if phone else "",
            email=email.group(0) if email else "",
            school=school,
            major=major,
            degree=degree,
            graduation_year=graduation_year,
            target_roles=target_roles or ["运营"],
            target_cities=target_cities,
            skills=skills,
            experiences=experiences,
            summary=summary or "\n".join(lines[:10])[:120],
            raw_sections={**sections, "_parse_method": "heuristic"},
        )

    def _merge_profiles(self, llm_profile: ResumeProfile, heuristic_profile: ResumeProfile) -> ResumeProfile:
        merged = ResumeProfile.from_dict(llm_profile.to_dict())
        if heuristic_profile.graduation_year:
            llm_year = (merged.graduation_year or "").strip()
            if not llm_year or llm_year != heuristic_profile.graduation_year:
                merged.graduation_year = heuristic_profile.graduation_year
        if heuristic_profile.school and (not merged.school or len(merged.school) > 40):
            merged.school = heuristic_profile.school
        if heuristic_profile.major and (
            not merged.major or len(merged.major) > 24 or any(token in merged.major for token in ["PPT", "Excel", "Word"])
        ):
            merged.major = heuristic_profile.major
        if heuristic_profile.degree and not merged.degree:
            merged.degree = heuristic_profile.degree
        merged.target_roles = self._merge_list(llm_profile.target_roles, heuristic_profile.target_roles)
        merged.target_cities = self._merge_list(llm_profile.target_cities, heuristic_profile.target_cities)
        merged.skills = self._merge_list(llm_profile.skills, heuristic_profile.skills)
        merged.experiences = self._merge_experiences(llm_profile.experiences, heuristic_profile.experiences)
        if heuristic_profile.summary and (not merged.summary or len(str(merged.summary)) > 160):
            merged.summary = heuristic_profile.summary
        merged.raw_sections = {**heuristic_profile.raw_sections, **llm_profile.raw_sections}
        return merged

    def _stringify_item(self, item) -> str:
        if item is None:
            return ""
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            for key in ["text", "value", "name", "title", "summary", "content", "description"]:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            text_parts = [str(value).strip() for value in item.values() if isinstance(value, str) and value.strip()]
            if text_parts:
                return " ".join(text_parts[:3]).strip()
            return json.dumps(item, ensure_ascii=False)
        return str(item).strip()

    def _merge_list(self, preferred: list, fallback: list) -> list[str]:
        values = []
        seen = set()
        for item in [*preferred, *fallback]:
            normalized = self._stringify_item(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            values.append(normalized)
        return values

    def _merge_experiences(self, preferred: list[str], fallback: list[str]) -> list[str]:
        if preferred and any(len(item) >= 18 for item in preferred):
            return self._merge_list(preferred, fallback)
        return self._merge_list(fallback, preferred)

    def _extract_sections(self, lines: list[str]) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current_heading = "header"
        sections[current_heading] = []
        for line in lines:
            compact = line.replace(" ", "")
            if compact in SECTION_HEADINGS:
                current_heading = compact
                sections.setdefault(current_heading, [])
                continue
            sections.setdefault(current_heading, []).append(line)
        return {key: "\n".join(value).strip() for key, value in sections.items() if any(value)}

    def _education_candidate_lines(self, lines: list[str], sections: dict[str, str]) -> list[str]:
        candidates = list(lines)
        education_block = sections.get("教育经历", "")
        if education_block:
            candidates = [line.strip() for line in education_block.splitlines() if line.strip()] + candidates
        return candidates

    def _find_education_line(self, education_lines: list[str]) -> str:
        for line in education_lines:
            if EDUCATION_PATTERN.search(line) or EDUCATION_INLINE_PATTERN.search(line):
                return line
        for index, line in enumerate(education_lines):
            if not any(token in line for token in ["大学", "学院", "学校"]):
                continue
            window = " ".join(education_lines[index : index + 3])
            if any(token in window for token in DEGREE_KEYWORDS) or DATE_RANGE_PATTERN.search(window):
                return window
        return ""

    def _infer_school(self, education_lines: list[str]) -> str:
        for line in education_lines:
            if not any(token in line for token in ["大学", "学院", "学校"]):
                continue
            match = re.search(r"[\u4e00-\u9fa5A-Za-z0-9（）()·\s]{2,40}(?:大学|学院|学校)", line)
            if match:
                return self._clean_line(match.group(0))
        return ""

    def _clean_major_value(self, value: str) -> str:
        normalized = self._clean_line(value)
        normalized = re.sub(r"^(专业|主修专业|所学专业)\s*[：: ]?", "", normalized)
        normalized = re.sub(r"(博士|硕士|研究生|本科|大专|专科).*$", "", normalized).strip("｜| /，,；;")
        normalized = re.sub(r"(GPA|绩点|排名|成绩).*$", "", normalized, flags=re.IGNORECASE).strip("｜| /，,；;")
        if any(token in normalized for token in ["课程", "技能", "证书", "奖学金"]):
            return ""
        return normalized if 2 <= len(normalized) <= 24 else ""

    def _infer_graduation_year(self, lines: list[str], education_line: str, sections: dict[str, str]) -> str:
        if education_line:
            match = DATE_RANGE_PATTERN.search(education_line)
            if match:
                return match.group(2)
        education_block = sections.get("教育经历", "")
        if education_block:
            candidate_years = [int(end) for _, end in DATE_RANGE_PATTERN.findall(education_block)]
            if candidate_years:
                return str(max(candidate_years))
        candidate_years: list[int] = []
        for line in lines:
            if "大学" in line or "学院" in line:
                for year in YEAR_PATTERN.findall(line):
                    candidate_years.append(int(year))
        if candidate_years:
            return str(max(candidate_years))
        fallback_years = [int(year) for year in YEAR_PATTERN.findall("\n".join(lines)) if int(year) >= 2024]
        return str(max(fallback_years)) if fallback_years else ""

    def _infer_major(self, lines: list[str], education_line: str, sections: dict[str, str]) -> str:
        if education_line:
            match = EDUCATION_PATTERN.search(education_line) or EDUCATION_INLINE_PATTERN.search(education_line)
            if match:
                return match.group("major").strip()
        education_block = sections.get("教育经历", "")
        for line in [line.strip() for line in education_block.splitlines() if line.strip()]:
            match = EDUCATION_INLINE_PATTERN.search(line)
            if match:
                return match.group("major").strip()
            if any(degree in line for degree in DEGREE_KEYWORDS):
                major = self._clean_major_value(line)
                if major:
                    return major
        for line in lines:
            if "专业" in line and len(line) <= 24:
                major = self._clean_major_value(line)
                if major:
                    return major
        return ""

    def _infer_target_roles(self, raw_text: str) -> list[str]:
        explicit_text = "\n".join(
            line
            for line in raw_text.splitlines()[:40]
            if any(token in line for token in ["求职意向", "目标岗位", "意向岗位", "应聘岗位", "职业目标"])
        )
        search_text = f"{explicit_text}\n{raw_text}" if explicit_text else raw_text
        found: list[str] = []
        for keyword in ROLE_KEYWORDS:
            if keyword not in search_text:
                continue
            if any(keyword in existing and keyword != existing for existing in found):
                continue
            found = [existing for existing in found if existing not in keyword]
            found.append(keyword)
        return found[:5]

    def _infer_target_cities(self, lines: list[str]) -> list[str]:
        cities: list[str] = []
        for line in lines[:20]:
            if "城市" not in line and "地点" not in line:
                continue
            cities.extend(CITY_INLINE_PATTERN.findall(line))
        return list(dict.fromkeys(cities))

    def _infer_skills(self, lines: list[str], sections: dict[str, str]) -> list[str]:
        skill_text = "\n".join(
            sections.get(name, "") for name in ["技能", "专业技能", "个人技能", "职业技能", "证书"]
        ).strip()
        if not skill_text:
            skill_text = "\n".join(line for line in lines if any(keyword in line for keyword in ["熟练", "掌握", "技能", "工具", "PPT", "Excel", "Word"]))
        tokens = re.split(r"[；;，,、\n/]+", skill_text)
        skills = []
        for token in tokens:
            normalized = token.replace("技能", "").replace("专业技能", "").replace("个人技能", "").strip("：: ")
            if 1 < len(normalized) <= 30:
                skills.append(normalized)
        return list(dict.fromkeys(skills))[:12]

    def _build_heuristic_summary(
        self,
        *,
        school: str,
        major: str,
        degree: str,
        target_roles: list[str],
        skills: list[str],
        experiences: list[str],
    ) -> str:
        parts: list[str] = []
        education_bits = [item for item in [school, major, degree] if item]
        if education_bits:
            parts.append("教育：" + " / ".join(education_bits[:3]))
        if target_roles:
            parts.append("目标：" + "、".join(target_roles[:3]))
        if skills:
            parts.append("技能：" + "、".join(skills[:4]))
        if experiences:
            parts.append("经历：" + self._clean_line(experiences[0])[:42])
        return "；".join(parts)[:120]

    def _clean_line(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
        normalized = normalized.replace("W ord", "Word")
        return normalized

    def _looks_like_title_with_date(self, line: str) -> bool:
        if not DATE_RANGE_PATTERN.search(line):
            return False
        if self._looks_like_date_only(line):
            return False
        if any(token in line for token in ["大学", "学院", "学校", "本科", "硕士", "博士"]):
            return False
        return True

    def _looks_like_date_only(self, line: str) -> bool:
        compact = self._clean_line(line)
        return bool(compact and DATE_RANGE_PATTERN.fullmatch(compact))

    def _format_experience_entry(self, title: str, details: list[str], limit: int = 280) -> str:
        detail_text = " ".join(self._clean_line(item) for item in details if self._clean_line(item))
        payload = f"{self._clean_line(title)} {detail_text}".strip()
        return payload[:limit]

    def _split_details_by_titles(self, detail_lines: list[str], title_count: int) -> list[list[str]]:
        if title_count <= 1 or len(detail_lines) <= 1:
            return [detail_lines]
        groups: list[list[str]] = [[] for _ in range(title_count)]
        group_index = 0
        for index, raw_line in enumerate(detail_lines):
            line = self._clean_line(raw_line)
            remaining_lines = len(detail_lines) - index
            remaining_groups = title_count - group_index - 1
            current_group = groups[group_index]
            should_start_new_group = False
            if group_index < title_count - 1 and current_group:
                if remaining_lines <= remaining_groups:
                    should_start_new_group = True
                elif "：" in line and not any("：" in existing for existing in current_group) and len(current_group) >= 2:
                    should_start_new_group = True
            if should_start_new_group:
                group_index += 1
            groups[group_index].append(line)
        return [group for group in groups if group]

    def _experiences_from_section_groups(self, section_text: str) -> list[str]:
        lines = [self._clean_line(line) for line in section_text.splitlines() if self._clean_line(line)]
        if not lines:
            return []
        entries: list[str] = []
        current_title = ""
        current_details: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if (
                next_line
                and self._looks_like_date_only(next_line)
                and not self._looks_like_date_only(line)
                and line not in SECTION_HEADINGS
            ):
                if current_title:
                    entries.append(self._format_experience_entry(current_title, current_details))
                current_title = f"{line} {next_line}"
                current_details = []
                index += 2
                continue
            if self._looks_like_title_with_date(line):
                if current_title:
                    entries.append(self._format_experience_entry(current_title, current_details))
                current_title = line
                current_details = []
                index += 1
                continue
            if current_title and self._looks_like_date_only(line):
                current_title = f"{current_title} {line}"
                index += 1
                continue
            if current_title:
                current_details.append(line)
            index += 1
        if current_title:
            entries.append(self._format_experience_entry(current_title, current_details))
        return [item for item in entries if item]

    def _header_title_lines(self, sections: dict[str, str]) -> list[str]:
        header_lines = [self._clean_line(line) for line in sections.get("header", "").splitlines() if self._clean_line(line)]
        return [line for line in header_lines if self._looks_like_title_with_date(line)]

    def _experiences_from_header_and_sections(self, sections: dict[str, str]) -> list[str]:
        title_lines = self._header_title_lines(sections)
        if not title_lines:
            return []
        internship_titles = [line for line in title_lines if not any(token in line for token in PROJECT_HINTS)]
        project_titles = [line for line in title_lines if any(token in line for token in PROJECT_HINTS)]
        internship_lines = [self._clean_line(line) for line in sections.get("实习经历", "").splitlines() if self._clean_line(line)]
        project_lines = [self._clean_line(line) for line in sections.get("项目经历", "").splitlines() if self._clean_line(line)]
        experiences: list[str] = []
        for title, detail_lines in zip(internship_titles, self._split_details_by_titles(internship_lines, len(internship_titles))):
            experiences.append(self._format_experience_entry(title, detail_lines))
        for title, detail_lines in zip(project_titles, self._split_details_by_titles(project_lines, len(project_titles))):
            experiences.append(self._format_experience_entry(title, detail_lines))
        return [item for item in experiences if item]

    def _infer_experiences(self, lines: list[str], sections: dict[str, str]) -> list[str]:
        grouped_experiences: list[str] = []
        for name in ["实习经历", "工作经历", "项目经历", "实践经历", "校园经历", "社团经历"]:
            if sections.get(name):
                grouped_experiences.extend(self._experiences_from_section_groups(sections[name]))
        if grouped_experiences:
            return list(dict.fromkeys(grouped_experiences))[:6]

        fallback_experiences = self._experiences_from_header_and_sections(sections)
        if fallback_experiences:
            return list(dict.fromkeys(fallback_experiences))[:6]

        text_lines = [self._clean_line(line) for line in lines if self._clean_line(line)]
        experiences: list[str] = []
        for index, line in enumerate(text_lines):
            if not DATE_RANGE_PATTERN.search(line):
                continue
            detail_lines = []
            if index > 0:
                previous_line = text_lines[index - 1]
                if not DATE_RANGE_PATTERN.search(previous_line) and previous_line not in SECTION_HEADINGS:
                    detail_lines.append(previous_line)
            detail_lines.append(line)
            for offset in range(1, 4):
                if index + offset >= len(text_lines):
                    break
                next_line = text_lines[index + offset]
                if DATE_RANGE_PATTERN.search(next_line):
                    break
                if any(next_line == item for item in SECTION_HEADINGS):
                    break
                detail_lines.append(next_line)
            experiences.append(" ".join(detail_lines)[:220])
        return list(dict.fromkeys(experiences))[:6]


def render_profile_summary(profile: ResumeProfile) -> str:
    lines = [
        f"姓名：{profile.name or '未识别'}",
        f"学校：{profile.school or '未识别'}",
        f"专业：{profile.major or '未识别'}",
        f"学历：{profile.degree or '未识别'}",
        f"毕业年份：{profile.graduation_year or '未识别'}",
        f"目标岗位：{', '.join(profile.target_roles) or '未识别'}",
        f"目标城市：{', '.join(profile.target_cities) or '不限'}",
        f"技能标签：{', '.join(profile.skills[:8]) or '未识别'}",
        f"总结：{profile.summary or '未识别'}",
        f"解析方式：{profile.raw_sections.get('_parse_method', '未知')}",
    ]
    if profile.experiences:
        lines.append("经历摘要：")
        lines.extend(f"- {item}" for item in profile.experiences[:3])
    else:
        lines.append("经历摘要：未识别")
    if profile.raw_sections.get("_parse_warning"):
        lines.append(f"解析警告：{profile.raw_sections['_parse_warning']}")
    return "\n".join(lines)
