#!/bin/bash
# Daily entry point (launchd). Loads secrets and runs the digest.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
set -a; . secrets/digest.env; set +a
[ -z "$GITHUB_TOKEN" ] && GITHUB_TOKEN=$(gh auth token 2>/dev/null) && export GITHUB_TOKEN
exec .venv/bin/python digest.py "$@" >> logs/digest.log 2>&1
