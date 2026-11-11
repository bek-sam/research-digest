"""Improvement #3: star-quality check from public engagement ratios.

GitHub closed the stargazer listing to non-maintainers in July 2026 and GH Archive now carries only a small
sample of star events, so account-level detection (StarScout, Dagster) is no longer possible from outside.
What remains visible is whether stars come with the activity real interest produces: forks, issues and PRs.
Calibrated on StarScout's surviving fake-star repos vs organic agent/LLM repos (tests/fixtures/star_calibration.json):
organic repos have forks >= 4% of stars and >= 7 issues+PRs per 1k stars; fakes show ~0-2% forks with no
issues, or more forks than stars (automated forks)."""
from __future__ import annotations

import datetime as dt

from . import util
from .sources import github


def assess(stars: int, forks: int, issues_prs: int | None, created: str, stars_per_day: float | None = None,
           owner_created: str | None = None, today: dt.date | None = None) -> dict | None:
    """Suspicion score 0-100 with reasons. None when there are too few stars to judge."""
    if stars < 100:
        return None
    today = today or util.now().date()
    age = max((today - dt.date.fromisoformat(created[:10])).days, 0)
    fr = forks / stars
    score, reasons = 0, []
    if fr < 0.01:
        score += 40
        reasons.append(f"forks only {fr:.1%} of stars (organic repos: 4-20%)")
    elif fr < 0.025:
        score += 25
        reasons.append(f"few forks ({fr:.1%} of stars)")
    if fr > 0.8:
        score += 50
        reasons.append(f"more forks than stars ({fr:.0%}) - forks look automated")
    if issues_prs is not None and age >= 14:
        ipk = issues_prs * 1000 / stars
        if ipk < 1:
            score += 30
            reasons.append("no issues or PRs despite the stars")
        elif ipk < 4:
            score += 15
            reasons.append(f"very few issues/PRs ({ipk:.1f} per 1k stars)")
    if age < 14 and (stars_per_day or 0) > 200 and fr < 0.03:
        score += 20
        reasons.append(f"burst of {stars_per_day:.0f} stars/day with almost no forks")
    if owner_created and stars > 300:
        owner_age = (today - dt.date.fromisoformat(owner_created[:10])).days
        if owner_age < 30:
            score += 15
            reasons.append(f"owner account is {owner_age} days old")
    score = min(score, 100)
    level = "suspicious" if score >= 50 else "watch" if score >= 30 else "ok"
    return {"level": level, "score": score, "reasons": reasons}


def check(repo: dict) -> dict | None:
    """Live check for one repo item (2 core API calls + 1 search call)."""
    name = repo["title"]
    h = github.headers()
    try:
        meta = util.get_json(f"https://api.github.com/repos/{name}", h)
        owner = util.get_json(meta["owner"]["url"], h)
    except Exception as e:
        util.log(f"star check {name}: {e}")
        return None
    try:
        issues = util.get_json(f"https://api.github.com/search/issues?q=repo:{meta['full_name']}&per_page=1", h)["total_count"]
    except Exception:
        issues = None
    return assess(meta["stargazers_count"], meta["forks_count"], issues, meta["created_at"],
                  repo.get("stars_per_day"), owner.get("created_at"))


def check_all(repos: list[dict], pause: float = 2.1) -> int:
    """Annotate repos (in place) with `star_check`; returns how many are suspicious."""
    import time
    flagged = 0
    for r in repos:
        if r.get("stars", 0) < 100:
            continue
        res = check(r)
        if res:
            r["star_check"] = res
            flagged += res["level"] == "suspicious"
        time.sleep(pause)  # issue search shares the 30/min search budget
    util.log(f"star check: {flagged}/{len(repos)} suspicious")
    return flagged
