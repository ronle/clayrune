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
    """Classify a Bash command string. Returns (blocked, reason)."""
    if not command or not command.strip():
        return FenceDecision(False, '')
    cmd = command.strip()

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


def main() -> int:
    """PreToolUse hook entrypoint. Reads the hook JSON on stdin; on a blocked
    action, emits a deny decision AND exits 2 (stderr reason) so the block lands
    across CLI versions. Fails OPEN on any parse error — a broken fence must not
    wedge the steward (best-effort posture); the directive still constrains it."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # fail open — never wedge the agent on a malformed hook event

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
