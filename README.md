# Government Agent — Personal Shareholder Voting Assistant

A personal governance agent that monitors proxy votes for your portfolio companies, makes AI-powered voting recommendations using Claude, and escalates important decisions to you with full context.

## Portfolio

| US (EDGAR) | International (manual) |
|---|---|
| SYF, OXY, BABA, BIDU, TSLA, GOOGL, NVDA, DOYU, AAL, USB, STZ, POOL, LEN, UNH | TTE (Euronext Paris), WISE (LSE), 1810.HK (HKEX) |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

## Workflow

```bash
# 1. Fetch proxy filings from SEC EDGAR for US companies
python main.py fetch

# 2. Manually enter ballot items for international companies
python main.py add --ticker TTE
python main.py add --ticker WISE
python main.py add --ticker 1810.HK

# 3. Run AI analysis on all pending proposals
python main.py analyze

# 4. Review items the agent flagged for your decision
python main.py review

# 5. Print full voting report
python main.py report --year 2025
```

## Preference Management

```bash
# Show current preferences
python main.py preferences list

# Override a specific preference
python main.py preferences set director_elections.attendance_threshold 0.80
python main.py preferences set executive_compensation.vote_against_pay_ratio_above 300

# Teach preferences from natural language
python main.py preferences learn
# > "I always vote against board members who sit on more than 3 other boards"

# Freeform chat about governance topics
python main.py chat
```

## How It Works

### Data Sources
- **US companies**: SEC EDGAR DEF 14A filings (free API). Claude extracts ballot items from the proxy HTML.
- **International companies**: Manual entry via `add` command. Check each company's IR page for proxy materials.

### Decision Logic
1. Claude analyzes each proposal against your preferences and corporate governance best practices.
2. Returns: recommendation (FOR/AGAINST/ABSTAIN), confidence score, importance score, reasoning.
3. **Escalation**: The agent asks you directly when it is *both* uncertain (confidence < 70%) *and* the issue is important (importance > 60%). Mergers, charter amendments, and equity plans are always escalated.

### Default Governance Principles (in `preferences/default_preferences.json`)
- Vote AGAINST directors with <75% meeting attendance
- Vote AGAINST overboarded directors (>4 public boards; >2 for CEOs)
- Support board gender diversity (flag if <20% women)
- Vote AGAINST say-on-pay with CEO pay ratio > 500x
- Support climate risk disclosure, political spending transparency, pay equity reporting
- Oppose poison pills, classified boards, supermajority requirements
- Support proxy access and majority voting

### Preference Learning
The agent learns from:
1. Direct statements via `preferences learn`
2. Overrides you make during `review`
3. Explicit `preferences set` commands

## Files

```
government_agent/
├── main.py                    # CLI entry point
├── config.py                  # Portfolio, CIK map, enums
├── requirements.txt
├── .env.example
├── data/
│   ├── edgar.py               # SEC EDGAR API client
│   ├── manual_input.py        # Manual ballot entry
│   └── storage.py             # SQLite database
├── agent/
│   ├── analyzer.py            # Claude analysis
│   ├── preference_engine.py   # Preference management
│   └── decision_engine.py     # Orchestration + escalation
├── ui/
│   └── cli.py                 # Click + Rich interface
└── preferences/
    └── default_preferences.json
```

Database is stored at `./governance.db` (configurable via `DB_PATH` in `.env`).
