"""github.com/trending (HTML): repos with the most stars today, any age."""
from __future__ import annotations

import html
import re

from .. import util


def parse(page: str) -> list[dict]:
    out = []
    for a in re.findall(r'<article class="Box-row">(.*?)</article>', page, re.S):
        m = re.search(r'<h2[^>]*>.*?href="/([^"/]+/[^"/]+)"', a, re.S)
        if not m:
            continue
        name = m.group(1)
        desc = re.search(r'<p class="[^"]*col-9[^"]*">(.*?)</p>', a, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', a)
        total = re.search(r'href="/[^"]+/stargazers"[^>]*>.*?([\d,]+)\s*</a>', a, re.S)
        forks = re.search(r'href="/[^"]+/forks"[^>]*>.*?([\d,]+)\s*</a>', a, re.S)
        today = re.search(r"([\d,]+) stars? today", a)
        num = lambda x: int(x.group(1).replace(",", "")) if x else 0  # noqa: E731
        out.append({
            "id": f"gh:{name.lower()}", "kind": "repo", "bucket": "trending", "title": name,
            "url": f"https://github.com/{name}",
            "description": html.unescape(re.sub(r"<[^>]+>|\s+", " ", desc.group(1))).strip()[:400] if desc else "",
            "language": lang.group(1).strip() if lang else None,
            "stars": num(total), "forks": num(forks), "stars_today": num(today),
            "stars_per_day": float(num(today)), "license": None, "topics": [], "homepage": "",
            "created": "", "pushed": "", "archived": False, "fork": False, "open_issues": 0,
        })
    return out


def fetch(cfg) -> list[dict]:
    out = {}
    for lang in cfg.get("languages", [""]):
        url = f"https://github.com/trending/{lang}?since=daily" if lang else "https://github.com/trending?since=daily"
        try:
            for r in parse(util.http_get(url, {"Accept": "text/html"}).decode("utf-8", "replace")):
                out.setdefault(r["id"], r)
        except Exception as e:
            util.log(f"github trending {lang or 'all'}: {e}")
    util.log(f"github trending: {len(out)} repos")
    return list(out.values())
