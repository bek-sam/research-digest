"""Baseline behaviour: sources, ranking, Claude wrapper, Telegram, end-to-end pipeline."""
import json

import pytest

from conftest import FIX
from rd import llm, pipeline, render, screen, telegram, util
from rd.sources import arxiv, github, hf, hn


# ---------- sources

def test_arxiv_parse_fields():
    papers = arxiv.parse((FIX / "arxiv.xml").read_bytes(), days=3)
    assert papers, "fixture should contain recent papers"
    p = next(iter(papers.values()))
    assert p["id"].startswith("arxiv:") and p["url"].startswith("https://arxiv.org/abs/")
    assert "v" not in p["arxiv_id"].split(".")[-1], "version suffix stripped"
    assert p["title"] and "\n" not in p["title"]
    assert len(p["abstract"]) <= 1500


def test_arxiv_query_shape(cfg):
    q = arxiv.build_query(cfg["arxiv"])
    assert q.startswith("(cat:cs.AI") and 'abs:"large language model"' in q and "abs:LLM" in q


def test_hf_merge_upvotes_and_new():
    papers = {}
    items = json.loads((FIX / "hf_daily.json").read_text())
    added = hf.merge(papers, items, "2026-11-09")
    assert added == len(papers) > 0
    aid = items[0]["paper"]["id"]
    papers[aid]["hf_upvotes"] = 0
    hf.merge(papers, items, "2026-11-09")
    assert papers[aid]["hf_upvotes"] == items[0]["paper"]["upvotes"], "existing paper gets upvotes"


def test_github_items_and_growth(store, web, cfg):
    cfg["github"]["topics"] = ["mcp"]
    repos = github.fetch(cfg["github"], store)
    assert repos and all(r["id"].startswith("gh:") for r in repos)
    assert len({r["id"] for r in repos}) == len(repos), "deduped across queries"
    r = repos[0]
    # second snapshot two days later with +100 stars -> 50/day
    import datetime as dt
    util.set_now(util.now() + dt.timedelta(days=2))
    r2 = dict(r, stars=r["stars"] + 100)
    github.add_growth(store, [r2])
    assert r2["stars_per_day"] == 50.0


def test_hn_items(web, cfg):
    items = hn.fetch(cfg["hackernews"])
    assert items and all(i["kind"] == "news" and i["url"].startswith("http") for i in items)


def test_source_failure_is_contained(store, web, cfg):
    web.add("huggingface.co", RuntimeError("down"))
    web.add("hn.algolia.com", RuntimeError("down"))
    papers = arxiv.fetch(cfg["arxiv"])
    hf.fetch(papers)  # must not raise
    assert hn.fetch(cfg["hackernews"]) == []


# ---------- ranking

def test_prescore_orders_by_signal():
    a = {"hf_upvotes": 50, "has_code": True, "n_authors": 5, "comment": ""}
    b = {"hf_upvotes": 0, "has_code": False, "n_authors": 5, "comment": ""}
    assert screen.paper_prescore(a) > screen.paper_prescore(b)
    fast = {"stars": 500, "stars_per_day": 100, "license": "MIT", "forks": 10}
    slow = {"stars": 5000, "stars_per_day": 1, "license": "MIT", "forks": 10}
    assert screen.repo_prescore(fast) > screen.repo_prescore(slow)


def test_pick_filters_and_sorts():
    by_id = {f"x{i}": {"id": f"x{i}", "kind": "paper"} for i in range(5)}
    scores = {f"x{i}": {"relevance": r, "quality": 5} for i, r in enumerate([9, 4, 7, 10, 5])}
    got = [it["id"] for it, _ in screen.pick(scores, by_id, "paper", 3)]
    assert got == ["x3", "x0", "x2"]


# ---------- Claude wrapper

def test_llm_run_ok_and_strips_api_key(claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-pass")
    out = llm.run('[{"id": "a"}]', system="s", schema=screen.SCORE_SCHEMA)
    assert [i["id"] for i in out["items"]] == ["a"]
    argv = claude()[0]["argv"]
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--json-schema" in argv


def test_llm_retries_then_succeeds(claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "once")
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = llm.run('[{"id": "a"}]', system="s", schema=screen.SCORE_SCHEMA)
    assert out["items"] and len(claude()) == 2


def test_llm_raises_after_retries(claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "always")
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(llm.LLMError):
        llm.run("x", system="s", schema=screen.SCORE_SCHEMA)


# ---------- Telegram + rendering

def test_chunk_respects_limit():
    body = "\n\n".join("x" * 1000 for _ in range(10)) + "\n\n" + "y" * 9000
    parts = telegram.chunk(body)
    assert all(len(p) <= telegram.MAX_LEN for p in parts)
    assert "".join(parts).count("x") == 10000 and "".join(parts).count("y") == 9000


def test_render_escapes_html():
    it = {"kind": "repo", "title": "a<b>&c", "url": 'https://x.y/"q', "stars": 1234,
          "stars_per_day": 2.0, "language": "C++", "bucket": "new", "created": "2026-11-01"}
    s = {"relevance": 8, "quality": 6, "summary": "uses <tags>", "verdict": "ok & fine", "red_flags": ""}
    out = render.item_html(1, it, s)
    assert "a&lt;b&gt;&amp;c" in out and "&lt;tags&gt;" in out and '&quot;q' in out and "⭐1,234" in out


def test_votes_recorded_once(store, tg):
    store.execute("INSERT INTO digest_items (msg_key, id, title, kind, summary) VALUES ('k1','arxiv:1','Paper one','paper','About agents')")
    tg.updates = [{"update_id": 10, "callback_query": {"data": "up:k1"}},
                  {"update_id": 11, "callback_query": {"data": "down:missing"}},
                  {"update_id": 12, "callback_query": {"data": "garbage"}}]
    assert telegram.collect_votes(store) == 1
    assert store.execute("SELECT vote FROM feedback WHERE id='arxiv:1'").fetchone()[0] == 1
    assert telegram.collect_votes(store) == 0, "offset advanced; no double count"


# ---------- end to end

def test_pipeline_end_to_end(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    total = pipeline.run(cfg, store)
    assert total > 0
    texts = [m["text"] for m in tg.sent]
    assert texts[0].startswith("🧠")
    assert any("📄 Papers" in t for t in texts) and any("🛠 Repos" in t for t in texts)
    assert all(m.get("parse_mode") == "HTML" for m in tg.sent)
    sent_n = store.execute("SELECT COUNT(*) FROM sent WHERE kind != 'linked'").fetchone()[0]
    assert sent_n == total
    # second run: nothing re-sent
    tg.sent.clear()
    pipeline.run(cfg, store)
    ids_again = store.execute("SELECT COUNT(*) FROM sent").fetchone()[0]
    assert ids_again >= sent_n
    first = {r[0] for r in store.execute("SELECT id FROM digest_items")}
    assert first


def test_pipeline_dry_run_sends_nothing(store, web, tg, claude, cfg, capsys):
    cfg["github"]["topics"] = ["mcp"]
    pipeline.run(cfg, store, dry=True)
    assert tg.sent == [] and store.execute("SELECT COUNT(*) FROM sent").fetchone()[0] == 0
    assert "📄 Papers" in capsys.readouterr().out
