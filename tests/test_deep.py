"""Improvement #1: deep review (full text / README + health)."""
import base64
import json

from conftest import FIX
from rd import deep, enrich, llm, pipeline, render

HTML = (FIX / "arxiv_paper.html").read_text()


def test_extract_sections_real_paper():
    secs = dict(enrich.extract_sections(HTML))
    assert {"Abstract", "Introduction", "Experiments and Evaluation", "Conclusion", "Limitations"} <= set(secs)
    assert "References" not in secs and not any(k.startswith("Appendix") for k in secs)
    assert "Report GitHub Issue" not in " ".join(secs.values()), "site chrome removed"
    assert "0.27" in secs["Experiments and Evaluation"], "numbers in math kept as LaTeX"


def test_select_text_budget_and_priority():
    txt = enrich.select_text(enrich.extract_sections(HTML))
    assert len(txt) <= enrich.PAPER_BUDGET + 500
    heads = [l for l in txt.splitlines() if l.startswith("## ")]
    assert heads[0] == "## Abstract" and "## Experiments and Evaluation" in heads and "## Limitations" in heads
    small = enrich.select_text(enrich.extract_sections(HTML), budget=5000)
    assert len(small) < 5600 and "## Abstract" in small


def test_select_text_unusual_headings_fallback():
    out = enrich.select_text([("Foo", "x" * 1000), ("Bar", "y" * 1000)], budget=1500)
    assert out.startswith("## Foo") and len(out) < 1600


def test_paper_fulltext_unavailable(web):
    assert enrich.paper_fulltext("2611.00000") is None  # 404 from fake web


def test_repo_health_signals(web):
    base = "api.github.com/repos/a/b"
    web.add(f"{base}/commits", [{"author": {"login": "u1"}, "commit": {"author": {"email": "x"}}},
                                {"author": {"login": "u2"}, "commit": {"author": {"email": "y"}}}])
    web.add(f"{base}/contributors", [{"login": "u1"}, {"login": "u2"}, {"login": "u3"}])
    web.add(f"{base}/contents/", [{"name": "tests"}, {"name": ".github"}, {"name": "pyproject.toml"}, {"name": "README.md"}])
    web.add(f"{base}/releases", [{"tag_name": "v1"}])
    web.add("api.scorecard.dev/projects/github.com/a/b", {"score": 6.1})
    h = enrich.repo_health("a/b")
    assert h == {"commits_30d": 2, "commit_authors_30d": 2, "contributors": 3, "has_tests": True, "has_ci": True,
                 "manifests": ["pyproject.toml"], "root_files": 4, "releases": 1, "openssf_scorecard": 6.1}


def test_repo_health_tolerates_failures(web):
    assert enrich.repo_health("x/y") == {}


def test_readme_strips_images(web):
    md = "# T\n![badge](https://x/y.svg)\n<img src='a.png'>\nUse `pip install t`\n\n\n\n\nEnd"
    web.add("api.github.com/repos/a/b/readme", {"content": base64.b64encode(md.encode()).decode()})
    r = enrich.readme("a/b")
    assert "badge" not in r and "img" not in r and "pip install t" in r and "\n\n\n" not in r


def test_paper_prompt_marks_missing_text():
    p = deep.paper_prompt({"title": "T", "abstract": "A"}, None)
    assert "[full text unavailable]" in p and "Abstract: A" in p


def test_review_uses_schema_effort_and_model(claude):
    r = llm.run("x", system="s", schema=deep.PAPER_SCHEMA, model="opus", effort="medium")
    argv = claude()[0]["argv"]
    assert argv[argv.index("--effort") + 1] == "medium" and set(deep.PAPER_SCHEMA["required"]) <= set(r)


def mkpair(i, kind="paper", rel=8, q=6):
    it = {"id": f"{kind}:{i}", "kind": kind, "title": f"T{i}", "url": "u", "arxiv_id": str(i),
          "hf_upvotes": 0, "has_code": False, "stars": 10, "bucket": "new", "created": "2026-11-01"}
    return it, {"relevance": rel, "quality": q, "summary": "s", "verdict": "v", "red_flags": ""}


