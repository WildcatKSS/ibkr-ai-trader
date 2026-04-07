"""
Alert notifier — email and webhook dispatch for trade events.

All notifications are best-effort: failures are logged but never raise
exceptions that could interrupt the trading loop.

Event types
-----------
``trade_opened``     Order filled / dryrun logged.
``trade_closed``     Position closed (EOD or stop/target hit).
``circuit_breaker``  Trading halted for the day.
``daily_summary``    End-of-day P&L summary.
``error``            Unhandled error in the trading loop.

Settings (from DB via ``bot.utils.config``)
-------------------------------------------
``ALERTS_EMAIL_ENABLED``    "true" / "false"
``ALERTS_EMAIL_FROM``       sender address
``ALERTS_EMAIL_TO``         recipient address
``ALERTS_SMTP_HOST``        SMTP host (default smtp.gmail.com)
``ALERTS_SMTP_PORT``        SMTP port (default 587)
``SMTP_PASSWORD``           from .env — never from DB
``ALERTS_WEBHOOKS_ENABLED`` "true" / "false"
``ALERTS_WEBHOOK_URL``      HTTP endpoint for POST
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.mime.text import MIMEText

from bot.utils.logger import get_logger

log = get_logger("trading")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def notify(event_type: str, payload: dict) -> None:
    """
    Dispatch an alert for *event_type*.

    Parameters
    ----------
    event_type:
        One of ``"trade_opened"``, ``"trade_closed"``, ``"circuit_breaker"``,
        ``"daily_summary"``, ``"error"``.
    payload:
        Key/value context for the alert (symbol, P&L, reason, etc.).
    """
    try:
        from bot.utils.config import get
        email_enabled = get("ALERTS_EMAIL_ENABLED", cast=bool, default=False)
        webhook_enabled = get("ALERTS_WEBHOOKS_ENABLED", cast=bool, default=False)
    except Exception as exc:
        log.warning("Cannot read alert settings", error=str(exc))
        return

    subject, body = _format_message(event_type, payload)

    if email_enabled:
        _send_email(subject, body)

    if webhook_enabled:
        _send_webhook(event_type, subject, payload)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_message(event_type: str, payload: dict) -> tuple[str, str]:
    """Return (subject, plain-text body) for the given event."""
    symbol = payload.get("symbol", "")
    mode = payload.get("trading_mode", "")

    if event_type == "trade_opened":
        subject = f"[IBKR Bot] Trade opened: {symbol} {payload.get('action', '')} [{mode}]"
        body = (
            f"Symbol:     {symbol}\n"
            f"Action:     {payload.get('action', '')}\n"
            f"Shares:     {payload.get('shares', '')}\n"
            f"Fill price: {payload.get('fill_price', '')}\n"
            f"Target:     {payload.get('target_price', '')}\n"
            f"Stop:       {payload.get('stop_price', '')}\n"
            f"Mode:       {mode}\n\n"
            f"{payload.get('explanation', '')}"
        )

    elif event_type == "trade_closed":
        pnl = payload.get("pnl", 0.0)
        sign = "+" if (pnl or 0) >= 0 else ""
        subject = f"[IBKR Bot] Trade closed: {symbol}  P&L {sign}{pnl:.2f}"
        body = (
            f"Symbol:     {symbol}\n"
            f"Exit price: {payload.get('exit_price', '')}\n"
            f"P&L:        {sign}{pnl:.2f} USD\n"
            f"Mode:       {mode}\n"
        )

    elif event_type == "circuit_breaker":
        subject = f"[IBKR Bot] ⚠ Circuit breaker tripped — trading halted"
        body = f"Reason: {payload.get('reason', '')}\nMode: {mode}\n"

    elif event_type == "daily_summary":
        pnl = payload.get("total_pnl", 0.0)
        sign = "+" if (pnl or 0) >= 0 else ""
        subject = f"[IBKR Bot] Daily summary — P&L {sign}{pnl:.2f}"
        body = (
            f"Trades:       {payload.get('trade_count', 0)}\n"
            f"Wins:         {payload.get('wins', 0)}\n"
            f"Losses:       {payload.get('losses', 0)}\n"
            f"Total P&L:    {sign}{pnl:.2f} USD\n"
        )

    elif event_type == "error":
        subject = f"[IBKR Bot] Error — {payload.get('error', '')[:80]}"
        body = f"Error: {payload.get('error', '')}\nContext: {payload.get('context', '')}\n"

    else:
        subject = f"[IBKR Bot] {event_type}"
        body = json.dumps(payload, indent=2)

    return subject, body


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def _send_email(subject: str, body: str) -> None:
    try:
        from bot.utils.config import get

        from_addr = get("ALERTS_EMAIL_FROM", default="")
        to_addr = get("ALERTS_EMAIL_TO", default="")
        smtp_host = get("ALERTS_SMTP_HOST", default="smtp.gmail.com")
        smtp_port = get("ALERTS_SMTP_PORT", cast=int, default=587)
    except Exception as exc:
        log.warning("Cannot read email settings", error=str(exc))
        return

    if not from_addr or not to_addr:
        log.warning("Email alerts enabled but FROM/TO addresses not configured")
        return

    password = os.getenv("SMTP_PASSWORD", "")
    if not password:
        log.warning("SMTP_PASSWORD not set — email alert skipped")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(from_addr, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("Email alert sent", subject=subject[:80])
    except Exception as exc:
        log.warning("Failed to send email alert", error=str(exc))


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def _send_webhook(event_type: str, subject: str, payload: dict) -> None:
    try:
        from bot.utils.config import get
        url = get("ALERTS_WEBHOOK_URL", default="")
    except Exception as exc:
        log.warning("Cannot read webhook URL", error=str(exc))
        return

    if not url:
        log.warning("Webhooks enabled but ALERTS_WEBHOOK_URL not configured")
        return

    data = {"event": event_type, "subject": subject, **payload}

    try:
        import httpx
        httpx.post(url, json=data, timeout=10)
        log.info("Webhook alert sent", event=event_type, url=url[:60])
    except Exception as exc:
        log.warning("Failed to send webhook alert", error=str(exc))
