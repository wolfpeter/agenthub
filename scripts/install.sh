#!/usr/bin/env bash
# AgentHub telepítő (SPECIFICATION 3.1). Használat:
#   curl -fsSL https://raw.githubusercontent.com/wolfpeter/agenthub/main/scripts/install.sh | bash
# Változók: AGENTHUB_HOME (alap: ~/AgentHub), AGENTHUB_REPO, AGENTHUB_PORT (alap: 8790), AGENTHUB_BIND
set -euo pipefail

AGENTHUB_HOME="${AGENTHUB_HOME:-$HOME/AgentHub}"
AGENTHUB_REPO="${AGENTHUB_REPO:-https://github.com/wolfpeter/agenthub.git}"
AGENTHUB_PORT="${AGENTHUB_PORT:-8790}"
AGENTHUB_BIND="${AGENTHUB_BIND:-0.0.0.0}"
export PATH="$HOME/.local/bin:$PATH"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf 'HIBA: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || fail "ne root-ként futtasd; a saját felhasználóddal (sudo-t a szkript kér, ha kell)"

say "Előfeltételek ellenőrzése"
need_apt=()
command -v git >/dev/null || need_apt+=(git)
command -v curl >/dev/null || need_apt+=(curl)
command -v docker >/dev/null || need_apt+=(docker.io)
docker compose version >/dev/null 2>&1 || need_apt+=(docker-compose-v2)
if [ ${#need_apt[@]} -gt 0 ]; then
  command -v apt-get >/dev/null || fail "hiányzik: ${need_apt[*]} — telepítsd kézzel (a szkript Ubuntura készült)"
  say "Telepítés: ${need_apt[*]} (sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq "${need_apt[@]}"
fi
if ! docker info >/dev/null 2>&1; then
  sudo usermod -aG docker "$USER"
  fail "a felhasználód most került a docker csoportba. Jelentkezz ki és be (vagy: newgrp docker), majd futtasd újra a telepítőt."
fi
if ! command -v uv >/dev/null; then
  say "uv telepítése"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
command -v claude >/dev/null || say "Figyelem: a Claude Code CLI (claude) nincs telepítve; az agentekhez kell: https://docs.claude.com/claude-code"

if [ -f "$AGENTHUB_HOME/.env" ]; then
  say "Meglévő telepítés: frissítés (mentéssel)"
  command -v agenthub >/dev/null || uv tool install --force "$AGENTHUB_HOME" >/dev/null
  exec agenthub update
fi

if [ -d "$AGENTHUB_HOME/.git" ]; then
  say "Repository frissítése: $AGENTHUB_HOME"
  git -C "$AGENTHUB_HOME" pull --ff-only
else
  [ ! -e "$AGENTHUB_HOME" ] || [ -z "$(ls -A "$AGENTHUB_HOME")" ] || fail "$AGENTHUB_HOME létezik és nem üres"
  say "Letöltés: $AGENTHUB_REPO → $AGENTHUB_HOME"
  git clone --depth 50 "$AGENTHUB_REPO" "$AGENTHUB_HOME"
fi

say "agenthub parancssor telepítése"
uv tool install --force "$AGENTHUB_HOME" >/dev/null
export AGENTHUB_HOME

say "Konfiguráció és kulcsok"
agenthub init --port "$AGENTHUB_PORT" --bind "$AGENTHUB_BIND"

say "Szolgáltatások indítása (Docker Compose: API + Dashboard + PostgreSQL)"
agenthub up

say "Claude Code skillek (/coordinator, /worker)"
agenthub skills install || say "A skilleket nem telepítettem, mert már van ilyen nevű skill. Lásd fent; felülírás mentéssel: agenthub skills install --force"

if [ "$AGENTHUB_HOME" != "$HOME/AgentHub" ]; then
  say "Nem alapértelmezett hely: tedd a shell-profilodba: export AGENTHUB_HOME=$AGENTHUB_HOME"
fi
agenthub welcome
