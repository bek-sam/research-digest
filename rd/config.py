"""Config loading: config.toml, then an optional machine-specific config.local.toml merged on top (#9)."""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: Path | str | None = None, local: Path | str | None = None) -> dict:
    path = Path(path or ROOT / "config.toml")
    cfg = tomllib.loads(path.read_text())
    local = Path(local) if local else path.with_name("config.local.toml")
    if local.exists():
        cfg = merge(cfg, tomllib.loads(local.read_text()))
    return cfg
