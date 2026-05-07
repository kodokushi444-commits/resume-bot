from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.resume_bot.config import load_config
from src.resume_bot.pipeline import ResumeBotPipeline
from src.resume_bot.types import JobPosting


class PipelineQueueImportTests(unittest.TestCase):
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
                        {"name": "boss_browser", "type": "boss-browser", "enabled": True},
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
        self.root = root

    def tearDown(self) -> None:
        self.env.stop()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def test_import_boss_queue_artifact_imports_jobs_payload(self) -> None:
        job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/job-1.html",
            title="运营助理",
            company_name="示例公司",
            city="深圳",
            salary_text="5-8K",
            source_job_id="job-1",
            fetch_session_id="session-001",
            raw_payload={"job_id": "job-1"},
        )
        job.ensure_ids()
        artifact_path = self.root / "queue-with-jobs.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_type": "boss_cdp_queue",
                    "fetch_session_id": "session-001",
                    "jobs": [job.to_dict()],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.pipeline.import_boss_queue_artifact("me", artifact_path)

        self.assertEqual(result["job_count"], 1)
        self.assertEqual(result["upsert"]["inserted"], 1)
        jobs = self.pipeline.store.load_jobs(["boss_browser"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "运营助理")
        self.assertEqual(jobs[0].fetch_session_id, "session-001")

    def test_import_boss_queue_artifact_repairs_old_jobs_payload(self) -> None:
        artifact_path = self.root / "queue-with-old-jobs.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_type": "boss_cdp_queue",
                    "fetch_session_id": "session-001a",
                    "jobs": [
                        {
                            "source": "boss_browser",
                            "url": "",
                            "apply_url": "",
                            "title": "海外用户运营",
                            "company_name": "示例甲公司",
                            "city": "深圳",
                            "salary_text": "12-18K",
                            "source_job_id": "job-1a",
                            "degree_requirement": "",
                            "raw_payload": {
                                "job_id": "job-1a",
                                "job_url": "https://www.zhipin.com/job_detail/job-1a.html",
                                "jobDegree": "本科",
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.pipeline.import_boss_queue_artifact("me", artifact_path)

        self.assertEqual(result["job_count"], 1)
        jobs = self.pipeline.store.load_jobs(["boss_browser"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].url, "https://www.zhipin.com/job_detail/job-1a.html")
        self.assertEqual(jobs[0].apply_url, "https://www.zhipin.com/job_detail/job-1a.html")
        self.assertEqual(jobs[0].degree_requirement, "本科")

    def test_import_boss_queue_artifact_falls_back_to_cards_payload(self) -> None:
        artifact_path = self.root / "queue-with-cards.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_type": "boss_cdp_queue",
                    "fetch_session_id": "session-002",
                    "source_name": "boss_browser",
                    "engine": "boss_cdp_list_probe",
                    "cards": [
                        {
                            "job_id": "job-2",
                            "security_id": "sec-2",
                            "lid": "lid-2",
                            "title": "新媒体运营",
                            "company_name": "示例乙公司",
                            "city": "深圳",
                            "salary_text": "8-12K",
                            "jobDegree": "大专",
                            "job_url": "https://www.zhipin.com/job_detail/job-2.html",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.pipeline.import_boss_queue_artifact("me", artifact_path)

        self.assertEqual(result["job_count"], 1)
        jobs = self.pipeline.store.load_jobs(["boss_browser"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "新媒体运营")
        self.assertEqual(jobs[0].fetch_session_id, "session-002")
        self.assertEqual(jobs[0].url, "https://www.zhipin.com/job_detail/job-2.html")
        self.assertEqual(jobs[0].apply_url, "https://www.zhipin.com/job_detail/job-2.html")
        self.assertEqual(jobs[0].degree_requirement, "大专")
        self.assertEqual(jobs[0].raw_payload.get("capture_engine"), "boss_cdp_list_probe")

    def test_import_boss_queue_artifact_applies_quick_filters_before_upsert(self) -> None:
        jobs: list[dict] = []
        for job in [
            JobPosting(
                source="boss_browser",
                url="https://www.zhipin.com/job_detail/job-intern.html",
                title="运营实习生",
                company_name="示例甲公司",
                city="深圳",
                source_job_id="job-intern",
                employment_mode="intern",
                degree_requirement="本科",
                fetch_session_id="session-filter",
            ),
            JobPosting(
                source="boss_browser",
                url="https://www.zhipin.com/job_detail/job-master.html",
                title="运营专员",
                company_name="示例乙公司",
                city="深圳",
                source_job_id="job-master",
                employment_mode="intern",
                degree_requirement="硕士",
                fetch_session_id="session-filter",
            ),
            JobPosting(
                source="boss_browser",
                url="https://www.zhipin.com/job_detail/job-fulltime.html",
                title="用户运营",
                company_name="示例丙公司",
                city="深圳",
                source_job_id="job-fulltime",
                employment_mode="full_time",
                degree_requirement="大专",
                fetch_session_id="session-filter",
            ),
        ]:
            job.ensure_ids()
            jobs.append(job.to_dict())
        artifact_path = self.root / "queue-with-filters.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_type": "boss_cdp_queue",
                    "fetch_session_id": "session-filter",
                    "city": "深圳",
                    "keyword": "运营",
                    "jobs": jobs,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.pipeline.import_boss_queue_artifact(
            "me",
            artifact_path,
            quick_filters={"degree_filter": "本科", "employment_mode_filter": "intern"},
        )

        self.assertEqual(result["raw_job_count"], 3)
        self.assertEqual(result["job_count"], 1)
        self.assertEqual(result["local_filter"]["dropped_count"], 2)
        self.assertEqual(result["local_filter"]["dropped_reasons"]["degree_requirement_too_high"], 1)
        self.assertEqual(result["local_filter"]["dropped_reasons"]["employment_mode_mismatch"], 1)
        stored_jobs = self.pipeline.store.load_jobs(["boss_browser"])
        self.assertEqual(len(stored_jobs), 1)
        self.assertEqual(stored_jobs[0].source_job_id, "job-intern")
        self.assertEqual(stored_jobs[0].raw_payload.get("local_quick_filter_passed"), True)

    def test_import_boss_queue_artifact_keeps_unknown_degree_for_later_detail_check(self) -> None:
        job = JobPosting(
            source="boss_browser",
            url="https://www.zhipin.com/job_detail/job-unknown-degree.html",
            title="AI 产品运营",
            company_name="示例 AI 公司",
            city="深圳",
            source_job_id="job-unknown-degree",
            employment_mode="unknown",
            degree_requirement="",
            fetch_session_id="session-ai",
        )
        job.ensure_ids()
        artifact_path = self.root / "queue-with-unknown-degree.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "artifact_type": "boss_cdp_queue",
                    "fetch_session_id": "session-ai",
                    "city": "深圳",
                    "keyword": "AI",
                    "jobs": [job.to_dict()],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.pipeline.import_boss_queue_artifact(
            "me",
            artifact_path,
            quick_filters={"degree_filter": "本科", "employment_mode_filter": "full_time"},
        )

        self.assertEqual(result["raw_job_count"], 1)
        self.assertEqual(result["job_count"], 1)
        self.assertEqual(result["local_filter"]["dropped_count"], 0)
        stored_jobs = self.pipeline.store.load_jobs(["boss_browser"])
        self.assertEqual(len(stored_jobs), 1)
        self.assertEqual(stored_jobs[0].fetch_session_id, "session-ai")
        self.assertEqual(stored_jobs[0].title, "AI 产品运营")


if __name__ == "__main__":
    unittest.main()
