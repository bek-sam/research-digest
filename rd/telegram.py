"""Telegram Bot API: sending digests, reading 👍/👎 votes."""
from __future__ import annotations

import html
import json
import os
import urllib.request

from . import util

MAX_LEN = 3900  # Telegram limit is 4096 chars per message


def call(method: str, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=util.SSL_CTX) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(f"telegram {method}: {res}")
    return res["result"]


def send(text: str, **extra):
    return call("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"], text=text,
                parse_mode="HTML", disable_web_page_preview=True, **extra)


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def attr(s) -> str:
    return html.escape(str(s or ""), quote=True)


def md_to_html(text: str) -> str:
    """Escape model text, then keep its light markdown as Telegram HTML (bold, code); drop heading marks."""
    import re
    t = esc(text)
    t = re.sub(r"^#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    return t


def chunk(body: str, limit: int = MAX_LEN) -> list[str]:
    """Split on blank lines so no message exceeds the limit (hard-cut any single oversized block)."""
    chunks, cur = [], ""
    for part in body.split("\n\n"):
        while len(part) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(part[:limit])
            part = part[limit:]
        if cur and len(cur) + 2 + len(part) > limit:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + part
    if cur:
        chunks.append(cur)
    return chunks


def vote_keyboard(keys: list[str]) -> list[list[dict]]:
    rows, row = [], []
    for i, key in enumerate(keys):
        row += [{"text": f"👍{i + 1}", "callback_data": f"up:{key}"},
                {"text": f"👎{i + 1}", "callback_data": f"down:{key}"}]
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def collect_votes(store) -> int:
    """Store 👍/👎 presses received since the last run."""
    offset = int(store.kv_get("tg_offset", 0))
    try:
        updates = call("getUpdates", offset=offset, timeout=0, allowed_updates=["callback_query", "message"])
    except Exception as e:
        util.log(f"votes: {e}")
        return 0
    n = 0
    for up in updates:
        offset = max(offset, up["update_id"] + 1)
        cb = up.get("callback_query")
        if cb and record_vote(store, cb.get("data", "")):
            n += 1
    store.kv_set("tg_offset", offset)
    store.commit()
    util.log(f"votes: {n} recorded")
    return n


def record_vote(store, data: str) -> bool:
    if ":" not in data:
        return False
    vote, key = data.split(":", 1)
    if vote not in ("up", "down"):
        return False
    row = store.execute("SELECT id, title, kind, summary FROM digest_items WHERE msg_key=?", (key,)).fetchone()
    if not row:
        return False
    store.execute("INSERT OR REPLACE INTO feedback (id, title, vote, ts, kind, summary) VALUES (?,?,?,?,?,?)",
                  (row[0], row[1], 1 if vote == "up" else -1, util.now().isoformat(), row[2], row[3]))
    return True
