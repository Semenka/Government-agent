"""
Notification module — sends the weekly governance digest via email and/or WhatsApp.

Email: SMTP (Gmail, Outlook, or any provider).
WhatsApp: Twilio WhatsApp Business API.

Configure in .env:
  Email:    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
  WhatsApp: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, WHATSAPP_TO
  Channel:  NOTIFICATION_CHANNEL=email|whatsapp|both
"""

import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    EMAIL_FROM,
    EMAIL_TO,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
    WHATSAPP_TO,
    NOTIFICATION_CHANNEL,
    PORTFOLIO,
)
from data import storage


def _build_html_report(rows: list, week_label: str) -> str:
    """Build an HTML email body from report rows."""
    vote_color = {
        "FOR": "#22c55e",
        "AGAINST": "#ef4444",
        "ABSTAIN": "#eab308",
        "WITHHOLD": "#eab308",
    }

    lines = [
        "<html><body>",
        f"<h2>Governance Voting Digest — Week of {week_label}</h2>",
        "<p>Here are the voting recommendations for upcoming shareholder meetings "
        "across your portfolio:</p>",
    ]

    # Split into auto-decided and needs-review
    auto = [r for r in rows if not (r["needs_review"] and not r["user_override"])]
    review = [r for r in rows if r["needs_review"] and not r["user_override"]]

    if not rows:
        lines.append("<p><em>No upcoming votes found this week.</em></p>")
    else:
        # Summary counts
        lines.append(
            f"<p><strong>{len(rows)}</strong> proposals total &mdash; "
            f"<strong>{len(auto)}</strong> auto-decided, "
            f"<strong style='color:#eab308'>{len(review)}</strong> need your review</p>"
        )

        # Table
        lines.append(
            '<table border="1" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse; font-family:monospace; font-size:13px;">'
        )
        lines.append(
            "<tr style='background:#1e293b;color:#fff'>"
            "<th>Ticker</th><th>Meeting</th><th>#</th><th>Proposal</th>"
            "<th>Type</th><th>Mgmt</th><th>Vote</th><th>Conf</th><th>Status</th></tr>"
        )

        for r in rows:
            final_vote = r["user_override"] or r["recommendation"] or "—"
            color = vote_color.get(final_vote, "#94a3b8")
            mgmt = r["management_rec"] or "—"
            conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"
            needs = r["needs_review"] and not r["user_override"]
            status = "<span style='color:#eab308'>REVIEW</span>" if needs else "OK"

            lines.append(
                f"<tr>"
                f"<td><strong>{r['ticker']}</strong></td>"
                f"<td>{r['meeting_date'] or '—'}</td>"
                f"<td>{r['proposal_number']}</td>"
                f"<td>{(r['title'] or '')[:45]}</td>"
                f"<td>{(r['proposal_type'] or '').replace('_',' ')}</td>"
                f"<td>{mgmt}</td>"
                f"<td style='color:{color};font-weight:bold'>{final_vote}</td>"
                f"<td>{conf}</td>"
                f"<td>{status}</td>"
                f"</tr>"
            )

        lines.append("</table>")

    if review:
        lines.append(
            "<br><p style='color:#eab308'><strong>Action required:</strong> "
            f"{len(review)} proposal(s) need your manual review. "
            "Run <code>python main.py review</code> to decide on them.</p>"
        )

    # Items needing review — detailed cards
    if review:
        lines.append("<h3>Items Needing Your Decision</h3>")
        for r in review:
            reasoning = r["reasoning"] or ""
            value_impact = r.get("value_impact") or ""
            card_lines = [
                f"<div style='border:1px solid #475569;padding:12px;margin:8px 0;border-radius:6px'>",
                f"<strong>{r['ticker']}</strong> — {r['company_name']}<br>",
                f"Proposal #{r['proposal_number']}: <strong>{r['title']}</strong><br>",
                f"AI says: <strong style='color:{vote_color.get(r['recommendation'], '#94a3b8')}'>",
                f"{r['recommendation']}</strong> ",
                f"(confidence {r['confidence']:.0%}, importance {r['importance']:.0%})<br>",
            ]
            if value_impact:
                card_lines.append(
                    f"<strong>Value impact:</strong> {value_impact[:150]}<br>"
                )
            card_lines.append(f"<em>{reasoning[:200]}</em>")
            card_lines.append(f"</div>")
            lines.append("".join(card_lines))

    lines.append(
        "<br><hr><p style='color:#94a3b8;font-size:11px'>"
        "Sent by Government Agent — Personal Shareholder Voting Assistant</p>"
        "</body></html>"
    )
    return "\n".join(lines)


