"""The daily run: collect -> dedupe -> pre-rank -> screen -> pick -> send."""
from __future__ import annotations

import json
import os

from . import deep, link, profile, render, screen, stars, telegram, util, weekly
from .sources import arxiv, ghtrending, github, hf, hfmodels, hn, lobsters, reddit
from .store import Store


class Output:
    """Sends to Telegram, or prints in dry-run mode."""

    def __init__(self, dry: bool):
        self.dry = dry

    def send(self, text: str, keyboard=None) -> list[int]:
        """Returns the Telegram message ids (empty in dry-run)."""
        if self.dry:
            print(text + ("\n[buttons: %d]" % sum(len(r) for r in keyboard) if keyboard else "") + "\n" + "-" * 60)
            return []
        ids = []
        chunks = telegram.chunk(text)
        for i, c in enumerate(chunks):
            extra = {"reply_markup": {"inline_keyboard": keyboard}} if keyboard and i == len(chunks) - 1 else {}
            ids.append(telegram.send(c, **extra)["message_id"])
        return ids


def collect(cfg, store: Store) -> dict[str, list[dict]]:
    papers = arxiv.fetch(cfg["arxiv"])
    hf.fetch(papers)
    on = lambda name: cfg.get(name, {}).get("enabled", True)  # noqa: E731
    trending = ghtrending.fetch(cfg["github_trending"]) if on("github_trending") and "github_trending" in cfg else []
    if trending:
        github.add_growth(store, [t for t in trending if t["created"]])
    news = hn.fetch(cfg["hackernews"])
    if on("reddit") and "reddit" in cfg:
        news += reddit.fetch(cfg["reddit"])
    if on("lobsters") and "lobsters" in cfg:
        news += lobsters.fetch(cfg["lobsters"])
    pools = {
        "paper": list(papers.values()),
        "repo": link.merge_repos(github.fetch(cfg["github"], store), trending),
        "news": link.merge_news(news),
        "model": hfmodels.fetch(cfg["hf_models"]) if on("hf_models") and "hf_models" in cfg else [],
    }
    pools = {k: [i for i in v if not store.already_sent(i["id"])] for k, v in pools.items()}
    n = link.link(pools)
    a = link.attach_discussions(pools)
    util.log(f"linked {n} paper<->repo pairs, attached {a} discussions")
    return pools


