"""Formatting digest items as Telegram HTML."""
from __future__ import annotations

import re

from .telegram import attr, esc

CAPS = {"summary": 420, "verdict": 220, "red_flags": 300, "claim": 170, "try_it": 170}


def clean(text, cap: int) -> str:
    """Model text -> one tidy line: no markdown emphasis/bullets, collapsed whitespace, cut at a word."""
    t = re.sub(r"\*\*|__|`", "", str(text or ""))
    t = re.sub(r"(^|\n)\s*[-*•]\s+", " · ", t)
    t = re.sub(r"\s+", " ", t).strip(" ·")
    if len(t) > cap:
        t = t[:cap].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t


def item_html(n: int, it: dict, s: dict) -> str:
    stars = "★" * max(1, round((s["relevance"] + s["quality"]) / 4))
    meta = []
    if it["kind"] == "paper":
        if it.get("hf_upvotes"):
            meta.append(f"▲{it['hf_upvotes']} HF")
        if it.get("org"):
            meta.append(esc(it["org"]))
        if it.get("code_url"):
            code_st = f" ⭐{it['code_stars']:,}" if it.get("code_stars") else ""
            grow = f" +{it['code_stars_per_day']}/day" if it.get("code_stars_per_day") else ""
            meta.append(f'<a href="{attr(it["code_url"])}">code</a>{code_st}{grow}')
        elif it.get("has_code"):
            meta.append("code")
    elif it["kind"] == "repo":
        meta.append(f"⭐{it['stars']:,}")
        if it.get("stars_per_day"):
            meta.append(f"+{it['stars_per_day']}/day")
        if it.get("language"):
            meta.append(esc(it["language"]))
        if it.get("bucket") == "new":
            meta.append(f"new {it['created']}")
    elif it["kind"] == "model":
        meta.append(f"♥{it.get('likes', 0):,}")
        if it.get("pipeline"):
            meta.append(esc(it["pipeline"]))
        if it.get("downloads"):
            meta.append(f"{it['downloads']:,} downloads")
    else:
        meta.append(disc_html(describe_self(it)))
        for d in it.get("also_on", [])[:3]:
            meta.append(disc_html(d))
    if it["kind"] != "news":
        for d in it.get("discussed", [])[:3]:
            meta.append("💬 " + disc_html(d))
    d = s.get("deep")
    tag = ""
    if d:
        tag = {"must-read": "🔥 ", "must-try": "🔥 "}.get(d["verdict"], "")
    lines = [f'<b>{n}. {tag}<a href="{attr(it["url"])}">{esc(it["title"])}</a></b>',
             f"{stars}  rel {s['relevance']}/10 · quality {s['quality']}/10 · " + " · ".join(meta),
             esc(clean(s["summary"], CAPS["summary"]))]
    if d:
        lines += deep_lines(it, d)
    lines.append(f"<i>{esc(clean(s['verdict'], CAPS['verdict']))}</i>")
    if s.get("red_flags"):
        lines.append(f"⚠️ {esc(clean(s['red_flags'], CAPS['red_flags']))}")
    sc = it.get("star_check")
    if sc and sc["level"] != "ok":
        icon = "🚩 Suspicious stars" if sc["level"] == "suspicious" else "👀 Star pattern to watch"
        lines.append(f"{icon}: {esc('; '.join(sc['reasons']))}")
    return "\n".join(lines)


def describe_self(it: dict) -> dict:
    from .link import describe
    return describe(it)


def disc_html(d: dict) -> str:
    if "rank" in d:
        label = f"{esc(d['source'])} #{d['rank']} today"
    else:
        label = f"{esc(d['source'])} {d.get('points', 0)} pts · {d.get('comments', 0)} comments"
    return f'<a href="{attr(d["url"])}">{label}</a>'


SUPPORT = {"strong": "✅", "partial": "🟡", "weak": "🔴", "untested": "❔"}


def deep_lines(it: dict, d: dict) -> list[str]:
    out = []
    if it["kind"] == "paper":
        out.append(f"<b>{esc(d['contribution'])}</b> · novelty {d['novelty']} · rigor {d['rigor']} · "
                   f"{esc(d['reproducibility'])}" + ("" if d.get("fulltext") else " · <i>abstract only</i>"))
        for c in d.get("claims", [])[:3]:
            out.append(f"{SUPPORT.get(c['support'], '•')} {esc(clean(c['claim'], CAPS['claim']))}")
    else:
        h = it.get("health") or {}
        bits = [esc(d["kind"]), esc(d["maturity"])]
        if "commits_30d" in h:
            bits.append(f"{h['commits_30d']} commits/30d")
        if "contributors" in h:
            bits.append(f"{h['contributors']} contributors")
        if h.get("has_tests"):
            bits.append("tests ✓")
        if h.get("has_ci"):
            bits.append("CI ✓")
        if h.get("openssf_scorecard") is not None:
            bits.append(f"scorecard {h['openssf_scorecard']}")
        out.append(" · ".join(bits))
    if d.get("try_it"):
        t = clean(d["try_it"], CAPS["try_it"])
        out.append(f"▶️ <code>{esc(t)}</code>" if it["kind"] == "repo" else f"▶️ {esc(t)}")
    return out


def section_html(title: str, picked: list[tuple[dict, dict]]) -> str:
    return f"<b>{title}</b>\n\n" + "\n\n".join(item_html(i + 1, it, s) for i, (it, s) in enumerate(picked))
