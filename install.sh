#!/usr/bin/env bash
# Uma Trainer — setup script (steps 2–5)
# Covers: venv creation, dependency install, .env config, KB import

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()    { echo -e "${GREEN}▶${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
error()   { echo -e "${RED}✗${RESET}  $*" >&2; }
section() { echo -e "\n${BOLD}$*${RESET}"; }

# ── Require macOS + Python 3.11+ ─────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  error "This script targets macOS (Apple Silicon). Exiting."
  exit 1
fi

PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c 'import sys; print(sys.version_info[:2])')
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3.11+ not found. Install it with: brew install python@3.11"
  exit 1
fi
info "Using $($PYTHON --version)"

# ── Step 2: Virtual environment ───────────────────────────────────────────────
section "Step 2 — Creating virtual environment"

if [[ -d ".venv" ]]; then
  warn ".venv already exists — skipping creation"
else
  "$PYTHON" -m venv .venv
  info "Created .venv"
fi

# Activate for the rest of the script
# shellcheck disable=SC1091
source .venv/bin/activate
info "Activated .venv  (Python: $(python --version))"

# ── Step 3: Install dependencies ─────────────────────────────────────────────
section "Step 3 — Installing dependencies"

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
info "Core dependencies installed"

if [[ -f "requirements-dev.txt" ]]; then
  read -r -p "$(echo -e "${YELLOW}?${RESET}  Install dev dependencies (pytest, ruff, etc.)? [y/N] ")" install_dev
  if [[ "${install_dev,,}" == "y" ]]; then
    pip install --quiet -r requirements-dev.txt
    info "Dev dependencies installed"
  fi
fi

# ── Step 4: Configure .env ────────────────────────────────────────────────────
section "Step 4 — Configuring environment variables"

if [[ -f ".env" ]]; then
  warn ".env already exists — skipping creation"
  # Still offer to update the API key if it's still the placeholder
  if grep -q 'ANTHROPIC_API_KEY=sk-ant-\.\.\.' .env 2>/dev/null; then
    warn "ANTHROPIC_API_KEY in .env is still the placeholder value."
    read -r -p "$(echo -e "${YELLOW}?${RESET}  Enter your Anthropic API key (or press Enter to skip): ")" api_key
    if [[ -n "$api_key" ]]; then
      sed -i '' "s|ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${api_key}|" .env
      info "Updated ANTHROPIC_API_KEY in .env"
    fi
  fi
else
  cp .env.example .env
  info "Created .env from .env.example"

  echo ""
  echo "  The Claude API key enables Tier 3 decisions (high-value event analysis)."
  echo "  You can skip this and add the key later by editing .env."
  echo "  Get a key at: https://console.anthropic.com/"
  echo ""

  while true; do
    read -r -p "$(echo -e "${YELLOW}?${RESET}  Enter your Anthropic API key (or press Enter to skip): ")" api_key
    if [[ -z "$api_key" ]]; then
      warn "Skipped — Claude API (Tier 3) will be disabled until you add a key to .env"
      break
    elif [[ "$api_key" != sk-ant-* ]]; then
      warn "That doesn't look like a valid Anthropic key (should start with 'sk-ant-'). Try again, or press Enter to skip."
    else
      sed -i '' "s|ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${api_key}|" .env
      info "Saved ANTHROPIC_API_KEY to .env"
      break
    fi
  done
fi

# ── Step 5: Import knowledge base ─────────────────────────────────────────────
section "Step 5 — Importing knowledge base"

python main.py import-kb
info "Knowledge base imported"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✓ Setup complete!${RESET}"
echo ""
echo "  Start the bot:           source .venv/bin/activate && python main.py run"
echo "  Dashboard only:          python main.py dashboard   → http://127.0.0.1:8080"
echo "  Run headless:            python main.py run --headless"
echo ""
echo "  Next: set up MuMuPlayer + ADB (see README.md)"
echo ""
