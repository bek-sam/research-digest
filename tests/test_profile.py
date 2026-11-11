"""Improvement #8: learned natural-language profile from votes."""
import datetime as dt
import json

from rd import pipeline, profile, telegram, util

BASE = {"interests": "AI agents", "avoid": "crypto"}


def vote(store, i, v, kind="repo", ts=None):
    store.execute("INSERT INTO feedback (id, title, vote, ts, kind, summary) VALUES (?,?,?,?,?,?)",
                  (f"x{i}", f"Title {i}", v, ts or util.now().isoformat(), kind, f"summary {i}"))


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.executescript("CREATE TABLE feedback (id TEXT PRIMARY KEY, title TEXT, vote INTEGER, ts TEXT);"
                    "CREATE TABLE digest_items (msg_key TEXT PRIMARY KEY, id TEXT, title TEXT);"
                    "INSERT INTO feedback VALUES ('a','t',1,'2026-01-01');")
    c.commit()
    c.close()
    from rd.store import Store
    s = Store(old)
    cols = {r[1] for r in s.execute("PRAGMA table_info(feedback)")}
    assert {"kind", "summary"} <= cols and s.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1


def test_vote_stores_kind_and_summary(store):
    store.execute("INSERT INTO digest_items (msg_key, id, title, kind, summary) VALUES ('k','gh:a/b','A','repo','An MCP server')")
    assert telegram.record_vote(store, "up:k")
    assert store.execute("SELECT kind, summary FROM feedback").fetchone() == ("repo", "An MCP server")


def test_due_rules(store):
    cfg = {"min_new_votes": 5, "max_days": 7}
    assert not profile.due(store, cfg)
    for i in range(4):
        vote(store, i, 1)
    assert not profile.due(store, cfg), "4 votes, never updated: wait for 5"
    vote(store, 9, -1)
    assert profile.due(store, cfg)
    store.kv_set("profile_updated", util.now().isoformat())
    assert not profile.due(store, cfg)
    vote(store, 10, 1, ts=(util.now() + dt.timedelta(minutes=1)).isoformat())
    util.set_now(util.now() + dt.timedelta(days=8))
    assert profile.due(store, cfg), "a week passed with a new vote"


def test_update_writes_file_and_returns_changes(store, claude, monkeypatch):
    for i in range(6):
        vote(store, i, 1 if i % 2 else -1)
    changes = profile.update(store, {}, BASE)
    assert isinstance(changes, list)
    assert profile.read() == "fake profile"
    assert profile.PROFILE_FILE.read_text().startswith("<!--"), "editing note kept"
    prompt = claude()[-1]["prompt"]
    assert "👍 [repo] Title 1 — summary 1" in prompt and "👎 [repo] Title 0" in prompt


def test_user_edits_are_the_next_baseline(store, claude):
    profile.write("Leans toward: runnable MCP servers. Less interested in: benchmarks.")
    vote(store, 1, 1)
    profile.update(store, {}, BASE)
    assert "Previous learned profile:\nLeans toward: runnable MCP servers" in claude()[-1]["prompt"]


def test_effective_profile_keeps_user_interests_first():
    assert profile.effective(BASE) == BASE
    profile.write("Leans toward: X")
    eff = profile.effective(BASE)
    assert eff["interests"].startswith("AI agents") and "Leans toward: X" in eff["interests"]
    assert eff["avoid"] == "crypto"


def test_profile_length_bounded():
    profile.write("x" * 5000)
    assert len(profile.read()) == profile.MAX_CHARS


def test_pipeline_refreshes_and_notifies(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    for i in range(5):
        vote(store, i, 1)
    pipeline.run(cfg, store)
    assert any(m["text"].startswith("🧭") for m in tg.sent)
    triage_prompt = next(c for c in claude() if "first-pass filter" in c["argv"][c["argv"].index("--system-prompt") + 1])
    assert "fake profile" in triage_prompt["argv"][triage_prompt["argv"].index("--system-prompt") + 1], \
        "learned profile reaches triage"


def test_profile_failure_does_not_block_digest(store, web, tg, claude, cfg, monkeypatch):
    cfg["github"]["topics"] = ["mcp"]
    for i in range(5):
        vote(store, i, 1)
    monkeypatch.setattr(profile, "update", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pipeline.run(cfg, store) > 0
