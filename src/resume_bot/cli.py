from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .pipeline import ResumeBotPipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resume bot CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")

    ingest = subparsers.add_parser("ingest-resume")
    ingest.add_argument("--user-id", default=None)
    ingest.add_argument("--file", required=True)

    show_profile = subparsers.add_parser("show-profile")
    show_profile.add_argument("--user-id", default=None)

    show_settings = subparsers.add_parser("show-settings")
    show_settings.add_argument("--user-id", default=None)

    update_preferences = subparsers.add_parser("update-preferences")
    update_preferences.add_argument("--user-id", default=None)
    update_preferences.add_argument("--text", required=True)

    add_company = subparsers.add_parser("add-company-watch")
    add_company.add_argument("--user-id", default=None)
    add_company.add_argument("--name", required=True)
    add_company.add_argument("--careers-url", default="")
    add_company.add_argument("--domain", default="")
    add_company.add_argument("--stage", default="")

    remove_company = subparsers.add_parser("remove-company-watch")
    remove_company.add_argument("--user-id", default=None)
    remove_company.add_argument("--name", required=True)

    bind = subparsers.add_parser("bind-feishu-user")
    bind.add_argument("--user-id", default=None)
    bind.add_argument("--receive-id", required=True)
    bind.add_argument("--receive-id-type", default="open_id")

    fetch_jobs = subparsers.add_parser("fetch-jobs")
    fetch_jobs.add_argument("--user-id", default=None)

    import_boss_queue = subparsers.add_parser("import-boss-queue")
    import_boss_queue.add_argument("--user-id", default=None)
    import_boss_queue.add_argument("--file", required=True)

    supplement_boss_details = subparsers.add_parser("supplement-boss-details")
    supplement_boss_details.add_argument("--user-id", default=None)
    supplement_boss_details.add_argument("--fetch-session-id", required=True)
    supplement_boss_details.add_argument("--limit", type=int, default=3)

    rank_jobs = subparsers.add_parser("rank-jobs")
    rank_jobs.add_argument("--user-id", default=None)
    rank_jobs.add_argument("--fetch-session-id", default="")
    rank_jobs.add_argument("--source", action="append", default=None)
    rank_jobs.add_argument("--review-profile", default="")

    review_session = subparsers.add_parser("review-session")
    review_session.add_argument("--user-id", default=None)
    review_session.add_argument("--fetch-session-id", required=True)
    review_session.add_argument("--source", action="append", default=None)
    review_session.add_argument("--limit", type=int, default=30)
    review_session.add_argument("--review-profile", default="")

    list_review_profiles = subparsers.add_parser("list-review-profiles")
    list_review_profiles.add_argument("--user-id", default=None)
    list_review_profiles.add_argument("--fetch-session-id", default="")
    list_review_profiles.add_argument("--source", action="append", default=None)

    build_digest = subparsers.add_parser("build-digest")
    build_digest.add_argument("--user-id", default=None)
    build_digest.add_argument("--include-history", action="store_true")
    build_digest.add_argument("--history-limit", type=int, default=None)
    build_digest.add_argument("--history-only", action="store_true")

    send_digest = subparsers.add_parser("send-digest")
    send_digest.add_argument("--user-id", default=None)
    send_digest.add_argument("--include-history", action="store_true")
    send_digest.add_argument("--history-limit", type=int, default=None)
    send_digest.add_argument("--history-only", action="store_true")

    run_daily = subparsers.add_parser("run-daily")
    run_daily.add_argument("--user-id", default=None)
    run_daily.add_argument("--dry-run", action="store_true")
    run_daily.add_argument("--include-history", action="store_true")
    run_daily.add_argument("--history-limit", type=int, default=None)
    run_daily.add_argument("--history-only", action="store_true")

    run_scheduler = subparsers.add_parser("run-scheduler")
    run_scheduler.add_argument("--user-id", default=None)
    run_scheduler.add_argument("--grace-minutes", type=int, default=15)
    run_scheduler.add_argument("--force", action="store_true")

    mark_job = subparsers.add_parser("mark-job")
    mark_job.add_argument("--user-id", default=None)
    mark_job.add_argument("--job-fingerprint", required=True)
    mark_job.add_argument("--action", required=True, choices=["applied", "disliked", "saved"])
    mark_job.add_argument("--notes", default="")

    list_sent = subparsers.add_parser("list-sent-jobs")
    list_sent.add_argument("--user-id", default=None)
    list_sent.add_argument("--limit", type=int, default=20)
    list_sent.add_argument("--keyword", default="")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    user_id = getattr(args, "user_id", None) or config.default_user_id

    if args.command == "init-db":
        print(f"Database ready: {config.db_path}")
        return 0
    if args.command == "ingest-resume":
        result = pipeline.ingest_resume(user_id, Path(args.file))
        print(result["profile_summary"])
        print()
        print("提取诊断：")
        print(
            json.dumps(
                {
                    "file_name": result["extraction"]["file_name"],
                    "file_type": result["extraction"]["file_type"],
                    "extraction_method": result["extraction"]["extraction_method"],
                    "parser_backend": result["extraction"]["parser_backend"],
                    "quality_score": result["extraction"]["quality_score"],
                    "quality_flags": result["extraction"]["quality_flags"],
                    "fallback_used": result["extraction"]["fallback_used"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print()
        print("当前设置：")
        print(result["settings_summary"])
        print()
        print("调试报告：")
        print(json.dumps(result["debug_report"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "show-profile":
        print(pipeline.show_profile(user_id))
        return 0
    if args.command == "show-settings":
        print(pipeline.show_settings(user_id))
        return 0
    if args.command == "update-preferences":
        result = pipeline.update_preferences(user_id, args.text)
        print("已应用 patch：")
        print(json.dumps(result["patch"], ensure_ascii=False, indent=2))
        print()
        print(result["summary"])
        print()
        print("调试报告：")
        print(json.dumps(result["debug_report"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "add-company-watch":
        print(
            pipeline.add_company_watch(
                user_id,
                name=args.name,
                careers_url=args.careers_url,
                domain=args.domain,
                stage=args.stage,
            )
        )
        return 0
    if args.command == "remove-company-watch":
        print(pipeline.remove_company_watch(user_id, name=args.name))
        return 0
    if args.command == "bind-feishu-user":
        print(pipeline.bind_feishu_user(user_id, args.receive_id, args.receive_id_type))
        return 0
    if args.command == "fetch-jobs":
        print(json.dumps(pipeline.fetch_jobs(user_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "import-boss-queue":
        print(json.dumps(pipeline.import_boss_queue_artifact(user_id, Path(args.file)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "supplement-boss-details":
        print(
            json.dumps(
                pipeline.supplement_boss_details(
                    user_id,
                    args.fetch_session_id,
                    limit=args.limit,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "rank-jobs":
        results = pipeline.rank_jobs(
            user_id,
            fetch_session_id=(args.fetch_session_id or "").strip() or None,
            selected_source_names=args.source,
            review_profile=(args.review_profile or "").strip() or None,
        )
        print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
        return 0
    if args.command == "review-session":
        print(
            json.dumps(
                pipeline.review_fetch_session(
                    user_id,
                    args.fetch_session_id,
                    selected_source_names=args.source,
                    limit=args.limit,
                    review_profile=(args.review_profile or "").strip() or None,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "list-review-profiles":
        print(
            json.dumps(
                pipeline.list_review_profiles(
                    user_id,
                    fetch_session_id=(args.fetch_session_id or "").strip() or None,
                    selected_source_names=args.source,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "build-digest":
        bundle = pipeline.build_digest(
            user_id,
            include_history=True if args.include_history else None,
            history_limit=args.history_limit,
            history_only=args.history_only,
        )
        print(
            json.dumps(
                {
                    "count": len(bundle.all_items()),
                    "new_count": len(bundle.new_items),
                    "history_count": len(bundle.history_items),
                    "empty_reason": bundle.empty_reason,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "send-digest":
        bundle = pipeline.build_digest(
            user_id,
            include_history=True if args.include_history else None,
            history_limit=args.history_limit,
            history_only=args.history_only,
        )
        print(json.dumps(pipeline.send_digest(user_id, bundle), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-daily":
        print(
            json.dumps(
                pipeline.run_daily(
                    user_id,
                    dry_run=args.dry_run,
                    include_history=True if args.include_history else None,
                    history_limit=args.history_limit,
                    history_only=args.history_only,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "run-scheduler":
        print(
            json.dumps(
                pipeline.run_scheduler(user_id, grace_minutes=args.grace_minutes, force=args.force),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "mark-job":
        pipeline.mark_job(user_id, args.job_fingerprint, args.action, notes=args.notes)
        print("ok")
        return 0
    if args.command == "list-sent-jobs":
        result = pipeline.list_sent_jobs(user_id, limit=args.limit, keyword=args.keyword)
        print(result["text"])
        print()
        print("调试报告：")
        print(json.dumps(result["debug_report"], ensure_ascii=False, indent=2))
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2
