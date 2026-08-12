"""HTML email delivery of a generated briefing.

Behaviour is carried over from the deployed version; what changed is that every
address, hostname and organisation label now comes from :class:`EmailSettings`
instead of being a literal default, and the retry loop returns explicitly
instead of falling out of the bottom of the ``for``.
"""

from __future__ import annotations

import html
import logging
import re
import smtplib
import socket
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from .config import EmailSettings

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VALID_STATUSES = ("STABLE", "STATUS A", "STATUS B", "STATUS C")

STATUS_COLORS = {
    "STABLE": "#28a745",
    "STATUS A": "#17a2b8",
    "STATUS B": "#ffc107",
    "STATUS C": "#dc3545",
}
DEFAULT_COLOR = "#0056b3"

MAX_EMAIL_BYTES = 10 * 1024 * 1024
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
SMTP_TIMEOUT_SECONDS = 30

_STATUS_LEGEND = """
    <div style="background-color:#fcfcfc;border:1px solid #eee;border-radius:4px;padding:12px;
                margin-bottom:15px;font-size:11px;color:#666;line-height:1.4;">
        <b style="color:#333;display:block;margin-bottom:4px;">Status classification reference</b>
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
            <tr><td style="vertical-align:top;width:75px;"><span style="color:#28a745;">&#9679;</span>
                <b>STABLE:</b></td>
                <td>No paused services; normal traffic; infrastructure healthy.</td></tr>
            <tr><td style="vertical-align:top;"><span style="color:#17a2b8;">&#9679;</span>
                <b>STATUS A:</b></td>
                <td>Slight increase in security events; all services active.</td></tr>
            <tr><td style="vertical-align:top;"><span style="color:#ffc107;">&#9679;</span>
                <b>STATUS B:</b></td>
                <td>Significant spike in blocks or abnormal performance.</td></tr>
            <tr><td style="vertical-align:top;"><span style="color:#dc3545;">&#9679;</span>
                <b>STATUS C:</b></td>
                <td>A service is paused, or critical failure, or high-intensity attack.</td></tr>
        </table>
    </div>
"""


def _validate(
    report_content: str, status_code: str, report_date: str, settings: EmailSettings
) -> str | None:
    """Return a reason the message must not be sent, or None."""
    if not report_content or not report_content.strip():
        return "report content is empty"
    if status_code not in VALID_STATUSES:
        return f"unrecognised status code {status_code!r}"
    if not DATE_RE.match(report_date):
        return f"report_date {report_date!r} is not YYYY-MM-DD"
    if not settings.smtp_host:
        return "SMTP_HOST is not set"
    if not EMAIL_RE.match(settings.sender_email):
        return "SENDER_EMAIL is not a valid address"
    if not settings.recipients:
        return "EMAIL_RECIPIENTS is empty"
    invalid = [r for r in settings.recipients if not EMAIL_RE.match(r)]
    if invalid:
        return f"{len(invalid)} recipient address(es) are malformed"
    return None


def render_html(
    report_content: str, status_code: str, report_date: str, settings: EmailSettings
) -> str:
    # The briefing is model-generated text going into an HTML document; escaping
    # it is the only thing standing between a prompt injection and script
    # execution in a reader's mail client.
    safe = html.escape(report_content)
    color = STATUS_COLORS.get(status_code, DEFAULT_COLOR)
    title = f"{settings.org_prefix} Cybersecurity Daily Briefing"
    return f"""
    <html>
    <body style="font-family:'Segoe UI',Arial,sans-serif;color:#333;line-height:1.6;">
        <div style="max-width:600px;margin:auto;border:1px solid #e0e0e0;border-radius:8px;
                    overflow:hidden;">
            <div style="background-color:#0056b3;color:#fff;padding:20px;text-align:center;">
                <h1 style="margin:0;font-size:20px;">{html.escape(title)}</h1>
            </div>
            <div style="padding:20px;">
                <p style="margin:0 0 5px 0;"><b>Date:</b> {report_date}</p>
                <p style="margin:0 0 15px 0;"><b>Current status:</b>
                   <span style="color:{color};font-weight:bold;">{status_code}</span></p>
                {_STATUS_LEGEND}
                <hr style="border:0;border-top:1px solid #eee;margin-bottom:20px;">
                <div style="background-color:#fff;white-space:pre-wrap;font-size:14px;color:#222;">
{safe}
                </div>
            </div>
            <div style="background-color:#f1f1f1;color:#888;padding:10px;text-align:center;
                        font-size:11px;">
                Automated briefing &middot; Cybersum
            </div>
        </div>
    </body>
    </html>
    """


def build_message(
    report_content: str, status_code: str, report_date: str, settings: EmailSettings
) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    message["Subject"] = (
        f"{settings.org_prefix} Cybersecurity Daily Briefing - {report_date} [{status_code}]"
    )
    message["From"] = formataddr((settings.sender_name, settings.sender_email))
    message["To"] = ", ".join(settings.recipients)
    message.attach(MIMEText(render_html(report_content, status_code, report_date, settings), "html"))
    return message


def send_security_report(
    report_content: str, status_code: str, report_date: str, settings: EmailSettings
) -> bool:
    """Deliver the briefing. Returns False rather than raising: a failed email
    must not fail the pipeline run that produced a perfectly good report."""
    if not settings.enabled:
        logger.info("Email delivery is disabled; skipping.")
        return False

    reason = _validate(report_content, status_code, report_date, settings)
    if reason is not None:
        logger.error("Not sending briefing: %s", reason)
        return False

    message = build_message(report_content, status_code, report_date, settings)
    size = len(message.as_string().encode("utf-8"))
    if size > MAX_EMAIL_BYTES:
        logger.error(
            "Briefing email is %.2f MB, over the %d MB limit; not sending.",
            size / 1024 / 1024,
            MAX_EMAIL_BYTES // 1024 // 1024,
        )
        return False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
            ) as server:
                server.set_debuglevel(1 if settings.debug_smtp else 0)
                server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            # The relay this was built against did not authenticate; if it now
            # demands credentials, retrying will not help.
            logger.error("SMTP authentication rejected: %s", exc)
            return False
        except (socket.timeout, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError) as exc:
            logger.warning("SMTP transport failure (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        except smtplib.SMTPException as exc:
            logger.warning("SMTP error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        except Exception as exc:  # noqa: BLE001 - delivery must never raise
            logger.warning("Unexpected send failure (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        else:
            logger.info(
                "Briefing sent to %d recipient(s) for %s [%s], %.1f KB, attempt %d/%d",
                len(settings.recipients), report_date, status_code,
                size / 1024, attempt, MAX_RETRIES,
            )
            return True

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    logger.error("Briefing delivery failed after %d attempts.", MAX_RETRIES)
    return False
