"""Improvement #7: Telegram listener and commands."""
import datetime as dt
import json

import pytest

from rd import bot as botmod
from rd import pipeline, profile, telegram


@pytest.fixture
def bot(store, tg, cfg):
    sent = []
    b = botmod.Bot(store, cfg, send=lambda text, **kw: sent.append(text))
    b.sent = sent
    return b


def seed_digest(store, day="2026-11-10"):
    for kind, n in (("paper", 3), ("repo", 3)):
        for i in range(1, n + 1):
            it = {"id": f"{kind}:{i}", "kind": kind, "title": f"{kind} {i}", "url": f"https://x/{kind}{i}",
                  "arxiv_id": None, "abstract": "abs", "description": "desc"}
            sc = {"relevance": 8, "quality": 7, "summary": f"summary {kind} {i}", "verdict": "good", "red_flags": ""}
            store.execute("INSERT INTO digest_items (msg_key, id, title, kind, summary, day, pos, data) VALUES (?,?,?,?,?,?,?,?)",
                          (f"20261110{kind[0]}{i - 1}", it["id"], it["title"], kind, sc["summary"], day, i,
                           json.dumps({"item": it, "score": sc})))
    store.execute("INSERT INTO messages VALUES (555, ?, 'repo')", (day,))
    for r in range(1, 5):
        it = {"id": f"repo:x{r}", "kind": "repo", "title": f"extra {r}", "url": "u", "stars": 10, "bucket": "new", "created": "2026-11-01"}
        store.execute("INSERT INTO extras (day, kind, rank, id, data) VALUES (?,?,?,?,?)",
                      (day, "repo", r, it["id"], json.dumps({"item": it, "score": {"relevance": 6, "quality": 5, "summary": "s", "verdict": "v", "red_flags": ""}})))
    store.commit()


def msg(text, chat="1", reply_to=None):
    m = {"chat": {"id": chat}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": 1, "message": m}


def test_ignores_strangers(bot):
    bot.handle(msg("/help", chat="999"))
    bot.handle({"update_id": 2, "callback_query": {"id": "c", "data": "up:x", "message": {"chat": {"id": "999"}}}})
    assert bot.sent == []


def test_instant_vote_with_confirmation(bot, store, tg):
    seed_digest(store)
    bot.handle({"update_id": 3, "callback_query": {"id": "cb1", "data": "up:20261110r1", "message": {"chat": {"id": "1"}}}})
    assert store.execute("SELECT vote, kind FROM feedback WHERE id='repo:2'").fetchone() == (1, "repo")


def test_why_by_code_and_by_reply(bot, store, claude):
    seed_digest(store)
    bot.handle(msg("/why p2"))
    assert bot.sent[-1].startswith("🔍 <b><a href=\"https://x/paper2\">paper 2</a></b>")
    bot.handle(msg("/why 3", reply_to=555))
    assert "repo 3" in bot.sent[-1]
    prompts = [c["prompt"] for c in claude()]
    assert '"title": "paper 2"' in prompts[0] and "summary paper 2" in prompts[0]


def test_why_unknown_item(bot, store):
    seed_digest(store)
    bot.handle(msg("/why p9"))
    assert bot.sent[-1].startswith("Which item?")


def test_more_pages_through_extras(bot, store):
    seed_digest(store)
    bot.handle(msg("/more repos"))
    assert "extra 1" in bot.sent[-1] and "extra 3" in bot.sent[-1] and "extra 4" not in bot.sent[-1]
    bot.handle(msg("/more r"))
    assert "extra 4" in bot.sent[-1]
    bot.handle(msg("/more repos"))
    assert bot.sent[-1] == "No more repos today."


def test_ask_uses_todays_items(bot, store, claude):
    seed_digest(store)
    bot.handle(msg("/ask which repo is best?"))
    assert bot.sent[-1].startswith("💬")
    call = claude()[-1]
    assert "which repo is best?" in call["prompt"] and "extra 2" in call["prompt"]
    assert call["argv"][call["argv"].index("--model") + 1] == "sonnet"


def test_like_avoid_and_interests(bot):
    bot.handle(msg("/like evals that ship code"))
    bot.handle(msg("/avoid robotics"))
    learned = profile.read()
    assert "Reader asked for more: evals that ship code" in learned and "Reader asked for less: robotics" in learned
    bot.handle(msg("/interests"))
    assert "Learned from votes" in bot.sent[-1] and "robotics" in bot.sent[-1]


def test_unknown_and_failing_commands_are_contained(bot, monkeypatch):
    bot.handle(msg("/nope"))
    assert bot.sent[-1].startswith("Unknown command.")
    monkeypatch.setattr(botmod.Bot, "cmd_ask", lambda self, a, m: 1 / 0)
    bot.handle(msg("/ask x"))
    assert bot.sent[-1].startswith("⚠️ /ask failed")


def test_non_commands_ignored(bot):
    bot.handle(msg("hello"))
    assert bot.sent == []


def test_daily_run_saves_message_map_and_extras(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    pipeline.run(cfg, store)
    assert store.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= 2
    row = store.execute("SELECT data FROM digest_items WHERE pos=1 AND kind='paper'").fetchone()
    assert json.loads(row[0])["item"]["kind"] == "paper"
    assert store.execute("SELECT COUNT(*) FROM extras").fetchone()[0] > 0


def test_daily_run_leaves_votes_to_live_listener(store, web, tg, claude, cfg, monkeypatch):
    cfg["github"]["topics"] = ["mcp"]
    store.kv_set("listener_heartbeat", dt.datetime.now(dt.timezone.utc).isoformat())
    called = []
    monkeypatch.setattr(telegram, "collect_votes", lambda s: called.append(1))
    pipeline.run(cfg, store)
    assert called == []
    store.kv_set("listener_heartbeat", (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat())
    pipeline.run(cfg, store)
    assert called == [1], "stale heartbeat: daily run collects votes itself"


def test_md_to_html_safe():
    out = telegram.md_to_html("## Title\n**PrefectHQ/fastmcp** uses `pip install x<1>` & <script>")
    assert out == "Title\n<b>PrefectHQ/fastmcp</b> uses <code>pip install x&lt;1&gt;</code> &amp; &lt;script&gt;"
