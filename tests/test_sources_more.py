"""Improvement #5: Reddit, Lobsters, GitHub Trending, HF models + cross-source merging."""
import json

from conftest import FIX
from rd import link, pipeline, render
from rd.sources import ghtrending, hfmodels, lobsters, reddit


def test_reddit_rss_parse():
    items = reddit.parse((FIX / "reddit.rss").read_bytes(), "LocalLLaMA")
    assert len(items) >= 20
    assert [i["rank"] for i in items[:3]] == [1, 2, 3]
    assert all(i["id"].startswith("reddit:") and i["discussion_url"].startswith("https://www.reddit.com/") for i in items)
    assert all("<" not in i["description"] for i in items)


def test_reddit_outbound_link_preferred():
    plain, link_ = reddit.text_of('&lt;a href="https://github.com/a/b"&gt;x&lt;/a&gt; &lt;a href="https://www.reddit.com/r/x"&gt;')
    assert link_ == "https://github.com/a/b"


def test_lobsters_items(cfg):
    stories = json.loads((FIX / "lobsters.json").read_text())
    items = [lobsters.to_item(s) for s in stories]
    assert all(i["kind"] == "news" and i["source"] == "Lobsters" for i in items)


def test_lobsters_fetch(web, cfg):
    items = lobsters.fetch(cfg["lobsters"])
    assert all(i["points"] >= cfg["lobsters"]["min_score"] for i in items)


def test_github_trending_parse():
    repos = ghtrending.parse((FIX / "gh_trending.html").read_text())
    assert len(repos) >= 10
    r = repos[0]
    assert "/" in r["title"] and r["stars"] > 0 and r["stars_today"] > 0 and r["bucket"] == "trending"
    assert not any(x["title"].startswith(("apps/", "sponsors/", "login")) for x in repos)


def test_hf_models_filter(cfg):
    models = json.loads((FIX / "hf_models.json").read_text())
    items = hfmodels.filter_models(models, cfg["hf_models"])
    assert items and all(i["pipeline"] in cfg["hf_models"]["pipelines"] for i in items)
    assert all(i["likes"] >= cfg["hf_models"]["min_likes"] for i in items)


def test_merge_news_across_sources():
    a = {"id": "hn:1", "kind": "news", "source": "HN", "url": "https://Example.com/post/?utm=x", "points": 200,
         "comments": 50, "discussion_url": "hn1", "title": "t"}
    b = {"id": "reddit:z", "kind": "news", "source": "r/LocalLLaMA", "url": "http://www.example.com/post",
         "rank": 2, "discussion_url": "rd", "title": "t"}
    c = {"id": "hn:2", "kind": "news", "source": "HN", "url": "https://other.com", "points": 90, "title": "u"}
    out = link.merge_news([b, a, c])
    assert len(out) == 2
    lead = next(x for x in out if x["id"] == "reddit:z"), "rank 2 of the day (435) beats 200 HN points"
    lead = lead[0]
    assert lead["also_on"][0]["source"] == "HN" and "hn:1" in lead["linked_ids"]
    assert lead["heat"] == 200 + 29 * 15


def test_discussion_attaches_to_paper_and_repo():
    pools = {
        "paper": [{"id": "arxiv:2611.09113", "kind": "paper", "arxiv_id": "2611.09113", "title": "P", "code_repo": "lab/code"}],
        "repo": [{"id": "gh:a/b", "kind": "repo", "title": "A/B"}],
        "news": [{"id": "hn:1", "source": "HN", "url": "https://arxiv.org/abs/2611.09113", "points": 120, "comments": 9, "title": "x"},
                 {"id": "reddit:q", "source": "r/AI_Agents", "url": "https://github.com/a/b", "rank": 1, "discussion_url": "d", "title": "y"},
                 {"id": "hn:2", "source": "HN", "url": "https://huggingface.co/papers/2611.09113", "points": 10, "title": "z"},
                 {"id": "hn:3", "source": "HN", "url": "https://github.com/lab/code", "points": 30, "title": "w"},
                 {"id": "hn:4", "source": "HN", "url": "https://blog.example.com", "points": 300, "title": "keep"}],
    }
    assert link.attach_discussions(pools) == 4
    assert [n["id"] for n in pools["news"]] == ["hn:4"]
    paper = pools["paper"][0]
    assert len(paper["discussed"]) == 3 and {"hn:1", "hn:2", "hn:3"} <= set(paper["linked_ids"])
    assert pools["repo"][0]["discussed"][0]["rank"] == 1


def test_merge_repos_trending_and_search():
    search = [{"id": "gh:a/b", "title": "a/b", "bucket": "new", "stars_per_day": 10.0}]
    trending = [{"id": "gh:a/b", "stars_today": 300, "stars_per_day": 300.0},
                {"id": "gh:c/d", "title": "c/d", "bucket": "trending", "stars_today": 50, "stars_per_day": 50.0}]
    out = {r["id"]: r for r in link.merge_repos(search, trending)}
    assert out["gh:a/b"]["stars_per_day"] == 300 and out["gh:a/b"]["bucket"] == "new+trending"
    assert "gh:c/d" in out


def test_render_new_kinds():
    s = {"relevance": 7, "quality": 6, "summary": "s", "verdict": "v", "red_flags": ""}
    model = {"id": "hfmodel:x", "kind": "model", "title": "org/Model-27B", "url": "u", "likes": 1200,
             "pipeline": "text-generation", "downloads": 5000}
    assert "♥1,200 · text-generation · 5,000 downloads" in render.item_html(1, model, s)
    news = {"id": "reddit:z", "kind": "news", "source": "r/LocalLLaMA", "rank": 3, "url": "u", "discussion_url": "d",
            "title": "t", "also_on": [{"source": "HN", "points": 150, "comments": 40, "url": "h"}]}
    out = render.item_html(1, news, s)
    assert "r/LocalLLaMA #3 today" in out and "HN 150 pts · 40 comments" in out
    paper = {"id": "arxiv:1", "kind": "paper", "title": "p", "url": "u", "hf_upvotes": 3,
             "discussed": [{"source": "HN", "points": 99, "comments": 5, "url": "h"}]}
    assert "💬 <a" in render.item_html(1, paper, s)


def test_pipeline_collects_all_sources(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    pools = pipeline.collect(cfg, store)
    assert pools["model"] and any(n.get("source", "").startswith("r/") for n in pools["news"])
    assert any("trending" in r.get("bucket", "") for r in pools["repo"])
    pipeline.run(cfg, store)
    texts = "\n".join(m["text"] for m in tg.sent)
    assert "🤗 Models" in texts or "models" in tg.sent[0]["text"]


def test_source_toggle_off(store, web, cfg):
    for k in ("reddit", "lobsters", "github_trending", "hf_models"):
        cfg[k]["enabled"] = False
    cfg["github"]["topics"] = ["mcp"]
    pools = pipeline.collect(cfg, store)
    assert pools["model"] == [] and not any(n.get("source", "HN") != "HN" for n in pools["news"])


def test_reddit_multireddit_one_request(web, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    items = reddit.fetch(cfg["reddit"])
    calls = [u for u in web.calls if "reddit.com" in u]
    assert len(calls) == 1 and "LocalLLaMA+MachineLearning" in calls[0]
    assert items and all(i["source"].startswith("r/") for i in items)


def test_reddit_fetch_gives_up_quietly(web, cfg, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    web.add("reddit.com", RuntimeError("429"))
    assert reddit.fetch(cfg["reddit"]) == []
