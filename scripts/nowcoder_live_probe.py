#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from resume_bot.config import load_config
from resume_bot.job_sources.base import SourceHaltError
from resume_bot.job_sources.nowcoder_direct import NowcoderDirectSource
from resume_bot.pipeline import ResumeBotPipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Low-traffic live probe for nowcoder direct fetching.")
    parser.add_argument("--user-id", default="me")
    parser.add_argument("--max-seed-pages", type=int, default=2)
    parser.add_argument("--max-detail-pages", type=int, default=6)
    parser.add_argument("--throttle-seconds", type=float, default=1.8)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    config = load_config()
    pipeline = ResumeBotPipeline(config)
    profile, _ = pipeline.store.get_active_resume(args.user_id)
    settings = pipeline.store.get_settings(args.user_id)
    source = NowcoderDirectSource(
        "nowcoder_live_probe",
        max_seed_pages=args.max_seed_pages,
        max_detail_pages=args.max_detail_pages,
        throttle_seconds=args.throttle_seconds,
        debug_dir=config.debug_dir,
    )
    payload: dict = {
        "user_id": args.user_id,
        "max_seed_pages": args.max_seed_pages,
        "max_detail_pages": args.max_detail_pages,
        "throttle_seconds": args.throttle_seconds,
    }
    try:
        jobs = source.fetch_jobs(settings, profile)
        payload["ok"] = True
        payload["count"] = len(jobs)
        payload["jobs"] = [
            {
                "title": job.title,
                "company_name": job.company_name,
                "city": job.city,
                "url": job.url,
            }
            for job in jobs[:10]
        ]
    except SourceHaltError as exc:
        payload["ok"] = False
        payload["halted"] = True
        payload["error"] = str(exc)
        payload["detail"] = exc.detail
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
    latest_path = config.debug_dir / f"latest-nowcoder-live-probe-{args.user_id}.json"
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
