#!/usr/bin/env bash
# SafeRoom installer — Linux (Debian/Ubuntu) first
set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
  pkgs=()
  command -v git     >/dev/null || pkgs+=(git)
  command -v python3 >/dev/null || pkgs+=(python3)
  # Never touch Docker if any Docker is already present (Docker CE, Desktop,
  # snap, ...). Installing docker.io over another Docker install conflicts with
  # the running daemon and can stop it.
  if command -v docker >/dev/null; then
    echo "Docker already installed ($(command -v docker)) — skipping docker.io"
  else
    pkgs+=(docker.io)
  fi
  if ((${#pkgs[@]})); then
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${pkgs[@]}"
  fi
  # Add the real user (not root, when run via sudo) to the docker group.
  u="${SUDO_USER:-${USER:-$(id -un)}}"
  if getent group docker >/dev/null && [[ "$u" != root ]]; then
    sudo usermod -aG docker "$u" || true
  fi
elif [[ "$(uname)" == "Darwin" ]]; then
  command -v docker >/dev/null || echo "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
fi
SRC="$(cd "$(dirname "$0")" && pwd)/saferoom.py"
sudo install -m 0755 "$SRC" /usr/local/bin/saferoom
echo "SafeRoom installed. Try: cd your-project && saferoom init"
