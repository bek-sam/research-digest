"""Reddit via RSS (the JSON API now returns 403 to anonymous clients). Rank in 'top of the day' is the signal."""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET

from .. import util

ATOM = {"a": "http://www.w3.org/2005/Atom"}


def text_of(content_html: str) -> tuple[str, str | None]:
    """(plain text, outbound link) from the entry's HTML content."""
    raw = html.unescape(content_html or "")
    links = [u for u in re.findall(r'href="([^"]+)"', raw) if "reddit.com" not in u and "redd.it" not in u]
    plain = re.sub(r"<[^>]+>", " ", raw)
    plain = re.sub(r"\s+", " ", plain.replace("submitted by", "").replace("[link] [comments]", "")).strip()
    return plain, (links[0] if links else None)


def parse(xml_bytes: bytes, sub: str | None = None) -> list[dict]:
    """Entries of a (multi-)subreddit top feed; the subreddit comes from each entry's category."""
    root = ET.fromstring(xml_bytes)
    out = []
    for rank, e in enumerate(root.findall("a:entry", ATOM), 1):
        cat = e.find("a:category", ATOM)
        entry_sub = cat.get("term") if cat is not None and cat.get("term") else sub
        link = e.find("a:link", ATOM)
        url = link.get("href") if link is not None else ""
        m = re.search(r"/comments/(\w+)/", url)
        if not m:
            continue
        plain, outbound = text_of(e.findtext("a:content", "", ATOM))
        out.append({
            "id": f"reddit:{m.group(1)}", "kind": "news", "source": f"r/{entry_sub}",
            "title": html.unescape(e.findtext("a:title", "", ATOM)).strip(),
            "url": outbound or url, "discussion_url": url, "rank": rank,
            "description": plain[:500],
        })
    return out


def fetch(cfg) -> list[dict]:
    """One request for all subreddits (a multireddit feed): Reddit rate-limits anonymous RSS hard,
    and a combined feed ranks posts across subs by their actual votes."""
    multi = "+".join(cfg["subreddits"])
    url = f"https://www.reddit.com/r/{multi}/top/.rss?t=day&limit={cfg.get('limit', 100)}"
    for attempt in range(3):
        try:
            out = parse(util.http_get(url, tries=2))
            util.log(f"reddit: {len(out)} posts")
            return out
        except Exception as e:
            util.log(f"reddit attempt {attempt + 1}: {e}")
            time.sleep(30 * (attempt + 1))
    return []
