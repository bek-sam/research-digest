"""Improvement #2: two-stage screening (cheap triage of everything -> shortlist -> main scoring)."""
import json

from rd import pipeline, screen


def mk(kind, i, **kw):
    base = {"id": f"{kind}:{i}", "kind": kind, "title": f"{kind} {i}"}
    if kind == "paper":
        base.update(abstract="a", hf_upvotes=0, has_code=False, n_authors=3, comment="")
    elif kind == "repo":
        base.update(description="d", stars=100, stars_per_day=1, license=None, forks=0)
    else:
        base.update(points=100)
    base.update(kw)
    return base


PROFILE = {"interests": "agents", "avoid": "crypto"}


def test_triage_covers_every_item_in_batches(claude):
    items = [mk("paper", i) for i in range(250)]
    rel = screen.triage(items, PROFILE, {"triage_batch": 100, "parallel": 3})
    assert set(rel) == {i["id"] for i in items}
    calls = claude()
    assert len(calls) == 3, "250 items / 100 per batch"
    assert all(c["argv"][c["argv"].index("--model") + 1] == "haiku" for c in calls)
    sizes = sorted(c["prompt"].count('"id"') for c in calls)
    assert sizes == [50, 100, 100]


def test_triage_view_is_compact():
    v = screen.triage_view(mk("paper", 1, abstract="x" * 2000, hf_upvotes=99))
    assert set(v) == {"id", "type", "title", "snippet"} and len(v["snippet"]) == 350


def test_unpopular_but_relevant_paper_reaches_shortlist():
    """The old pre-rank kept only the top-N by upvotes; triage must rescue a zero-upvote on-topic paper."""
    papers = [mk("paper", i, hf_upvotes=100 - i) for i in range(60)] + [mk("paper", "gem", hf_upvotes=0)]
    rel = {p["id"]: 4 for p in papers}
    rel["paper:gem"] = 10
    old = [p["id"] for p in sorted(papers, key=screen.paper_prescore, reverse=True)[:40]]
    assert "paper:gem" not in old
    kept = screen.shortlist({"paper": papers}, rel, {"paper": 40})
    assert kept[0]["id"] == "paper:gem"


def test_shortlist_drops_offtopic_keeps_missing():
    papers = [mk("paper", "off"), mk("paper", "on"), mk("paper", "missing")]
    kept = screen.shortlist({"paper": papers}, {"paper:off": 1, "paper:on": 8}, {"paper": 10})
    ids = [p["id"] for p in kept]
    assert "paper:off" not in ids and ids[0] == "paper:on" and "paper:missing" in ids


def test_signal_bonus_breaks_ties_but_never_beats_relevance():
    hot = mk("repo", "hot", stars_per_day=5000, stars=90000, forks=9000, license="MIT")
    cold = mk("repo", "cold", stars_per_day=0)
    assert 0 <= screen.signal_bonus(cold) < screen.signal_bonus(hot) <= 2
    kept = screen.shortlist({"repo": [hot, cold]}, {"repo:hot": 6, "repo:cold": 9}, {"repo": 2})
    assert kept[0]["id"] == "repo:cold", "+3 relevance outweighs max signal bonus"


def test_shortlist_limits_per_source():
    pools = {"paper": [mk("paper", i) for i in range(50)], "news": [mk("news", i) for i in range(50)]}
    kept = screen.shortlist(pools, {}, {"paper": 40, "news": 20})
    assert sum(k["kind"] == "paper" for k in kept) == 40 and sum(k["kind"] == "news" for k in kept) == 20


def test_relevance_clamped_and_foreign_ids_ignored(claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_REL", json.dumps({"paper:1": 99}))
    rel = screen.triage([mk("paper", 1)], PROFILE, {})
    assert rel == {"paper:1": 10}


def test_select_falls_back_when_triage_fails(claude, monkeypatch, cfg):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL_MODEL", "haiku")
    monkeypatch.setattr("time.sleep", lambda s: None)
    pools = {"paper": [mk("paper", i) for i in range(5)], "repo": [], "news": []}
    kept = pipeline.select(cfg, pools)
    assert len(kept) == 5, "heuristic pre-rank used instead"


def test_select_partial_batch_failure_keeps_unrated(claude, monkeypatch, cfg):
    omit = [f"paper:{i}" for i in range(10)]
    monkeypatch.setenv("FAKE_CLAUDE_OMIT", json.dumps(omit))
    pools = {"paper": [mk("paper", i) for i in range(30)], "repo": [], "news": []}
    kept = {k["id"] for k in pipeline.select(cfg, pools)}
    assert set(omit) <= kept, "unrated items are kept, not lost"


def test_triage_disabled_uses_prerank(claude, cfg):
    cfg["screen"]["triage"] = False
    pools = {"paper": [mk("paper", i) for i in range(5)], "repo": [], "news": []}
    pipeline.select(cfg, pools)
    assert claude() == []


def test_end_to_end_two_calls_models(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    pipeline.run(cfg, store)
    models = [c["argv"][c["argv"].index("--model") + 1] for c in claude()]
    assert "haiku" in models and models[-1] == "opus"
    final = claude()[-1]["prompt"]
    assert '"triage"' not in final, "internal triage field not leaked to the scorer"


def test_keyword_safety_net_rescues_cheap_model_miss():
    lasik = mk("repo", "lasik", description="BLA blinds your agent. Static de-blinding CLI + MCP server")
    traffic = mk("paper", "traffic", abstract="Large language models predict urban traffic in rain")
    kept = screen.shortlist({"all": [lasik, traffic]}, {"repo:lasik": 2, "paper:traffic": 2},
                            {"all": 10}, 4, ["agent", "mcp", "tool use"])
    assert [k["id"] for k in kept] == ["repo:lasik"]


def test_mentions_whole_word_and_hyphens():
    assert screen.mentions({"title": "Multi-Agent systems"}, ["multi agent"])
    assert not screen.mentions({"title": "x", "topics": ["ai-agents"]}, ["agent"]), "topics ignored"
    assert screen.mentions({"title": "x", "description": "An AI-agent runtime"}, ["agent"])
    assert not screen.mentions({"title": "x", "abstract": "y" * 300 + " agents"}, ["agent"])
    assert not screen.mentions({"title": "reagent chemistry"}, ["agent"])
    assert not screen.mentions({"title": "Storage drag"}, ["rag"])
