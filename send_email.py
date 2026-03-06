"""
send_email.py
=============
Reads the generated HTML file and sends it as a rich HTML email.
Called by the GitHub Actions workflow after generate_report.py succeeds.

Required environment variables (set as GitHub Actions secrets):
  SMTP_HOST      e.g. smtp.gmail.com
  SMTP_PORT      e.g. 587
  SMTP_USER      your sender email address
  SMTP_PASS      your app password (NOT your login password)
  EMAIL_TO       recipient address (can be same as SMTP_USER)
"""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from pathlib import Path


def send_report():
    # ── Read config from environment ─────────────────────────────────────────
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]   # will raise if missing
    smtp_pass = os.environ["SMTP_PASS"]
    email_to  = os.environ["EMAIL_TO"]

    # ── Read the generated HTML ───────────────────────────────────────────────
    html_path = Path("dividend_kings_email_live.html")
    if not html_path.exists():
        print("❌  HTML file not found. Did generate_report.py run successfully?", file=sys.stderr)
        sys.exit(1)

    html_content = html_path.read_text(encoding="utf-8")
    run_date = datetime.now().strftime("%B %-d, %Y")

    # ── Build email ───────────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏆 Dividend Kings Weekly Snapshot — {run_date}"
    msg["From"]    = f"Dividend Kings Report <{smtp_user}>"
    msg["To"]      = email_to

    # Plain-text fallback for email clients that strip HTML
    plain = f"""Dividend Kings Weekly Snapshot — {run_date}

Your weekly Dividend Kings report is ready. Open this email in an HTML-capable
client to view the full formatted report.

Generated automatically via GitHub Actions + Yahoo Finance data.

This email is for informational purposes only and does not constitute
financial advice.
"""
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    # Also attach the raw HTML as a file so recipients can open it in a browser
    attachment = MIMEBase("text", "html")
    attachment.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"dividend_kings_{datetime.now().strftime('%Y-%m-%d')}.html"
    )
    msg.attach(attachment)

    # ── Send ──────────────────────────────────────────────────────────────────
    print(f"📧 Connecting to {smtp_host}:{smtp_port}...")
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, email_to, msg.as_string())

    print(f"✅  Email sent to {email_to}")


if __name__ == "__main__":
    send_report()
