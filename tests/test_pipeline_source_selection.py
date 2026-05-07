from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.resume_bot.config import load_config
from src.resume_bot.pipeline import ResumeBotPipeline
from src.resume_bot.types import JobPosting


class PipelineSourceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "resume_bot.db"
        self.source_registry_path = root / "source_registry.json"
        self.default_settings_path = root / "default_settings.json"
        self.source_registry_path.write_text(
            json.dumps(
                {
                    "sources": [
                        {"name": "nowcoder_direct", "type": "nowcoder-direct", "enabled": True},
                        {"name": "nowcoder_schedule", "type": "nowcoder-schedule", "enabled": True},
                        {"name": "boss_cli", "type": "boss-cli", "enabled": False},
                        {"name": "boss_browser", "type": "boss-browser", "enabled": False},
                        {"name": "company_watchlist", "type": "company-watchlist", "enabled": True},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.default_settings_path.write_text(
            json.dumps(
                {
                    "preferred_roles": [],
                    "avoided_roles": [],
                    "preferred_cities": [],
                    "excluded_cities": [],
                    "preferred_company_stages": [],
                    "preferred_keywords": [],
                    "excluded_keywords": [],
                    "job_types": ["校招", "社招"],
                    "campus_role_mode": "full_time",
                    "salary_min": 0,
                    "salary_max": 0,
                    "accept_unspecified_salary": True,
                    "max_degree_requirement": "",
                    "company_watchlist": [],
                    "history_backfill_limit": 10,
                    "push_time": "09:00",
                    "notify_when_empty": True,
                    "allow_repush_when_updated": True,
                    "max_items_per_push": 20,
                    "skip_unknown_city_when_city_filtered": True,
                    "feishu_receive_id": "",
                    "feishu_receive_id_type": "open_id",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.env = patch.dict(
            os.environ,
            {
                "RESUME_BOT_DB_PATH": str(self.db_path),
                "RESUME_BOT_SOURCE_REGISTRY_PATH": str(self.source_registry_path),
                "RESUME_BOT_DEFAULT_SETTINGS_PATH": str(self.default_settings_path),
            },
            clear=False,
        )
        self.env.start()
        self.pipeline = ResumeBotPipeline(load_config())
        self.pipeline.config.enable_llm_rerank = False

    def tearDown(self) -> None:
        self.env.stop()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def test_available_fetch_sources_defaults(self) -> None:
        available = self.pipeline.available_fetch_sources()
        self.assertEqual([item["id"] for item in available], ["nowcoder", "nowcoder_schedule", "boss"])
        self.assertEqual(self.pipeline.default_fetch_source_groups(), ["nowcoder", "nowcoder_schedule"])

    def test_resolve_selected_source_names(self) -> None:
        self.assertEqual(self.pipeline.resolve_selected_source_names(["nowcoder"]), ["nowcoder_direct"])
        self.assertEqual(self.pipeline.resolve_selected_source_names(["nowcoder_schedule"]), ["nowcoder_schedule"])
        self.assertEqual(
            self.pipeline.resolve_selected_source_names(["boss"]),
            ["boss_browser"],
        )

    def test_build_sources_only_uses_selected_groups(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        sources = self.pipeline._build_sources(settings, None, ["nowcoder_direct"])
        self.assertEqual([source.name for source in sources], ["nowcoder_direct"])

    def test_build_sources_applies_runtime_fetch_limit(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        sources = self.pipeline._build_sources(
            settings,
            None,
            ["nowcoder_direct", "nowcoder_schedule"],
            runtime_fetch_limit=12,
        )
        by_name = {source.name: source for source in sources}
        self.assertEqual(by_name["nowcoder_direct"].max_jobs, 12)
        self.assertEqual(by_name["nowcoder_direct"].max_detail_pages, 12)
        self.assertEqual(by_name["nowcoder_schedule"].max_jobs, 12)

    def test_load_active_jobs_can_filter_by_fetch_session(self) -> None:
        job_a = JobPosting(source="nowcoder_direct", url="https://example.com/a", title="运营A", fetch_session_id="s1")
        job_a.ensure_ids()
        job_b = JobPosting(source="nowcoder_direct", url="https://example.com/b", title="运营B", fetch_session_id="s2")
        job_b.ensure_ids()
        with patch.object(self.pipeline.store, "load_jobs", return_value=[job_a, job_b]):
            with patch.object(self.pipeline, "active_source_names", return_value=["nowcoder_direct"]):
                jobs = self.pipeline.load_active_jobs("me", ["nowcoder_direct"], fetch_session_id="s2")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "运营B")

    def test_rank_jobs_with_fetch_session_reads_disabled_boss_source_history(self) -> None:
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            salary_min=10000,
            salary_max=15000,
            application_status="open",
            fetch_session_id="boss-session-1",
        )
        boss_job.ensure_ids()
        load_calls: list[list[str] | None] = []

        def fake_load_jobs(source_names=None):
            load_calls.append(source_names)
            if source_names and "boss_browser" in source_names:
                return [boss_job]
            return []

        with patch.object(self.pipeline.store, "load_jobs", side_effect=fake_load_jobs):
            with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
                with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                    ranked, debug = self.pipeline._rank_jobs_with_debug("me", fetch_session_id="boss-session-1")
        self.assertEqual(len(ranked), 1)
        self.assertIn("boss_browser", debug["source_names_used"])
        self.assertTrue(load_calls)
        self.assertIn("boss_browser", load_calls[0])

    def test_rank_jobs_with_explicit_source_selection_still_respects_selection(self) -> None:
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            salary_min=10000,
            salary_max=15000,
            application_status="open",
            fetch_session_id="boss-session-2",
        )
        boss_job.ensure_ids()

        def fake_load_jobs(source_names=None):
            if source_names and "boss_browser" in source_names:
                return [boss_job]
            return []

        with patch.object(self.pipeline.store, "load_jobs", side_effect=fake_load_jobs):
            with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
                with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                    ranked, debug = self.pipeline._rank_jobs_with_debug(
                        "me",
                        selected_source_names=["nowcoder_direct"],
                        fetch_session_id="boss-session-2",
                    )
        self.assertEqual(len(ranked), 0)
        self.assertEqual(debug["source_names_used"], ["nowcoder_direct"])

    def test_review_session_boss_social_profile_allows_boss_queue_preview(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        settings.job_types = ["校招"]
        self.pipeline.store.save_settings(settings)
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            salary_min=10000,
            salary_max=15000,
            application_status="unknown",
            fetch_session_id="boss-session-3",
        )
        boss_job.ensure_ids()

        def fake_load_jobs(source_names=None):
            if source_names and "boss_browser" in source_names:
                return [boss_job]
            return []

        with patch.object(self.pipeline.store, "load_jobs", side_effect=fake_load_jobs):
            with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
                with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                    default_review = self.pipeline.review_fetch_session("me", "boss-session-3", limit=5)
                    boss_review = self.pipeline.review_fetch_session(
                        "me",
                        "boss-session-3",
                        limit=5,
                        review_profile="boss_social",
                    )
        self.assertEqual(default_review["matched_count"], 0)
        self.assertEqual(boss_review["matched_count"], 1)
        self.assertEqual(boss_review["review_profile"]["name"], "boss_social")
        self.assertEqual(boss_review["items"][0]["job"]["application_status"], "open")

    def test_supplement_boss_details_no_pending_jobs_returns_noop(self) -> None:
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            apply_url="https://www.zhipin.com/job_detail/demo.html",
            source_job_id="demo",
            job_type="社招",
            employment_mode="full_time",
            application_status="open",
            detail_fetched=True,
            fetch_session_id="boss-session-detail-noop",
            description="完整 JD",
        )
        boss_job.ensure_ids()
        self.pipeline.store.upsert_jobs([boss_job])

        result = self.pipeline.supplement_boss_details("me", "boss-session-detail-noop", limit=3)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pending_job_count"], 0)
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["upsert"]["touched"], 0)

    def test_recommend_boss_session_uses_only_completed_jd_and_persists_snapshot(self) -> None:
        session_id = "boss-session-m8-recommend"
        completed_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/complete.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            apply_url="https://www.zhipin.com/job_detail/complete.html",
            source_job_id="complete",
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            salary_min=10000,
            salary_max=15000,
            degree_requirement="本科",
            application_status="open",
            detail_fetched=True,
            fetch_session_id=session_id,
            description="岗位职责：负责用户运营和内容运营。任职要求：熟悉数据分析，有活动策划经验。",
        )
        pending_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/pending.html",
            title="产品运营实习",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            apply_url="https://www.zhipin.com/job_detail/pending.html",
            source_job_id="pending",
            job_type="社招",
            employment_mode="full_time",
            salary_text="8-10K",
            application_status="open",
            detail_fetched=False,
            fetch_session_id=session_id,
            description="列表摘要：运营岗位",
        )
        other_session_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/other.html",
            title="产品运营",
            company_name="其他公司",
            city="深圳",
            city_list=["深圳"],
            apply_url="https://www.zhipin.com/job_detail/other.html",
            source_job_id="other",
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            application_status="open",
            detail_fetched=True,
            fetch_session_id="other-session",
            description="岗位职责：负责用户运营。",
        )
        for job in (completed_job, pending_job, other_session_job):
            job.ensure_ids()
        self.pipeline.store.upsert_jobs([completed_job, pending_job, other_session_job])

        with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
            with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                recommendation = self.pipeline.recommend_boss_session("me", session_id, limit=5)

        self.assertTrue(recommendation["ok"])
        self.assertEqual(recommendation["session_job_count"], 2)
        self.assertEqual(recommendation["recommendation_base_count"], 1)
        self.assertEqual(recommendation["matched_count"], 1)
        self.assertEqual(recommendation["items"][0]["job"]["source_job_id"], "complete")

        loaded = self.pipeline.load_boss_session_recommendation(session_id, limit=5)
        self.assertEqual(loaded["fetch_session_id"], session_id)
        self.assertEqual(loaded["recommendation_base_count"], 1)
        self.assertEqual(loaded["items"][0]["job"]["source_job_id"], "complete")

    def test_supplement_boss_session_and_recommend_targets_all_pending_jobs(self) -> None:
        session_id = "boss-session-m8-full-pending"
        seed_jobs: list[JobPosting] = []
        updated_jobs: list[dict] = []
        for index in range(2):
            job = JobPosting(
                source="boss_browser",
                url=f"https://www.zhipin.com/job_detail/m8-{index}.html",
                title=f"产品运营 {index}",
                company_name="测试公司",
                city="深圳",
                city_list=["深圳"],
                apply_url=f"https://www.zhipin.com/job_detail/m8-{index}.html",
                source_job_id=f"m8-{index}",
                job_type="社招",
                employment_mode="full_time",
                salary_text="10-15K",
                salary_min=10000,
                salary_max=15000,
                application_status="open",
                detail_fetched=False,
                fetch_session_id=session_id,
                description="列表摘要：产品运营",
            )
            job.ensure_ids()
            seed_jobs.append(job)
            updated = JobPosting.from_dict(job.to_dict())
            updated.description = f"岗位职责：负责用户运营和数据分析 {index}。任职要求：本科，沟通能力好。"
            updated.detail_fetched = True
            updated.application_status = "open"
            updated.ensure_ids()
            updated_jobs.append(updated.to_dict())
        self.pipeline.store.upsert_jobs(seed_jobs)

        captured: dict = {}

        def fake_detail_probe(jobs, *, limit, fetch_session_id):
            captured["limit"] = limit
            captured["fetch_session_id"] = fetch_session_id
            captured["jobs_count"] = len(jobs)
            return {
                "ok": True,
                "attempted_count": limit,
                "success_count": limit,
                "updated_jobs": updated_jobs,
                "results": [{"ok": True, "job_url": item["url"], "title": item["title"]} for item in updated_jobs],
            }

        with patch.object(self.pipeline, "_run_boss_cdp_detail_probe", side_effect=fake_detail_probe):
            with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
                with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                    result = self.pipeline.supplement_boss_session_and_recommend("me", session_id)

        self.assertTrue(result["ok"])
        self.assertEqual(captured["limit"], 2)
        self.assertEqual(captured["jobs_count"], 2)
        self.assertEqual(result["supplement"]["target_pending_job_count"], 2)
        self.assertEqual(result["supplement"]["detail_fetched_count"], 2)
        self.assertEqual(result["recommendation"]["recommendation_base_count"], 2)
        self.assertEqual(result["recommendation"]["matched_count"], 2)

    def test_supplement_boss_details_updates_jobs_from_detail_probe(self) -> None:
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            apply_url="https://www.zhipin.com/job_detail/demo.html",
            source_job_id="demo",
            job_type="社招",
            employment_mode="full_time",
            application_status="unknown",
            fetch_session_id="boss-session-detail-1",
            description="经验：1年\n学历：本科",
        )
        boss_job.ensure_ids()
        self.pipeline.store.upsert_jobs([boss_job])

        captured: dict = {}

        def fake_detail_probe(jobs, *, limit, fetch_session_id):
            captured["jobs"] = jobs
            captured["limit"] = limit
            captured["fetch_session_id"] = fetch_session_id
            updated = JobPosting.from_dict(boss_job.to_dict())
            updated.description = "岗位职责：负责用户运营\\n任职要求：熟悉数据分析\\n福利待遇：双休"
            updated.detail_fetched = True
            updated.application_status = "open"
            updated.ensure_ids()
            return {
                "ok": True,
                "attempted_count": 1,
                "success_count": 1,
                "updated_jobs": [updated.to_dict()],
                "results": [{"ok": True, "job_url": updated.url, "title": updated.title}],
            }

        with patch.object(self.pipeline, "_run_boss_cdp_detail_probe", side_effect=fake_detail_probe):
            result = self.pipeline.supplement_boss_details("me", "boss-session-detail-1", limit=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(result["upsert"]["touched"], 1)
        self.assertEqual(captured["limit"], 1)
        self.assertEqual(captured["fetch_session_id"], "boss-session-detail-1")
        self.assertEqual(len(captured["jobs"]), 1)
        reloaded = [
            job
            for job in self.pipeline.store.load_jobs(["boss_browser"])
            if job.fetch_session_id == "boss-session-detail-1"
        ]
        self.assertEqual(len(reloaded), 1)
        self.assertTrue(reloaded[0].detail_fetched)
        self.assertIn("岗位职责", reloaded[0].description)

    def test_supplement_boss_details_returns_all_updated_jobs_without_preview_truncation(self) -> None:
        session_id = "boss-session-detail-many"
        seed_jobs: list[JobPosting] = []
        updated_jobs: list[dict] = []
        for index in range(6):
            job = JobPosting(
                source="boss_browser",
                url=f"https://www.zhipin.com/job_detail/demo-{index}.html",
                title=f"role-{index}",
                company_name="demo-company",
                city="shenzhen",
                city_list=["shenzhen"],
                apply_url=f"https://www.zhipin.com/job_detail/demo-{index}.html",
                source_job_id=f"demo-{index}",
                job_type="full_time",
                employment_mode="full_time",
                application_status="unknown",
                fetch_session_id=session_id,
                description="short list summary",
            )
            job.ensure_ids()
            seed_jobs.append(job)

            updated = JobPosting.from_dict(job.to_dict())
            updated.description = f"full jd {index}"
            updated.detail_fetched = True
            updated.application_status = "open"
            updated.ensure_ids()
            updated_jobs.append(updated.to_dict())

        self.pipeline.store.upsert_jobs(seed_jobs)

        with patch.object(
            self.pipeline,
            "_run_boss_cdp_detail_probe",
            return_value={
                "ok": True,
                "attempted_count": 6,
                "success_count": 6,
                "updated_jobs": updated_jobs,
                "results": [{"ok": True, "job_url": item["url"], "title": item["title"]} for item in updated_jobs],
            },
        ):
            result = self.pipeline.supplement_boss_details("me", session_id, limit=6)

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated_count"], 6)
        self.assertEqual(len(result["updated_jobs"]), 6)
        self.assertEqual([item["title"] for item in result["updated_jobs"]], [f"role-{index}" for index in range(6)])

    def test_list_review_profiles_and_suggestion_for_boss_session(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        settings.job_types = ["校招"]
        self.pipeline.store.save_settings(settings)
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/demo.html",
            title="产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            salary_min=10000,
            salary_max=15000,
            application_status="unknown",
            fetch_session_id="boss-session-4",
        )
        boss_job.ensure_ids()

        def fake_load_jobs(source_names=None):
            if source_names and "boss_browser" in source_names:
                return [boss_job]
            return []

        with patch.object(self.pipeline.store, "load_jobs", side_effect=fake_load_jobs):
            with patch.object(self.pipeline.store, "last_action_for_job", return_value=""):
                with patch.object(self.pipeline.store, "was_pushed", return_value=False):
                    profiles = self.pipeline.list_review_profiles("me", fetch_session_id="boss-session-4")
                    review = self.pipeline.review_fetch_session("me", "boss-session-4", limit=5)
        profile_ids = [item["id"] for item in profiles]
        self.assertEqual(profile_ids, ["default", "boss_all", "boss_social", "boss_full_time"])
        self.assertEqual(review["matched_count"], 0)
        self.assertEqual(review["suggested_review_profile"], "boss_social")
        self.assertEqual(review["suggested_review_profile_detail"]["label"], "BOSS 社招预览")

    def test_boss_unknown_status_enters_default_preview(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        settings.preferred_roles = ["AI"]
        self.pipeline.store.save_settings(settings)
        boss_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/unknown.html",
            title="AI 产品运营",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="社招",
            employment_mode="full_time",
            salary_text="10-15K",
            application_status="unknown",
            detail_fetched=True,
            fetch_session_id="boss-session-unknown",
        )
        boss_job.ensure_ids()
        self.pipeline.store.upsert_jobs([boss_job])

        review = self.pipeline.recommend_boss_session("me", "boss-session-unknown", limit=5)

        self.assertEqual(review["matched_count"], 1)
        self.assertEqual(review["items"][0]["decision_status"], "hit")
        self.assertTrue(review["items"][0]["is_application_status_inferred"])
        self.assertEqual(review["items"][0]["boss_status_label"], "BOSS 状态未知")
        self.assertEqual(review["review_items"][0]["decision_status"], "hit")

    def test_boss_closed_and_pending_status_still_skip(self) -> None:
        settings = self.pipeline.store.get_settings("me")
        settings.preferred_roles = ["AI"]
        self.pipeline.store.save_settings(settings)
        jobs = []
        for status in ["closed", "pending"]:
            job = JobPosting(
                source="boss_browser",
                url=f"https://www.zhipin.com/job_detail/{status}.html",
                title=f"AI 产品运营 {status}",
                company_name="测试公司",
                city="深圳",
                city_list=["深圳"],
                job_type="社招",
                employment_mode="full_time",
                salary_text="10-15K",
                application_status=status,
                detail_fetched=True,
                fetch_session_id="boss-session-status-skip",
            )
            job.ensure_ids()
            jobs.append(job)
        self.pipeline.store.upsert_jobs(jobs)

        review = self.pipeline.recommend_boss_session("me", "boss-session-status-skip", limit=5)

        self.assertEqual(review["matched_count"], 0)
        self.assertEqual(len(review["review_items"]), 2)
        self.assertTrue(all(item["decision_status"] == "miss" for item in review["review_items"]))

    def test_list_review_profiles_can_show_intern_and_campus_variants(self) -> None:
        campus_intern_job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/intern.html",
            title="运营实习生",
            company_name="测试公司",
            city="深圳",
            city_list=["深圳"],
            job_type="校招",
            employment_mode="intern",
            salary_text="150-200元/天",
            application_status="unknown",
            fetch_session_id="boss-session-5",
        )
        campus_intern_job.ensure_ids()

        def fake_load_jobs(source_names=None):
            if source_names and "boss_browser" in source_names:
                return [campus_intern_job]
            return []

        with patch.object(self.pipeline.store, "load_jobs", side_effect=fake_load_jobs):
            profiles = self.pipeline.list_review_profiles("me", fetch_session_id="boss-session-5")
        profile_ids = [item["id"] for item in profiles]
        self.assertEqual(profile_ids, ["default", "boss_all", "boss_campus", "boss_intern"])


if __name__ == "__main__":
    unittest.main()
