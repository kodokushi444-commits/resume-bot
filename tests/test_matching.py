from __future__ import annotations

import unittest

from src.resume_bot.matching import heuristic_match, should_skip_job
from src.resume_bot.types import JobPosting, ResumeProfile, UserSettings


class MatchingTests(unittest.TestCase):
    def test_should_skip_unknown_application_status(self) -> None:
        settings = UserSettings(user_id="me")
        job = JobPosting(
            source="nowcoder_direct",
            url="https://www.nowcoder.com/jobs/detail/1",
            title="用户运营",
            company_name="快手",
            application_status="unknown",
        )
        skip, reason = should_skip_job(job, settings, last_action="")
        self.assertTrue(skip)
        self.assertEqual(reason, "投递状态未确认")

    def test_deferred_action_does_not_skip_job(self) -> None:
        settings = UserSettings(user_id="me")
        job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/1.html",
            title="AI 产品运营",
            company_name="测试公司",
            application_status="open",
        )
        skip, reason = should_skip_job(job, settings, last_action="deferred")
        self.assertFalse(skip)
        self.assertEqual(reason, "")

    def test_heuristic_match_cites_resume_target_and_skills(self) -> None:
        settings = UserSettings(user_id="me", job_types=["社招"], campus_role_mode="both")
        profile = ResumeProfile(target_roles=["内容运营"], skills=["SQL", "用户调研"])
        job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/1.html",
            title="内容运营专员",
            company_name="测试公司",
            city="深圳",
            job_type="社招",
            employment_mode="full_time",
            application_status="open",
            description="负责用户调研和 SQL 数据分析，沉淀内容策略。",
        )

        result = heuristic_match(job, profile, settings)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("匹配简历目标方向：内容运营", result.reasons)
        self.assertIn("命中简历技能：SQL, 用户调研", result.reasons)


if __name__ == "__main__":
    unittest.main()
