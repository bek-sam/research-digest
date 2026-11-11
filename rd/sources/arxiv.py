"""arXiv API: newest papers matching categories AND keywords."""
from __future__ import annotations

import datetime as dt
import urllib.parse
import xml.etree.ElementTree as ET

from .. import util

ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def build_query(cfg) -> str:
    cats = " OR ".join(f"cat:{c}" for c in cfg["categories"])
    kws = " OR ".join(f'abs:"{k}"' if " " in k else f"abs:{k}" for k in cfg["keywords"])
    return f"({cats}) AND ({kws})"


def parse(xml_bytes: bytes, days: int = 3) -> dict[str, dict]:
    root = ET.fromstring(xml_bytes)
    cutoff = util.now() - dt.timedelta(days=days)
    out = {}
    for e in root.findall("a:entry", ATOM):
        published = util.parse_ts(e.findtext("a:published", "", ATOM))
        if published < cutoff:
            continue
        aid = e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1].split("v")[0]
        abstract = " ".join(e.findtext("a:summary", "", ATOM).split())
        authors = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        cat = e.find("arxiv:primary_category", ATOM)
        out[aid] = {
            "id": f"arxiv:{aid}", "kind": "paper", "arxiv_id": aid,
            "title": " ".join(e.findtext("a:title", "", ATOM).split()),
            "url": f"https://arxiv.org/abs/{aid}",
            "abstract": abstract[:1500],
            "authors": authors[:8], "n_authors": len(authors),
            "category": cat.get("term") if cat is not None else "",
            "published": published.date().isoformat(),
            "has_code": "github.com" in abstract.lower(),
            "comment": (e.findtext("arxiv:comment", "", ATOM) or "")[:200],
            "hf_upvotes": 0,
        }
    return out


def fetch(cfg, days: int = 3) -> dict[str, dict]:
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": build_query(cfg), "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": cfg["max_results"]})
    out = parse(util.http_get(url, timeout=90), days)
    util.log(f"arxiv: {len(out)} recent papers")
    return out
