"""Live eval for deep review: catches unsupported claims, stays grounded, separates solid repos from lists."""
import re
from concurrent.futures import ThreadPoolExecutor

from common import FIX, load_cfg, report

from rd import deep, enrich, llm, util
from rd.sources import github

FAKE = ("Our mitigation method, DriftGuard, eliminates authorization drift entirely, achieving 99.8% "
        "compliance on all benchmarks while improving task success by 40%.")


def review_text(item, text, cfg):
    return llm.run(deep.paper_prompt(item, text), system=deep.PAPER_SYSTEM.format(interests=cfg["profile"]["interests"], avoid=cfg["profile"]["avoid"]),
                   schema=deep.PAPER_SCHEMA, model=cfg["deep"]["model"], effort=cfg["deep"]["effort"], label="eval-review")


def nums(s):
    return re.findall(r"\d+(?:\.\d+)?", s)


def main():
    cfg = load_cfg()
    secs = enrich.extract_sections((FIX / "arxiv_paper.html").read_text())
    original = enrich.select_text(secs)
    tampered_secs = [(t, x) for t, x in secs if not re.search(r"experiment|discussion|result", t, re.I)]
    tampered_secs = [(t, (x + " " + FAKE) if t == "Abstract" else x) for t, x in tampered_secs]
    tampered = enrich.select_text(tampered_secs)
    paper = {"title": "Authorization Drift in LLM Agents", "arxiv_id": "2611.09113"}

    def repo_item(name):
        r = util.get_json(f"https://api.github.com/repos/{name}", github.headers())
        return github.to_item(r, "established")

    with ThreadPoolExecutor(4) as ex:
        f_orig = ex.submit(review_text, paper, original, cfg)
        f_tamp = ex.submit(review_text, paper, tampered, cfg)
        good, lst = repo_item("langchain-ai/langgraph"), repo_item("punkpeye/awesome-mcp-servers")
        f_good = ex.submit(deep.review_repo, good, cfg["profile"], cfg["deep"]["model"], cfg["deep"]["effort"])
        f_list = ex.submit(deep.review_repo, lst, cfg["profile"], cfg["deep"]["model"], cfg["deep"]["effort"])
        o, t, g, l = f_orig.result(), f_tamp.result(), f_good.result(), f_list.result()

    fake_claims = [c for c in t["claims"] if re.search(r"driftguard|99\.8", c["claim"], re.I)]
    claim_nums = [n for c in o["claims"] for n in nums(c["claim"])]
    grounded = [n for n in claim_nums if n in original or n.lstrip("0") in original]
    ok = report("deep review", [
        ("A1 tampered: fake claim extracted", bool(fake_claims), str([c["claim"][:60] for c in t["claims"]])),
        ("A2 tampered: fake claim rated weak/untested", bool(fake_claims) and all(c["support"] in ("weak", "untested") for c in fake_claims),
         str([(c["support"], c["why"][:80]) for c in fake_claims])),
        ("A3 tampered rigor < original rigor", t["rigor"] < o["rigor"], f"orig={o['rigor']} tampered={t['rigor']}"),
        ("A4 original: >=1 claim strong/partial", any(c["support"] in ("strong", "partial") for c in o["claims"]),
         str([(c["support"], c["claim"][:50]) for c in o["claims"]])),
        ("B  claim numbers grounded in text (>=90%)", not claim_nums or len(grounded) / len(claim_nums) >= 0.9,
         f"{len(grounded)}/{len(claim_nums)}"),
        ("C1 langgraph: mature + quality>=7", g["maturity"] in ("production-ready", "usable") and g["quality"] >= 7,
         f"{g['kind']} {g['maturity']} q={g['quality']} try={g['try_it'][:50]!r}"),
        ("C2 awesome list: classified as list", l["kind"] == "list/collection", l["kind"]),
        ("C3 list scored well below framework", l["quality"] <= g["quality"] - 3 or l["verdict"] == "skip",
         f"list q={l['quality']} verdict={l['verdict']}"),
        ("C4 quickstart grounded (not invented)", "pip install" in g["try_it"] or "langgraph" in g["try_it"].lower(), g["try_it"][:80]),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
