"""Live eval for the weekly report: planted trends across two weeks must be detected."""
import datetime as dt
import tempfile
from pathlib import Path

from common import fixture_items, load_cfg, report

from rd import weekly
from rd.store import Store

W45 = {"mcp": ["gh:gogoymh/auto-ontology-mcp", "gh:devcrabs-inc/yandex-metrika-mcp"],
       "world": ["arxiv:2611.12557", "arxiv:2611.06748", "arxiv:2611.12324", "arxiv:2611.09124"],
       "misc": ["arxiv:2610.35959", "arxiv:2611.09113", "arxiv:2611.10404", "arxiv:2611.09894"]}
W46 = {"mcp": ["gh:josssphzhou/poki", "gh:shanselman/sandbox-mcp", "gh:wendersoon/radar-cnpj-mcp",
               "gh:njohansen/handle-mcp", "gh:mazulo/leis-mcp", "gh:exzade/exza-remote", "gh:pktq/lasik"],
       "memory": ["arxiv:2611.12872", "gh:sparkofspike/brain4agent", "arxiv:2611.12412"],
       "misc": ["arxiv:2611.12856", "arxiv:2611.12393", "arxiv:2611.12552", "arxiv:2611.12300"]}


def theme_of(rep, iid):
    return next((t["name"] for t in rep["themes"] if iid in t["item_ids"]), None)


def purity(rep, ids):
    names = [theme_of(rep, i) for i in ids]
    best = max(set(names), key=names.count)
    return (names.count(best) / len(ids), best)


def main():
    cfg = load_cfg()
    items = {i["id"]: i for i in fixture_items()}
    store = Store(Path(tempfile.mkdtemp()) / "w.db")
    for week, day in ((W45, "2026-11-05"), (W46, "2026-11-12")):
        for ids in week.values():
            for iid in ids:
                it = items[iid]
                summary = (it.get("abstract") or it.get("description") or "")[:200]
                store.record_seen(it, {"summary": summary, "relevance": 7, "quality": 6}, day)
    store.commit()
    r45 = weekly.build(store, cfg["profile"], today=dt.date(2026, 11, 8))
    r46 = weekly.build(store, cfg["profile"], today=dt.date(2026, 11, 15))
    print("W45:", [(t["name"], t["count"]) for t in r45["themes"]])
    print("W46:", [(t["name"], t["count"], t["status"]) for t in r46["themes"]], "gone:", [g["name"] for g in r46["gone"]])
    p_mcp, mcp_name = purity(r46, W46["mcp"])
    p_mem, mem_name = purity(r46, W46["memory"])
    p_world, world_name = purity(r45, W45["world"])
    status = {t["name"]: t["status"] for t in r46["themes"]}
    all_ids = {i for t in r46["themes"] for i in t["item_ids"]}
    valid = all_ids <= {i for ids in W46.values() for i in ids}
    html = weekly.render(r46)
    ok = report("weekly trends", [
        ("ids grounded in this week's items", valid, ""),
        ("coverage >= 70%", r46["coverage"] >= 0.7, f"{r46['coverage']:.0%}"),
        ("MCP items grouped together (>= 70%)", bool(p_mcp >= 0.7 and mcp_name), f"{p_mcp:.0%} in {mcp_name!r}"),
        ("MCP theme marked rising (name carried over)", status.get(mcp_name) == "rising", f"{mcp_name!r}: {status.get(mcp_name)}"),
        ("memory items grouped (>= 2/3)", bool(p_mem >= 0.66 and mem_name), f"{p_mem:.0%} in {mem_name!r}"),
        ("memory theme marked new", status.get(mem_name) == "new", f"{mem_name!r}: {status.get(mem_name)}"),
        ("world models grouped last week", bool(p_world >= 0.75 and world_name), f"{p_world:.0%} in {world_name!r}"),
        ("world models reported fading", world_name in [g["name"] for g in r46["gone"]], ""),
        ("report fits one Telegram message", len(html) < 4096, f"{len(html)} chars"),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
