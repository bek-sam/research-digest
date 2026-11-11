"""Hugging Face trending models (open-weight models relevant to building agents)."""
from __future__ import annotations

from .. import util


def to_item(m: dict) -> dict:
    return {
        "id": f"hfmodel:{m['id'].lower()}", "kind": "model", "title": m["id"],
        "url": f"https://huggingface.co/{m['id']}", "pipeline": m.get("pipeline_tag"),
        "likes": m.get("likes", 0), "trending": m.get("trendingScore", 0), "downloads": m.get("downloads", 0),
        "library": m.get("library_name"), "created": (m.get("createdAt") or "")[:10],
        "topics": [t for t in m.get("tags", []) if ":" not in t][:12],
    }


def filter_models(models: list[dict], cfg) -> list[dict]:
    allowed = set(cfg.get("pipelines", []))
    out = []
    for m in models:
        if allowed and m.get("pipeline_tag") not in allowed:
            continue
        if m.get("likes", 0) < cfg.get("min_likes", 50):
            continue
        out.append(to_item(m))
    return out


def fetch(cfg) -> list[dict]:
    try:
        models = util.get_json(f"https://huggingface.co/api/models?sort=trendingScore&limit={cfg.get('limit', 60)}")
    except Exception as e:
        util.log(f"hf models: {e}")
        return []
    out = filter_models(models, cfg)
    util.log(f"hf models: {len(out)} trending")
    return out
