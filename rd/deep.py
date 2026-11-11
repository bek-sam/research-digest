"""Improvement #1: deep review of the top candidates from full text (papers) or README + health (repos)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import enrich, llm, util

CLAIM = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "description": "a concrete claim with its key number, under 150 characters"},
        "support": {"type": "string", "enum": ["strong", "partial", "weak", "untested"]},
        "why": {"type": "string", "description": "the evidence in the text (or its absence), one sentence"},
    },
    "required": ["claim", "support", "why"],
    "additionalProperties": False,
}

PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2 plain sentences, under 350 characters: what they did and found"},
        "contribution": {"type": "string", "enum": ["new method", "benchmark/dataset", "empirical study",
                                                     "system/tool", "survey/position", "other"]},
        "claims": {"type": "array", "items": CLAIM, "description": "the 2-3 central claims, checked"},
        "reproducibility": {"type": "string", "enum": ["code+data released", "code released", "promised", "none", "unclear"]},
        "novelty": {"type": "integer", "description": "0-10"},
        "rigor": {"type": "integer", "description": "0-10: baselines, ablations, statistics, claim-evidence fit"},
        "usefulness": {"type": "integer", "description": "0-10 for this reader"},
        "quality": {"type": "integer", "description": "0-10 overall"},
        "verdict": {"type": "string", "enum": ["must-read", "worth skimming", "skip"]},
        "one_line": {"type": "string", "description": "short honest verdict, under 200 characters"},
        "concerns": {"type": "string", "description": "the 1-3 most important weaknesses as plain sentences (no markdown), under 280 characters, or empty"},
        "try_it": {"type": "string", "description": "one concrete next step for the reader, under 150 characters, or empty"},
    },
    "required": ["summary", "contribution", "claims", "reproducibility", "novelty", "rigor", "usefulness",
                 "quality", "verdict", "one_line", "concerns", "try_it"],
    "additionalProperties": False,
}

REPO_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2 plain sentences, under 300 characters: what it does and for whom"},
        "kind": {"type": "string", "enum": ["framework/library", "app/tool", "MCP server", "CLI",
                                            "template/boilerplate", "list/collection", "demo/toy", "thin wrapper", "other"]},
        "maturity": {"type": "string", "enum": ["production-ready", "usable", "early/experimental", "toy/abandoned"]},
        "docs": {"type": "integer", "description": "0-10 README clarity and completeness"},
        "engineering": {"type": "integer", "description": "0-10 from tests, CI, releases, activity, contributors"},
        "usefulness": {"type": "integer", "description": "0-10 for this reader"},
        "quality": {"type": "integer", "description": "0-10 overall"},
        "verdict": {"type": "string", "enum": ["must-try", "worth a look", "skip"]},
        "one_line": {"type": "string", "description": "short honest verdict, under 200 characters"},
        "concerns": {"type": "string", "description": "the 1-3 most important weaknesses as plain sentences (no markdown), under 280 characters, or empty"},
        "try_it": {"type": "string", "description": "the quickstart command from the README (one line), or empty"},
    },
    "required": ["summary", "kind", "maturity", "docs", "engineering", "usefulness", "quality", "verdict",
                 "one_line", "concerns", "try_it"],
    "additionalProperties": False,
}

PAPER_SYSTEM = """You are a rigorous, fair reviewer screening a new paper for one reader.

Reader's interests:
{interests}

Not wanted:
{avoid}

You get the paper's main sections (references and appendices removed; math as LaTeX). Review it like a strong
area chair, with this rubric:
1. Contribution: what is actually new? Classify it.
2. Claims: pick the 2-3 central claims (with their numbers) and check each against the evidence in the text.
   strong = direct experiments with baselines support it; partial = some support but narrow/missing controls;
   weak = evidence is thin, anecdotal or confounded; untested = asserted without evidence in the provided text.
3. Rigor: baselines (fair and current?), ablations, number of runs/variance, benchmark size, leakage risks.
   Score rigor only from evidence present in the provided text. Hard caps: no experiments/results section in the
   text -> rigor at most 3; any central claim rated untested -> rigor at most 4; no baselines -> at most 5.