def send_digest_email(
    rows: list | None = None,
    week_label: str | None = None,
) -> bool:
    """
    Build and send the weekly governance digest email.
    Returns True if sent successfully, False otherwise.
    """
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO]):
        print(
            "Email not configured — set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, "
            "EMAIL_FROM, EMAIL_TO in .env"
        )
        return False

    if rows is None:
        rows = storage.get_report_rows()

    if week_label is None:
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        week_label = monday.strftime("%B %d, %Y")

    html = _build_html_report(rows, week_label)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Governance Digest — Week of {week_label}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Plain text fallback
    plain = (
        f"Governance Voting Digest — Week of {week_label}\n\n"
        f"{len(rows)} proposals across your portfolio.\n"
        "Run 'python main.py report' for the full table.\n"
        "Run 'python main.py review' for items needing your input.\n"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"Digest email sent to {EMAIL_TO}")
        return True
    except Exception as exc:
        print(f"Failed to send email: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# WhatsApp digest (via Twilio)
# ──────────────────────────────────────────────────────────────────────────────

def _build_whatsapp_message(rows: list, week_label: str) -> str:
    """
    Build a concise plain-text digest suitable for WhatsApp.
    WhatsApp messages have a 1600-char limit per message, so we keep it tight
    and split into multiple messages if necessary.
    """
    vote_emoji = {"FOR": "FOR", "AGAINST": "AGAINST", "ABSTAIN": "ABSTAIN", "WITHHOLD": "ABSTAIN"}

    auto = [r for r in rows if not (r["needs_review"] and not r["user_override"])]
    review = [r for r in rows if r["needs_review"] and not r["user_override"]]

    lines = [
        f"*Governance Voting Digest*",
        f"Week of {week_label}",
        "",
        f"Total proposals: *{len(rows)}*",
        f"Auto-decided: *{len(auto)}* | Needs review: *{len(review)}*",
        "",
    ]

    if not rows:
        lines.append("_No upcoming votes found this week._")
        return "\n".join(lines)

    # Group by ticker for compact display
    by_ticker: dict[str, list] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    for ticker, proposals in by_ticker.items():
        company = proposals[0]["company_name"]
        lines.append(f"*{ticker}* ({company})")
        for r in proposals:
            final_vote = r["user_override"] or r["recommendation"] or "—"
            conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "—"
            needs = r["needs_review"] and not r["user_override"]
            flag = " [REVIEW]" if needs else ""
            lines.append(
                f"  #{r['proposal_number']}: {(r['title'] or '')[:40]} "
                f"-> *{final_vote}* ({conf}){flag}"
            )
        lines.append("")

    if review:
        lines.append("*Items needing your decision:*")
        for r in review:
            reasoning = (r["reasoning"] or "")[:120]
            lines.append(
                f"  {r['ticker']} #{r['proposal_number']}: {r['title'][:35]}"
            )
            lines.append(
                f"    AI: *{r['recommendation']}* "
                f"(conf {r['confidence']:.0%}, imp {r['importance']:.0%})"
            )
            if reasoning:
                lines.append(f"    _{reasoning}_")
            lines.append("")

    lines.append("Run `python main.py review` for interactive review.")
    return "\n".join(lines)


def send_digest_whatsapp(
    rows: list | None = None,
    week_label: str | None = None,
) -> bool:
    """
    Build and send the weekly governance digest via WhatsApp (Twilio).
    Returns True if sent successfully, False otherwise.
    """
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, WHATSAPP_TO]):
        print(
            "WhatsApp not configured — set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "TWILIO_WHATSAPP_FROM, WHATSAPP_TO in .env"
        )
        return False

    if rows is None:
        rows = storage.get_report_rows()

    if week_label is None:
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        week_label = monday.strftime("%B %d, %Y")

    body = _build_whatsapp_message(rows, week_label)

    try:
        from twilio.rest import Client

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # Twilio WhatsApp has a ~1600 char limit per message.
        # Split into chunks if needed.
        chunks = _split_message(body, max_len=1500)
        for chunk in chunks:
            client.messages.create(
                body=chunk,
                from_=TWILIO_WHATSAPP_FROM,
                to=WHATSAPP_TO,
            )

        print(f"Digest sent via WhatsApp to {WHATSAPP_TO}")
        return True
    except ImportError:
        print("Twilio library not installed. Run: pip install twilio")
        return False
    except Exception as exc:
        print(f"Failed to send WhatsApp message: {exc}")
        return False


def _split_message(text: str, max_len: int = 1500) -> list[str]:
    """Split a long message into chunks at line boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


# ──────────────────────────────────────────────────────────────────────────────
# Unified digest sender
# ──────────────────────────────────────────────────────────────────────────────

def send_digest(
    rows: list | None = None,
    week_label: str | None = None,
    channel: str | None = None,
) -> dict:
    """
    Send the weekly governance digest via the configured channel(s).
    channel: "email", "whatsapp", or "both" (default from NOTIFICATION_CHANNEL).
    Returns {"email_sent": bool, "whatsapp_sent": bool}.
    """
    ch = (channel or NOTIFICATION_CHANNEL).lower().strip()

    result = {"email_sent": False, "whatsapp_sent": False}

    if ch in ("email", "both"):
        result["email_sent"] = send_digest_email(rows=rows, week_label=week_label)

    if ch in ("whatsapp", "both"):
        result["whatsapp_sent"] = send_digest_whatsapp(rows=rows, week_label=week_label)

    return result
