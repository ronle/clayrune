"""Guide / walkthrough / scribe-read endpoints — blueprint 1.9 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py — FOUR source regions, 5 routes:
  • /api/guide/stream + /api/guide/ask — the "Ask Claydo" assistant, with its
    subprocess glue (_claydo_cwd, _CLAYDO_NO_TOOLS_FLAGS,
    _claydo_recent_changelog, _claydo_prepare_context). guide_stream is the
    plan-flagged 156-line SSE generator — moved whole, NOT split.
  • /api/project/<id>/scribe-stats — Scribe telemetry READ (SPEC §8). Route
    only: the Scribe/condense/checkpoint machinery (_scribe_extract,
    _write_session_memory, _commit_managed_entry, _mem_* helpers, anything
    taking _get_mem_write_lock) is dispatch/teardown family (1.12) and stays
    in server.py untouched — CLAUDE.md "Memory system" lock+atomic discipline.
  • /api/project/<id>/memory/search — read-only retrieval route (SPEC §3
    Leg B; the 1.5 splice-guard leftover). _memory_search itself STAYS in
    server.py: it is shared with the deterministic read floor in
    _build_agent_context (dispatch, 1.12) and walks the deep memory helpers
    (_get_memory_path/_get_archive_path/_mem_split) — so it arrives via wire().
  • /api/walkthrough/sample-project — first-run onboarding project creator,
    with its seed-file helpers (_clayrune_agent_rules, _clayrune_readme).
    NOT _clayrune_api_reference/_clayrune_universal_capabilities — those feed
    _build_agent_context and stay with dispatch.

Single permitted edits (the 1.7 SESSION_LABELS_PATH wired-placeholder
pattern): 5× `Path(__file__).parent` → wired `_SERVER_DIR` (in this module
__file__ would resolve to mc/blueprints/, breaking data/claydo + USER_GUIDE
+ CHANGELOG paths), and 1× `CONFIG.get` → `state.CONFIG.get` (Phase-0 live
alias, 1.7 precedent).
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

import skills as _skills
from mc import state
from mc.core import now_iso

bp = Blueprint('guide_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
save_project: Callable[..., Any] = None  # type: ignore[assignment]
DATA_DIR: Path = None  # type: ignore[assignment]
_memory_search: Callable[..., Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[[], str] = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
_SERVER_DIR: Path = None  # type: ignore[assignment]


def wire(*, load_project_fn, save_project_fn, data_dir, memory_search_fn,
         resolve_claude_fn, popen_flags, startupinfo, server_dir):
    """Late-bind cross-family deps: load_project/save_project (projects
    family, 1.11), _memory_search + _resolve_claude + the Popen platform
    consts (dispatch/memory family, 1.12), DATA_DIR, and the server-dir
    anchor (server.py's Path(__file__).parent — repo root in dev, app dir
    frozen). Called once from server.py at import, BEFORE
    app.register_blueprint(bp)."""
    global load_project, save_project, DATA_DIR, _memory_search
    global _resolve_claude, _POPEN_FLAGS, _STARTUPINFO, _SERVER_DIR
    load_project = load_project_fn
    save_project = save_project_fn
    DATA_DIR = data_dir
    _memory_search = memory_search_fn
    _resolve_claude = resolve_claude_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    _SERVER_DIR = server_dir



# ── "Ask Claydo" guide assistant ────────────────────────────────────────────

# Dedicated cwd for Claydo's claude subprocess. Without an explicit cwd, claude
# would inherit the server's working directory (= the Mission Control project's
# project_path) and dump its session transcripts into
# `~/.claude/projects/<encoded-mc-path>/`. The startup transcript-backfill then
# scans that directory and synthesizes agent_log entries — so Claydo
# conversations would appear in MC's Agent Log tab. Routing Claydo's claude
# into a sandbox dir under data/ encodes to a path no project owns, so the
# transcripts stay isolated.
def _claydo_cwd():
    d = _SERVER_DIR / 'data' / 'claydo'
    # One-time rename of the old data/playdo/ sandbox dir from before the
    # mascot was renamed Playdo -> Claydo. The directory holds Claude's
    # CLAUDE.md (regenerated on every call) and ~/.claude transcripts keyed
    # off this cwd. Renaming preserves transcript continuity.
    if not d.exists():
        old = _SERVER_DIR / 'data' / 'playdo'
        if old.exists():
            try:
                old.rename(d)
            except Exception:
                # Cross-device rename or permission issue — fall through
                # and just create the new dir; old transcripts stay where
                # they are (not catastrophic, just lose continuity).
                pass
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


# Hard-disable tools + MCP servers for Claydo's claude subprocess. Without
# these, the model — having all the user's built-in tools (Grep, LSP, Read,
# Bash…) and MCP servers (Gmail/Calendar/Drive/Uber) loaded — would reach
# for tools on feature-lookup questions ("how do I use remote control?")
# and trigger Grep/LSP. Those calls get denied by --print's dontAsk mode,
# the model has no turns left to recover, and the subprocess exits 1 with
# no usable text answer. Claydo answers strictly from CLAUDE.md anyway.
_CLAYDO_NO_TOOLS_FLAGS = [
    '--tools', '',
    '--strict-mcp-config',
    '--mcp-config', '{"mcpServers":{}}',
]


def _claydo_recent_changelog(max_entries=15):
    """Extract the last N entries from CHANGELOG.md so Claydo can answer
    about features shipped after USER_GUIDE.md was last updated.

    Entries are demarcated by `## [YYYY-MM-DD...]` headers. Returns the
    concatenated tail with a section header, or empty string on failure.
    """
    try:
        import re as _re
        cl_path = _SERVER_DIR / 'CHANGELOG.md'
        if not cl_path.exists():
            return ''
        text = cl_path.read_text(encoding='utf-8')
        # Split on `## [` boundaries while preserving the marker.
        parts = _re.split(r'(?m)^## \[', text)
        # parts[0] is preamble (title + intro); rest are entries with the
        # leading `## [` stripped.
        entries = parts[1:]
        if not entries:
            return ''
        recent = entries[:max_entries]
        rebuilt = '\n'.join('## [' + e.rstrip() for e in recent)
        return '\n\n---\n\n## Recent changes (from CHANGELOG)\n\n' + rebuilt
    except Exception:
        return ''


def _claydo_prepare_context():
    """Read USER_GUIDE.md, append a recent-CHANGELOG tail, and materialize
    the result as `data/claydo/CLAUDE.md` (idempotent — only rewrites when
    content drifts). Centralizes context setup for both `/api/guide/ask`
    and `/api/guide/stream`.

    Returns (cwd, None) on success; (cwd, (error_message, http_code)) on
    failure so the caller can `return jsonify({'error': ...}), code`.
    """
    cwd = _claydo_cwd()
    guide_path = _SERVER_DIR / 'docs' / 'USER_GUIDE.md'
    if not guide_path.exists():
        return cwd, ('guide not available — docs/USER_GUIDE.md missing', 500)
    try:
        guide_text = guide_path.read_text(encoding='utf-8')
    except Exception as e:
        return cwd, (f'guide read failed: {e}', 500)
    combined = guide_text + _claydo_recent_changelog()
    try:
        guide_md = Path(cwd) / 'CLAUDE.md'
        if not guide_md.exists() or guide_md.read_text(encoding='utf-8') != combined:
            guide_md.write_text(combined, encoding='utf-8')
    except Exception:
        pass  # Non-fatal — fall through; Claude will just see less context.
    return cwd, None


# ── Builder modes (Prompt Builder Phase 1, docs/PROMPT_BUILDER_DESIGN.md) ────
# Claydo's two workshop modes reuse the ask-mode engine (no-tools claude
# subprocess + materialized CLAUDE.md) with a different brief and their own
# sandbox cwds, so each mode keeps its own transcripts and context cache.

_CLAYDO_MODES = ('ask', 'prompt', 'character')

_CLAYDO_BUILDER_BRIEFS = {
    'prompt': 'PROMPT_BUILDER_BRIEF.md',
    'character': 'CHARACTER_BUILDER_BRIEF.md',
}


def _claydo_builder_cwd(mode: str) -> str:
    """Per-mode sandbox under data/claydo/ — same transcript-isolation
    rationale as _claydo_cwd (the parent dir encodes to a path no project
    owns)."""
    d = Path(_claydo_cwd()) / f'builder-{mode}'
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _claydo_prepare_builder_context(mode: str):
    """Materialize docs/claydo/<brief> as the builder cwd's CLAUDE.md
    (drift-checked, like _claydo_prepare_context). Same return contract."""
    cwd = _claydo_builder_cwd(mode)
    brief_path = _SERVER_DIR / 'docs' / 'claydo' / _CLAYDO_BUILDER_BRIEFS[mode]
    if not brief_path.exists():
        return cwd, (f'builder brief missing — docs/claydo/{_CLAYDO_BUILDER_BRIEFS[mode]}', 500)
    try:
        brief_text = brief_path.read_text(encoding='utf-8')
    except Exception as e:
        return cwd, (f'brief read failed: {e}', 500)
    try:
        brief_md = Path(cwd) / 'CLAUDE.md'
        if not brief_md.exists() or brief_md.read_text(encoding='utf-8') != brief_text:
            brief_md.write_text(brief_text, encoding='utf-8')
    except Exception:
        pass  # Non-fatal — Claydo just builds with less guidance.
    return cwd, None


def _claydo_project_context_block(project_id):
    """Compact, best-effort project grounding for builder modes (design §4):
    name, summary, AGENT_RULES head, installed skill names. Rides the
    per-request prompt ONLY — never the materialized CLAUDE.md, which is
    mode-global and cached across users of the modal."""
    if not project_id:
        return ''
    try:
        p = load_project(project_id)
    except Exception:
        return ''
    if not p:
        return ''
    lines = ["Context about the user's current project:",
             f"- Project: {p.get('name') or project_id}"]
    summary = (p.get('summary') or '').strip()
    if summary:
        lines.append(f'- Summary: {summary[:300]}')
    pp = p.get('project_path') or ''
    if pp:
        try:
            rules_path = Path(pp) / 'AGENT_RULES.md'
            if rules_path.is_file():
                head = rules_path.read_text(encoding='utf-8')[:1000].strip()
                if head:
                    lines.append('- Agent rules (head):\n' + head)
        except Exception:
            pass
        try:
            names = [s.get('name', '') for s in _skills.list_skills(
                project_path=pp, project_id=project_id, include_body=False)]
            names = [n for n in names if n][:15]
            if names:
                lines.append('- Installed skills: ' + ', '.join(names))
        except Exception:
            pass
    return '\n'.join(lines)[:2500]


@bp.route('/api/guide/stream', methods=['POST'])
def guide_stream():
    """Streaming variant of /api/guide/ask. Spawns claude with stream-json output
    and forwards text deltas to the client as Server-Sent Events.

    SSE protocol:
      data: {"type":"delta","text":"<chunk>"}\n\n
      data: {"type":"done","answer":"<full text>"}\n\n
      data: {"type":"error","message":"..."}\n\n

    The full assembled answer is emitted in the final `done` event so the
    client can run its existing marker parser on the complete text. The
    incremental `delta` events are purely for the typing-animation effect.
    """
    data = request.get_json() or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    if len(question) > 2000:
        return jsonify({'error': 'question too long (max 2000 chars)'}), 400

    # Builder modes (Prompt Builder Phase 1): same engine, different brief
    # + sandbox. 'ask' stays byte-identical to the original behavior.
    mode = (str(data.get('mode') or 'ask')).strip().lower()
    if mode not in _CLAYDO_MODES:
        return jsonify({'error': 'mode must be ask|prompt|character'}), 400
    project_id = (str(data.get('project_id') or '')).strip() or None

    history = data.get('history', [])
    if not isinstance(history, list):
        history = []
    # Builder interviews span more turns than help-desk Q&A — keep a
    # longer tail so round-1 answers survive to the draft.
    history = history[-12:] if mode != 'ask' else history[-6:]

    # Materialize the mode's context as CLAUDE.md in its working directory
    # so the Claude CLI auto-loads it. Avoids the Windows 32 KB
    # CreateProcess command-line limit which the 24 KB guide hit when
    # passed via `--append-system-prompt`.
    if mode == 'ask':
        cwd, err = _claydo_prepare_context()
    else:
        cwd, err = _claydo_prepare_builder_context(mode)
    if err is not None:
        return jsonify({'error': err[0]}), err[1]

    ctx_block = _claydo_project_context_block(project_id) if mode != 'ask' else ''

    lines = []
    if ctx_block:
        lines.append(ctx_block)
        lines.append('')
    if history:
        lines.append('Previous exchange in this conversation:')
        for m in history:
            role = 'User' if (m.get('role') or '') == 'user' else 'You'
            text = (m.get('text') or '').strip()[:1000]
            if text:
                lines.append(f'{role}: {text}')
        lines.append('')
        lines.append(f'Current question: {question}')
    elif ctx_block:
        lines.append(f'Current question: {question}')
    full_question = '\n'.join(lines) if lines else question
    # Tail-truncate (recency wins; the ctx block is the first casualty,
    # which is the right sacrifice). stdin has no length limit either way.
    cap = 14000 if mode != 'ask' else 8000
    if len(full_question) > cap:
        full_question = full_question[-cap:]

    # Send the question via stdin (JSONL stream-json input) instead of via
    # `-p <full_question>`. On Windows, claude.cmd is invoked through cmd.exe
    # which has an 8191-char command-line limit (much smaller than
    # CreateProcess's 32K). An 8 KB question + flags + the cmd.exe wrapper
    # blows past that — surfacing as "The command line is too long". stdin
    # has no such limit. Command line stays well under 200 chars regardless
    # of question length.
    # --max-turns 2 (not 1): on questions that nudge the model toward a tool
    # call, the tool-use attempt would count as turn 1 and `--max-turns 1`
    # would exit before the model could fall back to a text answer. Tools
    # are disabled below, so the model can't actually call anything; turn 2
    # is purely a safety margin.
    cmd = [_resolve_claude(),
           '--max-turns', '2',
           '--print', '--verbose',
           '--input-format', 'stream-json',
           '--output-format', 'stream-json',
           *_CLAYDO_NO_TOOLS_FLAGS]
    stdin_msg = json.dumps({
        'type': 'user',
        'message': {'role': 'user', 'content': full_question},
    }) + '\n'

    def sse(payload):
        return f'data: {json.dumps(payload)}\n\n'

    def generate():
        proc = None
        full_text_parts = []
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # The mode's sandbox dir — its CLAUDE.md is the brief the
                # CLI auto-loads (ask = guide, builders = workshop briefs).
                cwd=cwd,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            try:
                proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9): stdin=PIPE above
                proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9)
                proc.stdin.close()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9)
            except Exception as e:
                yield sse({'type': 'error', 'message': f'stdin write failed: {e}'})
                return
        except FileNotFoundError:
            yield sse({'type': 'error', 'message': 'Claude CLI not found on this server'})
            return
        except Exception as e:
            yield sse({'type': 'error', 'message': f'spawn failed: {e}'})
            return

        try:
            for raw in iter(proc.stdout.readline, ''):  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9): stdout=PIPE above
                line = raw.rstrip('\n')
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                # claude stream-json emits {type: "assistant", message: {role, content: [...]}}
                # for assistant turns. Each content block can be {type: "text", text: "..."}.
                if obj.get('type') == 'assistant':
                    msg = obj.get('message', {}) or {}
                    content = msg.get('content', []) or []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                t = str(block.get('text') or '')
                                if t:
                                    full_text_parts.append(t)
                                    yield sse({'type': 'delta', 'text': t})
                # Other event types (system, result, user echo) are ignored —
                # we only need the assistant text.

            proc.wait(timeout=5)
            if proc.returncode != 0:
                err = ''
                try:
                    err = (proc.stderr.read() or '').strip()[:500]  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9): stderr=PIPE above
                except Exception:
                    pass
                yield sse({'type': 'error', 'message': err or f'claude exit {proc.returncode}'})
                return
            full_text = ''.join(full_text_parts).strip()
            yield sse({'type': 'done', 'answer': full_text})
        except GeneratorExit:
            # Client disconnected (closed modal, asked new question, navigated
            # away). Kill the subprocess so we don't keep burning tokens.
            try:
                proc.kill()
            except Exception:
                pass
            raise
        except Exception as e:
            yield sse({'type': 'error', 'message': str(e)})
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',  # disable nginx buffering if behind a proxy
    })


@bp.route('/api/guide/ask', methods=['POST'])
def guide_ask():
    """Single-shot ask of the in-app Claydo guide assistant.

    Spawns a claude session with `docs/USER_GUIDE.md` as system prompt, runs
    the user's question (optionally with prior-turn context), returns the
    answer. No project context, no memory writes, no agent_log entry. Each
    call is fully independent — `history` is just prepended to the prompt.

    Request body: {question: str, history?: [{role: 'user'|'assistant', text: str}]}.
    The answer may contain inline `[clayrune:...]` markers — the frontend
    parses + strips them and triggers UI actions (highlight, goto, open-modal).
    """
    data = request.get_json() or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    # Cap length to avoid a runaway prompt eating tokens.
    if len(question) > 2000:
        return jsonify({'error': 'question too long (max 2000 chars)'}), 400

    # Validate + cap conversation history (last 6 messages = ~3 exchanges).
    history = data.get('history', [])
    if not isinstance(history, list):
        history = []
    history = history[-6:]

    # See `_claydo_prepare_context`: USER_GUIDE.md + recent CHANGELOG tail
    # is materialized as CLAUDE.md in Claydo's cwd (avoids the Windows 32 KB
    # CreateProcess limit that --append-system-prompt would hit).
    cwd, err = _claydo_prepare_context()
    if err is not None:
        return jsonify({'error': err[0]}), err[1]

    # Build the user prompt: prior turns (if any) + current question.
    if history:
        lines = ['Previous exchange in this conversation:']
        for m in history:
            role = 'User' if (m.get('role') or '') == 'user' else 'You'
            text = (m.get('text') or '').strip()[:1000]
            if text:
                lines.append(f'{role}: {text}')
        lines.append('')
        lines.append(f'Current question: {question}')
        full_question = '\n'.join(lines)
    else:
        full_question = question
    # Hard cap on the assembled prompt to keep us tame.
    if len(full_question) > 8000:
        full_question = full_question[-8000:]

    # See /api/guide/stream — Windows' cmd.exe wrapper around claude.cmd is
    # capped at 8191 chars, so an 8 KB question pushed via -p triggers
    # "command line too long". Send it through stdin (stream-json) instead.
    # --max-turns 2 + no-tools flags: see the matching block in guide_stream.
    cmd = [_resolve_claude(),
           '--max-turns', '2',
           '--print', '--verbose',
           '--input-format', 'stream-json',
           '--output-format', 'stream-json',
           *_CLAYDO_NO_TOOLS_FLAGS]
    stdin_msg = json.dumps({
        'type': 'user',
        'message': {'role': 'user', 'content': full_question},
    }) + '\n'
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=_claydo_cwd(),
            input=stdin_msg,
            timeout=60, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Claydo timed out (>60s)'}), 504
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found on this server'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if result.returncode != 0:
        err = (result.stderr or 'claude failed').strip()[:500]
        return jsonify({'error': err}), 500

    # With --output-format stream-json, stdout is JSONL. Reassemble the
    # assistant text from `assistant` events the same way the streaming
    # endpoint does.
    parts = []
    for raw in (result.stdout or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get('type') == 'assistant':
            msg = obj.get('message', {}) or {}
            for block in (msg.get('content') or []):
                if isinstance(block, dict) and block.get('type') == 'text':
                    t = str(block.get('text') or '')
                    if t:
                        parts.append(t)
    return jsonify({'answer': ''.join(parts).strip()})


# ── Scribe telemetry (SPEC §8) ───────────────────────────────────────────────

@bp.route('/api/project/<project_id>/scribe-stats', methods=['GET'])
def get_scribe_stats(project_id):
    """Scribe-outcome counters: scribe_extracted vs scribe_fell_back:<reason>.
    Lets a silent 100%-fallback be detected before relying on scribe output."""
    fp = DATA_DIR / f'{project_id}_scribe_stats.json'
    if not fp.exists():
        return jsonify({})
    try:
        return jsonify(json.loads(fp.read_text(encoding='utf-8') or '{}'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Memory retrieval (SPEC §3 Leg B) — read-only route; _memory_search wired ─────
@bp.route('/api/project/<project_id>/memory/search', methods=['GET'])
def memory_search(project_id):
    """Ranked-grep over the project memory corpus (SPEC §3 Leg B). The
    mc-memory-search skill wraps this; the deterministic read floor calls
    _memory_search directly at dispatch."""
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'missing q'}), 400
    try:
        k = int(request.args.get('k', 3))
    except (TypeError, ValueError):
        k = 3
    return jsonify(_memory_search(p, q, k))


# ── Walkthrough onboarding project ────────────────────────────────────────────

# Help-desk persona seeded as AGENT_RULES.md in the Clayrune project workspace.
# `_build_agent_context()` reads AGENT_RULES.md and prepends it to the agent's
# system prompt automatically, so any session dispatched inside Clayrune
# behaves as a platform expert with the right pointers to the install's docs.
def _clayrune_agent_rules(mc_root: Path) -> str:
    # Keep this short — it ships into every first-message `--append-system-prompt`
    # CLI arg, and Windows' CreateProcess command-line limit is ~32 KB. Verbose
    # personas plus rules + activity + recent conversations easily exceed it.
    docs = mc_root / 'docs' / 'USER_GUIDE.md'
    changelog = mc_root / 'CHANGELOG.md'
    return (
        "You are the in-app help desk for Clayrune, the platform this user "
        "is running. Help them use it: explain features, "
        "walk through workflows, fix confusion. Be concise.\n"
        "\n"
        f"User guide: {docs}\n"
        f"Changelog (feature history): {changelog}\n"
        f"Source: {mc_root}\n"
        "Read the user guide for how-to questions; the changelog for "
        "\"is X supported yet?\"; source only when deeply technical.\n"
        "\n"
        "When the user asks \"show me X\", describe the click path using the "
        "UI vocabulary (\"sidebar → Hivemind → New\", \"three-dot menu → "
        "Configure GitHub\"). Don't edit the install codebase unless the user "
        "explicitly asks — you're a help desk, not a developer here.\n"
    )


def _clayrune_readme() -> str:
    return (
        "# Clayrune — your onboarding project\n"
        "\n"
        "This project is your guided tour of Clayrune. Everything here is real:\n"
        "the backlog items are things to try, and the agent attached to this\n"
        "project is set up as the in-app help desk — ask it anything about\n"
        "how the platform works.\n"
        "\n"
        "## Try this first\n"
        "Open the Agent tab and type:\n"
        "\n"
        "    show me what this app can do\n"
        "\n"
        "The agent has been briefed (see `AGENT_RULES.md`) and can read the\n"
        "full user guide + changelog of your local install.\n"
        "\n"
        "## What's in the backlog\n"
        "A short list of platform features to explore — drag-snap, tile button,\n"
        "scheduler, hivemind, skills, MCP, GitHub sync. Tick them off as you go.\n"
        "\n"
        "## You can also use this project for real work\n"
        "Nothing about this workspace is special — once you're done with the\n"
        "tour, you can repurpose it, archive it, or just delete the project\n"
        "and start fresh.\n"
    )


@bp.route('/api/walkthrough/sample-project', methods=['POST'])
def create_sample_project():
    """Create the Clayrune onboarding project. Idempotent. The URL keeps the
    legacy `sample-project` slug so older walkthrough JS keeps working; the
    actual project ID is `clayrune`. Normally a no-op backup these days:
    seed_onboarding_on_startup() already created the project on first boot
    (skipping the tour used to mean a fresh install had no project at all)."""
    created = _seed_onboarding_project()
    return jsonify({'ok': True, 'id': 'clayrune', 'existed': not created})


def seed_onboarding_on_startup():
    """First-boot seeding, called from server startup: create the Clayrune
    onboarding project once, independent of the walkthrough — it used to be
    created only by tour step 6's onEnter, so skipping (or never starting)
    the tour left a fresh install with zero projects.

    Marker-gated (data/onboarding_seeded.flag — deliberately OUTSIDE
    DATA_DIR so load_projects() never sees it): a user who deletes the
    project stays deleted; an established install (any project json already
    in DATA_DIR) just gets the marker stamped so upgrades never resurrect
    a deleted onboarding project."""
    try:
        marker = DATA_DIR.parent / 'onboarding_seeded.flag'
        if marker.exists():
            return
        if not any(DATA_DIR.glob('*.json')):
            _seed_onboarding_project()
        marker.write_text(now_iso(), encoding='utf-8')
    except Exception as e:
        print(f"[onboarding] startup seed failed: {e}", flush=True)


def _seed_onboarding_project() -> bool:
    """Create the onboarding project if absent. Returns True if created,
    False if it already existed."""
    pid = 'clayrune'
    filepath = DATA_DIR / f'{pid}.json'
    if filepath.exists():
        return False

    # Auto-assign a workspace folder so the agent can dispatch immediately.
    base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
    workspace = base / 'clayrune'
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Seed README + AGENT_RULES so the dispatched agent acts as the help desk
    # with concrete pointers to the install's docs. We only write these files
    # if they don't already exist — the user may have edited them and we
    # shouldn't trample.
    try:
        mc_root = _SERVER_DIR
        readme_path = workspace / 'README.md'
        if not readme_path.exists():
            readme_path.write_text(_clayrune_readme(), encoding='utf-8')
        rules_path = workspace / 'AGENT_RULES.md'
        if not rules_path.exists():
            rules_path.write_text(_clayrune_agent_rules(mc_root), encoding='utf-8')
    except Exception:
        # Seeding files is best-effort — the project still works without them,
        # the agent will just be generic instead of help-desk-themed.
        pass

    ts = now_iso()
    project = {
        'id': pid,
        'name': 'Clayrune',
        'domain': 'general',
        'status': 'active',
        'project_path': str(workspace),
        'summary': 'Onboarding & help desk for the Clayrune platform — ask the agent anything.',
        'description': (
            "Your guided tour of Clayrune. Ask the agent in this project "
            "anything about how to use the platform — backlog, scheduler, "
            "hivemind, skills, MCP, agent modes, snap layouts, GitHub sync. "
            "Everything here is real; you can also use this project to "
            "actually run agents."
        ),
        'current_task': 'Tour Clayrune — ask the agent "show me what this app can do"',
        'next_action': 'Set up your first real project (click + on Home)',
        'last_updated': ts,
        'backlog': [
            {'id': 'cr-01', 'text': 'Tour Clayrune — ask the agent: "show me what this app can do"',
             'status': 'open', 'priority': 'high', 'created_at': ts},
            {'id': 'cr-02', 'text': 'Set up your first real project — click + on Home',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-03', 'text': 'Drag this modal\'s title bar near the right edge — it snaps to the right half',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-04', 'text': 'Click the grid icon in the header to tile all open modals',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-05', 'text': 'Use the pin icon (top-right of this modal) to collapse the data sheet',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-06', 'text': 'Connect GitHub: open a project → 3-dot menu → Configure GitHub sync',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-07', 'text': 'Set up a recurring agent run: Scheduler in the sidebar',
             'status': 'open', 'priority': 'low', 'created_at': ts},
            {'id': 'cr-08', 'text': 'Try a Hivemind: sidebar → Hivemind → New',
             'status': 'open', 'priority': 'low', 'created_at': ts},
            {'id': 'cr-09', 'text': 'Install a skill: sidebar → Skills → Browse built-ins',
             'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'cr-10', 'text': 'Configure an MCP server: sidebar → MCP',
             'status': 'open', 'priority': 'low', 'created_at': ts},
            {'id': 'cr-11', 'text': 'Toggle compact mode: Settings → Advanced features',
             'status': 'open', 'priority': 'low', 'created_at': ts},
        ],
        'activity_log': [
            {'ts': ts, 'msg': 'Clayrune onboarding project created'}
        ],
    }
    save_project(pid, project)
    return True
