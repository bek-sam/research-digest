"""Improvement #4: paper <-> repo linking."""
import json

from conftest import FIX
from rd import link, pipeline, render
from rd.sources import hf


def paper(aid, **kw):
    p = {"id": f"arxiv:{aid}", "kind": "paper", "arxiv_id": aid, "title": f"P {aid}", "abstract": "",
         "comment": "", "has_code": False, "hf_upvotes": 0, "n_authors": 2, "url": "u"}
    p.update(kw)
    return p


def repo(name, **kw):
    r = {"id": f"gh:{name.lower()}", "kind": "repo", "title": name, "description": "", "homepage": "",
         "stars": 500, "stars_per_day": 50.0, "url": f"https://github.com/{name}"}
    r.update(kw)
    return r


def test_norm_repo_variants():
    assert link.norm_repo("https://github.com/Foo/Bar.git") == "foo/bar"
    assert link.norm_repo("http://github.com/Foo/Bar/tree/main/src") == "foo/bar"
    assert link.norm_repo("see github.com/a-b/c.d.") == "a-b/c.d"
    assert link.norm_repo("https://github.com/orgs/x") is None
    assert link.norm_repo(None) is None and link.norm_repo("https://gitlab.com/a/b") is None


def test_paper_repos_from_all_fields():
    p = paper("1", code_url="https://github.com/A/One", abstract="Code: https://github.com/a/one and github.com/B/Two.",
              comment="also https://github.com/c/three)")
    assert link.paper_repos(p) == ["a/one", "b/two", "c/three"]


def test_repo_papers_from_description():
    r = repo("x/y", description="Official code for arXiv:2611.12345", homepage="https://arxiv.org/abs/2610.00001v2")
    assert link.repo_papers(r) == ["2611.12345", "2610.00001"]


def test_merge_removes_duplicate_and_attaches_stats():
    pools = {"paper": [paper("1", code_url="https://github.com/Lab/Tool")], "repo": [repo("Lab/Tool"), repo("Other/R")]}
    assert link.link(pools) == 1
    assert [r["title"] for r in pools["repo"]] == ["Other/R"]
    p = pools["paper"][0]
    assert p["code_stars"] == 500 and p["code_stars_per_day"] == 50.0 and "gh:lab/tool" in p["linked_ids"]


def test_merge_via_repo_description():
    pools = {"paper": [paper("2611.12345")], "repo": [repo("x/y", description="Code for arXiv 2611.12345")]}
    link.link(pools)
    assert pools["repo"] == [] and pools["paper"][0]["has_code"] and pools["paper"][0]["code_repo"] == "x/y"


def test_no_link_leaves_pools_unchanged():
    pools = {"paper": [paper("1")], "repo": [repo("a/b")]}
    assert link.link(pools) == 0 and len(pools["repo"]) == 1 and "linked_ids" not in pools["paper"][0]


def test_sent_paper_blocks_its_repo_later(store):
    p = paper("1", code_url="https://github.com/Lab/Tool")
    link.link({"paper": [p], "repo": []})
    store.mark_sent(p, "t")
    assert store.already_sent("gh:lab/tool")


def test_hf_extra_signals_from_fixture():
    papers = {}
    items = json.loads((FIX / "hf_daily.json").read_text())
    hf.merge(papers, items, "2026-11-09")
    with_code = [p for p in papers.values() if p.get("code_url")]
    assert with_code and all("code_stars" in p for p in with_code)
    assert any(p.get("keywords") for p in papers.values())


def test_render_shows_code_stars_and_org():
    it = paper("1", code_url="https://github.com/a/b", code_stars=1234, code_stars_per_day=12.5, org="Google")
    s = {"relevance": 8, "quality": 7, "summary": "s", "verdict": "v", "red_flags": ""}
    out = render.item_html(1, it, s)
    assert "code</a> ⭐1,234 +12.5/day" in out and "Google" in out


def test_pipeline_links_before_triage(store, web, tg, claude, cfg, monkeypatch):
    # make the HF fixture's first repo show up in GitHub search results
    hfd = json.loads((FIX / "hf_daily.json").read_text())
    first = next(x["paper"] for x in hfd if x["paper"].get("githubRepo"))
    name = link.norm_repo(first["githubRepo"])
    gh = json.loads((FIX / "github_search.json").read_text())
    clone = dict(gh["items"][0], full_name=name, html_url=f"https://github.com/{name}", stargazers_count=4321)
    gh["items"].insert(0, clone)
    web.add("api.github.com/search/repositories", gh)
    cfg["github"]["topics"] = ["mcp"]
    pools = pipeline.collect(cfg, store)
    assert not any(r["title"].lower() == name for r in pools["repo"]), "duplicate repo removed"
    p = next(p for p in pools["paper"] if p["arxiv_id"] == first["id"])
    assert p["code_stars"] == 4321
