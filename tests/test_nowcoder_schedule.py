from __future__ import annotations

import unittest
from unittest.mock import patch

from src.resume_bot.job_sources.nowcoder_schedule import NowcoderScheduleSource, _FetchedPage
from src.resume_bot.types import ResumeProfile, UserSettings


class NowcoderScheduleSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = NowcoderScheduleSource(
            "nowcoder_schedule",
            max_schedule_pages=1,
            max_enterprises=5,
            max_official_pages_per_enterprise=4,
            throttle_seconds=0.8,
        )
        self.settings = UserSettings(
            user_id="me",
            preferred_roles=["运营", "产品运营", "产品经理"],
            preferred_cities=["北京", "深圳"],
            excluded_keywords=["销售", "客服", "法务"],
            job_types=["校招"],
            campus_role_mode="full_time",
            max_degree_requirement="本科",
        )
        self.profile = ResumeProfile(target_roles=["内容运营"], target_cities=["北京"])

    def test_extract_enterprise_candidates_from_schedule_page(self) -> None:
        html = """
        <html><body>
          <div class="card">
            <a href="https://api-cdn.nowcoder.com/enterprise/961">蔚来</a>
            <span>26春招 北京 今日收录 官网投递</span>
          </div>
          <div class="card">
            <a href="/enterprise/1741">联发科技</a>
            <span>27届实习 深圳</span>
          </div>
        </body></html>
        """
        candidates = self.source._extract_enterprise_candidates(html, "https://mnowpick.nowcoder.com/jobs/school/schedule")
        self.assertEqual(len(candidates), 2)
        urls = {item["enterprise_url"] for item in candidates}
        self.assertIn("https://api-cdn.nowcoder.com/enterprise/961", urls)
        self.assertIn("https://api-cdn.nowcoder.com/enterprise/1741", urls)

    def test_extract_official_targets_from_enterprise_page(self) -> None:
        html = """
        <html><body>
          <a href="https://careers.example.com/campus">查看官网</a>
          <a href="https://www.nowcoder.com/jump?url=https%3A%2F%2Fjobs.example.com%2Fschool">官网投递</a>
          <a href="/enterprise/999">企业主页</a>
        </body></html>
        """
        targets = self.source._extract_official_targets(
            html,
            "https://api-cdn.nowcoder.com/enterprise/961",
            {"company_name": "蔚来"},
        )
        urls = {item["url"] for item in targets}
        self.assertIn("https://careers.example.com/campus", urls)
        self.assertIn("https://jobs.example.com/school", urls)

    def test_extract_official_targets_from_initial_state(self) -> None:
        html = """
        <html><body>
        <script>
        window.__INITIAL_STATE__={"store":{"enterprise":{"enterpriseInfo":{"simpleName":"中国联通","officalEncodeUrl":"/jump?type=ad&url=https%3A%2F%2Fcampus.example.com","buttonInfo":{"url":"https://jobs.example.com/campus"},"schedules":[{"url":"https://jobs.example.com/batch-a"}]}}}};(function(){var s;})();
        </script>
        </body></html>
        """
        enterprise = {"company_name": "收藏"}
        targets = self.source._extract_official_targets(
            html,
            "https://api-cdn.nowcoder.com/enterprise/998",
            enterprise,
        )
        urls = {item["url"] for item in targets}
        self.assertIn("https://campus.example.com/", urls)
        self.assertIn("https://jobs.example.com/campus", urls)
        self.assertIn("https://jobs.example.com/batch-a", urls)
        self.assertEqual(enterprise["company_name"], "中国联通")

    def test_crawl_official_site_keeps_only_matching_jobs(self) -> None:
        pages = {
            "https://jobs.example.com/campus": _FetchedPage(
                requested_url="https://jobs.example.com/campus",
                final_url="https://jobs.example.com/campus",
                status_code=200,
                text="""
                <html><body>
                  <a href="/jobs/content-ops">内容运营专员</a>
                  <a href="/jobs/sales">销售顾问</a>
                </body></html>
                """,
                title="校园招聘",
                content_type="text/html",
            ),
            "https://jobs.example.com/jobs/content-ops": _FetchedPage(
                requested_url="https://jobs.example.com/jobs/content-ops",
                final_url="https://jobs.example.com/jobs/content-ops",
                status_code=200,
                text="""
                <html><body>
                  <h1>内容运营专员</h1>
                  <div>工作地点：北京</div>
                  <div>本科及以上</div>
                  <div>岗位职责：负责内容运营、活动策划和用户增长。</div>
                  <div>任职要求：本科及以上，具备内容运营经验。</div>
                  <div>立即申请</div>
                </body></html>
                """,
                title="内容运营专员",
                content_type="text/html",
            ),
            "https://jobs.example.com/jobs/sales": _FetchedPage(
                requested_url="https://jobs.example.com/jobs/sales",
                final_url="https://jobs.example.com/jobs/sales",
                status_code=200,
                text="""
                <html><body>
                  <h1>销售顾问</h1>
                  <div>工作地点：北京</div>
                  <div>岗位职责：负责客户销售。</div>
                  <div>立即申请</div>
                </body></html>
                """,
                title="销售顾问",
                content_type="text/html",
            ),
        }

        def fake_fetch(url: str):
            return pages[url]

        with patch.object(self.source, "_fetch_page", side_effect=fake_fetch):
            result = self.source._crawl_official_site(
                {"url": "https://jobs.example.com/campus", "label": "官网投递"},
                {
                    "company_name": "示例公司",
                    "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/961",
                    "schedule_context": "北京 26春招 官网投递",
                    "cities": ["北京"],
                    "batch_text": "26春招",
                },
                self.settings,
                self.source._role_tokens(self.settings, self.profile),
            )
        jobs = result["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "内容运营专员")
        self.assertEqual(jobs[0].company_name, "示例公司")
        self.assertEqual(jobs[0].city, "北京")

    def test_extract_jobs_from_spdb_rows(self) -> None:
        page = _FetchedPage(
            requested_url="https://job.spdb.com.cn/campusJob",
            final_url="https://job.spdb.com.cn/campusJob",
            status_code=200,
            text="""
            <html><body>
              <div>所属机构 招聘岗位 学历要求 招聘人数 工作地点</div>
              <div>总行 产品运营岗 本科及以上 3 北京</div>
              <div>科技条线 销售顾问 本科及以上 2 上海</div>
            </body></html>
            """,
            title="浦发招聘",
            content_type="text/html",
        )
        jobs = self.source._extract_jobs_from_official_page(
            page,
            {
                "company_name": "浦发银行",
                "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/1009",
                "schedule_context": "北京 校招 官网投递",
                "cities": ["北京"],
                "batch_text": "26秋招",
            },
        )
        titles = {job.title for job in jobs}
        self.assertIn("产品运营岗", titles)
        self.assertIn("销售顾问", titles)

    def test_extract_jobs_from_baidu_list(self) -> None:
        page = _FetchedPage(
            requested_url="https://talent.baidu.com/jobs/list",
            final_url="https://talent.baidu.com/jobs/list",
            status_code=200,
            text="""
            <html><body>
              <div>北京-内容运营(J12345)</div>
              <div>北京市 校招 产品 98人 2026-02-28</div>
              <div>岗位职责：负责内容运营与活动策划</div>
              <div>任职要求：本科及以上</div>
              <div>深圳-算法工程师(J67890)</div>
              <div>深圳市 校招 技术 20人 2026-03-05</div>
              <div>岗位职责：负责算法模型研发</div>
            </body></html>
            """,
            title="百度校园招聘",
            content_type="text/html",
        )
        jobs = self.source._extract_jobs_from_official_page(
            page,
            {
                "company_name": "百度",
                "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/139",
                "schedule_context": "北京 校招 官网投递",
                "cities": ["北京"],
                "batch_text": "26春招",
            },
        )
        self.assertTrue(any(job.title == "内容运营" for job in jobs))
        self.assertTrue(any(job.source_job_id == "J12345" for job in jobs))

    def test_extract_jobs_from_text_blocks(self) -> None:
        page = _FetchedPage(
            requested_url="https://campus.51job.com/demo/job.html",
            final_url="https://campus.51job.com/demo/job.html",
            status_code=200,
            text="""
            <html><body>
              <h1>示例公司 2026 校园招聘</h1>
              <div>产品运营专员</div>
              <div>工作地点：北京</div>
              <div>岗位职责：负责内容策划、活动运营。</div>
              <div>任职要求：本科及以上，具备数据分析能力。</div>
              <div>立即投递</div>
              <div>销售顾问</div>
              <div>工作地点：上海</div>
              <div>岗位职责：负责客户销售。</div>
              <div>任职要求：沟通能力强。</div>
            </body></html>
            """,
            title="示例公司校园招聘",
            content_type="text/html",
        )
        jobs = self.source._extract_jobs_from_text_blocks(
            page,
            {
                "company_name": "示例公司",
                "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/961",
                "schedule_context": "北京 校招 官网投递",
                "cities": ["北京"],
                "batch_text": "26春招",
            },
        )
        self.assertTrue(any(job.title == "产品运营专员" for job in jobs))

    def test_extract_jobs_from_embedded_json_assignment(self) -> None:
        page = _FetchedPage(
            requested_url="https://demo.zhiye.com/campus/jobs",
            final_url="https://demo.zhiye.com/campus/jobs",
            status_code=200,
            text="""
            <html><body>
              <script>
                window.__INITIAL_STATE__={
                  "jobs":[
                    {
                      "jobName":"内容运营专员",
                      "cityName":"北京",
                      "requirement":"本科及以上，具备内容运营经验。",
                      "jobDescription":"岗位职责：负责内容策划与活动运营。",
                      "applyUrl":"/campus/job/1",
                      "jobId":"J001"
                    }
                  ]
                };
              </script>
            </body></html>
            """,
            title="示例公司校园招聘",
            content_type="text/html",
        )
        jobs = self.source._extract_jobs_from_official_page(
            page,
            {
                "company_name": "示例公司",
                "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/961",
                "schedule_context": "北京 校招 官网投递",
                "cities": ["北京"],
                "batch_text": "26春招",
            },
        )
        self.assertTrue(any(job.title == "内容运营专员" and job.city == "北京" for job in jobs))

    def test_extract_jobs_from_table_rows(self) -> None:
        page = _FetchedPage(
            requested_url="https://campus.example.com/jobs",
            final_url="https://campus.example.com/jobs",
            status_code=200,
            text="""
            <html><body>
              <table>
                <tr><th>岗位</th><th>地点</th><th>学历</th></tr>
                <tr><td>产品运营专员</td><td>北京</td><td>本科及以上</td></tr>
              </table>
            </body></html>
            """,
            title="校园招聘",
            content_type="text/html",
        )
        jobs = self.source._extract_jobs_from_official_page(
            page,
            {
                "company_name": "示例公司",
                "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/961",
                "schedule_context": "北京 校招 官网投递",
                "cities": ["北京"],
                "batch_text": "26春招",
            },
        )
        self.assertTrue(any(job.title == "产品运营专员" and job.city == "北京" for job in jobs))

    def test_marketing_platform_page_is_blocked(self) -> None:
        page = _FetchedPage(
            requested_url="https://mokahr.com/",
            final_url="https://mokahr.com/",
            status_code=200,
            text="""
            <html><body>
              <h1>Moka招聘智能化招聘管理系统</h1>
              <div>企业一体化招聘管理系统和人事管理系统</div>
              <div>校园招聘平台 解决方案 立即试用</div>
            </body></html>
            """,
            title="Moka招聘智能化招聘管理系统",
            content_type="text/html",
        )
        self.assertTrue(
            self.source._looks_like_platform_marketing_page(
                page,
                {"company_name": "江苏帝奥微电子股份有限公司"},
            )
        )

    def test_same_site_family_only_allows_www_variant(self) -> None:
        self.assertTrue(self.source._same_site_family("www.example.com", "example.com"))
        self.assertFalse(self.source._same_site_family("app.mokahr.com", "mokahr.com"))

    def test_same_site_family_allows_company_sibling_subdomains(self) -> None:
        self.assertTrue(self.source._same_site_family("jobs.example.com", "campus.example.com"))

    def test_recruitment_page_detection_rejects_solution_page(self) -> None:
        page = _FetchedPage(
            requested_url="https://www.nsfocus.com.cn/html/2019/228_1127/40.html",
            final_url="https://www.nsfocus.com.cn/html/2019/228_1127/40.html",
            status_code=200,
            text="""
            <html><body>
              <h1>一体化安全运营解决方案</h1>
              <div>为企业提供安全防护能力。</div>
            </body></html>
            """,
            title="一体化安全运营解决方案",
            content_type="text/html",
        )
        self.assertFalse(self.source._looks_like_recruitment_page(page))

    def test_extract_feishu_internal_targets(self) -> None:
        html = """
        <html><body>
          <script id="js-websiteInfo" type="text/json">
            {
              "website_info": {
                "path": "345030",
                "children_website_info": [
                  {"website_path": "index"}
                ]
              }
            }
          </script>
        </body></html>
        """
        targets = self.source._extract_platform_targets(html, "https://wdh.jobs.feishu.cn/345030")
        self.assertIn("https://wdh.jobs.feishu.cn/345030/position/list", targets)
        self.assertIn("https://wdh.jobs.feishu.cn/345030/campus/position/list", targets)
        self.assertIn("https://wdh.jobs.feishu.cn/index/position/list", targets)

    def test_resolve_hotjob_entry(self) -> None:
        with patch.object(
            self.source,
            "_post_json",
            return_value={
                "data": {
                    "linkData": {
                        "link": "https://bkhr.hotjob.cn/SU64ecb74d1c240e725e589d9a/pb/index.html"
                    }
                }
            },
        ):
            target = self.source._resolve_hotjob_entry("https://bkhr.hotjob.cn/")
        self.assertEqual(target, "https://bkhr.hotjob.cn/SU64ecb74d1c240e725e589d9a/pb/index.html")

    def test_extract_jobs_from_zhaopin_bundle_api(self) -> None:
        page = _FetchedPage(
            requested_url="https://syzp2026.zhaopin.com/assets/jzzjh-85d06dff.js",
            final_url="https://syzp2026.zhaopin.com/assets/jzzjh-85d06dff.js",
            status_code=200,
            text="""
            const globalData$1={xiaozhaoId:"104701",scene:"cam"};
            const params={orgDepartmentIds:10114068,customTags:"上研院 金种子计划"};
            """,
            title="zhaopin-bundle",
            content_type="application/javascript",
        )
        response = {
            "code": 200,
            "data": {
                "jobList": [
                    {
                        "company": {
                            "campusOrgShortName": "中移（上海）信息通信科技有限公司",
                        },
                        "job": {
                            "id": 40865531513,
                            "title": "算法开发",
                            "cityName": "上海",
                            "districtName": "浦东新区",
                            "deliveryPath": "https://campus1.zhaopin.com/Resume/CheckIntoApp?pid=CC000413680J40865531513",
                            "detail": "工作职责：负责模型研发。任职要求：博士研究生学历。",
                        },
                    }
                ]
            },
        }
        with patch.object(self.source, "_post_json", return_value=response) as mocked_post:
            jobs = self.source._extract_jobs_from_zhaopin_bundle(
                page,
                {
                    "company_name": "中国移动上海产业研究院",
                    "enterprise_url": "https://api-cdn.nowcoder.com/enterprise/27726",
                    "schedule_context": "上海 校招 官网投递",
                    "cities": ["上海"],
                    "batch_text": "26春招",
                },
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "算法开发")
        self.assertEqual(jobs[0].city, "上海")
        call_args = mocked_post.call_args.args
        self.assertEqual(call_args[0], "https://fe.zhaopin.com/grace/api/dsc/search-job-list")
        self.assertEqual(call_args[1]["orgNumbers"], ["104701"])
        self.assertEqual(call_args[1]["jobSource"], 2)


if __name__ == "__main__":
    unittest.main()