def test_finalize_drops_skips_keeps_failures_and_reranks(cfg, monkeypatch):
    pairs = [mkpair(i) for i in range(6)]
    by_id = {it["id"]: it for it, _ in pairs}
    scores = {it["id"]: s for it, s in pairs}
    rev = {"paper:0": {"verdict": "skip", "quality": 2, "summary": "x", "one_line": "weak", "concerns": "c"},
           "paper:1": {"verdict": "must-read", "quality": 10, "summary": "great", "one_line": "strong", "concerns": ""}}
    monkeypatch.setattr(deep, "review_all", lambda items, p, d: {k: v for k, v in rev.items()})
    out = pipeline.finalize(cfg, scores, by_id, "paper", 3)
    ids = [it["id"] for it, _ in out]
    assert "paper:0" not in ids, "skip verdict dropped"
    assert ids[0] == "paper:1" and out[0][1]["summary"] == "great", "deep result merged and re-ranked"
    assert len(out) == 3, "failed/unreviewed items keep their stage-2 judgement"


def test_finalize_news_not_reviewed(cfg, monkeypatch):
    called = []
    monkeypatch.setattr(deep, "review_all", lambda *a: called.append(1) or {})
    it, s = mkpair(1, "news")
    pipeline.finalize(cfg, {it["id"]: s}, {it["id"]: it}, "news", 3)
    assert not called


def test_render_deep_paper_and_repo():
    it, s = mkpair(1)
    d = {"verdict": "must-read", "contribution": "benchmark/dataset", "novelty": 7, "rigor": 8,
         "reproducibility": "code released", "claims": [{"claim": "DR 0.43-0.61", "support": "strong", "why": "t"},
                                                       {"claim": "fix works", "support": "untested", "why": "n"}],
         "try_it": "run the bench", "fulltext": True}
    out = render.item_html(1, it, deep.merge(s, dict(d, quality=8, summary="S", one_line="L", concerns="")))
    assert "🔥" in out and "✅ DR 0.43-0.61" in out and "❔ fix works" in out and "rigor 8" in out
    repo, rs = mkpair(2, "repo")
    repo["health"] = {"commits_30d": "100+", "contributors": 8, "has_tests": True, "has_ci": True}
    rd_ = {"verdict": "worth a look", "kind": "MCP server", "maturity": "usable", "try_it": "npx poki <x>"}
    out = render.item_html(1, repo, deep.merge(rs, dict(rd_, quality=6, summary="S", one_line="L", concerns="")))
    assert "MCP server · usable · 100+ commits/30d · 8 contributors · tests ✓ · CI ✓" in out
    assert "<code>npx poki &lt;x&gt;</code>" in out


def test_end_to_end_with_deep_review(store, web, tg, claude, cfg):
    cfg["github"]["topics"] = ["mcp"]
    web.add("arxiv.org/html/", HTML)
    pipeline.run(cfg, store)
    labels = [c["argv"][c["argv"].index("--system-prompt") + 1][:30] for c in claude()]
    assert any(l.startswith("You are a rigorous") for l in labels), "paper review ran"
    assert any(l.startswith("You are a senior engineer") for l in labels), "repo review ran"
    assert any("rigor" in m["text"] for m in tg.sent)


def test_paper_with_code_keeps_star_rating():
    """Regression: the code-stars label used to overwrite the ★ rating."""
    it = {"id": "arxiv:1", "kind": "paper", "title": "p", "url": "u", "code_url": "https://github.com/a/b", "code_stars": 50}
    s = {"relevance": 9, "quality": 6, "summary": "s", "verdict": "v", "red_flags": ""}
    line = render.item_html(1, it, s).splitlines()[1]
    assert line.startswith("★★★★  rel 9/10") and "code</a> ⭐50" in line


def test_render_caps_and_strips_markdown():
    it, s = mkpair(1)
    d = {"verdict": "worth skimming", "contribution": "new method", "novelty": 5, "rigor": 5, "reproducibility": "none",
         "claims": [{"claim": "x" * 400, "support": "partial", "why": "w"}], "try_it": "", "fulltext": True}
    long_concerns = "- **No variance:** each ran once. " * 30
    out = render.item_html(1, it, deep.merge(s, dict(d, quality=5, summary="word " * 300, one_line="ok", concerns=long_concerns)))
    assert "**" not in out and "- " not in out.split("⚠️")[1][:5]
    assert len(out) < 1600, len(out)
    assert "…" in out


def test_http_retries_github_secondary_rate_limit(monkeypatch):
    import io
    import urllib.error
    from rd import util
    calls = []

    def fake_open(req, timeout, context):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 403, "rate limit exceeded", {}, io.BytesIO(b""))
        return io.BytesIO(b"ok")
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert util.http_get("https://api.github.com/x") == b"ok" and len(calls) == 2


def test_http_403_forbidden_not_retried(monkeypatch):
    import io
    import urllib.error
    import pytest as _p
    from rd import util

    def fake_open(req, timeout, context):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    with _p.raises(urllib.error.HTTPError):
        util.http_get("https://x/y")
