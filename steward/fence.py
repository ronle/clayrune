#!/usr/bin/env python3
"""Steward reversibility FENCE — the load-bearing safety backstop.

An unattended steward runs with `--dangerously-skip-permissions` like every MC
agent, so its ONLY hard constraint is this fence, wired as a PreToolUse hook.
The prompt directive (mc-steward SKILL.md) is the *primary* control — it teaches
the steward to ask before irreversible actions. This fence is the *backstop*: a
denylist of catastrophic / irreversible command shapes that get hard-BLOCKED in
code even if the model tries them anyway. Belt (fence) + suspenders (directive).

DESIGN — asymmetric risk. A false-block (stopping a safe command) merely makes
the steward pause and post a decision-needed item; a false-allow (letting an
irreversible command through) can't be undone. So the fence biases to BLOCK on
the catastrophic verbs and only needs zero false-NEGATIVES on that set — it does
NOT try to catch everything (the directive covers judgment). Default is ALLOW so
reversible work (edits, reads, analysis, localhost API calls) flows unattended.

Self-contained (stdlib only) so it runs as a standalone hook script from any cwd:
    python "<repo>/steward/fence.py"      # reads PreToolUse JSON on stdin
"""
import json
import re
import sys
from typing import NamedTuple


class FenceDecision(NamedTuple):
    blocked: bool
    reason: str


# Markers that make a destructive path clearly scratch-scoped (→ allow deletes).
_SCRATCH_MARKERS = ('_scratch', 'scratchpad', '/tmp/', '\\temp\\', 'appdata/local/temp',
                    'appdata\\local\\temp')

# Hosts that count as "local" — the steward's own reporting/API calls target
# these and must pass the fence.
_LOCAL_HOSTS = ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]')

# Irreversible command shapes → BLOCK. Each entry: (compiled regex, human reason).
# Matched case-insensitively against the WHOLE command string (so chained
# commands like `cd x && git push` are still caught).
_BLOCK_PATTERNS = [
    (re.compile(r'\bgit\s+push\b', re.I),
     "git push writes to a shared remote (irreversible once others fetch)"),
    (re.compile(r'\bgit\s+reset\s+--hard\b', re.I),
     "git reset --hard discards working-tree state destructively"),
    (re.compile(r'\bgit\s+clean\b\s+-\w*f', re.I),
     "git clean -f deletes untracked files irrecoverably"),
    (re.compile(r'\bgit\s+(tag\s+-d|push\s+.*--delete|branch\s+-D)\b', re.I),
     "destructive git ref deletion"),
    # Publishes / deploys / releases.
    (re.compile(r'\bnpm\s+publish\b', re.I), "npm publish is an irreversible release"),
    (re.compile(r'\btwine\s+upload\b', re.I), "twine upload publishes a package"),
    (re.compile(r'\bdocker\s+push\b', re.I), "docker push publishes an image"),
    (re.compile(r'\bpip\s+.*\bupload\b', re.I), "package upload"),
    (re.compile(r'\bgh\s+release\s+(create|edit|delete)\b', re.I), "GitHub release mutation"),
    (re.compile(r'\bgh\s+(pr|issue|repo)\s+(create|edit|merge|close|delete|comment)\b', re.I),
     "GitHub write (leaves this box, notifies others)"),
    (re.compile(r'\bgh\s+api\b.*-X\s*(POST|PUT|PATCH|DELETE)', re.I),
     "GitHub API mutation"),
    # Cloud spend / provisioning.
    (re.compile(r'\b(gcloud|aws|az)\b[\s\S]*\b(create|delete|deploy|apply|update|run|start|stop|remove|rm|set|put|destroy)\b', re.I),
     "cloud provisioning/spend (gcloud/aws/az mutation)"),
    (re.compile(r'\bterraform\s+(apply|destroy)\b', re.I), "terraform state mutation"),
    (re.compile(r'\bkubectl\s+(apply|delete|create|scale|rollout)\b', re.I), "kubernetes mutation"),
    # Schema / migrations.
    (re.compile(r'\balembic\s+(upgrade|downgrade)\b', re.I), "database migration"),
    (re.compile(r'\b\w*migrate\b\s+(up|down|deploy|latest)\b', re.I), "database migration"),
    (re.compile(r'\bDROP\s+(TABLE|DATABASE|SCHEMA)\b', re.I), "destructive SQL"),
    (re.compile(r'\bTRUNCATE\s+TABLE\b', re.I), "destructive SQL"),
    # Restarting Clayrune itself (separately human-gated).
    (re.compile(r'/api/system/restart\b', re.I), "server restart is human-gated"),
    # System-destructive.
    (re.compile(r'\bmkfs\b|\bdd\s+if=', re.I), "disk-destructive operation"),
    (re.compile(r'\bshutdown\b|\breboot\b', re.I), "host power operation"),
]

