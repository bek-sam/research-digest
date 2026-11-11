"""Hugging Face Daily Papers: community upvotes, merged into the arXiv set."""
from __future__ import annotations

import datetime as dt

from .. import util


def merge(papers: dict[str, dict], items: list[dict], day: str) -> int:
    added = 0
    for it in items:
        p = it.get("paper", {})
        aid = p.get("id")
        if not aid:
            continue
        up = p.get("upvotes", 0)
        repo = p.get("githubRepo")
        extra = {k: v for k, v in {
            "org": (p.get("organization") or {}).get("fullname") or (p.get("organization") or {}).get("name"),
            "hf_comments": it.get("numComments") or 0,
            "keywords": (p.get("ai_keywords") or [])[:8],
        }.items() if v}
        if repo and p.get("githubStars") is not None:
            extra["code_stars"] = p["githubStars"]
        if aid in papers:
            papers[aid]["hf_upvotes"] = max(papers[aid]["hf_upvotes"], up)
            papers[aid].update(extra)
            if repo:
                papers[aid]["has_code"] = True
                papers[aid]["code_url"] = repo
        else:
            papers[aid] = {
                "id": f"arxiv:{aid}", "kind": "paper", "arxiv_id": aid,
                "title": " ".join((p.get("title") or it.get("title", "")).split()),
                "url": f"https://arxiv.org/abs/{aid}",
                "abstract": " ".join((p.get("summary") or "").split())[:1500],
                "authors": [a.get("name", "") for a in p.get("authors", [])][:8],
                "n_authors": len(p.get("authors", [])),
                "category": "hf-daily", "published": day,
                "has_code": bool(repo), "code_url": repo,
                "comment": "", "hf_upvotes": up, **extra,
            }
            added += 1
    return added


def fetch(papers: dict[str, dict], days: int = 3) -> None:
    added = 0
    for d in range(days):
        day = (util.now() - dt.timedelta(days=d)).date().isoformat()
        try:
            items = util.get_json(f"https://huggingface.co/api/daily_papers?date={day}")
        except Exception as e:
            util.log(f"hf {day}: {e}")
            continue
        added += merge(papers, items, day)
    util.log(f"hf daily papers: merged, {added} not in arXiv query")
