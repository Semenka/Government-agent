"""
Email notification module — sends the weekly governance digest.

Supports SMTP (Gmail, Outlook, or any provider).
Configure in .env:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
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
            lines.append(
                f"<div style='border:1px solid #475569;padding:12px;margin:8px 0;border-radius:6px'>"
                f"<strong>{r['ticker']}</strong> — {r['company_name']}<br>"
                f"Proposal #{r['proposal_number']}: <strong>{r['title']}</strong><br>"
                f"AI says: <strong style='color:{vote_color.get(r['recommendation'], '#94a3b8')}'>"
                f"{r['recommendation']}</strong> "
                f"(confidence {r['confidence']:.0%}, importance {r['importance']:.0%})<br>"
                f"<em>{reasoning[:200]}</em>"
                f"</div>"
            )

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
