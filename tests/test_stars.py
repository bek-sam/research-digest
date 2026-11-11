"""Improvement #3: star-quality check, calibrated on StarScout positives vs organic repos."""
import datetime as dt
import json

from conftest import FIX
from rd import pipeline, render, stars

CAL = json.loads((FIX / "star_calibration.json").read_text())
TODAY = dt.date(2026, 11, 10)


def run(x):
    return stars.assess(x["stars"], x["forks"], x["issues_prs"], x["created"], None, x["owner_created"], TODAY)


def test_no_false_alarms_on_organic_and_new_repos():
    organic = {n: run(x) for n, x in CAL.items() if x["label"] in ("real", "new")}
    assert len(organic) >= 15
    alarms = {n: r for n, r in organic.items() if r and r["level"] != "ok"}
    assert alarms == {}, alarms


def test_flags_most_known_fake_star_repos():
    fakes = [run(x) for x in CAL.values() if x["label"] == "fake"]
    judged = [r for r in fakes if r]
    flagged = [r for r in judged if r["level"] != "ok"]
    assert len(judged) >= 40 and len(flagged) / len(judged) >= 0.6, f"{len(flagged)}/{len(judged)}"


def test_both_fake_patterns_caught():
    lzs = run(CAL["m-boutaleb/LZS"])            # silent stars: no forks, no issues
    laq = run(CAL["LaqiraProtocol/LaqiraToken"])  # automated forks
    assert lzs["level"] == "suspicious" and any("forks only" in r for r in lzs["reasons"])
    assert laq["level"] == "suspicious" and any("more forks than stars" in r for r in laq["reasons"])


def test_young_repo_not_punished_for_missing_issues():
    r = stars.assess(500, 40, 0, "2026-11-08", 250, "2015-01-01", TODAY)
    assert r["level"] == "ok"


def test_burst_without_forks_and_new_owner():
    r = stars.assess(3000, 10, 0, "2026-11-05", 600, "2026-11-01", TODAY)
    assert r["level"] == "suspicious" and any("burst" in x for x in r["reasons"]) and any("owner" in x for x in r["reasons"])


def test_too_few_stars_not_judged():
    assert stars.assess(50, 0, 0, "2020-01-01", today=TODAY) is None


def test_check_uses_api(web):
    web.add("api.github.com/repos/a/b", {"stargazers_count": 1000, "forks_count": 2, "created_at": "2024-01-01T00:00:00Z",
                                         "owner": {"url": "https://api.github.com/users/a"}, "full_name": "a/b"})
    web.add("api.github.com/users/a", {"created_at": "2010-01-01T00:00:00Z"})
    web.add("search/issues", {"total_count": 0})
    r = stars.check({"title": "a/b", "stars_per_day": 5})
    assert r["level"] == "suspicious"


def test_check_failure_returns_none(web):
    assert stars.check({"title": "gone/repo"}) is None


def test_penalty_caps_quality_and_render_flag():
    it = {"id": "gh:a/b", "kind": "repo", "title": "a/b", "url": "u", "stars": 1000, "bucket": "established",
          "star_check": {"level": "suspicious", "score": 70, "reasons": ["forks only 0.2% of stars"]}}
    scores = {"gh:a/b": {"relevance": 9, "quality": 9, "summary": "s", "verdict": "v", "red_flags": ""}}
    pipeline.apply_star_penalty(scores, {"gh:a/b": it})
    assert scores["gh:a/b"]["quality"] == 4
    out = render.item_html(1, it, scores["gh:a/b"])
    assert "🚩 Suspicious stars: forks only 0.2% of stars" in out


def test_watch_level_renders_but_no_cap():
    it = {"id": "gh:a/b", "kind": "repo", "title": "a/b", "url": "u", "stars": 1000, "bucket": "established",
          "star_check": {"level": "watch", "score": 30, "reasons": ["few forks"]}}
    scores = {"gh:a/b": {"relevance": 9, "quality": 8, "summary": "s", "verdict": "v", "red_flags": ""}}
    pipeline.apply_star_penalty(scores, {"gh:a/b": it})
    assert scores["gh:a/b"]["quality"] == 8 and "👀" in render.item_html(1, it, scores["gh:a/b"])
