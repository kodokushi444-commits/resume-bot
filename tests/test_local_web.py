from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from src.resume_bot.fetch_source_rules import (
    decorate_fetch_sources_for_settings,
    sanitize_selected_source_groups,
)
from src.resume_bot.fetch_funnel import build_fetch_funnel
from src.resume_bot.local_web import (
    _apply_boss_stoploss,
    _build_boss_workbench_summary,
    _build_boss_workbench_capture_defaults,
    _boss_fetch_halt_error,
    _boss_gate_presenter,
    _boss_workbench_capture_block_reason,
    _effective_ai_provider_settings,
    _boss_launch_command,
    _boss_stoploss_reason,
    _classify_boss_gate_status,
    _inspect_boss_page_via_cdp,
    _merge_ai_provider_settings,
    create_app,
    AIProviderSettingsPayload,
    _parse_subprocess_json_output,
    _recommended_boss_capture_rounds,
    _remove_disabled_selected_sources,
    _run_boss_workbench_capture,
    _snapshot_from_target_payload,
)
from src.resume_bot.local_web_assets import INDEX_HTML
from src.resume_bot.types import UserSettings


class LocalWebTests(unittest.TestCase):
    def test_local_web_assets_promote_boss_workbench_and_keep_legacy_panel(self) -> None:
        self.assertIn("ensureBossWorkbenchUi()", INDEX_HTML)
        self.assertIn("/static/vendor/bootstrap/css/bootstrap.min.css", INDEX_HTML)
        self.assertIn("/static/vendor/bootstrap-icons/font/bootstrap-icons.min.css", INDEX_HTML)
        self.assertIn("/static/vendor/bootstrap/js/bootstrap.bundle.min.js", INDEX_HTML)
        self.assertIn("theme-toggle-btn", INDEX_HTML)
        self.assertIn("resumeBotTheme", INDEX_HTML)
        self.assertIn("海的守望", INDEX_HTML)
        self.assertIn("#404e5b", INDEX_HTML)
        self.assertIn("#396b8d", INDEX_HTML)
        self.assertIn("#db888b", INDEX_HTML)
        self.assertIn("让岗位筛选变轻松", INDEX_HTML)
        self.assertIn("准备资料", INDEX_HTML)
        self.assertIn("设置目标", INDEX_HTML)
        self.assertIn("采集岗位", INDEX_HTML)
        self.assertIn("查看推荐", INDEX_HTML)
        self.assertIn("故障排查", INDEX_HTML)
        self.assertIn("旧入口", INDEX_HTML)
        self.assertIn("展开旧抓取 / 推荐入口", INDEX_HTML)
        self.assertIn("boss-workbench-review-limit", INDEX_HTML)
        self.assertIn("列表上限控制采集多少条", INDEX_HTML)
        self.assertIn('max="120" value="5"', INDEX_HTML)
        self.assertIn("boss-session-strip", INDEX_HTML)
        self.assertIn("workbench-grid", INDEX_HTML)
        self.assertIn("boss-control-grid", INDEX_HTML)
        self.assertIn("boss-results-shell", INDEX_HTML)
        self.assertIn("boss-results-grid", INDEX_HTML)
        self.assertIn("boss-context-grid", INDEX_HTML)
        self.assertIn("岗位审阅", INDEX_HTML)
        self.assertIn("可投状态未知", INDEX_HTML)
        self.assertIn("稍后看", INDEX_HTML)
        self.assertIn("data-review-filter=\"unknown_status\"", INDEX_HTML)
        self.assertIn("data-job-action=\"deferred\"", INDEX_HTML)
        self.assertIn("AI 设置", INDEX_HTML)
        self.assertIn('data-nav-page="ai-settings"', INDEX_HTML)
        self.assertIn("api/ai-settings", INDEX_HTML)
        self.assertIn("api/ai-settings/models", INDEX_HTML)
        self.assertIn("data-ai-list-models", INDEX_HTML)
        self.assertIn("aiProviderSignature", INDEX_HTML)
        self.assertIn("form_signature", INDEX_HTML)
        self.assertIn('id="ai-${kind}-model"', INDEX_HTML)
        self.assertIn("先列出可用模型", INDEX_HTML)
        self.assertIn("输入新 Key 可替换", INDEX_HTML)
        self.assertIn('id="ai-${kind}-api-key"', INDEX_HTML)
        self.assertIn('id="ai-${kind}-base-url"', INDEX_HTML)
        self.assertIn('data-ai-list-models="${kind}"', INDEX_HTML)
        self.assertIn('type="text" value="${escapeHtml(draftApiKey)}"', INDEX_HTML)
        self.assertNotIn("model-list-panel", INDEX_HTML)
        self.assertNotIn("m2.7", INDEX_HTML)
        self.assertNotIn("当前填写", INDEX_HTML)
        self.assertNotIn('type="password"', INDEX_HTML)
        self.assertIn("flow-stage", INDEX_HTML)
        self.assertIn("boss-workbench-degree-filter", INDEX_HTML)
        self.assertIn("boss-workbench-employment-filter", INDEX_HTML)
        self.assertIn("学历快筛", INDEX_HTML)
        self.assertIn("正职 / 实习", INDEX_HTML)
        self.assertIn("本轮推荐结果", INDEX_HTML)
        self.assertIn("命中理由", INDEX_HTML)
        self.assertIn("未命中原因", INDEX_HTML)
        self.assertIn("默认只展示 Top 5", INDEX_HTML)
        self.assertIn("查看全部过线岗位", INDEX_HTML)
        self.assertIn("jd-fold", INDEX_HTML)
        self.assertIn("已补 JD", INDEX_HTML)
        self.assertIn("JD 摘要", INDEX_HTML)
        self.assertIn("本次补抓结果", INDEX_HTML)
        self.assertNotIn("boss-workbench-supplement-limit", INDEX_HTML)
        self.assertIn("补抓并推荐", INDEX_HTML)
        self.assertIn("最高可接受学历要求", INDEX_HTML)
        self.assertIn("岗位要求不要高于你选的学历", INDEX_HTML)
        self.assertIn('class="troubleshooting-fold" data-legacy-controls="1"', INDEX_HTML)
        self.assertNotIn("<details open data-legacy-controls=\"1\">", INDEX_HTML)

    def test_build_fetch_funnel_prefers_source_level_counts(self) -> None:
        funnel = build_fetch_funnel(
            {
                "sources": [
                    {"source": "nowcoder_schedule", "enterprise_count": 12, "discovered_job_count": 18},
                    {"source": "nowcoder_direct", "count": 7},
                ],
                "total_jobs": 25,
            },
            {"matched_before_rerank": 6, "matched_after_rerank": 4},
            4,
        )
        self.assertEqual(
            funnel,
            {
                "enterprise_count": 12,
                "discovered_job_count": 25,
                "rules_passed_count": 6,
                "final_recommendation_count": 4,
            },
        )

    def test_ai_settings_save_masks_secret_and_reloads_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            settings_path = temp_root / "resume_bot" / "ai_settings.local.json"

            initial_config = SimpleNamespace(
                data_dir=temp_root,
                debug_dir=temp_root,
                project_root=Path.cwd(),
                ai_settings_path=settings_path,
                llm_provider="",
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                vision_provider="",
                vision_base_url="",
                vision_api_key="",
                vision_model="",
            )
            updated_config = SimpleNamespace(
                data_dir=temp_root,
                debug_dir=temp_root,
                project_root=Path.cwd(),
                ai_settings_path=settings_path,
                llm_provider="openai-compatible",
                llm_base_url="https://example.test/v1",
                llm_api_key="dummy-test-key-1234",
                llm_model="demo-text",
                vision_provider="openai-compatible",
                vision_base_url="https://vision.example.test/v1",
                vision_api_key="vk-test-secret-5678",
                vision_model="demo-vision",
            )

            class FakeTextClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def complete_text(self, *_args, **_kwargs):
                    return "OK"

            fake_pipeline = SimpleNamespace(
                text_client=FakeTextClient(),
                vision_client=None,
                reload_ai_clients=lambda config: setattr(fake_pipeline, "config", config),
            )

            with patch("src.resume_bot.local_web.load_config", side_effect=[initial_config, updated_config]), patch(
                "src.resume_bot.local_web.ResumeBotPipeline",
                return_value=fake_pipeline,
            ), patch("src.resume_bot.local_web.OpenAICompatibleTextClient", FakeTextClient):
                client = TestClient(create_app())
                response = client.post(
                    "/api/ai-settings/save",
                    json={
                        "user_id": "me",
                        "text": {
                            "provider": "openai-compatible",
                            "base_url": "https://example.test/v1",
                            "model": "demo-text",
                            "api_key": "dummy-test-key-1234",
                        },
                        "vision": {
                            "provider": "openai-compatible",
                            "base_url": "https://vision.example.test/v1",
                            "model": "demo-vision",
                            "api_key": "vk-test-secret-5678",
                        },
                    },
                )
                test_response = client.post("/api/ai-settings/test", json={"user_id": "me", "target": "text"})

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["text"]["api_key_configured"])
            self.assertEqual(payload["text"]["api_key_tail"], "1234")
            self.assertNotIn("dummy-test-key-1234", json.dumps(payload, ensure_ascii=False))
            self.assertTrue(settings_path.exists())
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["text"]["api_key"], "dummy-test-key-1234")
            self.assertEqual(fake_pipeline.config.llm_model, "demo-text")
            self.assertEqual(test_response.status_code, 200)
            self.assertEqual(test_response.json()["reply"], "OK")

    def test_ai_settings_provider_switch_does_not_reuse_old_key(self) -> None:
        existing = {
            "provider": "openai-compatible",
            "base_url": "https://old.example.test/v1",
            "model": "old-model",
            "api_key": "old-secret",
        }
        payload = AIProviderSettingsPayload(
            provider="openai-compatible",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            model="mimo-v2.5",
        )

        merged = _merge_ai_provider_settings(existing, payload)
        self.assertNotIn("api_key", merged)

        config = SimpleNamespace(
            llm_provider="openai-compatible",
            llm_base_url="https://old.example.test/v1",
            llm_model="old-model",
            llm_api_key="old-secret",
            vision_provider="openai-compatible",
            vision_base_url="https://old.example.test/v1",
            vision_model="old-model",
            vision_api_key="old-secret",
        )
        effective = _effective_ai_provider_settings(config, "text", payload)
        self.assertEqual(effective["api_key"], "")

    def test_ai_settings_models_lists_openai_compatible_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            config = SimpleNamespace(
                data_dir=temp_root,
                debug_dir=temp_root,
                project_root=Path.cwd(),
                ai_settings_path=temp_root / "resume_bot" / "ai_settings.local.json",
                llm_provider="",
                llm_base_url="",
                llm_api_key="",
                llm_model="",
                vision_provider="",
                vision_base_url="",
                vision_api_key="",
                vision_model="",
            )
            response = Mock()
            response.json.return_value = {"data": [{"id": "demo-vision", "input_modalities": ["text", "image"]}]}
            response.raise_for_status.return_value = None

            with patch("src.resume_bot.local_web.load_config", return_value=config), patch(
                "src.resume_bot.local_web.ResumeBotPipeline",
                return_value=SimpleNamespace(text_client=None, vision_client=None),
            ), patch("src.resume_bot.local_web.requests.get", return_value=response) as get:
                client = TestClient(create_app())
                payload = client.post(
                    "/api/ai-settings/models",
                    json={
                        "user_id": "me",
                        "target": "vision",
                        "vision": {
                            "provider": "openai-compatible",
                            "base_url": "https://example.test/v1/chat/completions",
                            "api_key": "secret",
                        },
                    },
                )

        self.assertEqual(payload.status_code, 200)
        body = payload.json()
        self.assertEqual(body["model_count"], 1)
        self.assertEqual(body["models"][0]["id"], "demo-vision")
        self.assertEqual(get.call_args.args[0], "https://example.test/v1/models")

    def test_decorate_fetch_sources_disables_schedule_for_social_only(self) -> None:
        settings = UserSettings(user_id="me", job_types=["社招"])
        items = decorate_fetch_sources_for_settings(
            [
                {"id": "nowcoder", "label": "牛客", "default_checked": True},
                {"id": "nowcoder_schedule", "label": "牛客校招日程", "default_checked": True},
            ],
            settings,
        )
        by_id = {item["id"]: item for item in items}
        self.assertFalse(by_id["nowcoder"]["disabled"])
        self.assertTrue(by_id["nowcoder_schedule"]["disabled"])
        self.assertIn("只看社招", by_id["nowcoder_schedule"]["disabled_reason"])
        self.assertFalse(by_id["nowcoder_schedule"]["default_checked"])

    def test_sanitize_selected_source_groups_drops_schedule_for_social_only(self) -> None:
        settings = UserSettings(user_id="me", job_types=["社招"])
        allowed, dropped = sanitize_selected_source_groups(["nowcoder", "nowcoder_schedule"], settings)
        self.assertEqual(allowed, ["nowcoder"])
        self.assertEqual(dropped, ["nowcoder_schedule"])

    def test_snapshot_from_target_payload_detects_login_required(self) -> None:
        snapshot = _snapshot_from_target_payload(
            {
                "url": "https://www.zhipin.com/web/user/?ka=header-login",
                "title": "BOSS直聘注册登录",
                "type": "page",
            }
        )
        self.assertEqual(snapshot["page_state"], "login_required")
        self.assertTrue(snapshot["is_boss_domain"])

    def test_snapshot_from_target_payload_detects_security_verify(self) -> None:
        snapshot = _snapshot_from_target_payload(
            {
                "url": "https://www.zhipin.com/web/common/security-check.html?code=35",
                "title": "安全验证",
                "type": "page",
            }
        )
        self.assertEqual(snapshot["page_state"], "security_verify")

    def test_snapshot_from_target_payload_treats_results_page_as_ready_without_body(self) -> None:
        snapshot = _snapshot_from_target_payload(
            {
                "url": "https://www.zhipin.com/web/geek/jobs?query=%E8%BF%90%E8%90%A5&city=101280600",
                "title": "Shenzhen Jobs - BOSS",
                "type": "page",
            }
        )
        self.assertEqual(snapshot["page_state"], "ready")

    def test_classify_boss_gate_status_prefers_no_browser(self) -> None:
        status = _classify_boss_gate_status({"browser_connected": False})
        self.assertEqual(status, "no_browser")

    def test_classify_boss_gate_status_requires_boss_page(self) -> None:
        status = _classify_boss_gate_status({"browser_connected": True, "has_boss_page": False})
        self.assertEqual(status, "boss_page_missing")

    def test_classify_boss_gate_status_blocks_loading_and_cookie_conflict(self) -> None:
        loading_status = _classify_boss_gate_status(
            {
                "browser_connected": True,
                "has_boss_page": True,
                "page_state": "loading",
            }
        )
        cookie_conflict_status = _classify_boss_gate_status(
            {
                "browser_connected": True,
                "has_boss_page": True,
                "page_state": "ready",
                "page_url": "https://www.zhipin.com/web/geek/jobs?query=%E8%BF%90%E8%90%A5&city=101280600",
                "cookie_authenticated": False,
            }
        )
        self.assertEqual(loading_status, "uncertain")
        self.assertEqual(cookie_conflict_status, "uncertain")

    def test_classify_boss_gate_status_allows_ready_state(self) -> None:
        status = _classify_boss_gate_status(
            {
                "browser_connected": True,
                "has_boss_page": True,
                "page_state": "ready",
                "page_url": "https://www.zhipin.com/web/geek/jobs?query=%E8%BF%90%E8%90%A5&city=101280600",
                "cookie_authenticated": True,
            }
        )
        self.assertEqual(status, "ready")

    def test_classify_boss_gate_status_requires_results_page(self) -> None:
        status = _classify_boss_gate_status(
            {
                "browser_connected": True,
                "has_boss_page": True,
                "page_state": "ready",
                "page_url": "https://www.zhipin.com/tianjin/?seoRefer=index",
                "cookie_authenticated": True,
            }
        )
        self.assertEqual(status, "results_page_required")

    def test_boss_gate_presenter_no_browser_requires_launch_browser(self) -> None:
        payload = _boss_gate_presenter(
            "no_browser",
            {"checked_at": "2026-03-30T12:00:00", "login_browser_label": "Chrome"},
        )
        self.assertEqual(payload["action_kind"], "launch_browser")
        self.assertEqual(payload["action_label"], "打开登录浏览器")
        self.assertIn("Chrome", payload["message"])

    def test_boss_gate_presenter_results_page_required_mentions_capture_only(self) -> None:
        payload = _boss_gate_presenter(
            "results_page_required",
            {"checked_at": "2026-03-30T12:00:00", "login_browser_label": "Chrome"},
        )
        self.assertIn("不会阻止新工作台采集", payload["message"])
        self.assertIn("补抓 JD", payload["action_hint"])

    def test_inspect_boss_page_via_cdp_does_not_close_attached_browser(self) -> None:
        close_called = {"value": False}

        class FakeContext:
            def cookies(self, urls=None):
                return [{"name": "wt2"}, {"name": "__zp_stoken__"}]

        class FakeBrowser:
            def __init__(self):
                self.contexts = [FakeContext()]

            def close(self):
                close_called["value"] = True

        class FakeChromium:
            def connect_over_cdp(self, endpoint):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakePlaywrightManager:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, exc_type, exc, tb):
                return False

        config = SimpleNamespace(boss_browser_cdp_port=9222, boss_browser_cdp_url="")
        fake_snapshot = {
            "url": "https://www.zhipin.com/shenzhen/?seoRefer=index",
            "title": "BOSS 直聘",
            "page_state": "ready",
            "is_boss_domain": True,
        }
        fake_module = SimpleNamespace(sync_playwright=lambda: FakePlaywrightManager())
        with patch("src.resume_bot.local_web.resolve_cdp_endpoint", return_value="http://127.0.0.1:9222"), patch(
            "src.resume_bot.local_web.resolve_cdp_websocket_url",
            return_value="ws://127.0.0.1:9222/devtools/browser/test",
        ), patch.dict(sys.modules, {"playwright.sync_api": fake_module}), patch(
            "src.resume_bot.local_web._find_existing_boss_page",
            return_value=SimpleNamespace(),
        ), patch("src.resume_bot.local_web.extract_page_snapshot", return_value=fake_snapshot):
            payload = _inspect_boss_page_via_cdp(config)
        self.assertEqual(payload["snapshot"]["url"], fake_snapshot["url"])
        self.assertFalse(close_called["value"])

    def test_boss_launch_command_uses_capture_script(self) -> None:
        config = SimpleNamespace(project_root=Path(__file__).resolve().parents[1])
        with patch(
            "src.resume_bot.local_web._preferred_boss_login_browser",
            return_value=("chrome", "Chrome", r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"),
        ):
            command, command_text, browser_label = _boss_launch_command(config)
        self.assertEqual(browser_label, "Chrome")
        self.assertEqual(command[-3:], ["launch-windows-browser", "--browser", "chrome"])
        self.assertIn("capture_boss_session.py", command[1])
        self.assertIn("capture_boss_session.py", command_text)

    def test_boss_fetch_halt_error_returns_boss_halt_message(self) -> None:
        message = _boss_fetch_halt_error(
            {
                "total_jobs": 0,
                "sources": [
                    {
                        "source": "boss_browser",
                        "halted": True,
                        "error": "当前还不是职位结果页。先在登录浏览器里打开一个职位结果页再抓。",
                    }
                ],
            },
            selected_source_groups=["boss"],
            selected_source_names=["boss_browser"],
        )
        self.assertIn("职位结果页", message)

    def test_boss_fetch_halt_error_ignores_non_boss_or_partial_success(self) -> None:
        self.assertEqual(
            _boss_fetch_halt_error(
                {
                    "total_jobs": 0,
                    "sources": [{"source": "nowcoder_direct", "halted": True, "error": "noop"}],
                },
                selected_source_groups=["nowcoder"],
                selected_source_names=["nowcoder_direct"],
            ),
            "",
        )
        self.assertEqual(
            _boss_fetch_halt_error(
                {
                    "total_jobs": 1,
                    "sources": [{"source": "boss_browser", "halted": True, "error": "noop"}],
                },
                selected_source_groups=["boss"],
                selected_source_names=["boss_browser"],
            ),
            "",
        )

    def test_apply_boss_stoploss_disables_boss_source(self) -> None:
        items = _apply_boss_stoploss(
            [
                {"id": "nowcoder", "default_checked": True, "disabled": False, "disabled_reason": ""},
                {"id": "boss", "default_checked": True, "disabled": False, "disabled_reason": ""},
            ]
        )
        by_id = {item["id"]: item for item in items}
        self.assertFalse(by_id["nowcoder"]["disabled"])
        self.assertTrue(by_id["boss"]["disabled"])
        self.assertFalse(by_id["boss"]["default_checked"])
        self.assertIn(_boss_stoploss_reason(), by_id["boss"]["disabled_reason"])

    def test_remove_disabled_selected_sources_drops_boss(self) -> None:
        self.assertEqual(_remove_disabled_selected_sources(["boss", "nowcoder"]), ["nowcoder"])

    def test_parse_subprocess_json_output_handles_prefixed_noise(self) -> None:
        payload = _parse_subprocess_json_output("warn\\n{\"ok\": true, \"jobs_count\": 15}")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["jobs_count"], 15)

    def test_build_boss_workbench_capture_defaults_prefers_recent_run(self) -> None:
        settings = UserSettings(
            user_id="me",
            preferred_cities=["杭州"],
            preferred_roles=["产品运营"],
            preferred_keywords=["AI 运营"],
            max_degree_requirement="本科",
            campus_role_mode="intern",
        )
        defaults = _build_boss_workbench_capture_defaults(
            settings,
            [
                {"city": "深圳", "keyword": "运营", "quick_filters": {"degree_filter": "大专"}},
                {"city": "广州", "keyword": "用户运营"},
            ],
        )
        self.assertEqual(defaults["city"], "深圳")
        self.assertEqual(defaults["keyword"], "运营")
        self.assertEqual(defaults["limit"], 45)
        self.assertEqual(defaults["rounds"], 2)
        self.assertEqual(defaults["degree_filter"], "大专")
        self.assertEqual(defaults["employment_mode_filter"], "intern")

    def test_recommended_boss_capture_rounds_scales_with_limit(self) -> None:
        self.assertEqual(_recommended_boss_capture_rounds(12), 0)
        self.assertEqual(_recommended_boss_capture_rounds(30), 1)
        self.assertEqual(_recommended_boss_capture_rounds(45), 2)
        self.assertEqual(_recommended_boss_capture_rounds(120), 7)

    def test_boss_workbench_capture_block_reason_allows_homepage_state(self) -> None:
        self.assertEqual(
            _boss_workbench_capture_block_reason(
                {"status": "results_page_required", "title": "noop", "message": "noop"}
            ),
            "",
        )
        blocked = _boss_workbench_capture_block_reason(
            {"status": "security_verify", "title": "安全验证", "message": "先不要继续抓"}
        )
        self.assertIn("安全验证", blocked)

    def test_run_boss_workbench_capture_reuses_probe_script_and_imports_artifact(self) -> None:
        config = SimpleNamespace(project_root=Path(__file__).resolve().parents[1])
        imported_filters: dict = {}

        def fake_import(user_id, artifact_path, quick_filters=None):
            imported_filters.update(quick_filters or {})
            return {
                "fetch_session_id": "session-789",
                "job_count": 15,
                "artifact_path": str(artifact_path),
            }

        pipeline = SimpleNamespace(
            import_boss_queue_artifact=fake_import
        )
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "fetch_session_id": "session-789",
                    "jobs_count": 15,
                    "output_path": str(Path("E:/tmp/boss-queue.json")),
                },
                ensure_ascii=False,
            ),
            stderr="",
        )
        with patch("src.resume_bot.local_web.subprocess.run", return_value=completed) as run_mock:
            result = _run_boss_workbench_capture(
                config,
                pipeline,
                "me",
                city="深圳",
                keyword="运营",
                limit=45,
                rounds=2,
                degree_filter="本科",
                employment_mode_filter="intern",
            )
        self.assertEqual(result["fetch_session_id"], "session-789")
        self.assertEqual(result["import"]["job_count"], 15)
        self.assertEqual(imported_filters, {"degree_filter": "本科", "employment_mode_filter": "intern"})
        command = run_mock.call_args.kwargs["args"] if "args" in run_mock.call_args.kwargs else run_mock.call_args[0][0]
        self.assertIn("boss_cdp_list_probe.py", command[1])
        self.assertIn("--degree-filter", command)
        self.assertIn("--employment-mode-filter", command)

    def test_build_boss_workbench_summary_returns_empty_when_no_boss_runs(self) -> None:
        fake_pipeline = SimpleNamespace(
            store=SimpleNamespace(
                list_recent_source_runs=lambda limit=8, source_names=None: [],
                get_settings=lambda user_id: UserSettings(user_id=user_id),
            ),
            list_review_profiles=lambda user_id, selected_source_names=None, fetch_session_id=None: [],
        )
        summary = _build_boss_workbench_summary(fake_pipeline, "me")
        self.assertFalse(summary["available"])
        self.assertEqual(summary["latest_fetch_session_id"], "")
        self.assertEqual(summary["available_review_profiles"], [])
        self.assertEqual(summary["recent_source_runs"], [])
        self.assertEqual(summary["capture_defaults"]["city"], "深圳")
        self.assertEqual(summary["capture_defaults"]["keyword"], "运营")

    def test_build_boss_workbench_summary_uses_latest_boss_session(self) -> None:
        runs = [
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-04-03T10:00:00+08:00",
                "finished_at": "2026-04-03T10:01:00+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "20260403-100000",
                        "job_count": 45,
                        "city": "深圳",
                        "keyword": "运营",
                        "quick_filters": {"degree_filter": "本科", "employment_mode_filter": "intern"},
                        "url_filter_params": {"degree": "206"},
                        "url_filter_applied": True,
                        "local_filter": {"dropped_count": 3},
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        captured_calls: list[tuple[str | None, tuple[str, ...] | None]] = []

        def fake_list_review_profiles(user_id, selected_source_names=None, fetch_session_id=None):
            selected = tuple(selected_source_names or []) if selected_source_names is not None else None
            captured_calls.append((fetch_session_id, selected))
            return [
                {"id": "default", "label": "当前全局设置", "job_count": 45},
                {"id": "boss_social", "label": "BOSS 社招预览", "job_count": 45},
            ]

        fake_pipeline = SimpleNamespace(
            store=SimpleNamespace(
                list_recent_source_runs=lambda limit=8, source_names=None: runs,
                get_settings=lambda user_id: UserSettings(
                    user_id=user_id,
                    preferred_cities=["杭州"],
                    preferred_roles=["产品运营"],
                ),
            ),
            list_review_profiles=fake_list_review_profiles,
        )
        summary = _build_boss_workbench_summary(fake_pipeline, "me")
        self.assertTrue(summary["available"])
        self.assertEqual(summary["latest_fetch_session_id"], "20260403-100000")
        self.assertEqual(len(summary["available_review_profiles"]), 2)
        self.assertEqual(summary["recent_source_runs"][0]["job_count"], 45)
        self.assertEqual(summary["capture_defaults"]["city"], "娣卞湷")
        self.assertEqual(summary["capture_defaults"]["keyword"], "杩愯惀")
        self.assertEqual(captured_calls, [("20260403-100000", ("boss_browser",))])

    def test_build_boss_workbench_summary_uses_latest_boss_session(self) -> None:
        runs = [
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-04-03T10:00:00+08:00",
                "finished_at": "2026-04-03T10:01:00+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "20260403-100000",
                        "job_count": 45,
                        "city": "深圳",
                        "keyword": "运营",
                        "quick_filters": {"degree_filter": "本科", "employment_mode_filter": "intern"},
                        "url_filter_params": {"degree": "206"},
                        "url_filter_applied": True,
                        "local_filter": {"dropped_count": 3},
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        captured_calls: list[tuple[str | None, tuple[str, ...] | None]] = []

        def fake_list_review_profiles(user_id, selected_source_names=None, fetch_session_id=None):
            selected = tuple(selected_source_names or []) if selected_source_names is not None else None
            captured_calls.append((fetch_session_id, selected))
            return [
                {"id": "default", "label": "当前全局设置", "job_count": 45},
                {"id": "boss_social", "label": "BOSS 社招预览", "job_count": 45},
            ]

        fake_pipeline = SimpleNamespace(
            store=SimpleNamespace(
                list_recent_source_runs=lambda limit=8, source_names=None: runs,
                get_settings=lambda user_id: UserSettings(
                    user_id=user_id,
                    preferred_cities=["杭州"],
                    preferred_roles=["产品运营"],
                ),
            ),
            list_review_profiles=fake_list_review_profiles,
        )
        summary = _build_boss_workbench_summary(fake_pipeline, "me")
        self.assertTrue(summary["available"])
        self.assertEqual(summary["latest_fetch_session_id"], "20260403-100000")
        self.assertEqual(len(summary["available_review_profiles"]), 2)
        self.assertEqual(summary["recent_source_runs"][0]["job_count"], 45)
        self.assertEqual(summary["recent_source_runs"][0]["quick_filters"]["degree_filter"], "本科")
        self.assertEqual(summary["recent_source_runs"][0]["url_filter_params"], {"degree": "206"})
        self.assertTrue(summary["recent_source_runs"][0]["url_filter_applied"])
        self.assertEqual(summary["capture_defaults"]["city"], summary["recent_source_runs"][0]["city"])
        self.assertEqual(summary["capture_defaults"]["keyword"], summary["recent_source_runs"][0]["keyword"])
        self.assertEqual(summary["capture_defaults"]["degree_filter"], "本科")
        self.assertEqual(summary["capture_defaults"]["employment_mode_filter"], "intern")
        self.assertEqual(captured_calls, [("20260403-100000", ("boss_browser",))])

    def test_build_boss_workbench_summary_does_not_fallback_from_empty_preferred_session(self) -> None:
        runs = [
            {
                "source_name": "boss_browser",
                "status": "detail_failed",
                "started_at": "2026-05-03T21:19:00+08:00",
                "finished_at": "2026-05-03T21:19:10+08:00",
                "detail_json": json.dumps({"fetch_session_id": "session-old"}),
            },
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-05-03T21:18:00+08:00",
                "finished_at": "2026-05-03T21:18:20+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "session-ai-empty",
                        "job_count": 0,
                        "raw_job_count": 45,
                        "city": "深圳",
                        "keyword": "AI",
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-05-03T21:11:00+08:00",
                "finished_at": "2026-05-03T21:11:20+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "session-old",
                        "job_count": 45,
                        "city": "深圳",
                        "keyword": "运营",
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        captured_calls: list[str | None] = []

        def fake_list_review_profiles(user_id, selected_source_names=None, fetch_session_id=None):
            captured_calls.append(fetch_session_id)
            if fetch_session_id == "session-old":
                return [{"id": "default", "label": "当前全局设置", "job_count": 45}]
            return []

        fake_pipeline = SimpleNamespace(
            store=SimpleNamespace(
                list_recent_source_runs=lambda limit=8, source_names=None: runs,
                get_settings=lambda user_id: UserSettings(user_id=user_id),
            ),
            list_review_profiles=fake_list_review_profiles,
        )

        summary = _build_boss_workbench_summary(
            fake_pipeline,
            "me",
            preferred_fetch_session_id="session-ai-empty",
        )

        self.assertTrue(summary["available"])
        self.assertEqual(summary["latest_fetch_session_id"], "session-ai-empty")
        self.assertEqual(summary["available_review_profiles"], [])
        self.assertEqual(summary["recent_source_runs"][1]["keyword"], "AI")
        self.assertEqual(captured_calls, ["session-ai-empty"])

    def test_build_boss_workbench_summary_prefers_latest_imported_session_over_detail_run(self) -> None:
        runs = [
            {
                "source_name": "boss_browser",
                "status": "detail_failed",
                "started_at": "2026-05-03T21:19:00+08:00",
                "finished_at": "2026-05-03T21:19:10+08:00",
                "detail_json": json.dumps({"fetch_session_id": "session-old"}),
            },
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-05-03T21:18:00+08:00",
                "finished_at": "2026-05-03T21:18:20+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "session-ai-empty",
                        "job_count": 0,
                        "raw_job_count": 45,
                        "city": "深圳",
                        "keyword": "AI",
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "source_name": "boss_browser",
                "status": "imported",
                "started_at": "2026-05-03T21:11:00+08:00",
                "finished_at": "2026-05-03T21:11:20+08:00",
                "detail_json": json.dumps(
                    {
                        "fetch_session_id": "session-old",
                        "job_count": 45,
                        "city": "深圳",
                        "keyword": "运营",
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        captured_calls: list[str | None] = []

        def fake_list_review_profiles(user_id, selected_source_names=None, fetch_session_id=None):
            captured_calls.append(fetch_session_id)
            return []

        fake_pipeline = SimpleNamespace(
            store=SimpleNamespace(
                list_recent_source_runs=lambda limit=8, source_names=None: runs,
                get_settings=lambda user_id: UserSettings(user_id=user_id),
            ),
            list_review_profiles=fake_list_review_profiles,
        )

        summary = _build_boss_workbench_summary(fake_pipeline, "me")

        self.assertEqual(summary["latest_fetch_session_id"], "session-ai-empty")
        self.assertEqual(captured_calls, ["session-ai-empty"])

    def test_boss_workbench_capture_endpoint_does_not_recommend_immediately(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m8_capture"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-capture",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [],
            "capture_defaults": {"city": "深圳", "keyword": "运营", "limit": 45, "rounds": 2},
        }

        def fail_review(*args, **kwargs):
            raise AssertionError("capture endpoint must not trigger recommendation")

        fake_pipeline = SimpleNamespace(review_fetch_session=fail_review)
        fake_config = SimpleNamespace(
            data_dir=temp_root,
            debug_dir=temp_root,
            project_root=Path.cwd(),
        )
        capture_result = {
            "capture": {"jobs_count": 12, "fetch_session_id": "boss-session-capture"},
            "import": {"job_count": 12, "fetch_session_id": "boss-session-capture"},
            "fetch_session_id": "boss-session-capture",
        }

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._boss_gate_status",
            return_value={"gate": {"status": "ready"}},
        ), patch(
            "src.resume_bot.local_web._run_boss_workbench_capture",
            return_value=capture_result,
        ) as capture_mock, patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.post(
                "/api/boss/workbench/capture",
                json={
                    "user_id": "me",
                    "city": "深圳",
                    "keyword": "运营",
                    "degree_filter": "本科",
                    "employment_mode_filter": "intern",
                    "limit": 45,
                    "review_limit": 12,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["review"])
        self.assertEqual(payload["next_step"]["id"], "supplement_and_recommend")
        self.assertEqual(payload["workbench"]["latest_fetch_session_id"], "boss-session-capture")
        self.assertEqual(capture_mock.call_args.kwargs["degree_filter"], "本科")
        self.assertEqual(capture_mock.call_args.kwargs["employment_mode_filter"], "intern")
        self.assertEqual(payload["quick_filters"], {"degree_filter": "本科", "employment_mode_filter": "intern"})

    def test_assistant_boss_status_returns_stable_control_contract(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m84_status"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-control",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [
                {
                    "fetch_session_id": "boss-session-control",
                    "job_count": 12,
                    "raw_job_count": 15,
                    "city": "深圳",
                    "keyword": "运营",
                    "quick_filters": {"degree_filter": "本科", "employment_mode_filter": "intern"},
                    "local_filter": {"dropped_count": 3},
                }
            ],
            "capture_defaults": {"city": "深圳", "keyword": "运营", "limit": 45, "rounds": 2},
        }
        fake_pipeline = SimpleNamespace(
            boss_session_detail_status=lambda fetch_session_id: {
                "fetch_session_id": fetch_session_id,
                "session_job_count": 12,
                "detail_fetched_count": 0,
                "pending_job_count": 12,
            },
            load_boss_session_recommendation=lambda fetch_session_id, limit=5, review_profile=None: {},
            empty_boss_session_recommendation=lambda fetch_session_id, message="": {
                "ok": False,
                "stage": "recommendation_pending",
                "fetch_session_id": fetch_session_id,
                "session_job_count": 12,
                "detail_fetched_count": 0,
                "pending_job_count": 12,
                "recommendation_base_count": 0,
                "matched_count": 0,
                "displayed_count": 0,
                "items": [],
                "message": message,
            },
        )
        fake_config = SimpleNamespace(data_dir=temp_root, debug_dir=temp_root, project_root=Path.cwd())

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.get("/api/assistant/boss/status?user_id=me")
            refresh_response = client.post(
                "/api/assistant/boss/refresh",
                json={"user_id": "me", "fetch_session_id": "boss-session-control"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "current_status")
        self.assertEqual(payload["state"]["current_status"]["stage"], "list_imported")
        self.assertEqual(payload["next_action"]["id"], "supplement_and_recommend")
        self.assertEqual(
            [item["id"] for item in payload["state"]["available_actions"]],
            ["current_status", "start_capture", "supplement_and_recommend", "refresh_status"],
        )
        self.assertEqual(payload["state"]["active_session"]["fetch_session_id"], "boss-session-control")
        self.assertEqual(payload["state"]["active_session"]["local_filter"]["dropped_count"], 3)
        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(refresh_response.json()["action"], "refresh_status")

    def test_assistant_boss_start_capture_wraps_workbench_without_recommending(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m84_capture"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-control-capture",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [
                {
                    "fetch_session_id": "boss-session-control-capture",
                    "job_count": 12,
                    "city": "深圳",
                    "keyword": "运营",
                    "quick_filters": {"degree_filter": "本科", "employment_mode_filter": "intern"},
                }
            ],
            "capture_defaults": {"city": "深圳", "keyword": "运营", "limit": 45, "rounds": 2},
        }

        def fail_review(*args, **kwargs):
            raise AssertionError("assistant capture must not trigger recommendation")

        fake_pipeline = SimpleNamespace(
            review_fetch_session=fail_review,
            boss_session_detail_status=lambda fetch_session_id: {
                "fetch_session_id": fetch_session_id,
                "session_job_count": 12,
                "detail_fetched_count": 0,
                "pending_job_count": 12,
            },
            load_boss_session_recommendation=lambda fetch_session_id, limit=5, review_profile=None: {},
            empty_boss_session_recommendation=lambda fetch_session_id, message="": {
                "ok": False,
                "stage": "recommendation_pending",
                "fetch_session_id": fetch_session_id,
                "session_job_count": 12,
                "detail_fetched_count": 0,
                "pending_job_count": 12,
                "recommendation_base_count": 0,
                "matched_count": 0,
                "displayed_count": 0,
                "items": [],
                "message": message,
            },
        )
        fake_config = SimpleNamespace(data_dir=temp_root, debug_dir=temp_root, project_root=Path.cwd())
        capture_result = {
            "capture": {"jobs_count": 12, "fetch_session_id": "boss-session-control-capture"},
            "import": {"job_count": 12, "fetch_session_id": "boss-session-control-capture"},
            "fetch_session_id": "boss-session-control-capture",
        }

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._boss_gate_status",
            return_value={"gate": {"status": "ready"}},
        ), patch(
            "src.resume_bot.local_web._run_boss_workbench_capture",
            return_value=capture_result,
        ) as capture_mock, patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.post(
                "/api/assistant/boss/start-capture",
                json={
                    "user_id": "me",
                    "city": "深圳",
                    "keyword": "运营",
                    "degree_filter": "本科",
                    "employment_mode_filter": "intern",
                    "limit": 45,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action"], "start_capture")
        self.assertIsNone(payload["result"]["review"])
        self.assertEqual(payload["next_action"]["id"], "supplement_and_recommend")
        self.assertEqual(capture_mock.call_args.kwargs["degree_filter"], "本科")
        self.assertEqual(capture_mock.call_args.kwargs["employment_mode_filter"], "intern")

    def test_boss_workbench_supplement_endpoint_returns_recommendation_refresh(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m6"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-1",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [],
            "capture_defaults": {"city": "深圳", "keyword": "运营", "limit": 45, "rounds": 2},
        }
        fake_pipeline = SimpleNamespace(
            supplement_boss_session_and_recommend=lambda user_id, fetch_session_id, recommendation_limit=5, review_profile=None: {
                "ok": True,
                "fetch_session_id": fetch_session_id,
                "supplement": {
                    "ok": True,
                    "fetch_session_id": fetch_session_id,
                    "pending_job_count": 11,
                    "target_pending_job_count": 12,
                    "detail_fetched_count": 1,
                    "session_job_count": 12,
                    "updated_count": 1,
                    "attempted_count": 1,
                    "success_count": 1,
                    "updated_jobs": [
                        {
                            "title": "运营岗",
                            "company_name": "测试公司",
                            "city": "成都",
                            "salary_text": "8-13K",
                            "degree_requirement": "本科",
                            "apply_url": "https://www.zhipin.com/job_detail/demo.html",
                            "description": "这是补抓后的 JD 摘要",
                            "detail_fetched": True,
                        }
                    ],
                },
                "recommendation": {
                    "ok": True,
                    "fetch_session_id": fetch_session_id,
                    "matched_count": 12,
                    "recommendation_base_count": 1,
                    "session_job_count": 12,
                    "items": [{} for _ in range(min(recommendation_limit, 3))],
                    "review_profile": {"name": review_profile or "default", "label": "当前全局设置"},
                },
            },
            store=SimpleNamespace(
                get_settings=lambda user_id: UserSettings(user_id=user_id),
                list_recent_source_runs=lambda limit=8, source_names=None: [],
                load_jobs=lambda selected_source_names=None: [],
                get_active_resume=lambda user_id: ({}, ""),
                get_source_runs=lambda *args, **kwargs: [],
            ),
            resolve_selected_source_names=lambda groups: [],
            default_fetch_source_groups=lambda: [],
            load_active_jobs=lambda user_id, selected_source_names, fetch_session_id=None: [],
            list_review_profiles=lambda user_id, selected_source_names=None, fetch_session_id=None: [],
        )
        fake_config = SimpleNamespace(
            data_dir=temp_root,
            debug_dir=temp_root,
            project_root=Path.cwd(),
        )

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.post(
                "/api/boss/workbench/supplement",
                json={
                    "user_id": "me",
                    "fetch_session_id": "boss-session-1",
                    "limit": 3,
                    "review_limit": 12,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supplement"]["updated_count"], 1)
        self.assertEqual(len(payload["supplement"]["updated_jobs"]), 1)
        self.assertTrue(payload["supplement"]["updated_jobs"][0]["detail_fetched"])
        self.assertEqual(payload["review"]["matched_count"], 12)
        self.assertEqual(payload["recommendation"]["recommendation_base_count"], 1)
        self.assertEqual(payload["workbench"]["latest_fetch_session_id"], "boss-session-1")
        self.assertNotIn("dashboard", payload)

    def test_boss_workbench_review_recomputes_missing_profile_when_details_exist(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_review_profile_recompute"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-profile",
            "available_review_profiles": [{"id": "boss_social", "label": "BOSS 社招预览", "job_count": 3}],
            "recent_source_runs": [
                {
                    "fetch_session_id": "boss-session-profile",
                    "job_count": 3,
                    "city": "广州",
                    "keyword": "AI",
                    "quick_filters": {"degree_filter": "本科", "employment_mode_filter": ""},
                }
            ],
            "capture_defaults": {"city": "广州", "keyword": "AI", "limit": 45, "rounds": 2},
        }
        captured: dict = {}

        def fake_recommend_boss_session(user_id, fetch_session_id, limit=5, review_profile=None):
            captured["user_id"] = user_id
            captured["fetch_session_id"] = fetch_session_id
            captured["limit"] = limit
            captured["review_profile"] = review_profile
            return {
                "ok": True,
                "stage": "recommendation_done",
                "fetch_session_id": fetch_session_id,
                "matched_count": 3,
                "displayed_count": 3,
                "items": [],
                "review_profile": {"name": review_profile, "label": "BOSS 社招预览"},
            }

        fake_pipeline = SimpleNamespace(
            load_boss_session_recommendation=lambda fetch_session_id, limit=5, review_profile=None: {},
            boss_session_detail_status=lambda fetch_session_id: {
                "fetch_session_id": fetch_session_id,
                "session_job_count": 3,
                "detail_fetched_count": 3,
                "pending_job_count": 0,
            },
            recommend_boss_session=fake_recommend_boss_session,
            empty_boss_session_recommendation=lambda fetch_session_id, message="": {},
        )
        fake_config = SimpleNamespace(data_dir=temp_root, debug_dir=temp_root, project_root=Path.cwd())

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.get(
                "/api/boss/workbench/review?user_id=me&fetch_session_id=boss-session-profile&review_profile=boss_social&limit=9"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["review"]["matched_count"], 3)
        self.assertEqual(captured["fetch_session_id"], "boss-session-profile")
        self.assertEqual(captured["review_profile"], "boss_social")
        self.assertEqual(captured["limit"], 9)

    def test_assistant_boss_supplement_and_recommend_returns_ready_state(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m84_supplement"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-ready",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [
                {
                    "fetch_session_id": "boss-session-ready",
                    "job_count": 12,
                    "city": "深圳",
                    "keyword": "运营",
                }
            ],
            "capture_defaults": {"city": "深圳", "keyword": "运营", "limit": 45, "rounds": 2},
        }
        recommendation = {
            "ok": True,
            "stage": "recommendation_done",
            "fetch_session_id": "boss-session-ready",
            "matched_count": 8,
            "displayed_count": 5,
            "recommendation_base_count": 9,
            "session_job_count": 12,
            "items": [{} for _ in range(5)],
            "review_profile": {"name": "default", "label": "当前全局设置"},
        }
        fake_pipeline = SimpleNamespace(
            supplement_boss_session_and_recommend=lambda user_id, fetch_session_id, recommendation_limit=5, review_profile=None: {
                "ok": True,
                "fetch_session_id": fetch_session_id,
                "supplement": {
                    "ok": True,
                    "fetch_session_id": fetch_session_id,
                    "updated_count": 4,
                    "attempted_count": 4,
                    "success_count": 4,
                    "pending_job_count": 3,
                    "detail_fetched_count": 9,
                    "session_job_count": 12,
                    "updated_jobs": [],
                },
                "recommendation": recommendation,
            },
            boss_session_detail_status=lambda fetch_session_id: {
                "fetch_session_id": fetch_session_id,
                "session_job_count": 12,
                "detail_fetched_count": 9,
                "pending_job_count": 3,
            },
            load_boss_session_recommendation=lambda fetch_session_id, limit=5, review_profile=None: recommendation,
            empty_boss_session_recommendation=lambda fetch_session_id, message="": {},
        )
        fake_config = SimpleNamespace(data_dir=temp_root, debug_dir=temp_root, project_root=Path.cwd())

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.post(
                "/api/assistant/boss/supplement-and-recommend",
                json={"user_id": "me", "fetch_session_id": "boss-session-ready", "review_limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action"], "supplement_and_recommend")
        self.assertEqual(payload["state"]["current_status"]["stage"], "recommendation_ready")
        self.assertEqual(payload["next_action"]["id"], "refresh_status")
        self.assertEqual(payload["state"]["active_session"]["matched_count"], 8)
        self.assertEqual(payload["result"]["recommendation"]["matched_count"], 8)

    def test_boss_workbench_supplement_endpoint_returns_409_when_no_jd_was_updated(self) -> None:
        temp_root = Path.cwd() / "data" / "test_local_web_m6_zero"
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "available": True,
            "source_name": "boss_browser",
            "latest_fetch_session_id": "boss-session-2",
            "available_review_profiles": [{"id": "default", "label": "当前全局设置", "job_count": 12}],
            "recent_source_runs": [],
            "capture_defaults": {"city": "贵阳", "keyword": "运营", "limit": 45, "rounds": 2},
        }
        fake_pipeline = SimpleNamespace(
            supplement_boss_session_and_recommend=lambda user_id, fetch_session_id, recommendation_limit=5, review_profile=None: {
                "ok": False,
                "fetch_session_id": fetch_session_id,
                "supplement": {
                    "ok": False,
                    "fetch_session_id": fetch_session_id,
                    "pending_job_count": 12,
                    "updated_count": 0,
                    "attempted_count": 3,
                    "success_count": 0,
                    "updated_jobs": [],
                    "results": [
                        {
                            "ok": False,
                            "title": "运营岗",
                            "quality_issues": ["boss_description_too_short"],
                        }
                    ],
                },
                "recommendation": {
                    "ok": False,
                    "fetch_session_id": fetch_session_id,
                    "matched_count": 0,
                    "items": [],
                    "message": "这轮还没有任何完整 JD",
                },
            },
            store=SimpleNamespace(
                get_settings=lambda user_id: UserSettings(user_id=user_id),
                list_recent_source_runs=lambda limit=8, source_names=None: [],
                load_jobs=lambda selected_source_names=None: [],
                get_active_resume=lambda user_id: ({}, ""),
                get_source_runs=lambda *args, **kwargs: [],
            ),
            resolve_selected_source_names=lambda groups: [],
            default_fetch_source_groups=lambda: [],
            load_active_jobs=lambda user_id, selected_source_names, fetch_session_id=None: [],
            list_review_profiles=lambda user_id, selected_source_names=None, fetch_session_id=None: [],
        )
        fake_config = SimpleNamespace(
            data_dir=temp_root,
            debug_dir=temp_root,
            project_root=Path.cwd(),
        )

        with patch("src.resume_bot.local_web.load_config", return_value=fake_config), patch(
            "src.resume_bot.local_web.ResumeBotPipeline",
            return_value=fake_pipeline,
        ), patch(
            "src.resume_bot.local_web._build_boss_workbench_summary",
            return_value=summary,
        ):
            client = TestClient(create_app())
            response = client.post(
                "/api/boss/workbench/supplement",
                json={
                    "user_id": "me",
                    "fetch_session_id": "boss-session-2",
                    "limit": 3,
                    "review_limit": 12,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("质量判定未通过", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