4. Reproducibility: is code/data released or only promised?
5. Usefulness for this reader specifically.
Ignore self-promotional wording: confident prose, "state-of-the-art", "novel", "significant" are not evidence.
A claim with a number but no experiment behind it is untested. Be calibrated: most papers are "worth skimming";
reserve "must-read" for clearly strong and relevant work; "skip" for weak or off-topic work.
If text is marked unavailable, judge only what you have and keep scores moderate."""

REPO_SYSTEM = """You are a senior engineer screening a new open-source project for one reader.

Reader's interests:
{interests}

Not wanted:
{avoid}

You get the repo's metadata, measured health signals and README. Judge it with this rubric:
1. What it really is: a real framework/tool, or a thin wrapper, template, list, or demo?
2. Maturity: from the health signals (recent commits, number of commit authors, contributors, tests, CI,
   releases, OpenSSF Scorecard when present) and the README. One author, no tests, no releases = early.
3. Docs: can someone install and use it from the README?
4. Usefulness for this reader specifically. Star counts and hype words are not evidence of quality.
Lists/collections, templates/boilerplates, demos and thin wrappers are not tools: their quality is at most 4 and
the verdict is "skip", unless the reader's interests explicitly ask for that kind of resource. Anything in the
not-wanted list is "skip".
Give the actual quickstart command from the README in try_it when there is one (do not invent it)."""


def paper_prompt(it: dict, text: str | None) -> str:
    head = {k: it.get(k) for k in ("title", "authors", "org", "hf_upvotes", "code_url", "code_stars", "comment")
            if it.get(k)}
    body = text if text else f"[full text unavailable]\n\nAbstract: {it.get('abstract', '')}"
    return f"Paper metadata: {json.dumps(head, ensure_ascii=False)}\n\n{body}"


def repo_prompt(it: dict, health: dict, readme: str | None) -> str:
    head = {k: it.get(k) for k in ("title", "description", "stars", "stars_per_day", "forks", "open_issues",
                                   "language", "license", "created", "pushed", "topics", "star_check") if it.get(k)}
    return (f"Repo metadata: {json.dumps(head, ensure_ascii=False)}\n"
            f"Health signals: {json.dumps(health)}\n\nREADME:\n{readme or '[README unavailable]'}")


def review_paper(it: dict, profile: dict, model: str, effort: str) -> dict | None:
    text = enrich.paper_fulltext(it["arxiv_id"]) if it.get("arxiv_id") else None
    try:
        r = llm.run(paper_prompt(it, text), system=PAPER_SYSTEM.format(interests=profile["interests"].strip(), avoid=profile["avoid"].strip()),
                    schema=PAPER_SCHEMA, model=model, effort=effort, timeout=900, label=f"review {it['id']}")
    except llm.LLMError as e:
        util.log(f"deep review failed for {it['id']}: {e}")
        return None
    r["fulltext"] = bool(text)
    return r


def review_repo(it: dict, profile: dict, model: str, effort: str) -> dict | None:
    name = it["title"]
    health = enrich.repo_health(name)
    it["health"] = health
    try:
        r = llm.run(repo_prompt(it, health, enrich.readme(name)),
                    system=REPO_SYSTEM.format(interests=profile["interests"].strip(), avoid=profile["avoid"].strip()),
                    schema=REPO_SCHEMA, model=model, effort=effort, timeout=900, label=f"review {it['id']}")
    except llm.LLMError as e:
        util.log(f"deep review failed for {it['id']}: {e}")
        return None
    return r


def review_all(items: list[dict], profile: dict, dcfg: dict) -> dict[str, dict]:
    model, effort = dcfg.get("model", "opus"), dcfg.get("effort", "medium")

    def one(it):
        fn = review_paper if it["kind"] == "paper" else review_repo
        return it["id"], fn(it, profile, model, effort)

    out = {}
    with ThreadPoolExecutor(max_workers=dcfg.get("parallel", 3)) as ex:
        for iid, r in ex.map(one, items):
            if r:
                out[iid] = r
    util.log(f"deep review: {len(out)}/{len(items)} done")
    return out


def merge(score: dict, review: dict) -> dict:
    """Deep review overrides the stage-2 quality/summary/verdict; relevance stays from stage 2."""
    s = dict(score)
    s["quality"] = int(review["quality"])
    s["summary"] = review["summary"]
    s["verdict"] = review["one_line"]
    s["red_flags"] = review["concerns"]
    s["deep"] = review
    return s


def is_skip(review: dict) -> bool:
    return review["verdict"] == "skip"
