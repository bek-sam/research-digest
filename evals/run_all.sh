#!/bin/bash
# Runs every live eval (real Claude via your subscription, real APIs). ~15 min.
cd "$(dirname "$0")"
export GITHUB_TOKEN=${GITHUB_TOKEN:-$(gh auth token 2>/dev/null)}
fail=0
for e in triage_eval deep_eval stars_eval sources_eval profile_eval weekly_eval; do
  ../.venv/bin/python $e.py 2>/dev/null | grep -E "^== " || { echo "== $e: ERROR"; fail=1; }
  [ ${PIPESTATUS[0]} -eq 0 ] || fail=1
done
exit $fail
