# Government Agent — Personal Shareholder Voting Assistant

A personal governance agent that monitors proxy votes for your portfolio companies, runs a two-pass critique on each proposal, and **pushes actionable alerts to your Telegram chat in the days before each shareholder meeting** with FOR/AGAINST/ABSTAIN buttons that record your vote with one tap.

Backends: **local LLM** (gbrain / LM Studio / llama-server / vLLM via OpenAI-compat HTTP), **Ollama**, **Gemini 2.5 Flash-Lite**, or **Anthropic Claude**.

## Portfolio

| US (EDGAR) | International (manual) |
|---|---|
| SYF, OXY, TTE, BABA, BIDU, TSLA, GOOGL, NVDA, DOYU, AAL, USB, STZ, POOL, LEN, UNH | WISE (LSE), 1810.HK (HKEX) |

TTE (TotalEnergies) is fetched from EDGAR via its NYSE listing (CIK 0000879764). As a foreign private issuer it files 6-K instead of DEF 14A.

## What's new in v3

- **Meeting-aware fan-out** — alerts fire at T-14 / T-7 / T-3 / T-1 days before each meeting, not just one Monday-morning blast.
- **Telegram bot** with inline-button vote callbacks (long-polling, no public IP needed — works behind your Mac mini's NAT).
- **Two-pass analysis** — every proposal gets a draft pass, then a critique pass that argues the strongest counter-case and may revise the recommendation. Both passes are stored.
- **Portfolio-thematic context** — the critique sees other holdings facing the same proposal type ("you're voting climate disclosure at 4 of 5 holdings this season").
- **Local LLM support** — point at `gbrain` (or LM Studio / llama-server / vLLM / etc.) via an OpenAI-compatible URL.
- **Schema migrations** — meeting deadlines, peer recommendations, analysis traceability, vote outcomes for accuracy tracking.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

### Pick a backend

| Backend | When to use | `.env` |
|---|---|---|
| `local` | gbrain / LM Studio / llama-server / vLLM | `LLM_BACKEND=local`, `LOCAL_LLM_URL=http://127.0.0.1:1234/v1`, `LOCAL_LLM_MODEL=...` |
| `ollama` | Native Ollama (no OpenAI shim) | `LLM_BACKEND=ollama`, `OLLAMA_URL=http://127.0.0.1:11434`, `OLLAMA_MODEL=llama3.1:8b` |
| `gemini` | Cloud, cheap, fast | `LLM_BACKEND=gemini`, `GEMINI_API_KEY=...` |
| `anthropic` | Highest quality | `LLM_BACKEND=anthropic`, `ANTHROPIC_API_KEY=...` |

Verify your backend:
```bash
python main.py setup-local      # local LLM (gbrain etc.)
python main.py setup-gemini     # Gemini
```

### Telegram (recommended channel)

1. Open Telegram → `@BotFather` → `/newbot` → copy the token
2. Start a chat with your new bot, send any message
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`
4. Add to `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=123456:abcdef…
   TELEGRAM_CHAT_ID=123456789
   NOTIFIER_CHANNELS=telegram
   ```
5. Run the bot worker:
   ```bash
   python main.py telegram-bot
   ```

The bot uses **long-polling**, so you don't need a public IP, port forwarding, ngrok, or TLS. It works behind any home NAT.

### Email (optional fallback)

```env
NOTIFIER_CHANNELS=telegram,email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com
EMAIL_TO=your-email@gmail.com
```

## Quick start

```bash
# Fetch → analyze (two-pass) → notify
python main.py digest

# Step by step
python main.py fetch                       # Pull proxy filings from SEC EDGAR
python main.py backfill-meeting-dates      # Re-parse cached filings for meeting dates
python main.py add --ticker WISE           # Manual entry for international companies
python main.py analyze                     # AI analysis on all pending proposals
python main.py review                      # CLI review queue (or use Telegram instead)
python main.py report --year 2025          # Print voting report table
```

## How alerts work

```
       Mon-morning weekly digest                      Daily 07:00 meeting-check
       (full pipeline + report)                       (T-14 / T-7 / T-3 / T-1)
                  │                                              │
                  ▼                                              ▼
           ┌────────────────────────────────────────────────────────────┐
           │                  Notifier registry                         │
           │   Telegram (inline buttons)        Email (HTML digest)     │
           └────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
       FOR / AGAINST / ABSTAIN buttons → callback → applies user vote,
       updates DB, edits the message to confirm — no terminal needed.
```

Every (proposal, tier, channel) combination fires exactly **once** — deduped via the `meeting_alerts` table.

## Running on a Mac mini

Two long-running processes:

```bash
# Terminal 1: scheduler daemon (weekly digest + daily meeting-check)
python main.py schedule

# Terminal 2: Telegram bot worker (long-polling)
python main.py telegram-bot
```

For unattended boot-time start, add launchd plists. Replace `/Users/you/Government-agent` with your path:

`~/Library/LaunchAgents/com.user.governance.scheduler.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.user.governance.scheduler</string>
    <key>WorkingDirectory</key><string>/Users/you/Government-agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>main.py</string>
        <string>schedule</string>
    </array>
    <key>RunAtLoad</key>       <true/>
    <key>KeepAlive</key>       <true/>
    <key>StandardOutPath</key> <string>/Users/you/Government-agent/.cache/scheduler.log</string>
    <key>StandardErrorPath</key><string>/Users/you/Government-agent/.cache/scheduler.err</string>
</dict>
</plist>
```

`~/Library/LaunchAgents/com.user.governance.telegram.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.user.governance.telegram</string>
    <key>WorkingDirectory</key><string>/Users/you/Government-agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>main.py</string>
        <string>telegram-bot</string>
    </array>
    <key>RunAtLoad</key>       <true/>
    <key>KeepAlive</key>       <true/>
    <key>StandardOutPath</key> <string>/Users/you/Government-agent/.cache/telegram.log</string>
    <key>StandardErrorPath</key><string>/Users/you/Government-agent/.cache/telegram.err</string>
</dict>
</plist>
```

Load both:
```bash
launchctl load ~/Library/LaunchAgents/com.user.governance.scheduler.plist
launchctl load ~/Library/LaunchAgents/com.user.governance.telegram.plist
```

(Cron equivalent in `python main.py schedule` output if you prefer.)

## Testing without burning quota or hitting SEC

```bash
# Insert a synthetic proposal whose meeting is 7 days away
python main.py seed-test-meeting --ticker OXY --days 7

# See what would alert without actually firing the LLM or notifiers
python main.py meeting-check --dry-run

# Fire only the T-7 tier (for spot-tests)
python main.py meeting-check --tier T-7
```

EDGAR fetches reuse `.cache/edgar/` so re-runs are free.

## Telegram commands

| Command | What it does |
|---|---|
| `/start`, `/help` | Show available commands |
| `/queue` | List proposals needing your review (with vote buttons) |
| `/upcoming` | Meetings in the next 30 days |

When an alert message lands, tap **FOR / AGAINST / ABSTAIN / Details** to record your vote (the message is edited to confirm) or read the expanded reasoning.

## Preference management

```bash
python main.py preferences list
python main.py preferences set director_elections.attendance_threshold 0.80
python main.py preferences learn
# > "I always vote against board members who sit on more than 3 other boards"
python main.py chat                        # Freeform governance Q&A
```

When you override a recommendation, the agent stores both the structured preference *and* a context blob (your note + the AI's original reasoning) under `learned.<ticker>.<type>.context` so the trace is auditable.

## How the two-pass analysis works

1. **Pass 1 — analyze.** The model evaluates the proposal against your preferences and governance best practices, returning recommendation / confidence / importance / reasoning / concerns.
2. **Pass 2 — critique.** A second prompt feeds pass 1's JSON back along with portfolio-wide thematic context, asks "what is the strongest counter-argument?", and re-issues the analysis. If the recommendation flips, `critique_revised=1` is recorded.
3. **Persistence.** Both passes go into `analysis_passes` for full traceability. The Telegram alert shows `Pass1→X | Pass2→Y` so you can see whenever the critique disagreed with the draft.

## Escalation rules

You're asked to review when:
- Confidence < 70% **and** importance > 60%, OR
- The proposal is M&A, a charter/bylaw amendment, or an equity plan, OR
- It's executive compensation with importance ≥ 80%.

## Accuracy tracking

After a meeting, record the actual shareholder vote outcome:
```bash
python main.py record-outcome --proposal-id 42 --outcome FOR --support-pct 87.4
```
Run `python main.py report` to see your AI agreement rate against shareholder outcomes.

## Files

```
├── main.py                        # CLI entry point (16 commands)
├── config.py                      # Portfolio, CIK map, all settings
├── scheduler.py                   # Weekly digest + daily meeting-check
├── telegram_bot.py                # Long-polling Telegram bot worker
├── notifications.py               # SMTP digest builder (email)
├── requirements.txt
├── .env.example
├── data/
│   ├── edgar.py                   # SEC EDGAR client + meeting metadata extraction
│   ├── manual_input.py            # Manual ballot entry wizard
│   ├── migrations.py              # Forward-only SQLite schema migrations
│   └── storage.py                 # SQLite layer (proposals, decisions, alerts, …)
├── agent/
│   ├── llm_backend.py             # Local / Ollama / Gemini / Anthropic abstraction
│   ├── analyzer.py                # Two-pass critique + thematic context
│   ├── preference_engine.py       # Preference management + reasoning trace
│   └── decision_engine.py         # Orchestration, parallel analysis, vote application
├── notifiers/
│   ├── base.py                    # Notifier protocol + AlertTier
│   ├── email.py                   # SMTP notifier
│   ├── telegram.py                # Telegram Bot API notifier (alerts + digest)
│   └── registry.py                # Active-channel selection
├── ui/
│   └── cli.py                     # Click + Rich CLI
└── preferences/
    └── default_preferences.json   # ~40 governance rules (baseline)
```

## Schema (after v3 migration)

- `proposals` + `vote_deadline`, `meeting_url`, `extraction_truncated`
- `decisions` + `pass1_*`, `critique_text`, `critique_revised`, `peer_iss`, `peer_glass_lewis`
- `meeting_alerts(proposal_id, tier, channel)` — alert dedupe
- `analysis_passes(decision_id, pass_number)` — full two-pass trail
- `vote_outcomes(proposal_id, actual_outcome, support_pct)` — accuracy tracking
- `notification_log` — generic cross-channel dedupe by content hash
