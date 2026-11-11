"""Claude via the local Claude Code CLI (`claude -p`), so calls use the Claude subscription."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import util

ROOT = Path(__file__).resolve().parent.parent


class LLMError(RuntimeError):
    pass


def claude_bin() -> str:
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def run(prompt: str, *, system: str, schema: dict, model: str = "opus", effort: str | None = None,
        tools: str = "", timeout: int = 1200, tries: int = 2, label: str = "claude") -> dict:
    """One headless Claude call returning schema-validated JSON (`structured_output`)."""
    cmd = [claude_bin(), "-p", "--output-format", "json", "--model", model,
           "--tools", tools, "--strict-mcp-config", "--setting-sources", "",
           "--no-session-persistence", "--system-prompt", system,
           "--json-schema", json.dumps(schema)]
    if tools:
        cmd += ["--allowedTools", tools]
    if effort:
        cmd += ["--effort", effort]
    # API-key env vars would switch billing away from the subscription
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    err = ""
    for attempt in range(tries):
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  timeout=timeout, cwd=ROOT, env=env)
            res = json.loads(proc.stdout)
        except subprocess.TimeoutExpired:
            res = {"is_error": True, "result": f"timeout after {timeout}s"}
        except json.JSONDecodeError:
            res = {"is_error": True, "result": (proc.stderr or proc.stdout)[-500:]}
        out = res.get("structured_output")
        if not res.get("is_error") and isinstance(out, dict):
            u = res.get("usage") or {}
            util.log(f"{label} [{model}]: out={u.get('output_tokens')} tokens, "
                     f"{(res.get('duration_ms') or 0) // 1000}s")
            return out
        err = str(res.get("result"))[:300]
        util.log(f"{label} attempt {attempt + 1} failed: {err}")
        if attempt < tries - 1:
            time.sleep(20)
    raise LLMError(f"{label} failed: {err}")
