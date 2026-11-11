"""Shared helpers: clock, logging, HTTP."""
from __future__ import annotations

import datetime as dt
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

import certifi

UA = "research-digest/1.0"
SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_now: dt.datetime | None = None


def now() -> dt.datetime:
    """Run timestamp, fixed for the whole run (tests can set it via set_now)."""
    global _now
    if _now is None:
        _now = dt.datetime.now(dt.timezone.utc)
    return _now


def set_now(t: dt.datetime | None) -> None:
    global _now
    _now = t


def log(*a) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}]", *a, file=sys.stderr, flush=True)


def http_get(url: str, headers: dict | None = None, timeout: int = 40, tries: int = 3) -> bytes:
    h = {"User-Agent": UA, **(headers or {})}
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            limited = e.code == 429 or (e.code == 403 and "rate limit" in str(e.reason).lower())
            if (e.code < 500 and not limited) or i == tries - 1:
                raise
            wait = int(e.headers.get("Retry-After") or 0) if e.headers else 0
            log(f"retry {url[:80]}: HTTP {e.code}")
            if limited:
                time.sleep(min(max(wait, 20 * (i + 1)), 90))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1:
                raise
            log(f"retry {url[:80]}: {e}")
        time.sleep(5 * (i + 1))
    raise RuntimeError("unreachable")


def get_json(url: str, headers: dict | None = None, timeout: int = 40):
    return json.loads(http_get(url, headers, timeout))


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
