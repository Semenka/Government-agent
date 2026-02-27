#!/usr/bin/env bash
#
# Government Agent — Mac Mini setup script
#
# Prerequisites:
#   - macOS with Homebrew (or Linux)
#   - Node.js >= 22  (for OpenClaw)
#   - Python >= 3.11
#   - OpenClaw installed and WhatsApp linked
#
# Usage:
#   chmod +x setup.sh && ./setup.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  Government Agent — Mac Mini Setup"
echo "========================================"
echo

# ── 1. Check Python ──────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install it:"
    echo "  brew install python@3.12"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PY_VERSION"

# ── 2. Check Node.js (for OpenClaw) ─────────────────────────────────────────

if ! command -v node &>/dev/null; then
    echo "WARNING: Node.js not found. OpenClaw requires Node.js >= 22."
    echo "  brew install node@22"
fi

# ── 3. Create virtual environment ───────────────────────────────────────────

if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

# ── 4. Install Python dependencies ──────────────────────────────────────────

echo "Installing Python dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── 5. Create .env if missing ───────────────────────────────────────────────

if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo
    echo "IMPORTANT: Edit .env and fill in your API keys:"
    echo "  nano .env"
    echo
    echo "Required:"
    echo "  - GEMINI_API_KEY (or ANTHROPIC_API_KEY)"
    echo "  - WHATSAPP_TO (your phone number, e.g. +15555550123)"
    echo
fi

# ── 6. Initialize the database ──────────────────────────────────────────────

echo "Initializing database..."
python3 -c "from data.storage import init_db; init_db(); print('Database ready.')"

# ── 7. Check OpenClaw installation ──────────────────────────────────────────

OPENCLAW_SKILLS_DIR="$HOME/.openclaw/skills"
SKILL_LINK="$OPENCLAW_SKILLS_DIR/governance-agent"

if [ -d "$HOME/.openclaw" ]; then
    echo "OpenClaw detected at ~/.openclaw"

    # Register as an OpenClaw skill
    mkdir -p "$OPENCLAW_SKILLS_DIR"
    if [ ! -L "$SKILL_LINK" ] && [ ! -d "$SKILL_LINK" ]; then
        ln -s "$SCRIPT_DIR" "$SKILL_LINK"
        echo "Registered as OpenClaw skill: governance-agent"
        echo "  Symlink: $SKILL_LINK -> $SCRIPT_DIR"
    else
        echo "Skill already registered at $SKILL_LINK"
    fi
else
    echo
    echo "OpenClaw not found at ~/.openclaw"
    echo "To install OpenClaw:"
    echo "  git clone https://github.com/openclaw/openclaw"
    echo "  cd openclaw && npm install && npm start"
    echo "  (scan QR code with WhatsApp to link)"
    echo
    echo "After installing, re-run this script to register the skill."
fi

# ── 8. Summary ───────────────────────────────────────────────────────────────

echo
echo "========================================"
echo "  Setup Complete"
echo "========================================"
echo
echo "Quick start:"
echo "  source .venv/bin/activate"
echo "  python3 main.py digest           # one-shot: fetch + analyze + send digest"
echo "  python3 main.py schedule          # daemon: Monday 07:00 before market open"
echo
echo "Via OpenClaw (WhatsApp):"
echo "  Message your WhatsApp with: \"show me my governance digest\""
echo "  Or: \"what proposals need my review?\""
echo "  Or: \"vote FOR on TSLA proposal #1\""
echo
echo "Cron alternative (runs every Monday 7AM):"
echo "  crontab -e"
echo "  0 7 * * 1 cd $SCRIPT_DIR && .venv/bin/python main.py digest"
echo
