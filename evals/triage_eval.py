"""Live eval for stage-1 triage: recall on hand-labelled relevant items must be 100%."""
from common import fixture_items, load_cfg, report

from rd import screen

RELEVANT = """arxiv:2611.12872 arxiv:2611.12856 arxiv:2611.12799 arxiv:2611.12393 arxiv:2611.12300
arxiv:2611.12412 arxiv:2611.09017 arxiv:2611.07624 arxiv:2610.35959 arxiv:2611.09113 arxiv:2611.10404
arxiv:2611.07461 arxiv:2611.09891 arxiv:2611.12552 arxiv:2611.12386 arxiv:2611.12705
gh:josssphzhou/poki gh:shanselman/sandbox-mcp gh:kkkkhazix/agent-autoloop gh:sparkofspike/brain4agent
gh:sshah03/recalc gh:minimax-ai/mcode-agent-table gh:wanshuiyin/superaris gh:gmpetrov/agent-screenplay
gh:pktq/lasik""".split()
IRRELEVANT = """arxiv:2611.12816 arxiv:2611.12775 arxiv:2611.12635 arxiv:2611.12407 arxiv:2611.06043
arxiv:2611.08264 arxiv:2610.36896 arxiv:2611.09055 arxiv:2611.07723 arxiv:2611.01729 arxiv:2611.12420
gh:21stware/bowel gh:yorkeccak/globalhackmap gh:princesswonder5879/tower-events-resp-at-sunrise
arxiv:2611.06856""".split()


def main():
    cfg = load_cfg()
    items = fixture_items()
    ids = {i["id"] for i in items}
    assert set(RELEVANT + IRRELEVANT) <= ids, set(RELEVANT + IRRELEVANT) - ids
    rel = screen.triage(items, cfg["profile"], cfg["screen"])
    sc = cfg["screen"]
    kept = {i["id"] for i in screen.shortlist({"all": items}, rel, {"all": 10**6},
                                               sc["triage_min"], sc["keep_terms"])}
    missed = [i for i in RELEVANT if i not in kept]
    leaked = [i for i in IRRELEVANT if i in kept]
    reject_rate = 1 - len(leaked) / len(IRRELEVANT)
    dropped_total = len(items) - len(kept)
    ok = report("triage", [
        ("coverage: every item rated", len(rel) == len(items), f"{len(rel)}/{len(items)}"),
        ("recall on relevant = 100%", not missed, f"missed={[(m, rel.get(m)) for m in missed]}"),
        ("rejects >= 80% of off-topic", reject_rate >= 0.8, f"{reject_rate:.0%} leaked={[(l, rel.get(l)) for l in leaked]}"),
        ("actually filters (drops >= 25% overall)", dropped_total >= len(items) * 0.25, f"{dropped_total}/{len(items)} dropped"),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
