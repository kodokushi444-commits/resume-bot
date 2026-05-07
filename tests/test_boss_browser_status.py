from __future__ import annotations

import unittest

from src.resume_bot.job_sources.boss_browser import infer_boss_application_status


class BossBrowserStatusTests(unittest.TestCase):
    def test_infer_boss_application_status_from_detail_text(self) -> None:
        self.assertEqual(
            infer_boss_application_status({"buttonText": "立即沟通"})["status"],
            "open",
        )
        self.assertEqual(
            infer_boss_application_status({"statusDesc": "职位已关闭"})["status"],
            "closed",
        )
        self.assertEqual(
            infer_boss_application_status({"statusDesc": "暂未开放"})["status"],
            "pending",
        )
        self.assertEqual(
            infer_boss_application_status({"statusDesc": "普通岗位详情"})["status"],
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
