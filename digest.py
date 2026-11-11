#!/usr/bin/env python3
"""Daily research digest: arXiv + Hugging Face papers + GitHub + Hacker News,
screened by Claude (local `claude -p`, Claude subscription) against your interests, sent to Telegram.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional GITHUB_TOKEN, optional CLAUDE_BIN.
Usage: digest.py [--dry-run] [--config config.toml] [--db state.db]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rd import config, pipeline, util
from rd.store import Store

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--db", default=str(HERE / "state.db"))
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending; don't mark sent")
    ap.add_argument("--weekly", action="store_true", help="only build and send the weekly trend report now")
    args = ap.parse_args()
    cfg = config.load(args.config)
    store = Store(args.db)
    try:
        if args.weekly:
            from rd import profile, weekly
            cfg["profile"] = profile.effective(cfg["profile"])
            weekly.run(store, cfg, pipeline.Output(args.dry_run).send)
            return
        pipeline.run(cfg, store, dry=args.dry_run)
    except Exception as e:
        util.log(f"FAILED: {e!r}")
        if not args.dry_run:
            pipeline.notify_failure(e)
        raise


if __name__ == "__main__":
    main()
