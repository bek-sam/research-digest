"""Shared loader for live evals: real fixture items, real claude calls (subscription)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import FIX, FIXED_NOW, load_cfg  # noqa: E402
from rd import util  # noqa: E402
from rd.sources import arxiv, github, hf  # noqa: E402

util.set_now(FIXED_NOW)


def fixture_items() -> list[dict]:
    papers = arxiv.parse((FIX / "arxiv.xml").read_bytes(), 5)
    hf.merge(papers, json.loads((FIX / "hf_daily.json").read_text()), "2026-11-09")
    repos = [github.to_item(r, "new") for r in json.loads((FIX / "github_search.json").read_text())["items"]]
    return list(papers.values()) + repos


def report(name: str, checks: list[tuple[str, bool, str]]) -> bool:
    ok = all(c[1] for c in checks)
    print(f"\n== {name}: {'PASS' if ok else 'FAIL'} ({sum(c[1] for c in checks)}/{len(checks)})")
    for label, passed, detail in checks:
        print(f"  {'✓' if passed else '✗'} {label}  {detail}")
    return ok
