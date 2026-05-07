from __future__ import annotations

import unittest
from unittest.mock import patch

from src.resume_bot.job_sources.base import SourceHaltError
from src.resume_bot.job_sources.nowcoder_direct import NowcoderDirectSource, _FetchedPage
from src.resume_bot.types import ResumeProfile, UserSettings


class NowcoderDirectSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = NowcoderDirectSource(
            "nowcoder_direct",
            seed_urls=["https://www.nowcoder.com/jobs/recommend/campus"],
            throttle_seconds=0.8,
            max_seed_pages=2,
            max_detail_pages=2,
        )
        self.settings = UserSettings(
            user_id="me",
            preferred_roles=["运营"],
            preferred_cities=["深圳"],
            job_types=["校招", "社招"],
        )
        self.profile = ResumeProfile(target_roles=["社区运营"], target_cities=["深圳"])

    def test_extract_detail_candidates_from_seed_page(self) -> None:
        html = """
        <html><body>
          <a href="/jobs/detail/123">深圳社区运营</a>
          <div>更多岗位 https://www.nowcoder.com/job/456 </div>
          <a href="https://www.nowcoder.com/feed/main/detail/999">帖子页</a>
          <a href="https://example.com/jobs/detail/111">外站</a>
        </body></html>
        """
        candidates = self.source._extract_detail_candidates(html, "https://www.nowcoder.com/creation/circle/168")
        urls = {item["url"] for item in candidates}
        self.assertEqual(
            urls,
            {
                "https://www.nowcoder.com/jobs/detail/123",
                "https://www.nowcoder.com/job/456",
            },
        )

    def test_extract_detail_candidates_from_bing_wrapped_links(self) -> None:
        html = """
        <html><body>
          <a href="https://www.bing.com/ck/a?!&&p=abc&u=a1aHR0cHM6Ly93d3cubm93Y29kZXIuY29tL2pvYi80NTY&ntb=1">搜索结果</a>
        </body></html>
        """
        candidates = self.source._extract_detail_candidates(html, "https://www.bing.com/search?q=nowcoder")
        urls = {item["url"] for item in candidates}
        self.assertEqual(urls, {"https://www.nowcoder.com/job/456"})

    def test_extract_detail_candidates_from_nowcoder_jump_links(self) -> None:
        html = """
        <html><body>
          <a href="https://www.nowcoder.com/jump?type=ad&source=6&url=https%3A%2F%2Fwww.nowcoder.com%2Fjobs%2Fhr%2F111383&entityId=1">广发证券</a>
          <a href="https://www.nowcoder.com/jump?type=ad&source=6&url=https%3A%2F%2Fwww.nowcoder.com%2Fjobs%2Fcompany-project%3FprojectId%3D2571&entityId=2">快手</a>
        </body></html>
        """
        candidates = self.source._extract_detail_candidates(html, "https://www.nowcoder.com/jobs/recommend/campus")
        urls = {item["url"] for item in candidates}
        self.assertEqual(
            urls,
            {
                "https://www.nowcoder.com/jobs/hr/111383",
                "https://www.nowcoder.com/jobs/company-project?projectId=2571",
            },
        )

    def test_candidate_prefilter_rejects_direct_detail_with_wrong_city_context(self) -> None:
        reject, reason = self.source._should_reject_candidate(
            {
                "url": "https://www.nowcoder.com/jobs/detail/123",
                "context": "北京 产品运营 本科 立即投递",
            },
            self.settings,
        )
        self.assertTrue(reject)
        self.assertEqual(reason, "候选上下文城市不符")

    def test_parse_detail_page_accepts_valid_detail(self) -> None:
        html = """
        <html>
          <head><title>社区运营-深圳</title></head>
          <body>
            <h1>社区运营</h1>
            <div>公司名称：某某科技有限公司</div>
            <div>工作地点：深圳</div>
            <div>薪资：6k-8k/月</div>
            <div>学历要求：本科及以上</div>
            <div>立即投递</div>
            <section>岗位职责</section>
            <p>负责社区内容策划、用户活动运营和增长分析，跟进项目数据复盘。</p>
            <section>任职要求</section>
            <p>本科及以上，具备内容运营经验，熟悉用户增长和活动执行。</p>
          </body>
        </html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/detail/123",
            final_url="https://www.nowcoder.com/jobs/detail/123",
            status_code=200,
            text=html,
            title="社区运营-深圳",
            content_type="text/html",
        )
        job = self.source._parse_detail_page(page, {"url": page.final_url, "context": "深圳 社区运营"})
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.source, "nowcoder_direct")
        self.assertEqual(job.company_name, "某某科技有限公司")
        self.assertEqual(job.city, "深圳")
        self.assertTrue(job.detail_fetched)
        self.assertIn("岗位职责", job.description)
        self.assertEqual(job.application_status, "open")

    def test_parse_detail_page_reads_salary_and_open_status(self) -> None:
        html = """
        <html>
          <head><title>产品运营_京东校招_牛客网</title></head>
          <body>
            <h1>产品运营_京东校招_牛客网</h1>
            <div>10-20K * 19薪</div>
            <div>北京</div>
            <div>本科及以上</div>
            <div>立即投递</div>
            <div>投递时间：2025年8月12日-2027年9月12日</div>
            <div>公司名称：京东</div>
            <section>岗位职责</section>
            <p>负责产品规划设计和用户增长。</p>
            <section>岗位要求</section>
            <p>本科及以上，具备数据分析能力。</p>
          </body>
        </html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/detail/407435",
            final_url="https://www.nowcoder.com/jobs/detail/407435",
            status_code=200,
            text=html,
            title="产品运营_京东校招_牛客网",
            content_type="text/html",
        )
        job = self.source._parse_detail_page(page, {"url": page.final_url, "context": "北京 产品运营"})
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.company_name, "京东")
        self.assertEqual(job.salary_text, "10-20K")
        self.assertEqual(job.application_status, "open")
        self.assertEqual(job.deadline, "2027-09-12")

    def test_parse_detail_page_filters_pending_job(self) -> None:
        html = """
        <html>
          <head><title>产品运营_京东校招_牛客网</title></head>
          <body>
            <h1>产品运营_京东校招_牛客网</h1>
            <div>10-20K * 19薪</div>
            <div>北京</div>
            <div>待上线</div>
            <section>岗位职责</section>
            <p>负责产品规划设计和用户增长。</p>
            <section>岗位要求</section>
            <p>本科及以上，具备数据分析能力。</p>
          </body>
        </html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/detail/407435",
            final_url="https://www.nowcoder.com/jobs/detail/407435",
            status_code=200,
            text=html,
            title="产品运营_京东校招_牛客网",
            content_type="text/html",
        )
        job = self.source._parse_detail_page(page, {"url": page.final_url, "context": "北京 产品运营"})
        self.assertIsNone(job)

    def test_parse_careers_page_extracts_multiple_jobs(self) -> None:
        html = """
        <html>
          <head><title>职位详情-牛客网</title></head>
          <body>
            <div>【快手】用户运营专员</div>
            <div>北京 运营类 快手</div>
            <div>立即投递</div>
            <div>岗位职责</div>
            <div>负责社区运营、用户增长与活动策划，跟进活动落地、复盘和跨团队协作，持续优化用户转化效率。</div>
            <div>岗位要求</div>
            <div>本科及以上，具备良好沟通与数据分析能力，熟悉内容运营、活动执行和基础数据分析工具。</div>
            <div>【腾讯】产品运营专员</div>
            <div>深圳 产品类 腾讯</div>
            <div>立即申请</div>
            <div>岗位职责</div>
            <div>负责产品运营策略、活动配置与用户分层，协同产品和研发推进版本上线及运营复盘。</div>
            <div>岗位要求</div>
            <div>本科及以上，具备产品运营经验，能够独立推进项目并完成数据整理和运营分析。</div>
          </body>
        </html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/careers/demo/43",
            final_url="https://www.nowcoder.com/careers/demo/43",
            status_code=200,
            text=html,
            title="职位详情-牛客网",
            content_type="text/html",
        )
        jobs = self.source._parse_candidate_page(page, {"url": page.final_url, "context": "运营"})
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].company_name, "快手")
        self.assertEqual(jobs[0].city, "北京")
        self.assertEqual(jobs[1].company_name, "腾讯")
        self.assertEqual(jobs[1].city, "深圳")

    def test_parse_hr_page_extracts_multiple_jobs(self) -> None:
        html = """
        <html><body>
        <script>
        window.__INITIAL_STATE__={"prefetchData":{"1":{"companyDetail":{"companyName":"广发证券"},"hrInfo":{"id":111383},"jobListData":{"dataL":[],"dataR":[{"id":437468,"status":0,"recruitType":1,"jobName":"数据开发岗（26/27届）","jobCity":"广州","jobCityList":["广州"],"deliverBegin":1764777600000,"deliverEnd":1804608000000,"salaryType":2,"salaryMin":0,"salaryMax":9999999,"salaryMonth":12,"salaryShow":null,"allowDeliverCount":0,"jobKeys":"Zookeeper,CDH,Storm","parseExt":{"requirements":"硕士研究生学历,计算机相关专业。","infos":"负责数据资产建设与数据仓库开发。"}}]}}},"store":{},"app":{},"fullPath":"/jobs/hr/111383"};(function(){var s;})();
        </script>
        </body></html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/hr/111383",
            final_url="https://www.nowcoder.com/jobs/hr/111383",
            status_code=200,
            text=html,
            title="袁先生_广发证券股份有限公司_牛客网",
            content_type="text/html",
        )
        jobs = self.source._parse_candidate_page(page, {"url": page.final_url, "context": "广州 运营"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company_name, "广发证券")
        self.assertEqual(jobs[0].application_status, "open")
        self.assertIn("面议", jobs[0].salary_text)
        self.assertIn("岗位职责", jobs[0].description)

    def test_parse_company_project_page_extracts_multiple_jobs(self) -> None:
        html = """
        <html><body>
        <script>
        window.__INITIAL_STATE__={"prefetchData":{},"store":{},"app":{"93":{"companyDetail":{"companyName":"快手"},"companyJobList":[{"id":437882,"status":0,"recruitType":2,"jobName":"【留用实习】创作者运营","jobCity":"北京","jobCityList":["北京"],"careerJobName":"用户运营","deliverBegin":1773244800000,"deliverEnd":1782835200000,"salaryType":2,"salaryMin":0,"salaryMax":9999999,"salaryMonth":12,"salaryShow":null,"allowDeliverCount":1,"secondJobType":"运营","dockSourceProjectName":"27届实习生招聘","parseExt":{"requirements":"专业不限。","jobStrength":"有内容平台经验优先。","infos":"参与创作者运营与内容生态建设。"}}]}},"fullPath":"/jobs/company-project?projectId=2571"};(function(){var s;})();
        </script>
        </body></html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            final_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            status_code=200,
            text=html,
            title="快手专场职位_校园招聘_牛客网",
            content_type="text/html",
        )
        jobs = self.source._parse_candidate_page(page, {"url": page.final_url, "context": "运营"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company_name, "快手")
        self.assertEqual(jobs[0].city, "北京")
        self.assertEqual(jobs[0].job_type, "校招")
        self.assertEqual(jobs[0].application_status, "open")

    def test_parse_company_project_page_skips_nowcoder_company(self) -> None:
        html = """
        <html><body>
        <script>
        window.__INITIAL_STATE__={"prefetchData":{},"store":{},"app":{"93":{"companyDetail":{"companyName":"牛客网"},"companyJobList":[{"id":437882,"status":0,"recruitType":2,"jobName":"新媒体运营","jobCity":"北京","jobCityList":["北京"],"salaryType":2,"salaryMin":0,"salaryMax":9999999,"salaryMonth":12,"salaryShow":null,"allowDeliverCount":1,"parseExt":{"requirements":"专业不限。","infos":"参与内容运营。"}}]}},"fullPath":"/jobs/company-project?projectId=2571"};(function(){var s;})();
        </script>
        </body></html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            final_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            status_code=200,
            text=html,
            title="牛客专场职位_校园招聘_牛客网",
            content_type="text/html",
        )
        jobs = self.source._parse_candidate_page(page, {"url": page.final_url, "context": "运营"})
        self.assertEqual(jobs, [])

    def test_fetch_jobs_halts_on_blocked_seed_page(self) -> None:
        blocked_page = _FetchedPage(
            requested_url="https://www.nowcoder.com/creation/circle/168",
            final_url="https://www.nowcoder.com/creation/circle/168",
            status_code=403,
            text="<html><body>访问受限 请先登录</body></html>",
            title="访问受限",
            content_type="text/html",
        )
        with patch.object(self.source, "_fetch_page", return_value=blocked_page):
            with self.assertRaises(SourceHaltError):
                self.source.fetch_jobs(self.settings, self.profile)

    def test_prefilter_jobs_rejects_hard_mismatches_before_insert(self) -> None:
        html = """
        <html><body>
        <script>
        window.__INITIAL_STATE__={"prefetchData":{},"store":{},"app":{"93":{"companyDetail":{"companyName":"快手"},"companyJobList":[
        {"id":437882,"status":0,"recruitType":2,"jobName":"【留用实习】创作者运营","jobCity":"北京","jobCityList":["北京"],"deliverBegin":1773244800000,"deliverEnd":1782835200000,"salaryType":2,"salaryMin":12000,"salaryMax":18000,"salaryMonth":12,"salaryShow":null,"allowDeliverCount":1,"secondJobType":"运营","dockSourceProjectName":"27届实习生招聘","parseExt":{"requirements":"本科及以上。","jobStrength":"有内容平台经验优先。","infos":"参与创作者运营与内容生态建设。"}},
        {"id":437883,"status":0,"recruitType":1,"jobName":"数据开发岗（26/27届）","jobCity":"广州","jobCityList":["广州"],"deliverBegin":1773244800000,"deliverEnd":1782835200000,"salaryType":2,"salaryMin":18000,"salaryMax":26000,"salaryMonth":12,"salaryShow":null,"allowDeliverCount":1,"secondJobType":"开发","dockSourceProjectName":"27届校招","parseExt":{"requirements":"硕士研究生学历。","infos":"负责数据平台开发。"}}
        ]}},"fullPath":"/jobs/company-project?projectId=2571"};(function(){var s;})();
        </script>
        </body></html>
        """
        page = _FetchedPage(
            requested_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            final_url="https://www.nowcoder.com/jobs/company-project?projectId=2571",
            status_code=200,
            text=html,
            title="快手专场职位_校园招聘_牛客网",
            content_type="text/html",
        )
        parsed = self.source._parse_candidate_page(page, {"url": page.final_url, "context": "运营"})
        filtered = self.source._prefilter_jobs(
            parsed,
            UserSettings(
                user_id="me",
                preferred_roles=["运营"],
                preferred_cities=["广州", "深圳"],
                excluded_keywords=["开发", "算法"],
                job_types=["校招"],
                campus_role_mode="full_time",
                max_degree_requirement="本科",
            ),
            ["运营"],
            {
                "candidate_rejected": {},
                "candidate_rejected_examples": {},
                "job_rejected": {},
                "job_rejected_examples": {},
            },
        )
        self.assertEqual(filtered, [])


if __name__ == "__main__":
    unittest.main()
