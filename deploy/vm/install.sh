#!/usr/bin/env bash
# Runs ON the VM from ~/research-digest. Idempotent. Does not enable the timer/bot (see deploy/vm/README.md).
set -euo pipefail
cd "$(dirname "$0")/../.."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv >/dev/null
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt pytest
mkdir -p secrets logs
[ -f config.local.toml ] || cp deploy/vm/config.local.toml config.local.toml
if [ ! -f secrets/digest.env ]; then
  printf 'CLAUDE_CODE_OAUTH_TOKEN=\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\nGITHUB_TOKEN=\n' > secrets/digest.env
  chmod 600 secrets/digest.env
fi
command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ] || curl -fsSL https://claude.ai/install.sh | bash
sudo cp deploy/vm/research-digest.service deploy/vm/research-digest.timer deploy/vm/research-digest-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "installed. claude: $($HOME/.local/bin/claude --version 2>/dev/null || claude --version)"
