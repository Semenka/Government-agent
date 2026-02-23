# Government Agent — Personal Shareholder Voting Assistant

A personal governance agent that monitors proxy votes for your portfolio companies, makes AI-powered voting recommendations, and escalates important decisions to you with full context.

Supports both **Gemini 2.5 Flash-Lite** (Google AI) and **Anthropic Claude** backends.

## Portfolio

| US (EDGAR) | International (manual) |
|---|---|
| SYF, OXY, TTE, BABA, BIDU, TSLA, GOOGL, NVDA, DOYU, AAL, USB, STZ, POOL, LEN, UNH | WISE (LSE), 1810.HK (HKEX) |

TTE (TotalEnergies) is fetched from EDGAR via its NYSE listing (CIK 0000879764). As a foreign private issuer it files 6-K instead of DEF 14A.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env — see below for backend options
```

### Option A: Gemini 2.5 Flash-Lite (default)
```bash
python main.py setup-gemini               # Verify API key + test
```
```env
LLM_BACKEND=gemini
GEMINI_API_KEY=your-key-here              # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash-lite
```

### Option B: Anthropic Claude
```env
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-6
```

## Quick Start

```bash
# Fetch → analyze → email digest (one shot)
python main.py digest

# Or step by step:
python main.py fetch                       # Pull proxy filings from SEC EDGAR
python main.py add --ticker WISE           # Manual entry for international companies
python main.py analyze                     # AI analysis on all pending proposals
python main.py review                      # Decide on escalated items
python main.py report --year 2025          # Print voting report table
```

## Monday Morning Digest

The agent automatically runs the full pipeline (fetch → analyze → email) every Monday morning.

### Option 1: Built-in daemon
```bash
python main.py schedule
# Runs in foreground; keeps triggering every Monday at 08:00
# Use tmux, systemd, or Docker to keep it alive
```

### Option 2: Cron job
```bash
crontab -e
# Add:
0 8 * * 1  cd /path/to/Government-agent && python3 main.py digest >> /var/log/governance-agent.log 2>&1
```

### Email setup
Configure in `.env`:
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password            # Gmail: use App Password
EMAIL_FROM=your-email@gmail.com
EMAIL_TO=your-email@gmail.com
```

The digest email includes:
- Color-coded voting table for all proposals
- Detailed cards for items needing your review
- Summary counts (auto-decided vs. needs review)

## Preference Management

```bash
python main.py preferences list
python main.py preferences set director_elections.attendance_threshold 0.80
python main.py preferences learn
# > "I always vote against board members who sit on more than 3 other boards"
python main.py chat                        # Freeform governance Q&A
```

## How It Works

### Data Sources
- **US companies**: SEC EDGAR DEF 14A filings (free API) with disk caching and retry logic
- **TTE (TotalEnergies)**: SEC EDGAR 6-K filings (foreign private issuer, NYSE-listed)
- **International companies**: Manual entry via `add` command (WISE, 1810.HK)

### Decision Logic
1. The LLM analyzes each proposal against your preferences and governance best practices
2. Returns: recommendation (FOR/AGAINST/ABSTAIN), confidence, importance, reasoning
3. **Escalation**: asks you when confidence < 70% AND importance > 60%. Always escalates M&A, charter amendments, and equity plans.

### Optimizations
- **Disk cache** for EDGAR downloads (avoids re-fetching proxy statements)
- **Parallel analysis** via ThreadPoolExecutor (up to 4 proposals at once)
- **Retry with exponential backoff** for network errors
- **Robust JSON parsing** handles both Claude and Gemini output formats

### Default Governance Principles (`preferences/default_preferences.json`)
- Vote AGAINST directors with <75% meeting attendance
- Vote AGAINST overboarded directors (>4 public boards; >2 for CEOs)
- Support board gender diversity (flag if <20% women)
- Vote AGAINST say-on-pay with CEO pay ratio > 500x
- Support climate risk disclosure, political spending transparency
- Oppose poison pills, classified boards, supermajority requirements
- Support proxy access and majority voting

## Files

```
├── main.py                        # CLI entry point (10 commands)
├── config.py                      # Portfolio, CIK map, enums, all settings
├── scheduler.py                   # Monday digest pipeline + daemon
├── notifications.py               # HTML email builder + SMTP sender
├── requirements.txt
├── .env.example
├── data/
│   ├── edgar.py                   # SEC EDGAR client (cached + retried)
│   ├── manual_input.py            # Manual ballot entry wizard
│   └── storage.py                 # SQLite database layer
├── agent/
│   ├── llm_backend.py             # Gemini + Anthropic abstraction
│   ├── analyzer.py                # Proposal analysis via LLM
│   ├── preference_engine.py       # Preference management + learning
│   └── decision_engine.py         # Orchestration + parallel analysis
├── ui/
│   └── cli.py                     # Click + Rich CLI
└── preferences/
    └── default_preferences.json   # ~40 governance rules (baseline)
```
