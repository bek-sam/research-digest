"""Improvement #4: link papers and repos so one project shows up once, with both kinds of signal."""
from __future__ import annotations

import re

GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/|arxiv[:\s]\s*)(\d{4}\.\d{4,5})", re.I)
NOT_REPOS = {"orgs", "sponsors", "topics", "features", "marketplace", "settings", "about"}


def norm_repo(url: str | None) -> str | None:
    """'https://github.com/Foo/Bar.git#readme' -> 'foo/bar'."""
    if not url:
        return None
    m = GH_RE.search(url)
    if not m or m.group(1).lower() in NOT_REPOS:
        return None
    owner, name = m.group(1), re.sub(r"\.git$", "", m.group(2).rstrip(".,);:"))
    return f"{owner}/{name}".lower() if name else None


def paper_repos(p: dict) -> list[str]:
    found = [norm_repo(p.get("code_url"))]
    for field in ("abstract", "comment"):
        found += [norm_repo(m.group(0)) for m in GH_RE.finditer(p.get(field) or "")]
    return list(dict.fromkeys(r for r in found if r))


def repo_papers(r: dict) -> list[str]:
    text = f"{r.get('description') or ''} {r.get('homepage') or ''}"
    return list(dict.fromkeys(ARXIV_RE.findall(text)))


def link(pools: dict[str, list[dict]]) -> int:
    """Attach repo signals to papers and drop the duplicate repo items. Returns number of merges."""
    papers = {p["arxiv_id"]: p for p in pools.get("paper", []) if p.get("arxiv_id")}
    repos = {r["title"].lower(): r for r in pools.get("repo", [])}
    by_repo: dict[str, dict] = {}
    for p in papers.values():
        for name in paper_repos(p):
            by_repo.setdefault(name, p)
    for name, r in repos.items():
        for aid in repo_papers(r):
            if aid in papers:
                by_repo.setdefault(name, papers[aid])
    merged = set()
    for name, p in by_repo.items():
        p.setdefault("code_repo", name)
        p["has_code"] = True
        p.setdefault("code_url", f"https://github.com/{name}")
        if name == p["code_repo"] and f"gh:{name}" not in p.setdefault("linked_ids", []):
            p["linked_ids"].append(f"gh:{name}")
        r = repos.get(name)
        if r and name == p["code_repo"]:
            p["code_stars"] = r["stars"]
            if r.get("stars_per_day") is not None:
                p["code_stars_per_day"] = r["stars_per_day"]
            merged.add(name)
    if merged:
        pools["repo"] = [r for r in pools["repo"] if r["title"].lower() not in merged]
    return len(merged)


# ---------------------------------------------------------------- discussions (#5)

def norm_url(u: str | None) -> str:
    """Comparable form of a link: no scheme, www, query, fragment or trailing slash."""
    if not u:
        return ""
    u = re.sub(r"^https?://(www\.|m\.|old\.)?", "", u.strip().lower())
    u = re.sub(r"[?#].*$", "", u).rstrip("/")
    return u


def heat(n: dict) -> float:
    """Comparable popularity for a discussion across sources."""
    if n.get("source") == "Lobsters":
        return n.get("points", 0) * 4
    if n.get("source", "").startswith("r/"):
        return max(0, 31 - n.get("rank", 30)) * 15
    return n.get("points", 0)


def describe(n: dict) -> dict:
    d = {"source": n.get("source", "HN"), "url": n.get("discussion_url") or n.get("hn_url") or n["url"]}
    if n.get("source", "").startswith("r/"):
        d["rank"] = n.get("rank")
    else:
        d["points"] = n.get("points", 0)
        d["comments"] = n.get("comments", 0)
    return d


def merge_news(news: list[dict]) -> list[dict]:
    """One item per linked URL; the hottest copy leads, the others become `also_on`."""
    groups: dict[str, list[dict]] = {}
    for n in news:
        key = norm_url(n["url"]) or n["id"]
        groups.setdefault(key, []).append(n)
    out = []
    for g in groups.values():
        g.sort(key=heat, reverse=True)
        lead = g[0]
        lead["heat"] = sum(heat(x) for x in g)
        if len(g) > 1:
            lead["also_on"] = [describe(x) for x in g[1:]]
            lead.setdefault("linked_ids", []).extend(x["id"] for x in g[1:])
        out.append(lead)
    return out


def attach_discussions(pools: dict[str, list[dict]]) -> int:
    """A discussion about a paper or repo we already have becomes a signal on that item, not a separate entry."""
    papers = {p["arxiv_id"]: p for p in pools.get("paper", []) if p.get("arxiv_id")}
    repos = {r["title"].lower(): r for r in pools.get("repo", [])}
    for p in pools.get("paper", []):
        if p.get("code_repo"):
            repos.setdefault(p["code_repo"], p)  # discussion of a paper's code counts for the paper
    keep, n_attached = [], 0
    for n in pools.get("news", []):
        target = None
        m = ARXIV_RE.search(n["url"])
        if m and m.group(1) in papers:
            target = papers[m.group(1)]
        else:
            r = norm_repo(n["url"]) if "github.com" in n["url"] else None
            target = repos.get(r) if r else None
        if target is None:
            keep.append(n)
            continue
        target.setdefault("discussed", []).append(describe(n))
        target.setdefault("discussed", []).extend(n.get("also_on", []))
        target["heat"] = target.get("heat", 0) + n.get("heat", heat(n))
        target.setdefault("linked_ids", []).extend([n["id"], *n.get("linked_ids", [])])
        n_attached += 1
    pools["news"] = keep
    return n_attached


def merge_repos(search: list[dict], trending: list[dict]) -> list[dict]:
    """Trending-page repos join the search results; a repo in both keeps search metadata + today's stars."""
    by_id = {r["id"]: r for r in search}
    for t in trending:
        r = by_id.get(t["id"])
        if r:
            r["stars_today"] = t["stars_today"]
            r["stars_per_day"] = max(r.get("stars_per_day") or 0, t["stars_per_day"])
            r["bucket"] = r["bucket"] + "+trending"
        else:
            by_id[t["id"]] = t
    return list(by_id.values())
