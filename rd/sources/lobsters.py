"""Lobste.rs tag feeds (JSON)."""
from __future__ import annotations

import datetime as dt

from .. import util


def to_item(s: dict) -> dict:
    return {
        "id": f"lobsters:{s['short_id']}", "kind": "news", "source": "Lobsters",
        "title": s["title"], "url": s.get("url") or s["short_id_url"], "discussion_url": s["comments_url"],
        "points": s.get("score", 0), "comments": s.get("comment_count", 0),
        "description": (s.get("description_plain") or "")[:400],
    }


def fetch(cfg) -> list[dict]:
    cutoff = util.now() - dt.timedelta(days=cfg.get("days", 2))
    seen = {}
    for tag in cfg["tags"]:
        try:
            stories = util.get_json(f"https://lobste.rs/t/{tag}.json")
        except Exception as e:
            util.log(f"lobsters {tag}: {e}")
            continue
        for s in stories:
            if util.parse_ts(s["created_at"]) >= cutoff and s.get("score", 0) >= cfg.get("min_score", 10):
                seen.setdefault(s["short_id"], to_item(s))
    util.log(f"lobsters: {len(seen)} stories")
    return list(seen.values())
