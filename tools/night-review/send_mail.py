#!/usr/bin/env python3
"""Night-review mailer — send a plain-text email via Gmail SMTP.

Used by the Clayrune night-shift maintenance schedule to email the reviewer a
summary of each run. Stdlib only; no third-party deps.

Credentials (never committed) are read from, in order of precedence:
  1. Environment: NIGHT_MAIL_USER, NIGHT_MAIL_APP_PASSWORD, NIGHT_MAIL_TO,
     NIGHT_MAIL_HOST, NIGHT_MAIL_PORT
  2. JSON config at ~/.clayrune/night-mail.json:
       {
         "user": "you@gmail.com",
         "app_password": "abcd efgh ijkl mnop",   # Gmail App Password (2FA req'd)
         "to": "leviran1@gmail.com",
         "host": "smtp.gmail.com",   # optional, defaults to smtp.gmail.com
         "port": 587                  # optional, defaults to 587
       }

Gmail requires an App Password (https://myaccount.google.com/apppasswords),
NOT your normal account password. See tools/night-review/README.md.

Exit codes: 0 = sent, 2 = credentials missing / invalid args, 1 = send failed.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

CONFIG_PATH = Path.home() / ".clayrune" / "night-mail.json"
DEFAULT_TO = "leviran1@gmail.com"
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587


def _load_config() -> dict:
    """Read the JSON config, then overlay any env vars (env wins)."""
    cfg: dict = {}
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[send_mail] warning: could not read {CONFIG_PATH}: {e}",
              file=sys.stderr)
    for key, env in (
        ("user", "NIGHT_MAIL_USER"),
        ("app_password", "NIGHT_MAIL_APP_PASSWORD"),
        ("to", "NIGHT_MAIL_TO"),
        ("host", "NIGHT_MAIL_HOST"),
        ("port", "NIGHT_MAIL_PORT"),
    ):
        val = os.environ.get(env)
        if val:
            cfg[key] = val
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Send a plain-text email via Gmail SMTP.")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", help="Inline body text.")
    ap.add_argument("--body-file",
                    help="Path to a file whose contents become the body.")
    ap.add_argument("--to", help="Override recipient (default from config/env).")
    args = ap.parse_args()

    # Resolve body: --body > --body-file > stdin.
    if args.body is not None:
        body = args.body
    elif args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"[send_mail] cannot read --body-file {args.body_file}: {e}",
                  file=sys.stderr)
            return 2
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        print("[send_mail] no body: pass --body, --body-file, or pipe via stdin",
              file=sys.stderr)
        return 2

    cfg = _load_config()
    user = (cfg.get("user") or "").strip()
    # Gmail shows App Passwords as "abcd efgh ijkl mnop"; SMTP wants no spaces.
    app_password = (cfg.get("app_password") or "").replace(" ", "").strip()
    to_addr = (args.to or cfg.get("to") or DEFAULT_TO).strip()
    host = (cfg.get("host") or DEFAULT_HOST).strip()
    try:
        port = int(cfg.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    if not user or not app_password:
        print(
            "[send_mail] Gmail credentials not configured. Set NIGHT_MAIL_USER "
            f"+ NIGHT_MAIL_APP_PASSWORD, or create {CONFIG_PATH}. "
            "See tools/night-review/README.md.",
            file=sys.stderr,
        )
        return 2

    msg = EmailMessage()
    msg["Subject"] = args.subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            smtp.login(user, app_password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[send_mail] send failed via {host}:{port}: {e}", file=sys.stderr)
        return 1

    print(f"[send_mail] sent '{args.subject}' to {to_addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
