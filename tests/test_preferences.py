from __future__ import annotations

import unittest

from src.resume_bot.preferences import (
    apply_manual_settings,
    normalize_settings_lists,
    reseed_settings_from_profile,
    seed_settings_from_profile,
)
from src.resume_bot.types import ResumeProfile, UserSettings


class PreferencesTests(unittest.TestCase):
    def test_seed_settings_dedupes_keyword_roots(self) -> None:
        settings = UserSettings(
            user_id="me",
            preferred_keywords=["Excel", "SQL"],
        )
        profile = ResumeProfile(
            skills=[
                "Excel（VLOOKUP等高级公式、可视化报表）",
                "SQL（数据库查询）",
                "数据分析",
            ]
        )
        updated = seed_settings_from_profile(settings, profile)
        self.assertIn("Excel（VLOOKUP等高级公式、可视化报表）", updated.preferred_keywords)
        self.assertIn("SQL（数据库查询）", updated.preferred_keywords)
        self.assertIn("数据分析", updated.preferred_keywords)
        self.assertNotIn("Excel", updated.preferred_keywords)
        self.assertNotIn("SQL", updated.preferred_keywords)

    def test_apply_manual_settings_dedupes_keyword_roots(self) -> None:
        settings = UserSettings(user_id="me")
        updated = apply_manual_settings(
            settings,
            preferred_roles=[],
            preferred_cities=[],
            preferred_keywords=["Excel", "Excel（VLOOKUP）", "SQL", "SQL（数据库查询）"],
            excluded_keywords=[],
            job_types=["校招"],
            campus_role_mode="full_time",
            salary_min=0,
            salary_max=0,
            max_degree_requirement="本科",
        )
        self.assertEqual(updated.preferred_keywords, ["Excel（VLOOKUP）", "SQL（数据库查询）"])

    def test_normalize_settings_lists_cleans_legacy_duplicates(self) -> None:
        settings = UserSettings(
            user_id="me",
            preferred_roles=["运营", "运营", "产品运营"],
            preferred_keywords=["Excel", "Excel（VLOOKUP）", "SQL", "SQL（数据库查询）"],
            job_types=["校招", "校招"],
            max_degree_requirement="专科",
        )
        normalized = normalize_settings_lists(settings)
        self.assertEqual(normalized.preferred_roles, ["运营", "产品运营"])
        self.assertEqual(normalized.preferred_keywords, ["Excel（VLOOKUP）", "SQL（数据库查询）"])
        self.assertEqual(normalized.job_types, ["校招"])
        self.assertEqual(normalized.max_degree_requirement, "大专")

    def test_reseed_settings_from_profile_replaces_old_resume_seeded_fields(self) -> None:
        settings = UserSettings(
            user_id="me",
            preferred_roles=["运营", "产品运营", "财务助理"],
            preferred_cities=["北京", "成都", "天津"],
            preferred_keywords=["AI评测", "Excel（VLOOKUP）", "基金从业资格"],
            excluded_keywords=["销售"],
            salary_min=3000,
            salary_max=20000,
            max_degree_requirement="本科",
        )
        previous = ResumeProfile(
            target_roles=["运营", "产品运营"],
            target_cities=["北京", "成都"],
            skills=["AI评测", "Excel（VLOOKUP）"],
        )
        current = ResumeProfile(
            target_roles=["财务助理", "审计助理"],
            target_cities=["天津"],
            skills=["基金从业资格", "Excel（数据透视表）"],
            degree="本科",
        )

        updated = reseed_settings_from_profile(settings, current, previous)

        self.assertEqual(updated.preferred_roles, ["财务助理", "审计助理"])
        self.assertEqual(updated.preferred_cities, ["天津"])
        self.assertEqual(updated.preferred_keywords, ["基金从业资格", "Excel（数据透视表）"])
        self.assertEqual(updated.excluded_keywords, ["销售"])
        self.assertEqual(updated.salary_min, 3000)
        self.assertEqual(updated.salary_max, 20000)
        self.assertEqual(updated.max_degree_requirement, "本科")


if __name__ == "__main__":
    unittest.main()
