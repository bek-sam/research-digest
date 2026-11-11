"""Improvement #8: a short natural-language profile learned from 👍/👎 votes.

Follows the scrutable-recommendation idea (Radlinski et al. 2022; Ramos et al., ACL 2024): preferences live in
readable, editable text instead of hidden weights. The learned profile is saved to profile_learned.md; edits
there are respected (the file is the source of truth) and the reader's own config interests always come first."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import llm, util

ROOT = Path(__file__).resolve().parent.parent
PROFILE_FILE = ROOT / "profile_learned.md"
HEADER = ("<!-- Learned from your 👍/👎 votes. Edit freely: the digest reads this file, and the next update starts\n"
          "     from your edited version. Delete everything below to reset. -->\n")
MAX_CHARS = 900

SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "the updated learned profile, at most ~120 words"},
        "changes": {"type": "array", "items": {"type": "string"},
                    "description": "what changed vs the previous profile, short phrases with + or - prefix"},
    },
    "required": ["profile", "changes"],
    "additionalProperties": False,
}

SYSTEM = """You maintain a short profile of what one reader wants from a daily AI research digest, learned from
their 👍/👎 votes. The reader also wrote their own interests (below); those always take priority, so the learned
profile only adds what the votes reveal: finer preferences inside those interests (which kinds of items, topics,
styles, quality bars), and what they turn out not to want.

Reader's own interests:
{interests}

Reader's own not-wanted list:
{avoid}

Rules: update the previous profile incrementally (keep what still holds, change what new votes contradict, add
what they newly show); generalize from patterns of several votes, not from one vote; never contradict the reader's
own interests; be concrete (e.g. "prefers runnable MCP servers over MCP lists"); two short parts:
"Leans toward: ..." and "Less interested in: ..."; at most ~120 words. If votes show nothing new, return the
previous profile unchanged with no changes."""


def read() -> str:
    if not PROFILE_FILE.exists():
        return ""
    text = PROFILE_FILE.read_text()
    if text.startswith("<!--"):
        text = text.split("-->", 1)[-1]
    return text.strip()[:MAX_CHARS]


def write(text: str) -> None:
    PROFILE_FILE.write_text(HEADER + "\n" + text.strip() + "\n")


def votes_since(store, since: str | None) -> list[tuple]:
    q = "SELECT title, kind, summary, vote, ts FROM feedback"
    args: tuple = ()
    if since:
        q += " WHERE ts > ?"
        args = (since,)
    return store.execute(q + " ORDER BY ts", args).fetchall()


def due(store, pcfg: dict) -> bool:
    last = store.kv_get("profile_updated")
    new = votes_since(store, last)
    if len(new) >= pcfg.get("min_new_votes", 5):
        return True
    if new and last:
        age = util.now() - dt.datetime.fromisoformat(last)
        return age.days >= pcfg.get("max_days", 7)
    return False


def vote_lines(rows: list[tuple]) -> str:
    out = []
    for title, kind, summary, vote, _ in rows[-60:]:
        mark = "👍" if vote > 0 else "👎"
        out.append(f"{mark} [{kind or '?'}] {title}" + (f" — {summary[:160]}" if summary else ""))
    return "\n".join(out)


def update(store, profile_cfg: dict, base_profile: dict) -> list[str] | None:
    """Refresh the learned profile from votes. Returns the list of changes, or None if not run."""
    rows = votes_since(store, None)
    if not rows:
        return None
    prev = read()
    prompt = (f"Previous learned profile:\n{prev or '(none yet)'}\n\n"
              f"All votes so far (oldest first):\n{vote_lines(rows)}")
    out = llm.run(prompt, system=SYSTEM.format(interests=base_profile["interests"].strip(),
                                               avoid=base_profile["avoid"].strip()),
                  schema=SCHEMA, model=profile_cfg.get("model", "opus"), effort="medium", label="profile")
    text = out["profile"].strip()[:MAX_CHARS]
    if text and text != prev:
        write(text)
    store.kv_set("profile_updated", util.now().isoformat())
    store.commit()
    return out["changes"] if text != prev else []


def effective(base_profile: dict) -> dict:
    """The profile every prompt uses: the reader's own interests plus the learned part."""
    learned = read()
    if not learned:
        return base_profile
    return {**base_profile,
            "interests": base_profile["interests"].strip()
            + "\n\nLearned from the reader's votes (the reader's own interests above take priority):\n" + learned}