def prerank(pools: dict[str, list[dict]], k: int) -> list[dict]:
    pools["paper"].sort(key=screen.paper_prescore, reverse=True)
    pools["repo"].sort(key=screen.repo_prescore, reverse=True)
    pools["news"].sort(key=lambda h: h.get("heat", link.heat(h)), reverse=True)
    pools.setdefault("model", []).sort(key=lambda m: m.get("trending", 0), reverse=True)
    return pools["paper"][:k] + pools["repo"][:k] + pools["news"][: k // 2] + pools["model"][: k // 4]


def select(cfg, pools: dict[str, list[dict]]) -> list[dict]:
    """Stage 1: cheap triage of every item -> shortlist. Falls back to heuristic pre-ranking."""
    k = cfg["digest"]["candidates_per_source"]
    limits = {"paper": k, "repo": k, "news": k // 2, "model": k // 4}
    scfg = cfg.get("screen", {})
    everything = [i for v in pools.values() for i in v]
    if not everything:
        return []
    rel = screen.triage(everything, cfg["profile"], scfg) if scfg.get("triage", True) else {}
    if len(rel) < len(everything) * 0.5:
        util.log(f"triage covered {len(rel)}/{len(everything)} items - using heuristic pre-ranking")
        return prerank(pools, k)
    kept = screen.shortlist(pools, rel, limits, scfg.get("triage_min", 4), scfg.get("keep_terms", []))
    util.log(f"triage: {len(everything)} items -> {len(kept)} shortlisted")
    return kept


def apply_star_penalty(scores: dict, by_id: dict, cap: int = 4) -> None:
    """Suspicious star patterns cap quality, whatever the model said (#3)."""
    for iid, s in scores.items():
        sc = (by_id.get(iid) or {}).get("star_check")
        if sc and sc["level"] == "suspicious":
            s["quality"] = min(s["quality"], cap)


def listener_alive(store, max_age_s: int = 180) -> bool:
    hb = store.kv_get("listener_heartbeat")
    if not hb:
        return False
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(hb)).total_seconds() < max_age_s


def save_extras(store, day: str, kind: str, ranked: list[tuple[dict, dict]]) -> None:
    """Items that just missed the cut, for /more."""
    for rank, (it, s) in enumerate(ranked, 1):
        store.execute("INSERT OR REPLACE INTO extras (day, kind, rank, id, data, shown) VALUES (?,?,?,?,?,0)",
                      (day, kind, rank, it["id"], json.dumps({"item": it, "score": s}, default=str)))


def finalize(cfg, scores, by_id, kind: str, n: int) -> list[tuple[dict, dict]]:
    """Pick the final items of one section; papers and repos get a deep review first (#1)."""
    dcfg = cfg.get("deep", {})
    if kind in ("news", "model") or not dcfg.get("enabled", True):
        return screen.pick(scores, by_id, kind, n)
    pool = screen.pick(scores, by_id, kind, n + dcfg.get("extra", 3))
    reviews = deep.review_all([it for it, _ in pool], cfg["profile"], dcfg)
    final = []
    for it, s in pool:
        r = reviews.get(it["id"])
        if r is None:
            final.append((it, s))  # review failed: keep the stage-2 judgement
        elif not deep.is_skip(r):
            merged = deep.merge(s, r)
            if (it.get("star_check") or {}).get("level") == "suspicious":
                merged["quality"] = min(merged["quality"], 4)
            final.append((it, merged))
        else:
            util.log(f"deep review skipped {it['id']}: {r['one_line']}")
    final.sort(key=lambda x: x[1]["relevance"] * 0.6 + x[1]["quality"] * 0.4, reverse=True)
    return final[:n]


SECTIONS = [("📄 Papers", "paper", "papers"), ("🛠 Repos & tools", "repo", "repos"),
            ("🤗 Models", "model", "models"), ("📰 Discussed today", "news", "news")]


def run(cfg, store: Store, dry: bool = False) -> int:
    D = {"models": 3, **cfg["digest"]}
    out = Output(dry)
    if not dry:
        if listener_alive(store):
            util.log("bot listener is running: it records votes live")
        else:
            telegram.collect_votes(store)
        refresh_profile(cfg, store, out)
    cfg = {**cfg, "profile": profile.effective(cfg["profile"])}

    pools = collect(cfg, store)
    counts = {k: len(v) for k, v in pools.items()}
    candidates = select(cfg, pools)
    if not candidates:
        util.log("nothing new today")
        return 0
    if cfg.get("stars", {}).get("enabled", True):
        stars.check_all([c for c in candidates if c["kind"] == "repo"])
    by_id = {c["id"]: c for c in candidates}
    scores = screen.score(candidates, cfg["profile"], store, D["model"])
    apply_star_penalty(scores, by_id)

    day = util.now().date().isoformat()
    for iid, sc in scores.items():  # history for the weekly trend report (#6)
        if iid in by_id:
            store.record_seen(by_id[iid], sc, day)
    sections = [(title, finalize(cfg, scores, by_id, kind, D[n])) for title, kind, n in SECTIONS]
    for (title, picked), (_, kind, _) in zip(sections, SECTIONS):
        shown = {it["id"] for it, _ in picked}
        rest = [x for x in screen.pick(scores, by_id, kind, D.get("more", 9) + len(picked)) if x[0]["id"] not in shown]
        save_extras(store, day, kind, rest)
    total = sum(len(p) for _, p in sections)
    n = util.now()
    out.send(f"🧠 <b>AI agents & LLM tools — {n:%a %d %b}</b>\n"
             f"Screened {counts['paper']} papers, {counts['repo']} repos, {counts['model']} models, "
             f"{counts['news']} discussions "
             f"→ {total} picks.\nTap 👍/👎 to tune tomorrow's picks.")
    stamp = n.strftime("%Y%m%d")
    for title, picked in sections:
        if not picked:
            continue
        keys = []
        kind = picked[0][0]["kind"]
        for i, (it, _s) in enumerate(picked):
            key = f"{stamp}{kind[0]}{i}"
            store.execute("INSERT OR REPLACE INTO digest_items (msg_key, id, title, kind, summary, day, pos, data) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (key, it["id"], it["title"], it["kind"], _s.get("summary", ""), day, i + 1,
                           json.dumps({"item": it, "score": _s}, default=str)))
            keys.append(key)
        for mid in out.send(render.section_html(title, picked), telegram.vote_keyboard(keys)):
            store.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (mid, day, kind))
        if not dry:
            for it, sc in picked:
                store.mark_sent(it, n.isoformat())
                store.record_seen(it, sc, day, sent=True)
    store.commit()
    util.log(f"done: {total} items")
    wcfg = cfg.get("weekly", {})
    if wcfg.get("enabled", True) and weekly.due(store, wcfg):
        try:
            weekly.run(store, cfg, out.send)
        except Exception as e:
            util.log(f"weekly report failed: {e}")
    return total


def refresh_profile(cfg, store, out: Output) -> None:
    pcfg = cfg.get("learning", {})
    if not pcfg.get("enabled", True) or not profile.due(store, pcfg):
        return
    try:
        changes = profile.update(store, pcfg, cfg["profile"])
    except Exception as e:  # never block the digest on this
        util.log(f"profile update failed: {e}")
        return
    if changes:
        out.send("🧭 <b>Updated what I learned from your votes</b>\n" + "\n".join(telegram.esc(c) for c in changes[:8])
                 + "\n<i>Edit profile_learned.md to correct it.</i>")


def notify_failure(e: Exception) -> None:
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            telegram.send(f"⚠️ Research digest failed: {telegram.esc(str(e)[:300])}")
        except Exception:
            pass
