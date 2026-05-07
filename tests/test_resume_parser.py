from __future__ import annotations

import unittest

from src.resume_bot.llm import NoopTextClient
from src.resume_bot.resume_parser import ResumeParser, render_profile_summary


class ResumeParserTests(unittest.TestCase):
    def test_heuristic_parser_extracts_recommendation_fields_from_common_resume_layout(self) -> None:
        raw_text = """
张三
13800138000
zhangsan@example.com
求职意向：内容运营 / 用户增长 / AI产品运营

教育经历
上海财经大学
市场营销 本科
2022.09-2026.06

实习经历
字节跳动 内容运营实习生
2025.06-2025.09
负责短视频内容选题和账号数据复盘，提升内容发布效率。
使用 Excel、SQL 分析用户点击和转化表现。

项目经历
校园社群增长项目 负责人
2024.03-2024.06
搭建社群分层运营机制，完成用户调研并沉淀活动 SOP。

专业技能
Excel、SQL、用户调研、数据分析
""".strip()

        profile = ResumeParser(NoopTextClient()).parse(raw_text, file_name="resume.txt")

        self.assertEqual(profile.school, "上海财经大学")
        self.assertEqual(profile.major, "市场营销")
        self.assertEqual(profile.degree, "本科")
        self.assertEqual(profile.graduation_year, "2026")
        self.assertIn("内容运营", profile.target_roles)
        self.assertIn("用户增长", profile.target_roles)
        self.assertIn("AI产品运营", profile.target_roles)
        self.assertNotIn("运营", profile.target_roles[:3])
        self.assertIn("SQL", profile.skills)
        self.assertIn("用户调研", profile.skills)
        self.assertTrue(
            any("字节跳动 内容运营实习生 2025.06-2025.09" in item and "SQL" in item for item in profile.experiences)
        )
        self.assertTrue(
            any("校园社群增长项目 负责人 2024.03-2024.06" in item and "用户调研" in item for item in profile.experiences)
        )
        self.assertIn("内容运营", profile.summary)
        self.assertLessEqual(len(profile.summary), 120)
        self.assertIn("经历摘要", render_profile_summary(profile))


if __name__ == "__main__":
    unittest.main()
