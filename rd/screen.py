"""Pre-ranking and Claude screening."""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor

from . import llm, util

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevance": {"type": "integer", "description": "0-10 fit to the reader's interests"},
                    "quality": {"type": "integer", "description": "0-10 how solid/novel/useful it looks"},
                    "summary": {"type": "string", "description": "1-2 plain sentences: what it is and why it matters"},
                    "verdict": {"type": "string", "description": "short honest assessment"},
                    "red_flags": {"type": "string", "description": "concrete concerns, or empty string"},
                },
                "required": ["id", "relevance", "quality", "summary", "verdict", "red_flags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = """You screen new AI research and open-source projects for one reader and decide what is worth their time.

Reader's interests:
{interests}

Not wanted:
{avoid}
{feedback}
For every item you get, return relevance (fit to the interests), quality, a short summary, a verdict and red flags.
Judge quality from the evidence in front of you, not the hype: for papers, is the contribution concrete, are claims
backed by experiments, is code released, is it more than an incremental benchmark bump; for repos, does the
description show a real tool (versus a wrapper, a list, or a template), is growth organic, is it licensed and maintained;
for news, is it substantive; for models, is it a genuinely useful open model for building agents (not a merge,
uncensored fine-tune or quantization repost), judged from the name, tags, likes and trend. A repo's star_check (when present) is a measured engagement test: treat "suspicious" as
strong evidence the stars were bought, "watch" as a caution. You cannot run code or read full papers, so say "unverified" where a claim can't be judged
from the metadata, and put real concerns (overclaiming, no code, suspicious star growth, vague abstract) in red_flags.
Score harshly: most items should land below 6. Keep summaries plain and specific - no marketing words."""


def paper_prescore(p: dict) -> float:
    return (math.log1p(p["hf_upvotes"]) * 3 + math.log1p(p.get("heat", 0)) + (2 if p["has_code"] else 0)
            + min(p["n_authors"], 10) * 0.1 + (1 if p["comment"] else 0)
            + math.log1p(p.get("code_stars") or 0) * 0.5)


def repo_prescore(r: dict) -> float:
    spd = r.get("stars_per_day")
    if spd is None:  # established repo seen for the first time: no growth data yet
        return math.log1p(r["stars"]) * 0.5
    return (math.log1p(max(spd, 0)) * 3 + (1 if r["license"] else 0) + math.log1p(r["forks"]) * 0.3
            + math.log1p(r.get("heat", 0)))


def feedback_block(store) -> str:
    """Recent votes as examples (the learned profile in the interests carries the generalization)."""
    rows = store.execute("SELECT title, vote FROM feedback ORDER BY ts DESC LIMIT 20").fetchall()
    if not rows:
        return ""
    liked = [t for t, v in rows if v > 0]
    disliked = [t for t, v in rows if v < 0]
    s = "\nPast feedback from the reader (use it to calibrate relevance):\n"
    if liked:
        s += "Liked: " + "; ".join(liked[:25]) + "\n"
    if disliked:
        s += "Disliked: " + "; ".join(disliked[:15]) + "\n"
    return s


def compact(item: dict) -> dict:
    drop = {"kind", "url", "hn_url", "discussion_url", "authors", "triage", "linked_ids", "code_url", "homepage", "arxiv_id"}
    d = {k: v for k, v in item.items() if k not in drop and v not in (None, "", [], False)}
    if item.get("authors"):
        d["authors"] = ", ".join(item["authors"][:4]) + (" et al." if item["n_authors"] > 4 else "")
    return d


def score(items: list[dict], profile: dict, store, model: str) -> dict[str, dict]:
    system = SYSTEM.format(interests=profile["interests"].strip(), avoid=profile["avoid"].strip(),
                           feedback=feedback_block(store))
    payload = json.dumps([compact(i) for i in items], ensure_ascii=False)
    out = llm.run(f"Today is {util.now().date()}. Score all {len(items)} items:\n{payload}",
                  system=system, schema=SCORE_SCHEMA, model=model, label="score")
    return {s["id"]: s for s in out["items"]}


def pick(scores: dict[str, dict], by_id: dict[str, dict], kind: str, n: int,
         min_relevance: int = 5) -> list[tuple[dict, dict]]:
    ranked = [(by_id[i], s) for i, s in scores.items()
              if i in by_id and by_id[i]["kind"] == kind and s["relevance"] >= min_relevance]
    ranked.sort(key=lambda x: x[1]["relevance"] * 0.6 + x[1]["quality"] * 0.4, reverse=True)
    return ranked[:n]


# ---------------------------------------------------------------- stage 1: triage (cheap model, every item)

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevance": {"type": "integer", "description": "0-10 fit to the reader's interests"},
                },
                "required": ["id", "relevance"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

TRIAGE_SYSTEM = """You are the first-pass filter of a research digest. A stronger reviewer reads whatever you pass on,
so your job is recall: never drop something the reader might want.

Reader's interests:
{interests}

Not wanted:
{avoid}

Give every item a relevance score 0-10 for these interests, judging from title and snippet only.
9-10 squarely on-topic; 6-8 clearly related; 4-5 possibly related or unclear (give the benefit of the doubt);
0-3 clearly off-topic (other fields, unrelated applications, things in the not-wanted list).
Return exactly one entry per input id."""


def triage_view(it: dict) -> dict:
    text = it.get("abstract") or it.get("description") or ""
    d = {"id": it["id"], "type": it["kind"], "title": it["title"]}
    if it.get("pipeline"):
        d["pipeline"] = it["pipeline"]
    if text:
        d["snippet"] = text[:350]
    if it.get("topics"):
        d["topics"] = it["topics"][:6]
    return d


def signal_bonus(it: dict) -> float:
    """0..2 bonus from popularity signals, to break ties between equally relevant items."""
    if it["kind"] == "paper":
        raw = paper_prescore(it) / 15
    elif it["kind"] == "repo":
        raw = repo_prescore(it) / 20
    elif it["kind"] == "model":
        raw = math.log1p(it.get("trending", 0)) / 7
    else:
        raw = math.log1p(it.get("heat", it.get("points", 0))) / 7
    return max(0.0, min(raw, 1.0)) * 2


def triage(items: list[dict], profile: dict, scfg: dict) -> dict[str, int]:
    """Relevance 0-10 for every item from the cheap model. Missing ids are absent from the result."""
    system = TRIAGE_SYSTEM.format(interests=profile["interests"].strip(), avoid=profile["avoid"].strip())
    size = scfg.get("triage_batch", 100)
    batches = [items[i:i + size] for i in range(0, len(items), size)]

    def one(batch):
        payload = json.dumps([triage_view(i) for i in batch], ensure_ascii=False)
        try:
            out = llm.run(f"Rate all {len(batch)} items:\n{payload}", system=system, schema=TRIAGE_SCHEMA,
                          model=scfg.get("triage_model", "haiku"), timeout=600, label="triage")
            return {r["id"]: int(r["relevance"]) for r in out["items"]}
        except llm.LLMError as e:
            util.log(f"triage batch failed: {e}")
            return {}

    rel: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=scfg.get("parallel", 4)) as ex:
        for part in ex.map(one, batches):
            rel.update(part)
    valid = {i["id"] for i in items}
    return {k: max(0, min(v, 10)) for k, v in rel.items() if k in valid}


def mentions(it: dict, terms: list[str]) -> bool:
    """Case-insensitive word-prefix match of a core term in the title or the opening of the snippet.
    Topics and the rest of long abstracts are ignored: repo topics are often spammed and abstracts
    mention agents in passing."""
    if not terms:
        return False
    head = (it.get("abstract") or it.get("description") or "")[:200]
    text = f"{it.get('title', '')} {head}".lower().replace("-", " ")
    return any(re.search(rf"\b{re.escape(t.lower())}", text) for t in terms)


def shortlist(pools: dict[str, list[dict]], rel: dict[str, int], limits: dict[str, int],
              min_rel: int = 4, keep_terms: list[str] | None = None) -> list[dict]:
    """Per source: drop clearly off-topic, rank by triage relevance + signal bonus, keep the top N.
    Items the triage did not return are kept with a neutral score (recall over precision), and items that
    mention a core term are never dropped by triage alone (safety net against cheap-model misses)."""
    out = []
    for kind, items in pools.items():
        scored = []
        for it in items:
            r = rel.get(it["id"])
            if r is not None and r < min_rel and mentions(it, keep_terms or []):
                r = min_rel
            it["triage"] = r
            if r is not None and r < min_rel:
                continue
            scored.append((( r if r is not None else 5) + signal_bonus(it), it))
        scored.sort(key=lambda x: x[0], reverse=True)
        out += [it for _, it in scored[: limits.get(kind, 0)]]
    return out
