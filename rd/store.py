"""SQLite state: sent items, star history, votes, digest button map, key/value."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, title TEXT, kind TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS stars (repo TEXT, day TEXT, stars INTEGER, PRIMARY KEY (repo, day));
CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, title TEXT, vote INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS digest_items (msg_key TEXT PRIMARY KEY, id TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS seen (id TEXT, day TEXT, kind TEXT, title TEXT, url TEXT, summary TEXT,
                                 relevance INTEGER, quality INTEGER, sent INTEGER DEFAULT 0, PRIMARY KEY (id, day));
CREATE TABLE IF NOT EXISTS extras (day TEXT, kind TEXT, rank INTEGER, id TEXT, data TEXT, shown INTEGER DEFAULT 0,
                                   PRIMARY KEY (day, kind, rank));
CREATE TABLE IF NOT EXISTS messages (message_id INTEGER PRIMARY KEY, day TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS themes (week TEXT, name TEXT, description TEXT, item_ids TEXT, weight REAL,
                                   PRIMARY KEY (week, name));
"""


MIGRATIONS = [  # (table, column, type) added after the first release
    ("digest_items", "kind", "TEXT"),
    ("digest_items", "summary", "TEXT"),
    ("feedback", "kind", "TEXT"),
    ("feedback", "summary", "TEXT"),
    ("digest_items", "day", "TEXT"),
    ("digest_items", "pos", "INTEGER"),
    ("digest_items", "data", "TEXT"),
]


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")  # the bot listener and the daily run share this file
        self.db.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    def execute(self, *a):
        return self.db.execute(*a)

    def commit(self):
        self.db.commit()

    def kv_get(self, k, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def kv_set(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, str(v)))

    def already_sent(self, item_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM sent WHERE id=?", (item_id,)).fetchone() is not None

    def record_seen(self, item: dict, score: dict, day: str, sent: bool = False):
        self.db.execute("INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
                        (item["id"], day, item["kind"], item["title"], item.get("url", ""), score.get("summary", ""),
                         int(score.get("relevance", 0)), int(score.get("quality", 0)), int(sent)))

    def mark_sent(self, item: dict, ts: str):
        self.db.execute("INSERT OR IGNORE INTO sent VALUES (?,?,?,?)",
                        (item["id"], item["title"], item["kind"], ts))
        for lid in item.get("linked_ids", []):  # its merged repo must not come back alone tomorrow
            self.db.execute("INSERT OR IGNORE INTO sent VALUES (?,?,?,?)", (lid, item["title"], "linked", ts))
