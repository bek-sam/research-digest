"""GitHub Search: new repos per topic + established repos with fresh pushes; tracks star growth."""
from __future__ import annotations

import datetime as dt
import os
import time
import urllib.error
import urllib.parse

from .. import util


def headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return h


def queries(cfg) -> list[tuple[str, str]]:
    n = util.now()
    since_new = (n - dt.timedelta(days=cfg["new_repo_days"])).date().isoformat()
    since_push = (n - dt.timedelta(days=2)).date().isoformat()
    out = []
    for t in cfg["topics"]:
        out.append(("new", f"topic:{t} created:>={since_new} stars:>={cfg['new_repo_min_stars']}"))
        out.append(("established", f"topic:{t} pushed:>={since_push} stars:>={cfg['established_min_stars']}"))
    return out


def to_item(r: dict, bucket: str) -> dict:
    name = r["full_name"]
    return {
        "id": f"gh:{name.lower()}", "kind": "repo", "bucket": bucket,
        "title": name, "url": r["html_url"],
        "description": (r.get("description") or "")[:400],
        "homepage": r.get("homepage") or "",
        "stars": r["stargazers_count"], "forks": r["forks_count"],
        "open_issues": r["open_issues_count"], "language": r.get("language"),
        "license": (r.get("license") or {}).get("spdx_id"),
        "topics": r.get("topics", [])[:10],
        "created": r["created_at"][:10], "pushed": r["pushed_at"][:10],
        "archived": r.get("archived", False), "fork": r.get("fork", False),
    }


def add_growth(store, repos: list[dict]) -> None:
    """Record today's star count; stars_per_day from the previous snapshot (or lifetime avg for new repos)."""
    today = util.now().date()
    for r in repos:
        store.execute("INSERT OR REPLACE INTO stars VALUES (?,?,?)", (r["title"], today.isoformat(), r["stars"]))
        prev = store.execute(
            "SELECT day, stars FROM stars WHERE repo=? AND day<? ORDER BY day DESC LIMIT 1",
            (r["title"], today.isoformat())).fetchone()
        if prev:
            days = max((today - dt.date.fromisoformat(prev[0])).days, 1)
            r["stars_per_day"] = round((r["stars"] - prev[1]) / days, 1)
        else:
            age = max((today - dt.date.fromisoformat(r["created"])).days, 1)
            r["stars_per_day"] = round(r["stars"] / age, 1) if r["bucket"] == "new" else None
    store.commit()


def fetch(cfg, store) -> list[dict]:
    h = headers()
    pause = 2.2 if "Authorization" in h else 7  # search API: 30/min authed, 10/min anonymous
    repos: dict[str, dict] = {}
    for bucket, q in queries(cfg):
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 50})
        try:
            data = util.get_json(url, h)
        except urllib.error.HTTPError as e:
            util.log(f"github search failed ({e.code}) for {q}")
            time.sleep(pause * 3)
            continue
        for r in data.get("items", []):
            repos.setdefault(r["full_name"], to_item(r, bucket))
        time.sleep(pause)
    out = [r for r in repos.values() if not r["archived"] and not r["fork"]]
    add_growth(store, out)
    util.log(f"github: {len(out)} repos ({sum(r['bucket'] == 'new' for r in out)} new)")
    return out
