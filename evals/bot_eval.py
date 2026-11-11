"""Live eval for the bot: real Claude + real Telegram (messages go to your chat, marked 🧪).
Usage: bot_eval.py <state.db with a digest stored by the new pipeline>"""
import sys

from common import load_cfg, report

from rd import bot as botmod
from rd import telegram, util
from rd.store import Store

util.set_now(None)


def main(db):
    store = Store(db)
    cfg = load_cfg()
    sent = []

    def send(text, **kw):
        sent.append(text)
        return telegram.send("🧪 " + text if not text.startswith("🧪") else text, **kw)

    b = botmod.Bot(store, cfg, send=send)
    owner = str(cfg and __import__("os").environ["TELEGRAM_CHAT_ID"])

    def cmd(text, reply_to=None):
        m = {"chat": {"id": owner}, "text": text}
        if reply_to:
            m["reply_to_message"] = {"message_id": reply_to}
        n = len(sent)
        b.handle({"update_id": 0, "message": m})
        return sent[n:]

    day = b.latest_day()
    kinds = dict(store.execute("SELECT kind, COUNT(*) FROM digest_items WHERE day=? GROUP BY kind", (day,)).fetchall())
    why_p = cmd("/why p1")
    why_r = cmd("/why r1")
    more = cmd("/more repos")
    ask = cmd("/ask Which of today's repos would help me build an MCP server, and why?")
    help_ = cmd("/help")
    errors = [s for s in sent if s.startswith("⚠️")]
    ok = report("bot (live)", [
        ("digest stored for the bot", kinds.get("paper", 0) > 0 and kinds.get("repo", 0) > 0, f"{day}: {kinds}"),
        ("/why p1 explains the paper", bool(why_p) and why_p[0].startswith("🔍") and len("".join(why_p)) > 600,
         f"{len(''.join(why_p))} chars"),
        ("/why r1 explains the repo", bool(why_r) and why_r[0].startswith("🔍") and len("".join(why_r)) > 400,
         f"{len(''.join(why_r))} chars"),
        ("/more repos returns new items", bool(more) and "➕ More repos" in more[0], (more or [""])[0][:80]),
        ("/ask answers from today's items", bool(ask) and ask[0].startswith("💬") and len(ask[0]) > 80, (ask or [""])[0][:120]),
        ("/help lists commands", bool(help_) and "/why" in help_[0], ""),
        ("Telegram accepted every message (HTML valid)", not errors, str(errors)[:200]),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv[1])
