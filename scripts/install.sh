#!/usr/bin/env bash
# One-time setup on Ubuntu 22.04/24.04.
set -euo pipefail
cd "$(dirname "$0")/.."
# Broken third-party apt repos (old Coral, speedtest) fail update; the Ubuntu lists still refresh.
sudo apt-get update || true
sudo apt-get install -y python3-venv libreoffice-writer-nogui fonts-liberation curl
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
# --with-deps runs apt-get update too; if a broken repo stops it, fetch Chromium alone.
playwright install --with-deps chromium || playwright install chromium
[ -f config.yaml ]  || cp config.example.yaml config.yaml
[ -f profile.yaml ] || cp profile.example.yaml profile.yaml
mkdir -p data materials/context materials/applications
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null || { curl -fsSL https://claude.ai/install.sh | bash; }
echo
echo "Next: edit profile.yaml and config.yaml, copy your materials into materials/,"
echo "run 'claude' once to sign in, then 'pytest' to check the filler works."