# Destructive-delete verbs. Blocked UNLESS the command is clearly scratch-scoped.
_DELETE_PATTERNS = [
    re.compile(r'\brm\s+-\w*[rf]', re.I),       # rm -r / -f / -rf
    re.compile(r'\brmdir\b', re.I),
    re.compile(r'\bRemove-Item\b', re.I),
    re.compile(r'(^|[\s&|;])del\s', re.I),
    re.compile(r'\brd\s+/s', re.I),
]


# ── Inert-prose masking (false-positive precision fix, 2026-07-16) ───────────
# The block patterns match the WHOLE command string, so PROSE used to trip
# them: the steward could not `git commit` the wake-lock feature because the
# commit message legitimately said "shutdown" (real incident, 2026-07-13
# DECISION NEEDED note — the #1-priority feature sat uncommitted for days).
#
# Masking replaces spans that are PROVABLY inert data with a placeholder
# before classification. "Provably inert" means the shell cannot execute the
# span's content:
#   • a quoted argument directly following -m/--message — an argument to a
#     flag is data, never a command. Double-quoted args are masked only when
#     they contain no substitution tokens ($( or `), which WOULD execute.
#   • a quoted-delimiter heredoc body (<<'EOF' / <<"EOF" — quoting the
#     delimiter disables expansion) and a PowerShell LITERAL here-string
#     (@'...'@) — both are raw data, UNLESS the heredoc's own header line
#     mentions a shell/interpreter (bash <<'EOF' executes its stdin), in
#     which case that span is left unmasked and the patterns see everything.
#
# Command-position text is NEVER masked, so the deny-scope is unchanged:
# `git commit -m "x" && shutdown` still blocks on the unmasked tail, and
# `git commit -m "$(shutdown)"` is never masked at all. False-negative risk
# stays where the design puts it: zero on the catastrophic set.
_MASK_TOKEN = 'INERT_PROSE'

_MSG_ARG_RE = re.compile(
    r"""((?:^|\s)(?:-m|--message)(?:=|\s+))(?P<q>['"])(?P<body>(?:\\.|[^\\])*?)(?P=q)""",
    re.S)

_HEREDOC_RE = re.compile(
    r"""(?P<head>[^\n]*<<-?\s*(?P<q>['"])(?P<tag>\w+)(?P=q)[^\n]*\n)"""
    r"""(?P<body>.*?\n)(?P<term>[ \t]*(?P=tag)[ \t]*(?=\n|$|[)"';]))""",
    re.S)

_PS_HERESTRING_RE = re.compile(
    r"(?P<head>[^\n]*@'[ \t]*\n)(?P<body>.*?)(?P<term>\n'@)", re.S)

# Anything that can turn stdin/data back into execution.
_INTERPRETER_RE = re.compile(
    r'\b(bash|sh|zsh|dash|ksh|pwsh|powershell|python\w*|node|perl|ruby|'
    r'eval|source|cmd|iex|invoke-expression)\b', re.I)


def _mask_inert_prose(cmd: str) -> str:
    """Replace provably-inert data spans with a placeholder. Best-effort and
    conservative: any span we cannot PROVE inert is left untouched (fails
    toward blocking, never toward allowing)."""
    def _msg_repl(m):
        if m.group('q') == '"' and ('$(' in m.group('body') or '`' in m.group('body')):
            return m.group(0)          # substitution could execute — leave it
        return f"{m.group(1)}{m.group('q')}{_MASK_TOKEN}{m.group('q')}"

    def _block_repl(m):
        # If the consuming line mentions an interpreter, the "data" may be
        # executed (bash <<'EOF') — leave the whole span for the patterns.
        if _INTERPRETER_RE.search(m.group('head')):
            return m.group(0)
        return f"{m.group('head')}{_MASK_TOKEN}\n{m.group('term')}"

    out = _MSG_ARG_RE.sub(_msg_repl, cmd)
    out = _HEREDOC_RE.sub(_block_repl, out)
    out = _PS_HERESTRING_RE.sub(_block_repl, out)
    return out


def _touches_nonlocal_network(cmd: str) -> FenceDecision:
    """Block external network SENDS (mutating HTTP verbs / uploads to a non-local
    host). Reads (plain GET) and anything targeting localhost are allowed."""
    low = cmd.lower()
    if not re.search(r'\b(curl|wget|http|invoke-webrequest|invoke-restmethod|iwr)\b', low):
        return FenceDecision(False, '')
    mutating = bool(re.search(r'-X\s*(POST|PUT|PATCH|DELETE)', cmd, re.I)) or \
        bool(re.search(r'(^|\s)(--data\b|--data-raw\b|-d\b|--upload-file\b|-T\b|-F\b|--form\b)', cmd)) or \
        bool(re.search(r'-Method\s+(POST|PUT|PATCH|DELETE)', cmd, re.I))
    if not mutating:
        return FenceDecision(False, '')
    if any(h in low for h in _LOCAL_HOSTS):
        return FenceDecision(False, '')  # steward's own API calls
    return FenceDecision(True, "external network send (mutating HTTP to a non-local host)")


