# research-digest

Daily Telegram digest of AI-agent / LLM-tool papers, repos, models and discussions — screened, reviewed and
ranked by Claude through the local `claude -p` CLI (your Claude subscription, no API key).

## Pipeline
1. **Collect** — arXiv (categories × keywords), Hugging Face Daily Papers (upvotes, code links, orgs),
   GitHub Search (new + established repos, star growth from daily snapshots), GitHub Trending, Hacker News,
   Reddit (one multireddit RSS feed), Lobsters, Hugging Face trending models.
2. **Link** (`rd/link.py`) — a paper and its repo become one item; the same story across HN/Reddit/Lobsters is
   merged; a discussion about a paper or repo becomes a 💬 signal on it.
3. **Triage** (`rd/screen.py`) — Haiku rates *every* item for relevance (recall-first, keyword safety net),
   then a shortlist per source.
4. **Star check** (`rd/stars.py`) — forks and issues vs stars flag bought-star patterns (calibrated on
   StarScout data; stargazer lists are closed to outsiders since July 2026).
5. **Score** — Opus scores the shortlist (relevance, quality, summary, verdict, red flags).
6. **Deep review** (`rd/deep.py`) — for the top papers: full text from arXiv HTML, rubric with each central claim
   checked against the evidence; for the top repos: README + health signals (commits, contributors, tests, CI,
   releases, OpenSSF Scorecard). "Skip" verdicts are dropped.
7. **Send** — sections with 👍/👎 buttons; history saved for the weekly report, extras for `/more`.

Also: **learned profile** (`rd/profile.py`, `profile_learned.md`, editable) refreshed from votes;
**weekly trend report** on Sundays (`rd/weekly.py`); **bot** (`bot.py`) with instant votes and
`/why p3`, `/more repos`, `/ask …`, `/interests`, `/like …`, `/avoid …`, `/run`, `/weekly`.

## Run (Mac)
Lives in `~/research-digest` (Desktop has a shortcut): macOS blocks launchd jobs from reading ~/Desktop.
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
# secrets/digest.env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (GITHUB_TOKEN comes from `gh auth token`)
.venv/bin/python digest.py --dry-run           # print instead of sending
./run.sh                                       # real run
./run_bot.sh                                   # bot listener
deploy/install_launchd.sh                      # schedule: daily 07:00 + always-on bot
.venv/bin/python -m pytest tests               # offline tests (fake Claude/web/Telegram)
evals/run_all.sh                               # live evals (real Claude + APIs)
```
Edit `config.toml` for interests and sources; `config.local.toml` overrides it per machine.

## Run on the Oracle VM (optional)
`deploy/vm/deploy.sh user@host` ships git HEAD to the VM and installs venv, Claude Code and systemd units
(timer 07:00 Asia/Bishkek, bot service, memory-capped, one claude process at a time via
`deploy/vm/config.local.toml`). Then on the VM: put `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token` on the
Mac), `TELEGRAM_*` into `secrets/digest.env`, copy `state.db` + `profile_learned.md` over, unload the Mac launchd
jobs, and `sudo systemctl enable --now research-digest.timer research-digest-bot.service`.
Run only one place at a time (two bot listeners conflict, two timers double-send).
