from __future__ import annotations

import unittest

from src.resume_bot.normalization import infer_degree_requirement, infer_employment_mode


class NormalizationTests(unittest.TestCase):
    def test_infer_degree_requirement_handles_master_postgraduate_phrase(self) -> None:
        requirement, preference = infer_degree_requirement(
            "岗位要求\n1.硕士研究生及以上学历，计算机、自动化、软件工程专业优先。"
        )
        self.assertEqual(requirement, "硕士")
        self.assertEqual(preference, "")

    def test_infer_employment_mode_prefers_campus_full_time_over_intern_note(self) -> None:
        mode = infer_employment_mode(
            "数据开发岗（26/27届）",
            "岗位要求：硕士研究生学历，需线上或线下实习至少半个月。",
        )
        self.assertEqual(mode, "full_time")


if __name__ == "__main__":
    unittest.main()
