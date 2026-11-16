#!/usr/bin/env python3
"""
Email the latest status report (HTML in the body + attached).

Setup:
    cp email_config.example.json email_config.json   # then edit it
    SMTP_PASSWORD goes in ~/.config/pmo-status-report/secrets.env (see README)

Usage:
    python3 send_report.py --dry-run          # build the email as a .eml file, don't send
    python3 send_report.py                    # send latest report
    python3 send_report.py --failure log.txt  # send an alert (used by run_weekly.sh)
"""
import argparse
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

ROOT = Path(__file__).parent


def latest_report():
    reports = sorted((ROOT / "reports").glob("status_*.html"))
    if not reports:
        sys.exit("No reports found — run status_report.py first.")
    return reports[-1]


def counts_for(report):
    day = report.stem.removeprefix("status_")
    snap = ROOT / "history" / f"{day}.json"
    statuses = list(json.loads(snap.read_text()).values()) if snap.exists() else []
    return day, {s: statuses.count(s) for s in ("Red", "Amber", "Green", "Complete")}


def html_to_text(html):
    text = re.sub(r"<(script|style).*?</\1>", "", html, flags=re.S)
    text = re.sub(r"</(tr|li|h\d|div|p)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    import html as h
    return "\n".join(" ".join(l.split()) for l in h.unescape(text).splitlines() if l.strip())


def build(cfg, subject, html=None, text=None, attachment=None):
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    if cfg.get("cc"):
        msg["Cc"] = ", ".join(cfg["cc"])
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg.set_content(text or html_to_text(html or ""))
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype="text", subtype="html",
                           filename=attachment.name)
    return msg


def send(cfg, msg):
    password = os.environ.get("SMTP_PASSWORD", "")
    host, port = cfg["smtp_host"], int(cfg.get("smtp_port", 587))
    security = cfg.get("security", "starttls")  # starttls | ssl | none
    ctx = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
        else:
            server = smtplib.SMTP(host, port, timeout=60)
            if security == "starttls":
                server.starttls(context=ctx)
        with server:
            if cfg.get("username"):
                if not password:
                    sys.exit("SMTP_PASSWORD is not set.")
                server.login(cfg["username"], password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        sys.exit(f"SMTP login failed ({e.smtp_code}). Gmail/Outlook need an *app password*; "
                 f"some corporate Microsoft 365 tenants disable SMTP AUTH — ask IT.")
    except (smtplib.SMTPException, OSError) as e:
        sys.exit(f"Sending failed: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=ROOT / "email_config.json", type=Path)
    ap.add_argument("-r", "--report", type=Path, help="report to send (default: latest)")
    ap.add_argument("--dry-run", action="store_true", help="write .eml next to the report instead of sending")
    ap.add_argument("--failure", type=Path, metavar="LOG", help="send a failure alert with this log")
    args = ap.parse_args()

    if not args.config.exists():
        sys.exit(f"No config at {args.config}. Copy email_config.example.json to email_config.json.")
    cfg = json.loads(args.config.read_text())

    if args.failure:
        alert_cfg = {**cfg, "to": cfg.get("alert_to") or [cfg["from"]], "cc": []}
        log = args.failure.read_text(errors="replace")[-5000:] if args.failure.exists() else "(no log)"
        msg = build(alert_cfg, f"⚠ Portfolio status report FAILED — {date.today():%d %b %Y}",
                    text="The weekly status report did not go out. Last output:\n\n" + log)
        out = ROOT / "reports" / f"failure_{date.today()}.eml"
    else:
        report = args.report or latest_report()
        day, c = counts_for(report)
        subject = cfg.get("subject", "Portfolio Status — {date}: {red} Red, {amber} Amber, {green} Green").format(
            date=date.fromisoformat(day).strftime("%d %b %Y"),
            red=c["Red"], amber=c["Amber"], green=c["Green"], complete=c["Complete"])
        msg = build(cfg, subject, html=report.read_text(encoding="utf-8"),
                    attachment=report if cfg.get("attach_html", True) else None)
        out = report.with_suffix(".eml")

    if args.dry_run:
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(bytes(msg))
        print(f"Dry run — email saved to {out} (open it in Thunderbird/Outlook to preview)")
        print(f"  Subject: {msg['Subject']}\n  To: {msg['To']}" + (f"\n  Cc: {msg['Cc']}" if msg['Cc'] else ""))
        return
    send(cfg, msg)
    print(f"Sent '{msg['Subject']}' to {msg['To']}")


if __name__ == "__main__":
    main()
