"""Improvement #6: weekly trend report.

Themes follow TopicGPT (Pham et al., 2023): the model generates themes from the week's items and assigns each
item, with last week's theme names given so continuing themes keep their name (cross-window alignment as in
ANTM). Momentum is computed in code from relevance-weighted counts, in the spirit of BERTrend's popularity
signal: new / rising / steady / fading."""
from __future__ import annotations

import datetime as dt
import json

from . import llm, telegram, util

SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "2-5 words; reuse last week's exact name if it is the same theme"},
                    "description": {"type": "string", "description": "one sentence: what is happening in this theme"},
                    "item_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "description", "item_ids"],
                "additionalProperties": False,
            },
        },
        "takeaway": {"type": "string", "description": "2-3 sentences: the most important shift this week for the reader"},
    },
    "required": ["themes", "takeaway"],
    "additionalProperties": False,
}

SYSTEM = """You write the weekly trend report of an AI research digest for one reader.

Reader's interests:
{interests}

You get this week's screened items (papers, repos, models, discussions) and last week's theme names.
1. Group the items into 3-8 themes, each a real cluster of at least 2 items. A theme is one topic people are
   working on (e.g. "MCP servers", "agent memory", "coding-agent benchmarks"): concrete, but broad enough that
   different applications of the same thing stay together (MCP servers for different apps are one theme).
   Never split a theme just to describe variety inside it; say that in its description instead.
   If an item fits a theme from last week, use that theme with its exact name so the trend can be tracked;
   widen the name slightly if needed only when last week's name is too narrow.
2. Assign every item id to exactly one theme when it fits; leave out items that fit none (do not force them).
   Use only ids from the input.
3. Describe each theme in one plain sentence about what is actually happening, and write a short takeaway."""

ARROWS = {"new": "🆕", "rising": "📈", "steady": "➡️", "fading": "📉"}


def week_key(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_items(store, start: dt.date, end: dt.date, min_rel: int = 5) -> list[dict]:
    rows = store.execute(
        "SELECT id, kind, title, url, summary, MAX(relevance), MAX(quality), MAX(sent) FROM seen "
        "WHERE day >= ? AND day < ? GROUP BY id", (start.isoformat(), end.isoformat())).fetchall()
    items = [dict(zip(("id", "kind", "title", "url", "summary", "relevance", "quality", "sent"), r)) for r in rows]
    return [i for i in items if i["relevance"] >= min_rel]


def momentum(weight: float, prev_weight: float | None) -> str:
    if prev_weight is None or prev_weight == 0:
        return "new"
    ratio = weight / prev_weight
    if ratio >= 1.4:
        return "rising"
    if ratio <= 0.6:
        return "fading"
    return "steady"


def build(store, profile: dict, model: str = "opus", today: dt.date | None = None) -> dict | None:
    today = today or util.now().date()
    start = today - dt.timedelta(days=7)
    items = week_items(store, start, today + dt.timedelta(days=1))
    if len(items) < 6:
        util.log(f"weekly: only {len(items)} items, skipping")
        return None
    this_week, last_week = week_key(today), week_key(start - dt.timedelta(days=1))
    prev = {name: (desc, w) for name, desc, w in
            store.execute("SELECT name, description, weight FROM themes WHERE week=?", (last_week,)).fetchall()}
    payload = json.dumps([{"id": i["id"], "type": i["kind"], "title": i["title"], "summary": i["summary"][:200]}
                          for i in items], ensure_ascii=False)
    prompt = (f"Last week's themes: {json.dumps(list(prev))}\n\nThis week's {len(items)} items:\n{payload}")
    out = llm.run(prompt, system=SYSTEM.format(interests=profile["interests"].strip()), schema=SCHEMA,
                  model=model, effort="medium", label="weekly")
    by_id = {i["id"]: i for i in items}
    themes, assigned = [], set()
    for t in out["themes"]:
        ids = [i for i in dict.fromkeys(t["item_ids"]) if i in by_id and i not in assigned]  # grounded, one theme each
        if len(ids) < 2:
            continue
        assigned.update(ids)
        weight = sum(by_id[i]["relevance"] for i in ids) / 10
        prev_w = prev.get(t["name"], (None, None))[1]
        themes.append({"name": t["name"], "description": t["description"], "item_ids": ids, "weight": round(weight, 2),
                       "count": len(ids), "status": momentum(weight, prev_w)})
    themes.sort(key=lambda t: t["weight"], reverse=True)
    current = {t["name"] for t in themes}
    faded = [{"name": n, "description": d, "status": "fading", "weight": 0, "count": 0, "item_ids": []}
             for n, (d, w) in prev.items() if n not in current and w >= 1.5]
    for t in themes:
        store.execute("INSERT OR REPLACE INTO themes VALUES (?,?,?,?,?)",
                      (this_week, t["name"], t["description"], json.dumps(t["item_ids"]), t["weight"]))
    store.commit()
    votes = dict(store.execute("SELECT id, vote FROM feedback").fetchall())
    top = sorted(items, key=lambda i: (votes.get(i["id"], 0), i["sent"], i["relevance"] * 0.6 + i["quality"] * 0.4),
                 reverse=True)[:5]
    return {"week": this_week, "themes": themes, "gone": faded, "takeaway": out["takeaway"], "top": top,
            "n_items": len(items), "coverage": len(assigned) / len(items), "by_id": by_id}


def render(rep: dict) -> str:
    esc, attr = telegram.esc, telegram.attr
    lines = [f"🗓 <b>Weekly trends — {rep['week']}</b>", esc(rep["takeaway"]), "", "<b>Themes</b>"]
    for t in rep["themes"][:8]:
        ex = [rep["by_id"][i] for i in t["item_ids"][:2]]
        links = ", ".join(f'<a href="{attr(e["url"])}">{esc(e["title"][:42])}</a>' for e in ex)
        lines.append(f"{ARROWS[t['status']]} <b>{esc(t['name'])}</b> ({t['count']}) — {esc(t['description'])}\n   e.g. {links}")
    for t in rep["gone"][:3]:
        lines.append(f"📉 <b>{esc(t['name'])}</b> — quiet this week")
    lines += ["", "<b>Top of the week</b>"]
    for n, i in enumerate(rep["top"], 1):
        lines.append(f'{n}. <a href="{attr(i["url"])}">{esc(i["title"])}</a> — {esc(i["summary"][:100])}')
    lines.append(f"\n<i>{rep['n_items']} relevant items this week, {rep['coverage']:.0%} grouped into themes.</i>")
    return "\n".join(lines)


def due(store, wcfg: dict, today: dt.date | None = None) -> bool:
    today = today or util.now().date()
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if days[today.weekday()] != wcfg.get("day", "sun"):
        return False
    return store.kv_get("weekly_done") != week_key(today)


def run(store, cfg, send) -> bool:
    rep = build(store, cfg["profile"], cfg.get("weekly", {}).get("model", "opus"))
    if not rep:
        return False
    send(render(rep))
    store.kv_set("weekly_done", rep["week"])
    store.commit()
    return True