def classify_bash(command: str) -> FenceDecision:
    """Classify a Bash command string. Returns (blocked, reason).

    Classification runs on the inert-prose-MASKED command: commit-message /
    literal-heredoc content is data, not commands (see _mask_inert_prose).
    Command-position text always survives masking, so every shape the fence
    blocked before, it still blocks."""
    if not command or not command.strip():
        return FenceDecision(False, '')
    try:
        cmd = _mask_inert_prose(command.strip())
    except Exception:
        cmd = command.strip()   # masking is best-effort; unmasked = stricter

    for pat, reason in _BLOCK_PATTERNS:
        if pat.search(cmd):
            return FenceDecision(True, reason)

    for pat in _DELETE_PATTERNS:
        if pat.search(cmd):
            low = cmd.lower()
            if any(m in low for m in _SCRATCH_MARKERS) and '..' not in cmd:
                break  # scratch-scoped delete → allowed
            return FenceDecision(True, "destructive delete outside scratch (irreversible)")

    net = _touches_nonlocal_network(cmd)
    if net.blocked:
        return net

    return FenceDecision(False, '')


def classify_action(tool_name: str, tool_input: dict) -> FenceDecision:
    """Classify any tool call. Bash is where terminal danger lives; other tools
    default to allow (edits/writes are working-tree-reversible). Extend here if a
    non-Bash irreversible surface appears (e.g. an MCP tool that sends email)."""
    name = (tool_name or '')
    ti = tool_input or {}
    if name == 'Bash':
        return classify_bash(ti.get('command', '') or '')
    # Writing to global config outside the project is out-of-scope for a
    # project steward — block edits/writes targeting ~/.claude or a home dotfile.
    if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = str(ti.get('file_path', '') or ti.get('notebook_path', '') or '')
        low = path.replace('\\', '/').lower()
        if '/.claude/' in low or low.endswith('/.claude'):
            return FenceDecision(True, "editing global ~/.claude config (out of project scope)")
    return FenceDecision(False, '')


STEWARD_MARKER = '[Steward cycle]'


def _first_user_text(transcript_path: str) -> str:
    """Return the text of the FIRST user message in a CC transcript jsonl, or ''.
    A steward session's turn-1 message is the build_cycle_task() prompt, which
    starts with the STEWARD_MARKER — so this is a reliable session-type signal."""
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                msg = entry.get('message', entry)
                role = entry.get('type') or msg.get('role') or ''
                if role != 'user':
                    continue
                content = msg.get('content', '')
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get('type') == 'text':
                            return b.get('text', '')
                        if isinstance(b, str):
                            return b
                return ''
    except Exception:
        return ''
    return ''


def _session_is_steward(payload: dict):
    """True/False if we can confirm the session type from the transcript; None if
    unknown (no/unreadable transcript). Steward cycle → first user msg carries the
    marker."""
    tp = payload.get('transcript_path') or payload.get('transcriptPath') or ''
    if not tp:
        return None
    text = _first_user_text(tp)
    if not text:
        return None
    return STEWARD_MARKER in text


def main() -> int:
    """PreToolUse hook entrypoint. Reads the hook JSON on stdin.

    SELF-GATING: the fence is installed repo-wide (project .claude/settings.json)
    but ENFORCES only for steward-cycle sessions — so manual/dev sessions in the
    same project are unaffected. Gate: `confirmed non-steward → allow all`;
    `steward OR unknown → enforce` (fail-closed on ambiguity, since the fence is
    only ever installed in steward-enabled projects).

    On a blocked action, exits 2 (stderr reason) — the fail-closed block contract.
    Fails OPEN on any parse error — a broken fence must never wedge the agent."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # fail open — never wedge the agent on a malformed hook event

    # Only steward-cycle sessions are fenced; confirmed dev/manual sessions pass.
    if _session_is_steward(payload) is False:
        return 0

    tool_name = payload.get('tool_name') or payload.get('toolName') or ''
    tool_input = payload.get('tool_input') or payload.get('toolInput') or {}

    try:
        decision = classify_action(tool_name, tool_input)
    except Exception:
        return 0  # fail open

    if not decision.blocked:
        return 0

    msg = (f"STEWARD FENCE blocked this action: {decision.reason}. "
           f"This is irreversible/mutating and you are running unattended. "
           f"Do NOT retry it. Instead post a `DECISION NEEDED:` note to your "
           f"charter with the exact command so the human can approve it.")
    # Exit 2 + stderr is the fail-CLOSED block contract: it denies the tool call
    # across all CLI versions (verified against hooks.md — exit 2 blocks even
    # under --dangerously-skip-permissions). JSON permissionDecision is the
    # alternative, but under exit 2 stdout is ignored, and exit-0+JSON would
    # fail OPEN on any CLI that doesn't parse it — wrong posture for a safety
    # backstop. So: exit 2, stderr reason, nothing on stdout.
    print(msg, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
