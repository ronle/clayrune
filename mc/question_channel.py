"""Offline question channel — deliver an agent's question when nobody is watching.

An agent raises a question (an ```mc:question``` fence — see
`agent_runtime.MC_TOOL_PROTOCOL_PROMPT`). If the user is looking at the session,
the existing SSE → interactive-form path handles it and this module does nothing.

If the run is **unattended** — a schedule, a steward cycle, the night review, or
simply a tab the user closed — that form renders to an empty room. The agent then
hangs until the guardian notices, and the question is effectively lost. This
module is the fallback: it sends the question over the user's configured channel
so they can answer it **offline**, and feeds the reply back into the same
follow-up path the UI uses.

## Attended vs unattended

A question is *attended* if an SSE viewer polled the session recently
(`session['_last_sse_poll_time']` — the same heartbeat the guardian already uses
to decide a question "may have been missed").

The **grace window is the whole trick**. Deliver instantly and a user who opens
the tab three seconds later gets a pointless email; never deliver and an
unattended run hangs forever. So: raise → wait `question_channel_grace_s` →
re-check for a viewer → deliver only if there is still nobody there.

## Posture

Best-effort, and **never load-bearing**: every entry point swallows its own
errors. A dead mailbox must not break an agent turn. Same rule as Scribe and the
Distiller.

## Reply → resume

The subject carries the question id. `poll_replies()` matches an inbound reply to
a pending question, maps the body to an option (a number, a label, or free text)
and answers via `POST /agent/followup` — the exact path the chat form uses, so
there is no second resume mechanism to keep in sync.

**Idempotency is load-bearing**: a question is delivered once and answered once.
Anything else trains the user to ignore the channel.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from mc import state
from mc.core import _log

# Question ids we have already delivered / already answered. In-memory is the
# right scope: a restart drops the agent sessions these refer to anyway.
_delivered: set[str] = set()
_answered: set[str] = set()
_lock = threading.Lock()

# qid -> everything poll_replies() needs to answer it without touching sessions.
_outbox: Dict[str, Dict[str, Any]] = {}

_SUBJECT_TAG = "[Clayrune question]"
_QID_RE = re.compile(r"\bq:([0-9a-f]{8})\b")

_MAILER = Path(__file__).resolve().parent.parent / "tools" / "night-review" / "send_mail.py"


# ─── Config ──────────────────────────────────────────────────────────────────


def _cfg(project: Optional[dict], key: str, default: Any) -> Any:
    """Per-project value, falling back to global CONFIG, falling back to default."""
    if project:
        v = project.get(key)
        if v not in (None, ""):
            return v
    v = state.CONFIG.get(key)
    return default if v in (None, "") else v


def channel_for(project: Optional[dict]) -> str:
    """`email` | `off`. Default email — a question nobody sees is the bug."""
    return str(_cfg(project, "question_channel", "email")).lower()


def grace_seconds(project: Optional[dict]) -> int:
    try:
        return max(0, int(_cfg(project, "question_channel_grace_s", 45)))
    except (TypeError, ValueError):
        return 45


# ─── Attended? ───────────────────────────────────────────────────────────────


def is_attended(session: dict, *, grace: int) -> bool:
    """Is a human actually looking at this session right now?

    The signal is the SSE poll heartbeat, which only ticks while a browser is
    streaming this session. `trigger_type` alone is NOT enough: a scheduled run
    the user happens to be watching should still answer in the chat, and a manual
    run whose tab was closed is just as unattended as a cron job.
    """
    last = session.get("_last_sse_poll_time") or 0
    return (time.time() - last) <= grace


# ─── Rendering ───────────────────────────────────────────────────────────────


def render(project_name: str, session: dict, qid: str, questions: list) -> tuple[str, str]:
    """(subject, body). The body must stand alone — it may be read days later, on
    a phone, by someone with no memory of this session."""
    subject = f"{_SUBJECT_TAG} {project_name} · q:{qid[:8]}"

    task = (session.get("task") or "").strip()
    lines = [
        "An agent is waiting on your answer. Nobody was watching the session, so",
        "it is coming to you here instead.",
        "",
        f"Project : {project_name}",
    ]
    if task:
        lines.append(f"Task    : {task[:300]}")
    trig = session.get("trigger_type") or "manual"
    lines += [f"Trigger : {trig}", "", "-" * 60, ""]

    for qi, q in enumerate(questions, 1):
        if not isinstance(q, dict):
            continue
        header = (q.get("header") or "").strip()
        text = (q.get("question") or "").strip()
        lines.append(f"Q{qi}. {text}" + (f"   [{header}]" if header else ""))
        opts = q.get("options") or []
        for oi, opt in enumerate(opts, 1):
            if isinstance(opt, dict):
                label = (opt.get("label") or "").strip()
                desc = (opt.get("description") or "").strip()
            else:
                label, desc = str(opt), ""
            lines.append(f"    {oi}. {label}" + (f" — {desc}" if desc else ""))
        lines.append("")

    lines += [
        "-" * 60,
        "",
        "TO ANSWER: reply to this email. Keep the subject line intact (it carries",
        f"the question id q:{qid[:8]}). The first line of your reply is the answer:",
        "",
        "  • a number   -> picks that option (e.g. \"2\")",
        "  • a label    -> picks that option by name",
        "  • anything else -> sent to the agent verbatim",
        "",
        "The agent stays parked until you reply. If you never do, nothing happens —",
        "it simply never resumes.",
        "",
        "-- Clayrune",
    ]
    return subject, "\n".join(lines)


# ─── Delivery ────────────────────────────────────────────────────────────────


def _send_email(subject: str, body: str, to: Optional[str]) -> bool:
    """Reuse the existing mailer. Per AGENT_RULES: no new SMTP code, no new creds."""
    if not _MAILER.exists():
        _log(f"[question-channel] mailer not found at {_MAILER}", flush=True)
        return False
    cmd = [sys.executable, str(_MAILER), "--subject", subject, "--body", body]
    if to:
        cmd += ["--to", to]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            _log(f"[question-channel] send failed rc={r.returncode}: "
                 f"{(r.stderr or r.stdout or '').strip()[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        _log(f"[question-channel] send failed: {e}", flush=True)
        return False


def on_question_raised(session: dict) -> None:
    """Called at the turn boundary when a question was parsed. Non-blocking.

    Arms a timer for the grace window rather than deciding now: the user may be
    two seconds from opening the tab, and an email they didn't need is how a
    notification channel earns itself an ignore rule.
    """
    pending = session.get("pending_questions") or []
    if not pending:
        return
    q = pending[-1]
    qid = q.get("question_id") or ""
    if not qid:
        return

    with _lock:
        if qid in _delivered:
            return          # exactly-once, even if the turn scan runs twice
        _delivered.add(qid)

    project_id = session.get("project_id") or ""
    try:
        # Imported lazily: project_routes imports plenty, and this module is
        # pulled in from inside a stream reader.
        from mc.blueprints.project_routes import load_project
        project = load_project(project_id) or {}
    except Exception:
        project = {}

    if channel_for(project) == "off":
        return

    grace = grace_seconds(project)
    _outbox[qid] = {
        "project_id": project_id,
        "project_name": project.get("name") or project_id,
        "session_id": session.get("session_id") or "",
        "questions": q.get("questions") or [],
        "to": _cfg(project, "question_channel_to", None),
    }

    t = threading.Timer(grace, _deliver_if_still_unattended, args=(qid, session, grace))
    t.daemon = True
    t.start()


def _deliver_if_still_unattended(qid: str, session: dict, grace: int) -> None:
    try:
        # Answered in the chat while we waited? Then there WAS someone there.
        if not session.get("waiting_for_question"):
            return
        if is_attended(session, grace=grace):
            return

        meta = _outbox.get(qid)
        if not meta:
            return

        subject, body = render(meta["project_name"], session, qid, meta["questions"])
        if _send_email(subject, body, meta.get("to")):
            _log(f"[question-channel] delivered q:{qid[:8]} "
                 f"({meta['project_name']}) — nobody was watching", flush=True)
            session.setdefault("log_lines", []).append(
                f"[question sent to your offline channel — reply to the email "
                f"(q:{qid[:8]}) and I'll pick it up]")
    except Exception as e:
        _log(f"[question-channel] delivery failed for {qid[:8]}: {e}", flush=True)


# ─── Reply → answer ──────────────────────────────────────────────────────────


def match_answer(reply_line: str, questions: list) -> str:
    """Map the first line of a reply onto an option label, or pass it through.

    A number picks by position; otherwise an exact (then loose) label match wins;
    otherwise the text goes to the agent verbatim. We never *guess* an option
    from a vague reply — passing the words through is always safe, silently
    picking the wrong option is not.
    """
    text = (reply_line or "").strip()
    if not text:
        return ""
    opts = []
    for q in questions or []:
        if isinstance(q, dict):
            for o in (q.get("options") or []):
                opts.append((o.get("label") or "") if isinstance(o, dict) else str(o))

    if text.isdigit() and opts:
        i = int(text)
        if 1 <= i <= len(opts):
            return opts[i - 1]
        return text

    low = text.lower()
    for label in opts:
        if label and label.lower() == low:
            return label
    for label in opts:
        if label and (low in label.lower() or label.lower() in low):
            return label
    return text


def _answer(qid: str, answer: str) -> bool:
    """Resume the agent through the SAME follow-up path the chat form uses."""
    meta = _outbox.get(qid)
    if not meta or not answer:
        return False
    # Same resolution order as server.py: env wins over config.
    port = int(os.environ.get("MC_PORT") or state.CONFIG.get("port") or 5199)
    url = f"http://localhost:{port}/api/project/{meta['project_id']}/agent/followup"
    payload = json.dumps({
        "message": answer,
        "session_id": meta["session_id"],
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = 200 <= r.status < 300
        if ok:
            _log(f"[question-channel] q:{qid[:8]} answered offline: {answer[:60]!r}",
                 flush=True)
        return ok
    except Exception as e:
        _log(f"[question-channel] could not deliver answer for {qid[:8]}: {e}", flush=True)
        return False


def handle_reply(subject: str, body: str) -> bool:
    """Given a reply's subject + body, answer the question it refers to.

    Returns True if an agent was actually resumed. Idempotent: a qid is answered
    at most once, so a mail poller re-reading the same thread cannot double-send.
    """
    m = _QID_RE.search(subject or "")
    if not m:
        return False
    short = m.group(1)

    qid = next((k for k in _outbox if k.startswith(short)), None)
    if not qid:
        return False   # unknown or expired — ignore, never guess

    with _lock:
        if qid in _answered:
            return False
        _answered.add(qid)

    first = ""
    for raw in (body or "").splitlines():
        ln = raw.strip()
        # Skip quoted text and the usual reply cruft.
        if not ln or ln.startswith(">") or ln.startswith("On ") and ln.endswith("wrote:"):
            continue
        first = ln
        break

    answer = match_answer(first, _outbox[qid].get("questions") or [])
    if not answer:
        with _lock:
            _answered.discard(qid)   # empty reply — let a later one work
        return False
    return _answer(qid, answer)


# ─── Inbound poller ──────────────────────────────────────────────────────────
#
# A purpose-built IMAP read, NOT a reuse of tools/mail-mcp/server.py: that server
# is agent-facing and returns human-formatted strings, which is the wrong shape
# for programmatic matching. It does share the same credentials, so there is
# still only one secret.

_CREDS = Path.home() / ".clayrune" / "night-mail.json"
_IMAP_HOST = "imap.gmail.com"


def _creds() -> Optional[tuple[str, str]]:
    user = os.environ.get("NIGHT_MAIL_USER", "")
    pw = os.environ.get("NIGHT_MAIL_APP_PASSWORD", "")
    if not (user and pw):
        try:
            cfg = json.loads(_CREDS.read_text(encoding="utf-8"))
            user = user or cfg.get("user", "")
            pw = pw or cfg.get("app_password", "")
        except Exception:
            return None
    pw = (pw or "").replace(" ", "").strip()
    return (user, pw) if user and pw else None


def poll_replies() -> int:
    """Scan the inbox for replies to outstanding questions. Returns #answered.

    Only looks when we are actually waiting on something — an idle Clayrune must
    not sit there logging into a mailbox every minute for no reason.
    """
    with _lock:
        outstanding = [q for q in _outbox if q not in _answered]
    if not outstanding:
        return 0

    creds = _creds()
    if not creds:
        return 0

    import imaplib
    from email import message_from_bytes
    from email.header import decode_header, make_header

    answered = 0
    try:
        M = imaplib.IMAP4_SSL(_IMAP_HOST, 993, timeout=30)
        M.login(*creds)
        try:
            M.select("INBOX", readonly=True)
            typ, data = M.search(None, 'SUBJECT', '"Clayrune question"')
            if typ != "OK":
                return 0
            uids = (data[0].split() if data and data[0] else [])[-30:]
            for uid in uids:
                typ, msg_data = M.fetch(uid, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = message_from_bytes(msg_data[0][1])
                subject = str(make_header(decode_header(msg.get("Subject", ""))))
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            raw = part.get_payload(decode=True)
                            if isinstance(raw, bytes):
                                body = raw.decode(
                                    part.get_content_charset() or "utf-8", "replace")
                            break
                else:
                    raw = msg.get_payload(decode=True)
                    if isinstance(raw, bytes):
                        body = raw.decode(
                            msg.get_content_charset() or "utf-8", "replace")
                if handle_reply(subject, body):
                    answered += 1
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except Exception as e:
        _log(f"[question-channel] inbox poll failed: {e}", flush=True)
    return answered


def start_poller(interval_s: int = 120) -> None:
    """Background loop. Best-effort; a mailbox outage must never touch an agent."""
    def _loop():
        while True:
            try:
                poll_replies()
            except Exception as e:
                _log(f"[question-channel] poller error: {e}", flush=True)
            time.sleep(max(30, interval_s))

    t = threading.Thread(target=_loop, name="question-channel-poller", daemon=True)
    t.start()
    _log("[question-channel] reply poller started", flush=True)
