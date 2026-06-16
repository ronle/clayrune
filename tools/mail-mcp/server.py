#!/usr/bin/env python3
"""Global stdio MCP server: READ-ONLY access to a Gmail inbox over IMAP.

Reuses the same Gmail App Password already configured for the night-review
mailer — no new credential. Resolution order (env wins), identical to
tools/night-review/send_mail.py so one config serves both:

  1. Env: NIGHT_MAIL_USER, NIGHT_MAIL_APP_PASSWORD
  2. JSON at ~/.clayrune/night-mail.json: {"user": "...", "app_password": "..."}

Exposes three tools, all READ-ONLY (IMAP SELECT is readonly=True — this server
can never flag, move, or delete mail):
  - list_recent(count, mailbox)     -> recent message headers
  - search_email(query, count)      -> Gmail-syntax search (X-GM-RAW)
  - read_email(uid, mailbox)        -> full text body of one message

Stdlib only (imaplib/email/json). Registered globally in the Clayrune MCP
config so any project can read the inbox.

CLI self-test (no MCP):  python server.py --selftest
"""
from __future__ import annotations

import imaplib
import json
import os
import sys
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from pathlib import Path

CONFIG_PATH = Path.home() / ".clayrune" / "night-mail.json"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SERVER_INFO = {"name": "mail", "version": "0.1.0"}


# ── credentials ────────────────────────────────────────────────────────────
def _load_creds() -> tuple[str, str]:
    cfg: dict = {}
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[mail-mcp] warning: cannot read {CONFIG_PATH}: {e}",
              file=sys.stderr)
    user = (os.environ.get("NIGHT_MAIL_USER") or cfg.get("user") or "").strip()
    pw = (os.environ.get("NIGHT_MAIL_APP_PASSWORD")
          or cfg.get("app_password") or "").replace(" ", "").strip()
    if not user or not pw:
        raise RuntimeError(
            "Gmail credentials not configured. Set NIGHT_MAIL_USER + "
            f"NIGHT_MAIL_APP_PASSWORD or create {CONFIG_PATH}.")
    return user, pw


def _connect() -> imaplib.IMAP4_SSL:
    user, pw = _load_creds()
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    M.login(user, pw)
    return M


# ── helpers ────────────────────────────────────────────────────────────────
def _dec(raw: str | None) -> str:
    if not raw:
        return ""
    out = ""
    for part, enc in decode_header(raw):
        if isinstance(part, bytes):
            out += part.decode(enc or "utf-8", "replace")
        else:
            out += part
    return out


