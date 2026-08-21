#!/usr/bin/env bash
# SafeRoom installer — Linux (Debian/Ubuntu) first
set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq git python3 docker.io
  sudo usermod -aG docker "$USER" || true
elif [[ "$(uname)" == "Darwin" ]]; then
  command -v docker >/dev/null || echo "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
fi
SRC="$(cd "$(dirname "$0")" && pwd)/saferoom.py"
sudo install -m 0755 "$SRC" /usr/local/bin/saferoom
echo "SafeRoom installed. Try: cd your-project && saferoom init"
