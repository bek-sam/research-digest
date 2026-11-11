"""Hacker News via Algolia: last-24h stories matching keywords, above a points floor."""
from __future__ import annotations

import datetime as dt
import urllib.parse

from .. import util


def to_item(h: dict) -> dict:
    oid = h["objectID"]
    return {
        "id": f"hn:{oid}", "kind": "news", "source": "HN", "title": h.get("title", ""),
        "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
        "hn_url": f"https://news.ycombinator.com/item?id={oid}",
        "discussion_url": f"https://news.ycombinator.com/item?id={oid}",
        "points": h.get("points", 0), "comments": h.get("num_comments", 0),
    }


def fetch(cfg) -> list[dict]:
    since = int((util.now() - dt.timedelta(days=1)).timestamp())
    seen: dict[str, dict] = {}
    for q in cfg["queries"]:
        url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({
            "query": q, "tags": "story",
            "numericFilters": f"created_at_i>{since},points>={cfg['min_points']}",
            "hitsPerPage": 30})
        try:
            hits = util.get_json(url)["hits"]
        except Exception as e:
            util.log(f"hn {q}: {e}")
            continue
        for h in hits:
            seen.setdefault(h["objectID"], to_item(h))
    util.log(f"hn: {len(seen)} stories")
    return list(seen.values())
