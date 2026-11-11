import datetime as dt
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

from rd import telegram, util  # noqa: E402
from rd.store import Store  # noqa: E402

# Fixtures were recorded 2026-11-10; pin the clock so date windows match them.
FIXED_NOW = dt.datetime(2026, 11, 10, 19, 0, tzinfo=dt.timezone.utc)


def load_cfg():
    import tomllib
    return tomllib.loads((ROOT / "config.toml").read_text())


class FakeWeb:
    """Routes util.http_get by URL substring to fixture files or callables."""

    def __init__(self):
        self.routes: list[tuple[str, object]] = []
        self.calls: list[str] = []

    def add(self, needle, body):
        self.routes.insert(0, (needle, body))

    def get(self, url, headers=None, timeout=40, tries=3):
        self.calls.append(url)
        for needle, body in self.routes:
            if needle in url:
                if callable(body):
                    body = body(url)
                if isinstance(body, Exception):
                    raise body
                if isinstance(body, Path):
                    return body.read_bytes()
                if isinstance(body, (dict, list)):
                    return json.dumps(body).encode()
                return body if isinstance(body, bytes) else str(body).encode()
        raise urllib.error.HTTPError(url, 404, "no fixture", {}, None)


class FakeTelegram:
    def __init__(self):
        self.sent: list[dict] = []
        self.updates: list[dict] = []

    def call(self, method, **params):
        if method == "sendMessage":
            assert len(params["text"]) <= 4096, "telegram message too long"
            self.sent.append(params)
            return {"message_id": len(self.sent)}
        if method == "getUpdates":
            off = params.get("offset", 0)
            return [u for u in self.updates if u["update_id"] >= off]
        return True


@pytest.fixture(autouse=True)
def isolated_profile(monkeypatch, tmp_path):
    from rd import profile
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile_learned.md")


@pytest.fixture(autouse=True)
def clock():
    util.set_now(FIXED_NOW)
    yield
    util.set_now(None)


@pytest.fixture
def web(monkeypatch):
    w = FakeWeb()
    w.add("export.arxiv.org", FIX / "arxiv.xml")
    w.add("huggingface.co/api/daily_papers", FIX / "hf_daily.json")
    w.add("api.github.com/search/repositories", FIX / "github_search.json")
    w.add("hn.algolia.com", FIX / "hn.json")
    w.add("reddit.com/r/", FIX / "reddit.rss")
    w.add("lobste.rs/t/", FIX / "lobsters.json")
    w.add("github.com/trending", FIX / "gh_trending.html")
    w.add("huggingface.co/api/models", FIX / "hf_models.json")
    monkeypatch.setattr(util, "http_get", w.get)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return w


@pytest.fixture
def tg(monkeypatch):
    t = FakeTelegram()
    monkeypatch.setattr(telegram, "call", t.call)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    return t


@pytest.fixture
def claude(monkeypatch, tmp_path):
    log = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CLAUDE_BIN", str(Path(__file__).parent / "fake_claude.py"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return calls


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.db")


@pytest.fixture
def cfg():
    return load_cfg()
