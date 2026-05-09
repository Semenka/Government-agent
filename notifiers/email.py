"""SMTP email notifier — wraps the existing notifications.py module
behind the Notifier protocol so the scheduler can route through a
uniform interface.

Behavior is unchanged from v2: same HTML layout, same SMTP plumbing,
same env vars.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO,
)
from notifications import _build_html_report, send_digest_email
from notifiers.base import AlertTier


class EmailNotifier:
    name = "email"

    def is_configured(self) -> bool:
        return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO])

    def send_alert(self, proposal: dict, decision: dict, tier: AlertTier) -> str:
        """Send a one-off alert for an upcoming meeting."""
        if not self.is_configured():
            return ""
        subject = (
            f"[{proposal.get('ticker','?')}] "
            f"{tier.code} — Vote alert: {proposal.get('title','')[:60]}"
        )
        body = self._build_alert_html(proposal, decision, tier)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(self._plain_alert(proposal, decision, tier), "plain"))
        msg.attach(MIMEText(body, "html"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
            # SMTP doesn't expose a stable id we can edit later, so return ""
            return ""
        except Exception as exc:
            print(f"Email alert failed: {exc}")
            return ""

    def send_digest(self, rows: list[dict], week_label: str) -> bool:
        return send_digest_email(rows=rows, week_label=week_label)

    # ------------------------------------------------------------------

    @staticmethod
    def _plain_alert(proposal: dict, decision: dict, tier: AlertTier) -> str:
        rec = decision.get("recommendation") or "—"
        conf = decision.get("confidence") or 0.0
        return (
            f"{tier.code} alert — {proposal.get('ticker','')} "
            f"meeting on {proposal.get('meeting_date','?')}\n"
            f"Proposal #{proposal.get('proposal_number','?')}: {proposal.get('title','')}\n"
            f"AI recommends: {rec} (confidence {conf:.0%})\n"
            f"Reasoning: {decision.get('reasoning','')[:300]}\n"
        )

    @staticmethod
    def _build_alert_html(proposal: dict, decision: dict, tier: AlertTier) -> str:
        color = {
            "FOR": "#22c55e", "AGAINST": "#ef4444",
            "ABSTAIN": "#eab308", "WITHHOLD": "#eab308",
        }.get(decision.get("recommendation", ""), "#94a3b8")
        return (
            "<html><body>"
            f"<h2>{proposal.get('ticker','')} — {tier.code} alert</h2>"
            f"<p><strong>Meeting:</strong> {proposal.get('meeting_date','?')}<br>"
            f"<strong>Proposal #{proposal.get('proposal_number','?')}:</strong> "
            f"{proposal.get('title','')}</p>"
            f"<p>AI recommends "
            f"<span style='color:{color};font-weight:bold;font-size:18px'>"
            f"{decision.get('recommendation','—')}</span> "
            f"(confidence {decision.get('confidence', 0):.0%}, "
            f"importance {decision.get('importance', 0):.0%})</p>"
            f"<p><strong>Reasoning:</strong> {decision.get('reasoning','')}</p>"
            "<p><em>To approve or override, run "
            "<code>python main.py review</code> or use the Telegram bot.</em></p>"
            "</body></html>"
        )
