"""Improvement #7: always-on Telegram listener - instant votes and commands (owner only)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from . import deep, enrich, llm, profile, render, telegram, util, weekly

ROOT = Path(__file__).resolve().parent.parent
KIND_LETTER = {"p": "paper", "r": "repo", "m": "model", "n": "news"}
LETTER = {v: k for k, v in KIND_LETTER.items()}
COMMANDS = [
    ("why", "Deep dive on an item: /why p3, or reply /why 3 to a digest message"),
    ("more", "Next picks that missed the cut: /more repos"),
    ("ask", "Ask about today's items: /ask which repo helps with browser agents?"),
    ("interests", "Show what the digest looks for"),
    ("like", "Add a preference: /like evals that ship code"),
    ("avoid", "Add a dislike: /avoid robotics"),
    ("run", "Run today's digest now"),
    ("weekly", "Send the weekly trend report now"),
    ("help", "List commands"),
]
HELP = "\n".join(f"/{c} — {d}" for c, d in COMMANDS)

WHY_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string", "description": "plain-text explanation, short paragraphs, under 2500 characters"},
    },
    "required": ["explanation"],
    "additionalProperties": False,
}
WHY_SYSTEM = """You explain one item from an AI research digest to its reader, like a knowledgeable colleague.

Reader's interests:
{interests}

Cover, in short plain paragraphs: the problem it addresses; how it works (concretely); what the evidence
shows and how strong it is; how the reader could use or try it (commands from the README when given);
caveats. Use only the material provided; say when something is not in it. No markdown headings, no tables."""

ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "description": "plain-text answer under 1500 characters"}},
    "required": ["answer"],
    "additionalProperties": False,
}
ASK_SYSTEM = """You answer the reader's question about today's AI research digest. Use only the items given
(title, summary, verdict, url); cite items by title. If they don't answer it, say so briefly."""


