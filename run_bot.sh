#!/bin/bash
# launchd entry point for the always-on bot listener.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
set -a; . secrets/digest.env; set +a
[ -z "$GITHUB_TOKEN" ] && GITHUB_TOKEN=$(gh auth token 2>/dev/null) && export GITHUB_TOKEN
exec .venv/bin/python bot.py >> logs/bot.log 2>&1
