from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "scripts" / "boss_cdp_list_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("boss_cdp_list_probe", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load boss_cdp_list_probe module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BossCdpListProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_module()

    def test_merge_cards_dedupes_by_job_id_and_updates_existing_fields(self):
        existing = [
            {
                "job_id": "job-1",
                "job_url": "https://example.com/job-1",
                "salary_text": "10-15K",
            }
        ]
        incoming = [
            {
                "job_id": "job-1",
                "job_url": "https://example.com/job-1",
                "salary_text": "12-18K",
                "company_name": "Acme",
            },
            {
                "job_id": "job-2",
                "job_url": "https://example.com/job-2",
                "salary_text": "8-12K",
            },
        ]

        merged, added = self.module._merge_cards(existing, incoming)

        self.assertEqual(added, 1)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["salary_text"], "12-18K")
        self.assertEqual(merged[0]["company_name"], "Acme")
        self.assertEqual(merged[1]["job_id"], "job-2")

    def test_merge_cards_falls_back_to_job_url_when_job_id_missing(self):
        existing = [
            {
                "job_id": "",
                "job_url": "https://example.com/job-a",
                "title": "运营",
            }
        ]
        incoming = [
            {
                "job_id": "",
                "job_url": "https://example.com/job-a",
                "title": "运营",
                "salary_text": "5-8K",
            }
        ]

        merged, added = self.module._merge_cards(existing, incoming)

        self.assertEqual(added, 0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["salary_text"], "5-8K")

    def test_is_results_url_only_accepts_jobs_surface(self):
        self.assertTrue(self.module._is_results_url("https://www.zhipin.com/web/geek/jobs?query=%E8%BF%90%E8%90%A5"))
        self.assertFalse(self.module._is_results_url("https://www.zhipin.com/shenzhen/"))
        self.assertFalse(self.module._is_results_url("about:blank"))

    def test_build_search_url_accepts_verified_extra_params(self):
        url = self.module.build_boss_search_url(
            "https://www.zhipin.com/web/geek/jobs",
            keyword="运营",
            city_code="101280600",
            extra_params={"degree": "203", "bad-key": "ignored"},
        )

        self.assertEqual(
            url,
            "https://www.zhipin.com/web/geek/jobs?query=%E8%BF%90%E8%90%A5&city=101280600&degree=203",
        )

    def test_quick_filter_url_params_only_emit_verified_degree_param(self):
        params = self.module.build_boss_quick_filter_url_params(
            degree_filter="本科",
            employment_mode_filter="intern",
        )

        self.assertEqual(params, {"degree": "203"})

    def test_quick_filter_degree_codes_match_boss_dropdown_values(self):
        self.assertEqual(self.module.build_boss_quick_filter_url_params(degree_filter="大专"), {"degree": "204"})
        self.assertEqual(self.module.build_boss_quick_filter_url_params(degree_filter="本科"), {"degree": "203"})
        self.assertEqual(self.module.build_boss_quick_filter_url_params(degree_filter="硕士"), {"degree": "202"})
        self.assertEqual(self.module.build_boss_quick_filter_url_params(degree_filter="博士"), {"degree": "201"})

    def test_should_stop_after_round_for_limit(self):
        reason = self.module._should_stop_after_round(
            cards_count=30,
            total_count=300,
            limit=30,
            consecutive_empty_rounds=0,
            max_empty_rounds=2,
        )
        self.assertEqual(reason, "reached_limit")

    def test_should_stop_after_round_for_total_count(self):
        reason = self.module._should_stop_after_round(
            cards_count=45,
            total_count=45,
            limit=None,
            consecutive_empty_rounds=0,
            max_empty_rounds=2,
        )
        self.assertEqual(reason, "reached_total_count")

    def test_should_stop_after_round_for_max_empty_rounds(self):
        reason = self.module._should_stop_after_round(
            cards_count=30,
            total_count=300,
            limit=None,
            consecutive_empty_rounds=2,
            max_empty_rounds=2,
        )
        self.assertEqual(reason, "max_empty_rounds")

    def test_build_queue_jobs_sets_fetch_session_and_source(self):
        cards = [
            {
                "job_id": "job-1",
                "security_id": "sec-1",
                "lid": "lid-1",
                "title": "运营助理",
                "company_name": "示例公司",
                "city": "深圳",
                "salary_text": "5-8K",
                "degree_requirement": "本科",
                "job_url": "https://www.zhipin.com/job_detail/job-1.html",
            }
        ]

        jobs = self.module._build_queue_jobs(cards, fetch_session_id="session-123")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "boss_browser")
        self.assertEqual(jobs[0]["fetch_session_id"], "session-123")
        self.assertEqual(jobs[0]["source_job_id"], "job-1")
        self.assertEqual(jobs[0]["url"], "https://www.zhipin.com/job_detail/job-1.html")
        self.assertEqual(jobs[0]["apply_url"], "https://www.zhipin.com/job_detail/job-1.html")
        self.assertEqual(jobs[0]["degree_requirement"], "本科")
        self.assertEqual(jobs[0]["raw_payload"]["capture_engine"], "boss_cdp_list_probe")

    def test_build_boss_search_probe_card_includes_degree_and_links(self):
        card = self.module.build_boss_search_probe_card(
            {
                "encryptJobId": "job-3",
                "securityId": "sec-3",
                "lid": "lid-3",
                "jobName": "AI 运营",
                "brandName": "示例丙公司",
                "cityName": "深圳",
                "salaryDesc": "10-15K",
                "jobDegree": "本科",
                "jobExperience": "1-3年",
            }
        )

        self.assertEqual(card["degree_requirement"], "本科")
        self.assertEqual(card["experience_name"], "1-3年")
        self.assertEqual(card["job_url"], "https://www.zhipin.com/job_detail/job-3.html")
        self.assertEqual(card["url"], "https://www.zhipin.com/job_detail/job-3.html")
        self.assertEqual(card["apply_url"], "https://www.zhipin.com/job_detail/job-3.html")

    def test_write_queue_artifact_creates_latest_copy(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            output_path = tmp_path / "session-a.json"
            latest_path = tmp_path / "latest.json"

            self.module._write_queue_artifact(
                {"ok": True, "jobs": [{"title": "运营"}]},
                output_path=output_path,
                latest_path=latest_path,
            )

            self.assertTrue(output_path.exists())
            self.assertTrue(latest_path.exists())
            self.assertEqual(output_path.read_text(encoding="utf-8"), latest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