class Bot:
    def __init__(self, store, cfg, send=None):
        self.store = store
        self.cfg = cfg
        self.owner = str(os.environ["TELEGRAM_CHAT_ID"])
        self.send = send or (lambda text, **kw: telegram.send(text, **kw))

    # ------------------------------------------------------------ dispatch

    def handle(self, update: dict) -> None:
        cb = update.get("callback_query")
        if cb:
            if str((cb.get("message") or {}).get("chat", {}).get("id")) == self.owner:
                self.on_vote(cb)
            return
        msg = update.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != self.owner:
            return  # only the owner can use the bot
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        cmd, _, arg = text[1:].partition(" ")
        cmd = cmd.split("@")[0].lower()
        fn = getattr(self, f"cmd_{cmd}", None)
        if not fn:
            self.reply("Unknown command.\n" + HELP)
            return
        try:
            fn(arg.strip(), msg)
        except Exception as e:  # a bad command must never kill the listener
            util.log(f"command /{cmd} failed: {e!r}")
            self.reply(f"⚠️ /{cmd} failed: {telegram.esc(str(e)[:200])}")

    def reply(self, text: str, **kw):
        for c in telegram.chunk(text):
            self.send(c, **kw)

    # ------------------------------------------------------------ votes

    def on_vote(self, cb: dict) -> None:
        data = cb.get("data", "")
        ok = telegram.record_vote(self.store, data)
        self.store.commit()
        note = "Not found (old digest?)"
        if ok:
            vote, key = data.split(":", 1)
            row = self.store.execute("SELECT pos, title FROM digest_items WHERE msg_key=?", (key,)).fetchone()
            note = f"Saved {'👍' if vote == 'up' else '👎'} for #{row[0]}: {row[1][:60]}" if row and row[0] else "Saved"
        try:
            telegram.call("answerCallbackQuery", callback_query_id=cb["id"], text=note)
        except Exception as e:
            util.log(f"answerCallbackQuery: {e}")

    # ------------------------------------------------------------ item lookup

    def latest_day(self) -> str | None:
        row = self.store.execute("SELECT MAX(day) FROM digest_items WHERE day IS NOT NULL").fetchone()
        return row[0] if row else None

    def find(self, arg: str, msg: dict) -> tuple[dict, dict] | None:
        """'p3' / 'repo 2' / '3' as a reply to a section message."""
        m = re.match(r"^\s*([prmn])\w*\s*#?(\d+)\s*$", arg, re.I)
        kind = pos = day = None
        if m:
            kind, pos = KIND_LETTER[m.group(1).lower()], int(m.group(2))
            day = self.latest_day()
        elif re.match(r"^\s*#?\d+\s*$", arg) and msg.get("reply_to_message"):
            row = self.store.execute("SELECT day, kind FROM messages WHERE message_id=?",
                                     (msg["reply_to_message"]["message_id"],)).fetchone()
            if row:
                day, kind = row
                pos = int(arg.strip().lstrip("#"))
        if not (kind and pos and day):
            return None
        row = self.store.execute("SELECT data FROM digest_items WHERE day=? AND kind=? AND pos=?", (day, kind, pos)).fetchone()
        if not row or not row[0]:
            return None
        d = json.loads(row[0])
        return d["item"], d["score"]

    # ------------------------------------------------------------ commands

    def cmd_help(self, arg, msg):
        self.reply("<b>Commands</b>\n" + telegram.esc(HELP))

    cmd_start = cmd_help

    def cmd_why(self, arg, msg):
        found = self.find(arg, msg)
        if not found:
            self.reply("Which item? Use /why p3 (p=paper, r=repo, m=model, n=news) or reply /why 3 to a digest message.")
            return
        it, s = found
        telegram.call("sendChatAction", chat_id=self.owner, action="typing")
        material = [f"Item: {json.dumps({k: it.get(k) for k in ('title', 'url', 'kind', 'abstract', 'description', 'code_url', 'stars', 'health')}, default=str)}",
                    f"Digest judgement: {json.dumps(s, default=str)[:4000]}"]
        if it["kind"] == "paper" and it.get("arxiv_id"):
            material.append("Paper text:\n" + (enrich.paper_fulltext(it["arxiv_id"]) or "[unavailable]"))
        elif it["kind"] == "repo":
            material.append("README:\n" + (enrich.readme(it["title"]) or "[unavailable]"))
        prof = profile.effective(self.cfg["profile"])
        out = llm.run("\n\n".join(material), system=WHY_SYSTEM.format(interests=prof["interests"]), schema=WHY_SCHEMA,
                      model=self.cfg.get("deep", {}).get("model", "opus"), effort="medium", label="why")
        self.reply(f'🔍 <b><a href="{telegram.attr(it["url"])}">{telegram.esc(it["title"])}</a></b>\n\n'
                   + telegram.md_to_html(out["explanation"]))

    def cmd_more(self, arg, msg):
        word = (arg or "repos").lower()
        kind = next((k for l, k in KIND_LETTER.items() if word.startswith(l) or word.startswith(k)), None)
        if not kind:
            self.reply("Use /more papers, /more repos, /more models or /more news.")
            return
        day = self.store.execute("SELECT MAX(day) FROM extras").fetchone()[0]
        rows = self.store.execute("SELECT rank, data FROM extras WHERE day=? AND kind=? AND shown=0 ORDER BY rank LIMIT 3",
                                  (day, kind)).fetchall()
        if not rows:
            self.reply(f"No more {kind}s today.")
            return
        picked = [(json.loads(d)["item"], json.loads(d)["score"]) for _, d in rows]
        for r, _ in rows:
            self.store.execute("UPDATE extras SET shown=1 WHERE day=? AND kind=? AND rank=?", (day, kind, r))
        self.store.commit()
        self.reply(render.section_html(f"➕ More {kind}s", picked))

    def cmd_ask(self, arg, msg):
        if not arg:
            self.reply("Usage: /ask your question about today's items")
            return
        day = self.latest_day()
        rows = self.store.execute("SELECT data FROM digest_items WHERE day=?", (day,)).fetchall()
        rows += self.store.execute("SELECT data FROM extras WHERE day=?", (day,)).fetchall()
        items = []
        for (d,) in rows:
            x = json.loads(d)
            items.append({"title": x["item"]["title"], "kind": x["item"]["kind"], "url": x["item"]["url"],
                          "summary": x["score"].get("summary"), "verdict": x["score"].get("verdict")})
        telegram.call("sendChatAction", chat_id=self.owner, action="typing")
        out = llm.run(f"Question: {arg}\n\nToday's items:\n{json.dumps(items, ensure_ascii=False)}",
                      system=ASK_SYSTEM, schema=ASK_SCHEMA, model="sonnet", effort="low", label="ask")
        self.reply("💬 " + telegram.md_to_html(out["answer"]))

    def cmd_interests(self, arg, msg):
        p = self.cfg["profile"]
        learned = profile.read() or "(nothing learned yet - vote with 👍/👎)"
        self.reply(f"<b>Your interests</b>\n{telegram.esc(p['interests'].strip())}\n\n<b>Not wanted</b>\n"
                   f"{telegram.esc(p['avoid'].strip())}\n\n<b>Learned from votes</b>\n{telegram.esc(learned)}")

    def _note(self, prefix: str, arg: str):
        if not arg:
            self.reply(f"Usage: /{prefix.split()[0].lower()} <text>")
            return
        cur = profile.read()
        line = f"{prefix}: {arg.strip()[:200]}"
        profile.write((cur + "\n" if cur else "") + line)
        self.reply(f"✅ Added to your learned profile: <i>{telegram.esc(line)}</i>")

    def cmd_like(self, arg, msg):
        self._note("Reader asked for more", arg)

    def cmd_avoid(self, arg, msg):
        self._note("Reader asked for less", arg)

    def cmd_run(self, arg, msg):
        lock = ROOT / "logs" / "run.lock"
        if lock.exists() and (dt.datetime.now().timestamp() - lock.stat().st_mtime) < 3600:
            self.reply("A digest run is already in progress.")
            return
        lock.parent.mkdir(exist_ok=True)
        lock.touch()
        subprocess.Popen(["/bin/bash", "-c", f"'{ROOT}/run.sh'; rm -f '{lock}'"], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.reply("⏳ Running the digest now; it takes about 10 minutes.")

    def cmd_weekly(self, arg, msg):
        telegram.call("sendChatAction", chat_id=self.owner, action="typing")
        cfg = {**self.cfg, "profile": profile.effective(self.cfg["profile"])}
        if not weekly.run(self.store, cfg, lambda t, kb=None: self.reply(t)):
            self.reply("Not enough items in the last 7 days for a trend report yet.")


def serve(store, cfg, poll_timeout: int = 50) -> None:
    bot = Bot(store, cfg)
    telegram.call("setMyCommands", commands=[{"command": c, "description": d[:256]} for c, d in COMMANDS])
    offset = int(store.kv_get("tg_offset", 0))
    util.log("bot listening")
    while True:
        store.kv_set("listener_heartbeat", dt.datetime.now(dt.timezone.utc).isoformat())
        store.commit()
        try:
            updates = telegram.call("getUpdates", offset=offset, timeout=poll_timeout,
                                    allowed_updates=["message", "callback_query"])
        except Exception as e:
            util.log(f"getUpdates: {e}")
            import time
            time.sleep(10)
            continue
        for up in updates:
            offset = max(offset, up["update_id"] + 1)
            store.kv_set("tg_offset", offset)
            store.commit()
            bot.handle(up)
