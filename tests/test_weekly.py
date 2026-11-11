"""Improvement #6: weekly trend report."""
import datetime as dt
import json

from rd import pipeline, weekly

TODAY = dt.date(2026, 11, 15)  # a Sunday


def seed(store, n, day, prefix="x", rel=7):
    for i in range(n):
        store.record_seen({"id": f"{prefix}{i}", "kind": "repo", "title": f"{prefix} item {i}", "url": f"https://e/{prefix}{i}"},
                          {"summary": f"about {prefix}", "relevance": rel, "quality": 6}, day)
    store.commit()


def test_momentum_rules():
    assert weekly.momentum(3, None) == "new" and weekly.momentum(3, 0) == "new"
    assert weekly.momentum(3, 2) == "rising" and weekly.momentum(1, 2) == "fading" and weekly.momentum(2, 2.2) == "steady"


def test_week_items_window_and_threshold(store):
    seed(store, 3, "2026-11-12")
    seed(store, 2, "2026-11-01", "old")
    seed(store, 2, "2026-11-13", "low", rel=3)
    got = {i["id"] for i in weekly.week_items(store, dt.date(2026, 11, 8), dt.date(2026, 11, 16))}
    assert got == {"x0", "x1", "x2"}


def test_build_grounds_ids_and_one_theme_per_item(store, claude, monkeypatch):
    seed(store, 8, "2026-11-12")
    fake = {"themes": [{"name": "A", "description": "d", "item_ids": ["x0", "x1", "x1", "ghost"]},
                       {"name": "B", "description": "d", "item_ids": ["x1", "x2", "x3"]},
                       {"name": "C", "description": "d", "item_ids": ["x4"]}],
            "takeaway": "t"}
    monkeypatch.setattr(weekly.llm, "run", lambda *a, **k: fake)
    rep = weekly.build(store, {"interests": "agents"}, today=TODAY)
    names = {t["name"]: t for t in rep["themes"]}
    assert names["A"]["item_ids"] == ["x0", "x1"], "ghost id dropped, duplicate removed"
    assert names["B"]["item_ids"] == ["x2", "x3"], "x1 already assigned to A"
    assert "C" not in names, "single-item theme dropped"
    assert rep["coverage"] == 4 / 8


def test_momentum_against_last_week(store, monkeypatch):
    store.execute("INSERT INTO themes VALUES ('2026-W45','MCP tools','d','[]',1.4)")
    store.execute("INSERT INTO themes VALUES ('2026-W45','World models','d','[]',2.1)")
    seed(store, 8, "2026-11-12")
    fake = {"themes": [{"name": "MCP tools", "description": "d", "item_ids": [f"x{i}" for i in range(5)]},
                       {"name": "Agent memory", "description": "d", "item_ids": ["x5", "x6"]}], "takeaway": "t"}
    monkeypatch.setattr(weekly.llm, "run", lambda *a, **k: fake)
    rep = weekly.build(store, {"interests": "agents"}, today=TODAY)
    st = {t["name"]: t["status"] for t in rep["themes"]}
    assert st == {"MCP tools": "rising", "Agent memory": "new"}
    assert [g["name"] for g in rep["gone"]] == ["World models"]
    saved = store.execute("SELECT name FROM themes WHERE week='2026-W46'").fetchall()
    assert {r[0] for r in saved} == {"MCP tools", "Agent memory"}


def test_last_week_names_given_to_model(store, claude):
    store.execute("INSERT INTO themes VALUES ('2026-W45','MCP tools','d','[]',1.4)")
    seed(store, 8, "2026-11-12")
    weekly.build(store, {"interests": "agents"}, today=TODAY)
    assert 'Last week\'s themes: ["MCP tools"]' in claude()[-1]["prompt"]


def test_too_few_items_skips(store):
    seed(store, 3, "2026-11-12")
    assert weekly.build(store, {"interests": "a"}, today=TODAY) is None


def test_render_and_top_prefers_votes(store, monkeypatch):
    seed(store, 8, "2026-11-12")
    store.execute("INSERT INTO feedback (id, title, vote, ts) VALUES ('x7','t',1,'2026-11-12')")
    fake = {"themes": [{"name": "A <b>", "description": "d&d", "item_ids": ["x0", "x1"]}], "takeaway": "big week"}
    monkeypatch.setattr(weekly.llm, "run", lambda *a, **k: fake)
    rep = weekly.build(store, {"interests": "a"}, today=TODAY)
    assert rep["top"][0]["id"] == "x7"
    html = weekly.render(rep)
    assert html.startswith("🗓 <b>Weekly trends — 2026-W46</b>") and "🆕 <b>A &lt;b&gt;</b> (2)" in html and "d&amp;d" in html
    assert len(html) < 4096


def test_due_once_per_week_on_configured_day(store):
    assert weekly.due(store, {"day": "sun"}, TODAY)
    assert not weekly.due(store, {"day": "sun"}, TODAY - dt.timedelta(days=1))
    store.kv_set("weekly_done", weekly.week_key(TODAY))
    assert not weekly.due(store, {"day": "sun"}, TODAY)


def test_daily_run_records_history(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    pipeline.run(cfg, store)
    n_seen = store.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    n_sent = store.execute("SELECT COUNT(*) FROM seen WHERE sent=1").fetchone()[0]
    assert n_seen > n_sent > 0
