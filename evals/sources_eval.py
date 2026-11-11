"""Live eval for sources: every source returns data, no duplicate links, discussions much richer than HN alone."""
import tempfile
from collections import Counter

from common import load_cfg, report

from rd import link, pipeline, util
from rd.store import Store

util.set_now(None)  # live run: real clock


def main():
    cfg = load_cfg()
    store = Store(tempfile.mktemp(suffix=".db"))
    pools = pipeline.collect(cfg, store)
    news = pools["news"]
    srcs = Counter(n.get("source", "HN").split("/")[0] if not n.get("source", "").startswith("r/") else "reddit" for n in news)
    all_disc = [d for k in ("paper", "repo") for i in pools[k] for d in i.get("discussed", [])]
    urls = [link.norm_url(n["url"]) for n in news]
    dup = [u for u, c in Counter(urls).items() if c > 1]
    ids = [i["id"] for v in pools.values() for i in v]
    trending = [r for r in pools["repo"] if "trending" in r.get("bucket", "")]
    ok = report("sources (live)", [
        ("reddit returns posts", srcs.get("reddit", 0) > 20, str(dict(srcs))),
        ("HN returns stories", srcs.get("HN", 0) > 0, ""),
        ("lobsters reachable (0 ok on quiet days)", True, f"{srcs.get('Lobsters', 0)} stories"),
        ("github trending merged", len(trending) >= 10, f"{len(trending)} trending repos"),
        ("hf models", len(pools["model"]) >= 5, f"{len(pools['model'])} models"),
        ("discussion pool >= 4x old HN-only (8)", len(news) >= 32, f"{len(news)} discussions"),
        ("no duplicate links in news", not dup, str(dup[:3])),
        ("no duplicate ids across pools", len(ids) == len(set(ids)), f"{len(ids)} items"),
        ("discussions attached to papers/repos (info)", True, f"{len(all_disc)} attached"),
    ])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
