---
name: governance-agent
description: Personal shareholder governance voting assistant — monitors proxy votes, makes AI recommendations, and sends weekly digests
metadata: {"openclaw":{"requires":{"bins":["python3"],"env":["GEMINI_API_KEY"]},"primaryEnv":"GEMINI_API_KEY","os":["darwin","linux"]}}
---

# Governance Agent — Shareholder Voting Assistant

Use this skill when the user asks about:
- Corporate governance, proxy votes, or shareholder meetings
- Their portfolio companies' voting proposals
- Voting recommendations or how to vote on a ballot
- Running the weekly governance digest
- Checking proposal status or upcoming votes

## Portfolio

The user's portfolio: SYF, OXY, TTE, WISE, BABA, BIDU, TSLA, GOOGL, NVDA, DOYU, AAL, USB, STZ, POOL, LEN, UNH, 1810.HK

## Tools / Commands

All scripts are in `{baseDir}/scripts/` and should be run from `{baseDir}`.

### Get the weekly digest (most common)

When the user asks for their voting digest, upcoming votes, or weekly summary:

```bash
cd {baseDir} && python3 scripts/digest.py
```

This fetches filings from SEC EDGAR, analyzes proposals with AI, and prints the full digest.

To only show this week's votes (skip fetch+analyze if data is fresh):

```bash
cd {baseDir} && python3 scripts/digest.py --report-only
```

### Check proposal status / report

When the user asks "what's the status", "show me the report", or "what proposals do we have":

```bash
cd {baseDir} && python3 scripts/report.py
```

For a specific ticker:

```bash
cd {baseDir} && python3 scripts/report.py --ticker TSLA
```

### Fetch new proxy filings

When the user asks to check for new filings or refresh data:

```bash
cd {baseDir} && python3 scripts/fetch.py
```

For a specific ticker:

```bash
cd {baseDir} && python3 scripts/fetch.py --ticker NVDA
```

### Analyze pending proposals

When the user asks to analyze or get AI recommendations:

```bash
cd {baseDir} && python3 scripts/analyze.py
```

### Show items needing review

When the user asks what needs their attention or decision:

```bash
cd {baseDir} && python3 scripts/review.py
```

### Record a vote decision

When the user says they want to vote FOR/AGAINST/ABSTAIN on a specific proposal:

```bash
cd {baseDir} && python3 scripts/vote.py --ticker TSLA --proposal 1 --vote FOR --note "User's reasoning"
```

### Show or update preferences

```bash
cd {baseDir} && python3 scripts/preferences.py list
cd {baseDir} && python3 scripts/preferences.py set executive_comp.max_acceptable_ratio 200
```

### Ask a governance question (freeform)

When the user asks a question about governance, a specific company, or wants analysis:

```bash
cd {baseDir} && python3 scripts/chat.py "What is TSLA's board composition like?"
```

## Response Format

All scripts output plain text formatted for WhatsApp (using *bold* and _italic_ markers). Present the output directly to the user without modification. If the output is long, send it as-is — OpenClaw's WhatsApp channel handles chunking automatically.

## Important Notes

- The agent stores all data in a local SQLite database at `{baseDir}/governance.db`
- SEC EDGAR data is cached in `{baseDir}/.cache/edgar/`
- The agent uses Gemini (or Anthropic Claude) for AI analysis — the LLM backend is configured in `{baseDir}/.env`
- Proposals flagged with [REVIEW] need the user's manual decision — prompt them to vote
- When showing review items, ask the user how they want to vote on each one