def _headers(M: imaplib.IMAP4_SSL, uid: bytes) -> dict:
    typ, d = M.uid("FETCH", uid,
                   "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if typ != "OK" or not d or not d[0]:
        return {"uid": uid.decode(), "from": "", "subject": "", "date": ""}
    msg = message_from_bytes(d[0][1])
    return {
        "uid": uid.decode(),
        "from": _dec(msg.get("From")),
        "subject": _dec(msg.get("Subject")),
        "date": _dec(msg.get("Date")),
    }


def _body_text(msg: Message) -> str:
    """Prefer text/plain; fall back to a crude HTML strip."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8",
                                      "replace")
            except Exception:
                continue
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            text = (payload.decode(msg.get_content_charset() or "utf-8",
                                   "replace") if payload else "")
        except Exception:
            text = msg.get_payload() or ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    if plain:
        return plain.strip()
    if html:
        import re
        no_tags = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"[ \t]*\n[ \t]*", "\n", no_tags).strip()
    return ""


# ── tool implementations ───────────────────────────────────────────────────
def tool_list_recent(count: int = 10, mailbox: str = "INBOX") -> str:
    count = max(1, min(int(count), 50))
    M = _connect()
    try:
        M.select(mailbox, readonly=True)
        typ, data = M.uid("SEARCH", None, "ALL")
        ids = data[0].split() if data and data[0] else []
        recent = ids[-count:][::-1]
        rows = [_headers(M, u) for u in recent]
    finally:
        try:
            M.logout()
        except Exception:
            pass
    if not rows:
        return f"(no messages in {mailbox})"
    return "\n".join(
        f"[uid {r['uid']}] {r['date']}\n  FROM: {r['from']}\n  SUBJ: {r['subject']}"
        for r in rows)


def tool_search_email(query: str, count: int = 20) -> str:
    count = max(1, min(int(count), 50))
    M = _connect()
    try:
        M.select("INBOX", readonly=True)
        # Gmail full search syntax (from:, subject:, newer_than:1d, has:attachment…)
        typ, data = M.uid("SEARCH", None, "X-GM-RAW", f'"{query}"')
        if typ != "OK":
            typ, data = M.uid("SEARCH", None, "TEXT", query)
        ids = data[0].split() if data and data[0] else []
        hits = ids[-count:][::-1]
        rows = [_headers(M, u) for u in hits]
    finally:
        try:
            M.logout()
        except Exception:
            pass
    if not rows:
        return f"(no matches for: {query})"
    return f"{len(rows)} match(es) for '{query}':\n\n" + "\n".join(
        f"[uid {r['uid']}] {r['date']}\n  FROM: {r['from']}\n  SUBJ: {r['subject']}"
        for r in rows)


def tool_read_email(uid: str, mailbox: str = "INBOX") -> str:
    M = _connect()
    try:
        M.select(mailbox, readonly=True)
        typ, d = M.uid("FETCH", str(uid).encode(), "(RFC822)")
        if typ != "OK" or not d or not d[0]:
            return f"(no message with uid {uid} in {mailbox})"
        msg = message_from_bytes(d[0][1])
        head = (f"FROM: {_dec(msg.get('From'))}\n"
                f"TO: {_dec(msg.get('To'))}\n"
                f"DATE: {_dec(msg.get('Date'))}\n"
                f"SUBJECT: {_dec(msg.get('Subject'))}\n"
                f"{'-'*60}\n")
        body = _body_text(msg)
        return head + (body if body else "(empty body)")
    finally:
        try:
            M.logout()
        except Exception:
            pass


TOOLS = {
    "list_recent": {
        "fn": tool_list_recent,
        "schema": {
            "name": "list_recent",
            "description": ("List the most recent emails in a Gmail mailbox "
                            "(headers only: uid, from, subject, date). "
                            "Read-only. Use read_email with a uid for the body."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer",
                              "description": "How many recent messages (1-50, default 10)."},
                    "mailbox": {"type": "string",
                                "description": "Mailbox/label (default INBOX)."},
                },
            },
        },
    },
    "search_email": {
        "fn": tool_search_email,
        "schema": {
            "name": "search_email",
            "description": ("Search the Gmail account using full Gmail query "
                            "syntax (e.g. 'from:amazon newer_than:7d', "
                            "'subject:invoice has:attachment'). Returns matching "
                            "headers. Read-only."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Gmail search query."},
                    "count": {"type": "integer",
                              "description": "Max results (1-50, default 20)."},
                },
                "required": ["query"],
            },
        },
    },
    "read_email": {
        "fn": tool_read_email,
        "schema": {
            "name": "read_email",
            "description": ("Read the full text body + headers of one email by "
                            "its uid (from list_recent / search_email). "
                            "Read-only."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string",
                            "description": "Message uid from a list/search result."},
                    "mailbox": {"type": "string",
                                "description": "Mailbox the uid came from (default INBOX)."},
                },
                "required": ["uid"],
            },
        },
    },
}


# ── MCP stdio loop ─────────────────────────────────────────────────────────
def _resp(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            _resp(rid, {"protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": SERVER_INFO})
        elif method == "notifications/initialized":
            continue  # notification: no response
        elif method == "tools/list":
            _resp(rid, {"tools": [t["schema"] for t in TOOLS.values()]})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            tool = TOOLS.get(name)
            if not tool:
                _resp(rid, error={"code": -32601,
                                  "message": f"unknown tool: {name}"})
                continue
            try:
                text = tool["fn"](**args)
                _resp(rid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                _resp(rid, {"content": [{"type": "text",
                                         "text": f"error: {e}"}],
                            "isError": True})
        elif rid is not None:
            _resp(rid, error={"code": -32601,
                              "message": f"unknown method: {method}"})


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("=== list_recent(3) ===")
        print(tool_list_recent(3))
        sys.exit(0)
    _serve()
