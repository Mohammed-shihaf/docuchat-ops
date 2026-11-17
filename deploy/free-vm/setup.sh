#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VM (Oracle Always Free, or any VPS). Run as a sudo user:
#   curl -fsSL https://raw.githubusercontent.com/Mohammed-shihaf/docuchat-ops/main/deploy/free-vm/setup.sh | bash
set -euo pipefail

if ! command -v docker >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Oracle images ship with iptables rules that block 80/443 even after opening the security list.
if sudo iptables -L INPUT -n 2>/dev/null | grep -q "REJECT"; then
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo sh -c 'command -v netfilter-persistent >/dev/null && netfilter-persistent save || true'
fi

mkdir -p ~/docuchat && cd ~/docuchat
base=https://raw.githubusercontent.com/Mohammed-shihaf/docuchat-ops/main/deploy/free-vm
for f in docker-compose.yml Caddyfile .env.example; do curl -fsSL "$base/$f" -o "$f"; done
[ -f .env ] || cp .env.example .env

# Free auto-updates: pull the newest image from GHCR nightly (no CD server to pay for).
( crontab -l 2>/dev/null | grep -v docuchat; echo "0 3 * * * cd ~/docuchat && docker compose pull -q && docker compose up -d # docuchat" ) | crontab -

echo
echo "Next: edit ~/docuchat/.env (DOMAIN + LLM settings), then:"
echo "  cd ~/docuchat && sudo docker compose up -d"
