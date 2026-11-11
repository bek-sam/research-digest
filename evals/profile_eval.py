"""Live eval for vote learning: the learned profile captures the pattern, shifts scores the right way, and
the reader's edits take effect."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import fixture_items, load_cfg, report

from rd import profile, screen, util
from rd.store import Store

LIKES = [("gh:l1", "browserbase/mcp-server", "repo", "MCP server that gives agents a real browser; npx install, works with Claude Code"),
         ("gh:l2", "github/github-mcp-server", "repo", "Official GitHub MCP server: issues, PRs, code search for agents"),
         ("gh:l3", "openai/codex", "repo", "Terminal coding agent you can run locally with sandboxed execution"),
         ("gh:l4", "e2b-dev/e2b", "repo", "Sandboxed cloud VMs for agents to run code safely, SDK in Python/JS"),
         ("gh:l5", "microsoft/playwright-mcp", "repo", "Playwright MCP server for browser automation by LLM agents"),
         ("gh:l6", "mem0ai/mem0", "repo", "Memory layer for agents, drop-in library with self-hosted option")]
DISLIKES = [("arxiv:d1", "AgentBench-Pro: A Benchmark for LLM Agents in 12 Environments", "paper", "New benchmark, no new method"),
            ("arxiv:d2", "ToolEval-X: Evaluating Tool Use Across 40 APIs", "paper", "Benchmark of tool calling accuracy"),
            ("arxiv:d3", "MARBLE: A Multi-Agent Reasoning Benchmark", "paper", "Benchmark suite for multi-agent reasoning"),
            ("arxiv:d4", "WebArena-Hard: Harder Web Tasks for Agents", "paper", "Harder benchmark tasks for web agents"),
            ("arxiv:d5", "CodeAgentBench: Measuring Repository-Level Coding Agents", "paper", "Benchmark for coding agents"),
            ("arxiv:d6", "SafeToolBench: Safety Evaluation of Tool-Using Agents", "paper", "Safety benchmark for tool agents")]
TOOLS = ["gh:josssphzhou/poki", "gh:shanselman/sandbox-mcp", "gh:sshah03/recalc", "gh:pktq/lasik",
         "gh:minimax-ai/mcode-agent-table", "gh:exzade/exza-remote"]
BENCH = ["arxiv:2611.12856", "arxiv:2611.09017", "arxiv:2611.12393", "arxiv:2611.12557", "arxiv:2611.12640", "arxiv:2611.08175"]


def mean(xs):
    return sum(xs) / len(xs)


def main():
    cfg = load_cfg()
    tmp = Path(tempfile.mkdtemp())
    profile.PROFILE_FILE = tmp / "profile_learned.md"
    store = Store(tmp / "s.db")
    for i, (iid, title, kind, summ) in enumerate(LIKES + DISLIKES):
        store.execute("INSERT INTO feedback (id, title, vote, ts, kind, summary) VALUES (?,?,?,?,?,?)",
                      (iid, title, 1 if iid.startswith("gh:") else -1, f"2026-11-0{1 + i % 9}T10:00:00", kind, summ))
    store.commit()
    changes = profile.update(store, cfg["learning"], cfg["profile"])
    learned = profile.read()

    items = {i["id"]: i for i in fixture_items()}
    held = [items[i] for i in TOOLS + BENCH]
    edited = ("Leans toward: research papers that introduce new benchmarks and evaluations.\n"
              "Less interested in: MCP servers and agent tool repos.")
    empty = Store(tmp / "empty.db")  # no vote examples, so only the profile text differs between runs

    def run(prof):
        return screen.score(held, prof, empty, cfg["digest"]["model"])

    with ThreadPoolExecutor(3) as ex:
        f_base = ex.submit(run, cfg["profile"])
        f_learn = ex.submit(run, profile.effective(cfg["profile"]))
        profile_edit = {**cfg["profile"], "interests": cfg["profile"]["interests"] + "\n\nLearned from the reader's votes:\n" + edited}
        f_edit = ex.submit(run, profile_edit)
        base, learn, edit = f_base.result(), f_learn.result(), f_edit.result()

    def avg(sc, ids):
        return mean([sc[i]["relevance"] for i in ids if i in sc])
    for i in TOOLS + BENCH:
        print(f"  {i:38} base={base.get(i, {}).get('relevance')} learned={learn.get(i, {}).get('relevance')} edited={edit.get(i, {}).get('relevance')}")
    low = learned.lower()
    d_tools, d_bench = avg(learn, TOOLS) - avg(base, TOOLS), avg(learn, BENCH) - avg(base, BENCH)
    ok = report("vote learning", [
        ("profile captures likes (MCP/tools)", "mcp" in low or "tool" in low, learned.replace("\n", " | ")[:300]),
        ("profile captures dislikes (benchmarks)", "benchmark" in low, ""),
        ("profile bounded", len(learned) <= profile.MAX_CHARS, f"{len(learned)} chars"),
        ("changes reported", bool(changes), str(changes)[:200]),
        # the base interests already target agent tools, so liked-kind items start near the top: the learned
        # effect shows up as the gap (below); here they only must not fall beyond scoring noise
        ("liked-kind relevance holds (>= -0.25)", d_tools >= -0.25, f"{avg(base, TOOLS):.2f} -> {avg(learn, TOOLS):.2f}"),
        ("benchmark relevance falls with learned profile", d_bench < 0, f"{avg(base, BENCH):.2f} -> {avg(learn, BENCH):.2f}"),
        ("gap widens by >= 1 point", (d_tools - d_bench) >= 1, f"{d_tools - d_bench:+.2f}"),
        ("reader's edit reverses it (bench > tools)", avg(edit, BENCH) > avg(edit, TOOLS),
         f"edited: tools {avg(edit, TOOLS):.2f}, bench {avg(edit, BENCH):.2f}"),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
