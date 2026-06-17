#!/usr/bin/env python3
"""Cross-project status — a one-shot digest of every Mission Control project.

Replaces "open each project and ask for a brief status update". Pure read-only
client over the EXISTING `GET /api/projects` endpoint (server-authoritative
`live_agent` + `activity_log`) — no server changes, no restart, no new state.

The status collapse mirrors the dashboard's own `friendlyStatus()`
(static/js/render-core.js) so this prints the SAME five states the UI shows for
a closed project: working / asking / stuck / done / idle.

Usage:
  python tools/status/status.py            # digest (hides projects dormant >30d)
  python tools/status/status.py --all      # include dormant projects too
  python tools/status/status.py --needs    # ONLY projects that need you (quick check)
  python tools/status/status.py --json     # machine-readable
  python tools/status/status.py --port 5199
  MC_PORT=5199 python tools/status/status.py

Exit code: 0 = ok, 1 = server unreachable, 2 = something needs you (with --needs).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Windows consoles default to cp1252 and choke on the status glyphs / em-dashes
# in activity messages (arch_misc_tips "Python emoji on Windows"). Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

DEFAULT_PORT = int(os.environ.get("MC_PORT", "5199"))

# Bucket order = urgency. asking+stuck collapse into one "NEEDS YOU" headline
# (mirrors the dashboard's _buildAttentionList in static/js/feed.js).
GROUPS = [
    ("needs", "⚠  NEEDS YOU"),      # ⚠
    ("working", "▶  WORKING"),       # ▶
    ("done", "✓  DONE (recent)"),    # ✓
    ("idle", "·  IDLE / where we left off"),  # ·
]


def _fetch(port, timeout=15):
    url = f"http://localhost:{port}/api/projects"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        sys.stderr.write(
            f"Cannot reach Mission Control at {url} ({reason}).\n"
            f"Is the server running? Try a different --port if it's not on {port}.\n"
        )
        raise SystemExit(1)


def _last_activity(p):
    al = p.get("activity_log") or []
    return al[0] if al else None


def friendly_status(p):
    """Server-side subset of static/js/render-core.js friendlyStatus().

    The client also consults agentHistory/hivemind caches, but those are only
    fresh for a project whose modal is open — exactly the staleness this digest
    avoids by trusting the server-authoritative live_agent. For a CLOSED errored
    session there is no live_agent (the live map keeps only running/idle), so we
    additionally catch an unambiguous 'resume failed' from the latest activity
    entry; the noisier ' error' substring match is intentionally NOT used here,
    to avoid a status digest that cries wolf.
    """
    la = p.get("live_agent") or None
    if la:
        st = la.get("state")
        if st == "asking":
            return "asking"
        if st == "working":
            return "working"
        if st == "idle":
            return "done"  # idle-agent (process alive, nothing pending) → done
    if p.get("blocked"):
        return "stuck"
    la0 = _last_activity(p)
    if la0 and (la0.get("msg") or "").lower().startswith("resume failed"):
        return "stuck"
    status = p.get("status")
    if status == "waiting":
        return "asking"
    if status == "blocked":
        return "stuck"
    if status == "completed":
        return "done"
    if status == "parked":
        return "idle"
    return "idle"


def _bucket(fs):
    if fs in ("asking", "stuck"):
        return "needs"
    return fs  # working / done / idle


def _clean(s, width):
    s = " ".join(str(s or "").split())  # collapse newlines/runs of whitespace
    if len(s) > width:
        s = s[: width - 1].rstrip() + "…"  # …
    return s


def line_icon(p, fs):
    if fs == "asking":
        reason = (p.get("live_agent") or {}).get("reason")
        if reason == "plan":
            return "\U0001F4CB"  # 📋
        if reason == "question":
            return "❓"      # ❓
        return "✋"          # ✋
    if fs == "stuck":
        return "⚠"          # ⚠
    if fs == "working":
        return "⟳"          # ⟳
    if fs == "done":
        return "✓"          # ✓
    return "·"              # ·


def brief(p, fs):
    la = p.get("live_agent") or {}
    if fs == "asking":
        reason = la.get("reason")
        if reason == "plan":
            return "Plan ready — needs approval"
        if reason == "question":
            return "Question pending — needs answer"
        return "Awaiting input to proceed"
    if fs == "stuck":
        if p.get("blocked"):
            r = p.get("blocked_reason")
            return f"Blocked: {r}" if r else "Blocked"
        return "Stuck — needs intervention"
    if fs == "working":
        t = (la.get("task") or p.get("current_task") or "").strip()
        return t or "Working…"
    # done / idle: the "where we left off" line = last logged activity.
    la0 = _last_activity(p)
    if la0 and la0.get("msg"):
        return la0["msg"].strip()
    return (p.get("summary") or "").strip() or "—"


def when(p, fs):
    """For live states, how long it's been waiting; else when it last did
    something."""
    if fs in ("working", "asking", "stuck"):
        return p.get("last_updated_relative") or ""
    la0 = _last_activity(p)
    return (la0 or {}).get("ts_relative") or p.get("last_updated_relative") or ""


def _age_days(p):
    ts = p.get("last_updated")
    if not ts:
        return 1e9
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return 1e9


def main():
    ap = argparse.ArgumentParser(description="Cross-project Mission Control status digest")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--all", action="store_true", help="include projects dormant >30 days")
    ap.add_argument("--needs", action="store_true", help="only show projects that need you")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--dormant-days", type=int, default=30, help="hide idle/done projects older than this (default 30)")
    args = ap.parse_args()

    projects = _fetch(args.port)

    rows = []
    for p in projects:
        fs = friendly_status(p)
        rows.append({
            "id": p.get("id"),
            "name": p.get("name") or p.get("id"),
            "domain": p.get("domain"),
            "state": fs,
            "bucket": _bucket(fs),
            "brief": brief(p, fs),
            "when": when(p, fs),
            "age_days": _age_days(p),
            "blocked": bool(p.get("blocked")),
            "_icon": line_icon(p, fs),
        })

    if args.json:
        for r in rows:
            r.pop("_icon", None)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    by_bucket = {k: [] for k, _ in GROUPS}
    for r in rows:
        by_bucket[r["bucket"]].append(r)
    # Freshest first within each bucket, by the project's real last_updated.
    order = {p.get("id"): p.get("last_updated") or "" for p in projects}
    for k in by_bucket:
        by_bucket[k].sort(key=lambda r: order.get(r["id"], ""), reverse=True)

    needs = by_bucket["needs"]

    # --needs: terse, scriptable "anything blocked?" check.
    if args.needs:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not needs:
            print(f"✓ Nothing needs you — {len(rows)} projects, all clear.  ({ts})")
            raise SystemExit(0)
        print(f"⚠ {len(needs)} need you  ({ts})")
        w = min(28, max(len(r["name"]) for r in needs))
        for r in needs:
            print(f"  {r['_icon']} {r['name']:<{w}}  {_clean(r['brief'], 60)}  ({r['when']})")
        raise SystemExit(2)

    # Full digest.
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {k: len(by_bucket[k]) for k, _ in GROUPS}
    summary = "  ·  ".join(
        f"{counts[k]} {k}" for k, _ in GROUPS if counts[k]
    ) or "no projects"
    print(f"Mission Control — cross-project status   ({ts})")
    print(summary)

    all_names = [r["name"] for r in rows] or [""]
    w = min(28, max(len(n) for n in all_names))

    for key, header in GROUPS:
        bucket = by_bucket[key]
        if not bucket:
            continue
        shown, hidden = bucket, []
        if key in ("idle", "done") and not args.all:
            shown = [r for r in bucket if r["age_days"] <= args.dormant_days]
            hidden = [r for r in bucket if r["age_days"] > args.dormant_days]
        if not shown and not hidden:
            continue
        print(f"\n{header} ({len(bucket)})")
        for r in shown:
            tail = f"  ({r['when']})" if r["when"] else ""
            print(f"  {r['_icon']} {r['name']:<{w}}  {_clean(r['brief'], 58)}{tail}")
        if hidden:
            # No silent caps: say exactly what was withheld and how to see it.
            print(f"  … +{len(hidden)} dormant >{args.dormant_days}d (use --all)")


if __name__ == "__main__":
    main()
