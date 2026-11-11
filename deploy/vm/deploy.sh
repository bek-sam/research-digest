#!/usr/bin/env bash
# Runs on the Mac: ships the committed code (git HEAD, no secrets/state) to the VM and installs it.
# Usage: VM_SSH_KEY=~/.ssh/<key> deploy/vm/deploy.sh ubuntu@<vm-ip>
set -euo pipefail
cd "$(dirname "$0")/../.."
HOST=${1:?usage: deploy.sh user@host}
KEY=${VM_SSH_KEY:-$HOME/.ssh/id_ed25519}
git archive --format=tar.gz -o /tmp/research-digest.tgz HEAD
scp -i "$KEY" -q /tmp/research-digest.tgz "$HOST":/tmp/
ssh -i "$KEY" "$HOST" 'mkdir -p ~/research-digest && tar xzf /tmp/research-digest.tgz -C ~/research-digest && ~/research-digest/deploy/vm/install.sh'
