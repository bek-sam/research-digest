"""Improvement #1 inputs: paper full text (arXiv HTML) and repo README + health signals (GitHub API)."""
from __future__ import annotations

import base64
import datetime as dt
import re
from html.parser import HTMLParser

from . import util
from .sources import github

SKIP_SECTIONS = re.compile(r"reference|bibliograph|acknowledg|appendix|supplementary|author contribution|ethics statement|checklist", re.I)
PRIORITY = [  # (pattern, max chars) - sections kept in this order of importance
    (re.compile(r"abstract", re.I), 2500),
    (re.compile(r"introduction", re.I), 5000),
    (re.compile(r"experiment|evaluation|result|benchmark|empirical", re.I), 9000),
    (re.compile(r"method|approach|framework|system|design|architecture|model", re.I), 5000),
    (re.compile(r"limitation|discussion|threat", re.I), 2500),
    (re.compile(r"conclusion|summary", re.I), 2000),
]
PAPER_BUDGET = 28000  # chars ~ 7k tokens


VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source", "embed"}


class _ArxivHTML(HTMLParser):
    """Collects (heading, text) sections from arXiv's LaTeXML HTML.
    Content starts at the document title (site chrome before it is ignored); h2 / abstract titles
    start sections, deeper headings stay inline; figures' captions are kept, math becomes its LaTeX."""

    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "button", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.started = False
        self.sections: list[list[str]] = []
        self.depth = 0
        self.skip_at: int | None = None
        self.head_at: int | None = None
        self.head_buf = ""
        self.head_major = False

    def _cur(self) -> list[str]:
        if not self.sections:
            self.sections.append(["Front matter", ""])
        return self.sections[-1]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in VOID:
            return
        self.depth += 1
        if self.skip_at is not None:
            return
        if "ltx_title_document" in cls:
            self.started = True
        if not self.started:
            return
        if tag in self.SKIP_TAGS or "ltx_bibliography" in cls or "ltx_role_footnote" in cls:
            self.skip_at = self.depth
        elif tag == "math":
            self._cur()[1] += f" {a.get('alttext', '')} "
            self.skip_at = self.depth
        elif "ltx_title" in cls and tag in ("h2", "h3", "h4", "h5", "h6") and "ltx_title_document" not in cls:
            self.head_at = self.depth
            self.head_buf = ""
            self.head_major = tag == "h2" or "ltx_title_abstract" in cls

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.skip_at is not None and self.depth == self.skip_at:
            self.skip_at = None
        elif self.head_at is not None and self.depth == self.head_at:
            title = " ".join(self.head_buf.split())
            if self.head_major:
                title = re.sub(r"^(Appendix\s+)?[\dA-Z]{1,2}(\.\d+)*\s+", "", title) if not title.startswith("Appendix") else title
                self.sections.append([title, ""])
            else:
                self._cur()[1] += f"\n### {title}\n"
            self.head_at = None
        self.depth -= 1

    def handle_data(self, data):
        if not self.started or self.skip_at is not None:
            return
        if self.head_at is not None:
            self.head_buf += data
        else:
            self._cur()[1] += data


def extract_sections(html: str) -> list[tuple[str, str]]:
    p = _ArxivHTML()
    p.feed(html)
    out = []
    for title, text in p.sections:
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        if len(text) > 80 and not SKIP_SECTIONS.search(title):
            out.append((title, text))
    return out


def select_text(sections: list[tuple[str, str]], budget: int = PAPER_BUDGET) -> str:
    """Keep the most informative sections within the budget, in document order."""
    chosen: dict[int, str] = {}
    used = 0
    for pat, cap in PRIORITY:
        for idx, (title, text) in enumerate(sections):
            if idx in chosen or not pat.search(title):
                continue
            take = text[: min(cap, budget - used)]
            if len(take) < 200:
                break
            chosen[idx] = take
            used += len(take)
    if not chosen:  # unusual headings: take from the start
        for idx, (title, text) in enumerate(sections):
            take = text[: budget - used]
            if len(take) < 200:
                break
            chosen[idx] = take
            used += len(take)
    return "\n\n".join(f"## {sections[i][0]}\n{t}" for i, t in sorted(chosen.items()))


def paper_fulltext(arxiv_id: str) -> str | None:
    try:
        html = util.http_get(f"https://arxiv.org/html/{arxiv_id}", timeout=60, tries=2).decode("utf-8", "replace")
    except Exception as e:
        util.log(f"fulltext {arxiv_id}: {e}")
        return None
    text = select_text(extract_sections(html))
    return text or None


def readme(name: str, limit: int = 12000) -> str | None:
    try:
        r = util.get_json(f"https://api.github.com/repos/{name}/readme", github.headers())
        text = base64.b64decode(r.get("content", "")).decode("utf-8", "replace")
    except Exception as e:
        util.log(f"readme {name}: {e}")
        return None
    text = re.sub(r"<img[^>]*>|!\[[^\]]*\]\([^)]*\)", "", text)  # images/badges carry no information
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


TEST_DIRS = {"test", "tests", "__tests__", "spec", "testing", "e2e"}
MANIFESTS = {"pyproject.toml", "setup.py", "package.json", "cargo.toml", "go.mod", "pom.xml",
             "build.gradle", "gemfile", "requirements.txt", "dockerfile", "makefile"}


def repo_health(name: str) -> dict:
    """Concrete maintenance signals (each lookup is optional; failures leave the field out)."""
    h = github.headers()
    base = f"https://api.github.com/repos/{name}"
    out: dict = {}
    since = (util.now() - dt.timedelta(days=30)).isoformat().replace("+00:00", "Z")

    def get(url):
        try:
            return util.get_json(url, h)
        except Exception:
            return None

    commits = get(f"{base}/commits?since={since}&per_page=100")
    if isinstance(commits, list):
        out["commits_30d"] = len(commits) if len(commits) < 100 else "100+"
        authors = {(c.get("author") or {}).get("login") or c["commit"]["author"].get("email") for c in commits}
        out["commit_authors_30d"] = len(authors)
    contrib = get(f"{base}/contributors?per_page=100&anon=1")
    if isinstance(contrib, list):
        out["contributors"] = len(contrib) if len(contrib) < 100 else "100+"
    root = get(f"{base}/contents/")
    if isinstance(root, list):
        names = {e["name"].lower() for e in root}
        out["has_tests"] = bool(names & TEST_DIRS) or any(n.startswith("test") for n in names)
        out["has_ci"] = ".github" in names or ".gitlab-ci.yml" in names or ".circleci" in names
        out["manifests"] = sorted(names & MANIFESTS)
        out["root_files"] = len(names)
    rel = get(f"{base}/releases?per_page=10")
    if isinstance(rel, list):
        out["releases"] = len(rel)
    sc = get(f"https://api.scorecard.dev/projects/github.com/{name}")
    if isinstance(sc, dict) and sc.get("score") is not None:
        out["openssf_scorecard"] = sc["score"]
    return out
