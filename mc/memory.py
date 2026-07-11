"""Memory / Scribe / Condense engine — mop-up extraction (non-blueprint).

The most load-bearing code in the repo: the server-side memory pipeline
(docs/MEMORY_SYSTEM_SPEC.md, docs/CONDENSE_STRUCTURED_DESIGN.md). Moved VERBATIM
out of server.py to drive it toward its <2,000-line target. ZERO behavior
change — a PURE MOVE. The ONLY mechanical edit applied to the moved bodies is
`CONFIG` -> `state.CONFIG` (the live-alias rewrite, the 1.7/1.10/1.11
precedent); every other name resolves identically (state/core names imported
by name; the dispatch-family + path/Popen deps late-bound via wire()).

LOAD-BEARING DISCIPLINE (CLAUDE.md "Memory system"): the MEMORY.md write path is
leaf-locked + atomic. `_commit_managed_entry` (completion / checkpoint /
reconcile) and `_condense_apply` (structured Leg C) are co-equal writers — both
take the SAME per-project `state._get_mem_write_lock`, both write via
`core._atomic_write_text`, both route archive overflow through the shared
`_append_to_archive`. The `<!-- clayrune:wm:<sid> ... -->` watermark markers and
the `<!-- clayrune:managed:begin/end -->` sentinels are load-bearing — never
altered. The permanent archive is append-only cold storage — never truncated.

NO import cycle: this module imports leaf modules only (mc.state, mc.core,
agent_runtime, distiller, stdlib). It NEVER imports server or any blueprint.
Cross-family deps (dispatch helpers + path/config roots) arrive via wire(),
called once by server.py before the blueprints' own wire() stanzas resolve the
memory values they pass on.
"""
from pathlib import Path
from typing import Any, Callable, Optional
import json
import os
import subprocess
import threading
import time as _time
import uuid

import agent_runtime as _agent_runtime  # multi-provider runtime (transcript + oneshot)
import distiller as _distiller          # Phase 4 learning observer (best-effort)

from mc import state
from mc.core import _atomic_write_text, _log, now_iso
from mc.state import (
    _checkpoint_guard,
    _checkpoint_inflight,
    _checkpoint_sema,
    _checkpoint_sema_guard,
    _condense_lock,
    _condense_status,
    _condense_triggered_at,
    _condensing_projects,
    _get_mem_write_lock,
    _scribe_lock,
    _scribing_projects,
    agent_sessions,
)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
# Path/config roots stay home in server.py (other families still read them);
# the dispatch-family fns live in agent_routes (1.12) / project_routes (1.11).
# All late-bound here (the 1.7 SESSION_LABELS_PATH wired-placeholder pattern +
# the 1.10/1.11/1.12 cross-family call seams). CONFIG is NOT wired — it is read
# live via state.CONFIG.
DATA_DIR: Path = None  # type: ignore[assignment]
MEMORY_DIR: Path = None  # type: ignore[assignment]
CLAUDE_HOME: Path = None  # type: ignore[assignment]
_SESSION_SIZE_LIMIT: int = 0
_POPEN_FLAGS: int = 0
_STARTUPINFO = None
# dispatch-family call seams (agent_routes 1.12 / project_routes 1.11). Typed as
# Callable (the 1.13 scheduler_routes precedent) so the placeholder None doesn't
# trip pyright reportOptionalCall at the verbatim call sites below.
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[[], str] = None  # type: ignore[assignment]
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_read_agent_stream: Callable[..., Any] = None  # type: ignore[assignment]
_hide_windows_delayed: Callable[[int], Any] = None  # type: ignore[assignment]


def wire(*, data_dir, memory_dir, claude_home, session_size_limit,
         popen_flags, startupinfo, load_project_fn, get_manager_fn,
         resolve_claude_fn, register_process_fn, read_agent_stream_fn,
         hide_windows_delayed_fn):
    """Late-bind path/config roots + dispatch-family deps. Called once by
    server.py BEFORE the blueprint wire() stanzas that pass memory.* values
    (agent_routes' write_session_memory_fn/scribe_call_fn/dispatch_condense_fn
    /..., project_routes' get_memory_path_fn, guide_routes' memory_search_fn).
    """
    global DATA_DIR, MEMORY_DIR, CLAUDE_HOME, _SESSION_SIZE_LIMIT
    global _POPEN_FLAGS, _STARTUPINFO
    global load_project, get_manager, _resolve_claude, _register_process
    global _read_agent_stream, _hide_windows_delayed
    DATA_DIR = data_dir
    MEMORY_DIR = memory_dir
    CLAUDE_HOME = claude_home
    _SESSION_SIZE_LIMIT = session_size_limit
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    load_project = load_project_fn
    get_manager = get_manager_fn
    _resolve_claude = resolve_claude_fn
    _register_process = register_process_fn
    _read_agent_stream = read_agent_stream_fn
    _hide_windows_delayed = hide_windows_delayed_fn


def _encode_project_path(project_path):
    """Encode a project path to Claude Code's ~/.claude/projects/<encoded>
    directory name.  C:\\Users\\foo\\bar  →  C--Users-foo-bar.

    Returns None when the path is empty or cannot be resolved (callers
    treat that as "no transcript dir").  Extracted from four inline
    duplicates (IMPROVEMENT_PLAN_V2.md P1-2); the underscore→dash
    fallback some callers also try stays at the call site since not all
    of them want it.
    """
    if not project_path:
        return None
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return None
    return resolved.replace(':', '-').replace('\\', '-').replace('/', '-')


def _session_transcript_path(project_path, claude_session_id):
    """Return the .jsonl transcript path for a Claude session (no existence check).
    Delegates to ClaudeRuntime._build_transcript_path() — path construction lives
    in the runtime so non-claude providers automatically return None.
    """
    return _agent_runtime.get_runtime('claude')._build_transcript_path(  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)
        project_path, claude_session_id)


def _session_too_large(project_path, claude_session_id):
    """Check if a session transcript exceeds the size limit."""
    p = _session_transcript_path(project_path, claude_session_id)
    if p and p.exists():
        try:
            size = p.stat().st_size
            return size > _SESSION_SIZE_LIMIT, size
        except OSError:
            pass
    return False, 0


def _long_session_advisory(s):
    """Advisory (NOT enforced): a long-running Mode-B session may be
    compacting away its own early-session context. Step 6 has captured that
    learning durably to MEMORY.md, so restarting the session reloads it
    fresh (a fresh process re-loads MEMORY.md + gets the read-floor) at
    near-zero loss. Distinct from _session_too_large (that's the 5 MB
    resume-perf HARD cap); this is turn-count keyed, fires far earlier, and
    is a soft human-in-loop nudge for Mode-B sessions only.
    SPEC docs/MEMORY_SYSTEM.md Open item #6.
    """
    if not state.CONFIG.get('long_session_advisory_enabled', True):
        return False
    if s.get('mode') != 'B':
        return False  # Mode A spawns per-turn — no persistent-process amnesia
    if s.get('housekeeping') or s.get('incognito'):
        return False
    if s.get('status') not in ('running', 'idle'):
        return False  # only a live session can be usefully restarted
    thr = int(state.CONFIG.get('long_session_advisory_turns', 25) or 25)
    return int(s.get('num_turns', 0) or 0) >= thr


def _resume_is_fragile(was_resume, resume_confirmed):
    """Decide whether a dead Mode B session that was a `-r` resume must be
    abandoned (fresh restart, losing the transcript) vs. resumed again.

    Only a resume that NEVER produced output is "fragile" — re-`-r`-ing it
    would just loop, so we go fresh. A resume that produced output is healthy:
    if it dies LATER (the AskUserQuestion `proc.kill()`, idle-eviction, or a
    crash) it must be resumed with `-r` so the conversation is preserved.

    Before this guard existed, ANY session that was ever a resume reset to a
    fresh, context-less session on its next process death — which is why an
    AskUserQuestion in a resumed session lost the whole conversation. See the
    followup respawn path and tests/test_resume_revival.py.
    """
    return bool(was_resume) and not bool(resume_confirmed)


def _extract_user_text(msg_field):
    """Extract plain user text from a jsonl message field, skipping tool_result blocks."""
    if not isinstance(msg_field, dict) or msg_field.get('role') != 'user':
        return ''
    content = msg_field.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(str(block.get('text', '')))
        return ' '.join(t.strip() for t in texts if t).strip()
    return ''


def _recent_claude_transcripts(project_path, limit=5):
    """Scan the Claude transcript directory for a project.

    Returns [{session_id, mtime, first_user, last_user, turns, size}] sorted by mtime desc.
    Delegates to ClaudeRuntime.list_sessions() — scanning logic lives in the runtime.
    """
    return _agent_runtime.get_runtime('claude').list_sessions(project_path, limit=limit)  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)


def _find_transcript_file(project_path, claude_session_id):
    """Locate the Claude Code transcript JSONL for a given csid, or None.
    Delegates to ClaudeRuntime.transcript_path() — path logic lives in the runtime.
    """
    return _agent_runtime.get_runtime('claude').transcript_path(
        project_path, claude_session_id)


def _parse_transcript_messages(f, max_messages=2000):
    """Parse a Claude Code JSONL transcript into [{role, text, tool, timestamp}] for read-only display.

    role: 'user' | 'assistant' | 'tool_call'
    Returns at most max_messages entries; on overflow, keeps the TAIL (most
    recent) — see ClaudeRuntime.parse_transcript_file() for the rationale.
    """
    return _agent_runtime.get_runtime('claude').parse_transcript_file(f, max_messages=max_messages)  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)


def _native_memory_path(project_path):
    """Derive the Claude Code native MEMORY.md path for a project.

    Claude stores memory at ~/.claude/projects/<encoded-path>/memory/MEMORY.md
    where the path encoding replaces : and path separators with -.
    """
    encoded = _encode_project_path(project_path)
    if not encoded:
        return None
    mem_path = CLAUDE_HOME / encoded / 'memory' / 'MEMORY.md'
    # Claude Code may also replace underscores with dashes — check both
    # and prefer whichever was modified most recently
    encoded_alt = encoded.replace('_', '-')
    if encoded_alt != encoded:
        alt_path = CLAUDE_HOME / encoded_alt / 'memory' / 'MEMORY.md'
        if alt_path.exists() and mem_path.exists():
            if alt_path.stat().st_mtime > mem_path.stat().st_mtime:
                return alt_path
        elif alt_path.exists():
            return alt_path
    return mem_path


def _get_memory_path(project):
    """Get the memory file path for a project — native Claude path preferred, fallback to MC data dir."""
    pp = project.get('project_path', '')
    if pp:
        native = _native_memory_path(pp)
        if native:
            return native
    return MEMORY_DIR / f'{project["id"]}.md'


def _get_archive_path(project):
    """Get the MEMORY_ARCHIVE.md path — sibling to the project's MEMORY.md."""
    mem_path = _get_memory_path(project)
    return mem_path.parent / 'MEMORY_ARCHIVE.md'


_MEM_BEGIN = '<!-- clayrune:managed:begin -->'


_MEM_END = '<!-- clayrune:managed:end -->'


_MEM_LOG_HEADER = '## Session Log'


_MEM_WM_PREFIX = '<!-- clayrune:wm:'


def _mem_split_full(content):
    """Split MEMORY.md into (curated_text, [entry_lines], [wm_marker_lines]).

    Managed region = sentinel-delimited (or a legacy bare '## Session Log').
    `entries` = lines starting with '- [' (curated pointer lines, also
    '- [...]', are never collected — they're above the sentinel).
    `wm_markers` = full lines starting with the Step-6 watermark prefix.
    Pure function.
    """
    content = content or ''
    if _MEM_BEGIN in content and _MEM_END in content:
        i = content.index(_MEM_BEGIN)
        j = content.index(_MEM_END, i)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_BEGIN):j]
    elif _MEM_LOG_HEADER in content:
        i = content.index(_MEM_LOG_HEADER)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_LOG_HEADER):]
    else:
        return content.rstrip(), [], []
    entries, wm = [], []
    for ln in mid.splitlines():
        s = ln.strip()
        if s.startswith('- ['):
            entries.append(ln)
        elif s.startswith(_MEM_WM_PREFIX):
            wm.append(s)
    return curated, entries, wm


def _mem_split(content):
    """Back-compat 2-tuple (curated, entries) — every pre-Step-6 caller uses
    this. wm markers are dropped from the return but NOT from the file (the
    write path uses _mem_split_full + _mem_compose(..., wm) to preserve them).
    """
    c, e, _w = _mem_split_full(content)
    return c, e


def _mem_compose(curated, entries, wm_markers=None):
    """Rebuild canonical MEMORY.md from curated + entry lines (+ optional wm
    markers). Always one sentinel-delimited managed region. With wm_markers
    falsy, output is byte-identical to the pre-Step-6 form (existing callers
    unaffected). wm markers are emitted after entries, before the END sentinel.
    """
    curated = (curated or '').rstrip()
    block = f'{_MEM_BEGIN}\n{_MEM_LOG_HEADER}\n'
    body = '\n'.join(entries)
    if body:
        block += body + '\n'
    if wm_markers:
        block += '\n'.join(wm_markers) + '\n'
    block += f'{_MEM_END}\n'
    return (curated + '\n\n' + block) if curated else block


def _mem_migrate(content):
    """Idempotent, additive migration to the Leg 0 canonical format.

    Already-migrated content round-trips unchanged. Legacy bare
    '## Session Log' sections get wrapped in sentinels. Files with no managed
    content gain an empty managed region. Curated content is preserved
    verbatim (modulo trailing whitespace); curated lines are never reordered
    or dropped. wm markers (Step 6) are preserved.
    """
    return _mem_compose(*_mem_split_full(content))


_MEM_WM_SUMMARY_CAP = 600


def _wm_line(rec):
    """Build the single physical marker line for a watermark record.

    rec keys: session_id, claude_session_id, transcript_path, byte_offset,
    slice_hash, running_summary. running_summary is sanitized to stay on one
    line and not prematurely close the HTML comment.
    """
    sid = str(rec.get('session_id', ''))
    safe = dict(rec)
    rs = str(safe.get('running_summary', '') or '')
    rs = rs.replace('\n', ' ').replace('\r', ' ').replace('-->', '—>')
    safe['running_summary'] = rs[:_MEM_WM_SUMMARY_CAP]
    js = json.dumps(safe, separators=(',', ':'), ensure_ascii=False)
    return f"{_MEM_WM_PREFIX}{sid} {js} -->"


def _wm_parse(line):
    """Parse a marker line back to a record dict, or None if malformed."""
    line = (line or '').strip()
    if not line.startswith(_MEM_WM_PREFIX) or not line.endswith(' -->'):
        return None
    core = line[len(_MEM_WM_PREFIX):].rsplit(' -->', 1)[0]
    sp = core.split(' ', 1)
    if len(sp) != 2:
        return None
    try:
        rec = json.loads(sp[1])
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _wm_find(wm_markers, session_id):
    """Return the parsed record for session_id from a wm_markers list, or None."""
    for ln in wm_markers or []:
        r = _wm_parse(ln)
        if r and str(r.get('session_id', '')) == str(session_id):
            return r
    return None


def _wm_upsert(wm_markers, rec):
    """Return a new wm_markers list with rec's session replaced (or appended)."""
    sid = str(rec.get('session_id', ''))
    kept = [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]
    kept.append(_wm_line(rec))
    return kept


def _wm_remove(wm_markers, session_id):
    """Return a new wm_markers list without session_id's marker (teardown)."""
    sid = str(session_id)
    return [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]


def _memory_search(project, query, topk=3):
    """Ranked-grep over the project's memory corpus (SPEC §3 Leg B).

    Corpus = the memory dir's topic *.md files + MEMORY_ARCHIVE.md entries +
    the MANAGED region of MEMORY.md. The curated MEMORY.md index is excluded
    by construction — the agent already auto-loads it. Deterministic, no
    model. Returns [{file, score, snippet}] sorted by score desc.
    """
    import re  # module has no top-level `re` import (see _re_auth pattern)
    terms = [t for t in re.findall(r'[a-z0-9_]+', (query or '').lower())
             if len(t) >= 3]
    if not terms:
        return []
    try:
        mem_path = _get_memory_path(project)
        mem_dir = mem_path.parent
    except Exception:
        return []
    if not mem_dir.is_dir():
        return []
    mem_name = mem_path.name
    arch_name = _get_archive_path(project).name
    units = []  # (label, text)
    for f in sorted(mem_dir.glob('*.md')):
        try:
            txt = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        if f.name == mem_name:
            for e in _mem_split(txt)[1]:           # managed entries only
                units.append((f'{f.name}#managed', e))
        elif f.name == arch_name:
            for ln in txt.splitlines():
                if ln.strip().startswith('- ['):
                    units.append((f.name, ln.strip()))
        else:
            units.append((f.name, txt))            # topic file (whole)
    scored = []
    for label, text in units:
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score <= 0:
            continue
        if any(t in label.lower() for t in terms):
            score += 2                              # filename relevance bonus
        pos = min((low.find(t) for t in terms if t in low), default=0)
        start = max(0, pos - 120)
        snip = text[start:start + 400].replace('\n', ' ').strip()
        scored.append({'file': label, 'score': score, 'snippet': snip})
    scored.sort(key=lambda r: r['score'], reverse=True)
    return scored[:max(1, topk)]


def _condense_combined_bytes(project):
    """Combined size of a project's MEMORY.md + archive (0 if absent)."""
    total = 0
    for p in (_get_memory_path(project), _get_archive_path(project)):
        try:
            if p and p.exists():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _set_condense_status(pid, **kw):
    with _condense_lock:
        cur = _condense_status.get(pid, {})
        cur.update(kw)
        _condense_status[pid] = cur


def _get_condense_status(pid):
    with _condense_lock:
        st = _condense_status.get(pid)
        return dict(st) if st else {'state': 'idle'}


def _has_running_agent(project_id):
    """Return True if any non-housekeeping agent is running or idle for this project."""
    for s in agent_sessions.values():
        if s.get('project_id') == project_id and not s.get('housekeeping'):
            if s.get('status') in ('running', 'idle'):
                return True
    return False


def _should_condense(project, include_claude_md=False):
    """Check whether memory condensation should be triggered for this project.

    If include_claude_md is True, also count the project's CLAUDE.md in the size check.
    This is used by the pre-dispatch context budget check.
    """
    if not state.CONFIG.get('condense_enabled', True):
        return False
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return False
        # Cooldown: don't re-trigger within 1 hour of the last dispatch. This
        # prevents the pre-dispatch check from firing on back-to-back sessions
        # when CLAUDE.md + MEMORY.md keep the total above threshold while the
        # previous condense job is still running or just finished.
        _cooldown = int(state.CONFIG.get('condense_cooldown_secs', 3600) or 3600)
        if _time.time() - _condense_triggered_at.get(pid, 0) < _cooldown:
            return False
    # Skip running-agent check when called from pre-dispatch (agent hasn't started yet)
    if not include_claude_md and _has_running_agent(pid):
        return False
    # The structured executor is line-keyed and only ever acts on MEMORY.md's
    # managed region. Trigger it on the auto-loaded file's LINE count vs. the
    # model-tier budget — NOT on combined bytes. Byte-keying would let a large
    # CLAUDE.md (which structured deliberately doesn't touch) keep the trigger
    # permanently hot, firing a no-op model call every session-end. This also
    # makes the structured trigger and its target agree in units (closes
    # docs/CONDENSE_STRUCTURED_DESIGN.md Open Question #5). The legacy agent
    # path keeps its existing combined-byte trigger below, unchanged.
    if (state.CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return False
        try:
            n_lines = len(mem_path.read_text(encoding='utf-8').splitlines())
        except Exception:
            return False  # a trigger check must never raise
        return n_lines > int(state.CONFIG.get('index_line_budget', 160) or 160)
    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    combined = 0
    if mem_path.exists():
        combined += mem_path.stat().st_size
    if archive_path.exists():
        combined += archive_path.stat().st_size
    if include_claude_md:
        pp = project.get('project_path', '')
        if pp:
            claude_md = Path(pp) / 'CLAUDE.md'
            if claude_md.exists():
                try:
                    combined += claude_md.stat().st_size
                except OSError:
                    pass
    threshold = state.CONFIG.get('condense_threshold_kb', 30) * 1024
    return combined > threshold


_MEM_ARCHIVE_HEADER = '## Archived Session Log'


def _append_to_archive(project, lines):
    """Append raw '- [' lines to the project's permanent archive, creating the
    file + header on first write. Read-modify-write under the caller's leaf
    lock; the archive is append-only cold storage — never truncated (SPEC D3).
    Shared by _commit_managed_entry (mechanical floor) and _condense_apply."""
    if not lines:
        return
    ap = _get_archive_path(project)
    ap.parent.mkdir(parents=True, exist_ok=True)
    prev = ap.read_text(encoding='utf-8').rstrip() if ap.exists() else ''
    if _MEM_ARCHIVE_HEADER not in prev:
        prev = (prev + f'\n\n{_MEM_ARCHIVE_HEADER}'
                if prev else _MEM_ARCHIVE_HEADER)
    _atomic_write_text(ap, prev + '\n' + '\n'.join(lines) + '\n')


def _commit_managed_entry(p, mem_entry=None, wm_upsert=None, wm_remove_sid=None):
    """Leaf-locked atomic MEMORY.md commit — the write path shared by the
    completion scribe, the Step-6 checkpoint worker, and teardown (the
    structured Leg C `_condense_apply` is a co-equal writer under the SAME
    leaf lock + atomic primitive; both route archive overflow through
    `_append_to_archive`). In a single
    per-project mem-write-locked, atomic (temp+replace) operation:
      • optionally append `mem_entry` ('- [' line) to the managed region,
      • optionally `_wm_upsert`/`_wm_remove` this session's watermark marker,
      • run the lossless line-keyed floor (relocates only '- [' entries;
        wm markers never popped but DO count toward the budget),
      • write MEMORY.md (+archive overflow) atomically.
    No scribe call and no condense dispatch inside the lock (the slow/process
    parts stay out). Returns whether condense should fire; caller dispatches it
    OUTSIDE the lock. Never raises. SPEC §3.A.MID committee blocker #3.
    """
    project_id = p.get('id', '')
    mem_path = _get_memory_path(p)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    hard_floor = int(state.CONFIG.get('index_line_hard_floor', 185) or 185)
    with _get_mem_write_lock(project_id):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        # Leg 0: idempotent, additive migration; curated region untouched.
        curated, mem_entries, wm_markers = _mem_split_full(_mem_migrate(existing))
        if mem_entry:
            mem_entries.append(mem_entry)
        if wm_remove_sid is not None:
            wm_markers = _wm_remove(wm_markers, wm_remove_sid)
        if wm_upsert is not None:
            wm_markers = _wm_upsert(wm_markers, wm_upsert)
        overflow = []
        while mem_entries and len(
                _mem_compose(curated, mem_entries, wm_markers).splitlines()) > hard_floor:
            overflow.append(mem_entries.pop(0))  # oldest → archive
        _append_to_archive(p, overflow)
        _atomic_write_text(mem_path,
                           _mem_compose(curated, mem_entries, wm_markers))
        return _should_condense(p, include_claude_md=True)


def _gc_stale_watermarks(projects):
    """Drop `<!-- clayrune:wm:<sid> -->` markers whose session is no longer live.

    A watermark is removed by `_wm_remove` on clean teardown only. A hard MC kill
    (or a startup reconcile that baseline-stamps history without scribing it)
    leaves the marker behind forever, so they accumulate across restarts: 67 of
    them (37.8KB) had piled up in this repo's own MEMORY.md by 2026-07-11 and
    pushed the curated index past the harness's read cap — everything below the
    cut was silently dropped from the agent's context.

    LIVE markers are load-bearing (Step-6 checkpointing reads `byte_offset` to
    render only the transcript delta), so a session still in `agent_sessions` is
    NEVER pruned — the membership test is re-done inside the lock so a session
    revived concurrently with this sweep can't lose its marker. A pruned dead
    marker costs nothing: its session can never checkpoint again.

    Same discipline as every other MEMORY.md writer: per-project leaf lock,
    atomic write, curated + entry lines byte-preserved. Best-effort — never
    raises, never blocks startup.
    """
    total = 0
    for p in projects or []:
        project_id = p.get('id', '')
        if not project_id:
            continue
        try:
            mem_path = _get_memory_path(p)
            if not mem_path.exists():
                continue
            with _get_mem_write_lock(project_id):
                existing = mem_path.read_text(encoding='utf-8')
                curated, mem_entries, wm_markers = _mem_split_full(existing)
                if not wm_markers:
                    continue
                live = {s.get('session_id') or s.get('id')
                        for s in agent_sessions.values()}
                kept = [ln for ln in wm_markers
                        if (_wm_parse(ln) or {}).get('session_id') in live]
                dropped = len(wm_markers) - len(kept)
                if not dropped:
                    continue
                _atomic_write_text(mem_path,
                                   _mem_compose(curated, mem_entries, kept))
                total += dropped
                _log(f"[wm-gc] {project_id}: pruned {dropped} stale watermark(s), "
                     f"kept {len(kept)} live")
        except Exception as e:
            _log(f"[wm-gc] {project_id}: sweep failed: {e}")
    return total


def _write_session_memory(p, session, status, summary_fallback, ts_date):
    """Shared Leg A/0/C memory write — completion path & startup reconciler.
    Scribe over the full .jsonl → brief (fallback to summary, then a
    guaranteed breadcrumb) → _commit_managed_entry (which also drops this
    session's Step-6 wm marker = clean teardown) → condense trigger. Returns
    True iff a memory entry was written. Never raises.
    SPEC docs/MEMORY_SYSTEM_SPEC.md §3 Leg A/0/C.
    """
    project_id = p.get('id', '')
    task = (session.get('task', '') or '').strip()
    # Scribe model call is the slow (≤180s) part — OUTSIDE the leaf lock.
    scribed, _why = _scribe_extract(p, session)
    _scribe_stat(project_id, 'scribe_extracted' if scribed
                 else f'scribe_fell_back:{_why}')
    fb = (summary_fallback or '')[:300].replace('\n', ' ').strip()
    brief = (scribed or fb
             or f"ended with status={status}, no captured output"
             ).replace('\n', ' ').strip()
    tag = '' if status == 'completed' else f' _({status})_'
    mem_entry = f"- [{ts_date}] **{task[:80]}**{tag} — {brief}"
    # Terminal write also removes this session's live wm marker (clean
    # teardown — SPEC §3.A.MID Fix-B coordination), in the same atomic write.
    do_condense = _commit_managed_entry(
        p, mem_entry=mem_entry,
        wm_remove_sid=session.get('session_id') or session.get('id'))
    if do_condense:
        _dispatch_condense(p)
    # Phase 4 Distiller — daemon-thread dispatch parallel to Scribe (v2.1 §4.8).
    # Best-effort: failure NEVER blocks Scribe / MEMORY.md / completion. The
    # entry point gates itself via _distiller_should_proceed at session_end_extract.
    try:
        csid = session.get('claude_session_id', '')
        sid = session.get('session_id') or session.get('id') or ''
        if not csid:
            _log(f"[distiller] dispatch SKIP project_id={project_id} sid={sid}: "
                 f"no claude_session_id on session object")
        else:
            tf = _find_transcript_file(p.get('project_path', ''), csid)
            jsonl_path = str(tf) if tf else None
            # _UNATTENDED_LOOP_RULE: stamp steward-cycle provenance onto every
            # artifact this session's evidence produces, so the read-floor can
            # keep autonomous output from becoming autonomous input.
            unattended = _distiller.is_unattended_task(task)
            _log(f"[distiller] dispatch FIRE project_id={project_id} sid={sid[:12]} "
                 f"csid={csid[:8]} jsonl_path={'yes' if jsonl_path else 'no'} "
                 f"origin={'unattended' if unattended else 'interactive'}")
            threading.Thread(
                target=_distiller._distill_extract_and_aggregate,
                args=(project_id, sid, jsonl_path, unattended),
                daemon=True,
                name=f"distiller-{project_id}",
            ).start()
    except Exception as _dist_disp_err:
        # Was bare `except: pass` — silently swallowed any error in the dispatch
        # path including AttributeError if _distiller wasn't registered. Log it
        # so we can see if dispatch fails.
        _log(f"[distiller] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_dist_disp_err).__name__}: {_dist_disp_err!r}")
    # Beacon — regenerate this project's cross-project heartbeat brief on
    # session-close (the brief is the expensive field, so it regenerates here,
    # not on dashboard load). Threaded + best-effort, exactly like the Distiller
    # dispatch above: failure NEVER blocks Scribe / MEMORY.md / completion.
    try:
        from beacon.hooks import regenerate_brief_async as _beacon_regen
        _beacon_regen(project_id, status)
    except Exception as _beacon_err:
        _log(f"[beacon] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_beacon_err).__name__}: {_beacon_err!r}")
    return True


def _sha8(s):
    import hashlib
    return hashlib.sha1((s or '').encode('utf-8', 'replace')).hexdigest()[:8]


def _get_checkpoint_sema(pid):
    with _checkpoint_sema_guard:
        s = _checkpoint_sema.get(pid)
        if s is None:
            s = threading.BoundedSemaphore(2)  # ≤2 concurrent checkpoints/project
            _checkpoint_sema[pid] = s
    return s


def _checkpoint_prev_offset(p, sid):
    """Cheap read of this session's last watermark byte_offset (0 if none)."""
    try:
        mp = _get_memory_path(p)
        if not mp.exists():
            return 0
        _c, _e, wm = _mem_split_full(mp.read_text(encoding='utf-8'))
        r = _wm_find(wm, sid)
        return int(r.get('byte_offset', 0)) if r else 0
    except Exception:
        return 0


def _maybe_checkpoint(session):
    """Mode-B turn-boundary hook (clones the _auto_snapshot_notes_on_turn
    precedent). FAST gate only — no model call here: config flags,
    incognito/housekeeping, real-boundary, KB-delta debounce, one-in-flight
    per session. Spawns the worker on a daemon thread. Never raises (must not
    break the reader)."""
    try:
        if not state.CONFIG.get('scribe_checkpoint_enabled', False):
            return
        kb = int(state.CONFIG.get('scribe_checkpoint_kb', 0) or 0)
        if kb <= 0 or not state.CONFIG.get('scribe_enabled', True):
            return
        if session.get('incognito') or session.get('housekeeping'):
            return
        if (session.get('waiting_for_question')
                or session.get('waiting_for_plan_approval')):
            return  # not a real work boundary
        if not session.get('process_alive', True):
            return
        pid = session.get('project_id', '')
        sid = session.get('session_id') or session.get('id')
        csid = session.get('claude_session_id', '')
        if not (pid and sid and csid):
            return
        p = load_project(pid)
        if not p:
            return
        pp = p.get('project_path', '')
        tf = _find_transcript_file(pp, csid)
        if not tf:
            return
        try:
            size = os.path.getsize(tf)
        except OSError:
            return
        if size - _checkpoint_prev_offset(p, sid) < kb * 1024:
            return  # not enough new transcript yet (debounce)
        with _checkpoint_guard:
            if sid in _checkpoint_inflight:
                _scribe_stat(pid, 'checkpoint_coalesced')
                return  # previous worker still running; next boundary covers more
            _checkpoint_inflight.add(sid)
        snap = {'pid': pid, 'sid': sid, 'csid': csid,
                'task': (session.get('task', '') or '').strip(),
                'tf': str(tf)}
        threading.Thread(target=_checkpoint_worker, args=(snap,),
                         daemon=True).start()
    except Exception:
        pass


def _checkpoint_worker(snap):
    """Render the delta since the last watermark, fold it into the running
    summary, append a self-contained `_(live)_` entry + upsert the wm marker
    in one leaf-locked atomic write. SPEC §3.A.MID. Never raises."""
    pid, sid, csid, task, tf = (snap['pid'], snap['sid'], snap['csid'],
                                snap['task'], snap['tf'])
    sema = _get_checkpoint_sema(pid)
    if not sema.acquire(blocking=False):
        _scribe_stat(pid, 'checkpoint_coalesced')  # project at fan-out cap
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)
        return
    try:
        p = load_project(pid)
        if not p:
            return
        prev_off, prev_summary = 0, ''
        try:
            mp = _get_memory_path(p)
            if mp.exists():
                _c, _e, wm = _mem_split_full(mp.read_text(encoding='utf-8'))
                r = _wm_find(wm, sid)
                if r:
                    prev_summary = r.get('running_summary', '') or ''
                    if r.get('transcript_path') == tf:
                        prev_off = int(r.get('byte_offset', 0))
                    else:
                        # resume opened a new .jsonl → restart offset, KEEP
                        # the running summary as the reduce base (no loss).
                        _scribe_stat(pid, 'checkpoint_offset_reset')
        except Exception:
            prev_off, prev_summary = 0, ''
        delta, new_off = _scribe_render_delta(tf, prev_off)
        if not delta.strip() or new_off == prev_off:
            return  # nothing new complete; retry next boundary (offset kept)
        model = state.CONFIG.get('scribe_model', '') or 'haiku'
        dsum, reason = _scribe_summarize_text(delta, model)
        rec = {'session_id': sid, 'claude_session_id': csid,
               'transcript_path': tf, 'byte_offset': new_off,
               'slice_hash': _sha8(delta)}
        if reason != 'extracted':
            # Thin/refused/error delta — advance the offset (that span had
            # nothing material) but write NO entry and keep prev summary.
            rec['running_summary'] = prev_summary
            if _commit_managed_entry(p, wm_upsert=rec):
                _dispatch_condense(p)
            _scribe_stat(pid, f'checkpoint_skipped:{reason}')
            return
        if prev_summary:
            try:
                merged = _scribe_call(
                    model, _SCRIBE_CHECKPOINT_REDUCE,
                    f"PREVIOUS:\n{prev_summary}\n\nNEW:\n{dsum}")
                merged = (merged or '').strip().replace('\n', ' ').strip() or dsum
            except Exception:
                merged = dsum
        else:
            merged = dsum
        merged = merged[:300]
        rec['running_summary'] = merged
        entry = f"- [{now_iso()[:10]}] **{task[:80]}** _(live)_ — {merged}"
        if _commit_managed_entry(p, mem_entry=entry, wm_upsert=rec):
            _dispatch_condense(p)
        _scribe_stat(pid, 'checkpoint_extracted')
    except Exception:
        pass
    finally:
        sema.release()
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)


_SCRIBE_PROMPT = (
    "You are a project-memory scribe. Below is a full agent session transcript "
    "(actions, tool results, reasoning). Write ONE dense line (max 280 chars, no "
    "newlines) for a project memory log: what was done, what was decided/learned, "
    "and any gotcha or follow-up. Be concrete (files, names, decisions). Output "
    "ONLY that line — no preamble, no markdown, no quotes."
)


_SCRIBE_MAP_PROMPT = (
    "This is ONE CHUNK of a longer agent session transcript. In 1-2 tight "
    "sentences, note what was done/decided/learned/broken in THIS chunk only. "
    "Output only those sentences."
)


_SCRIBE_REDUCE_PROMPT = (
    "Below are ordered partial notes from consecutive chunks of one agent "
    "session. Synthesize them into ONE dense line (max 280 chars, no newlines) "
    "for a project memory log: what was done, decided/learned, and any gotcha. "
    "Output ONLY that line."
)


_SCRIBE_CHECKPOINT_REDUCE = (
    "PREVIOUS is the running summary of an IN-PROGRESS agent session so far; "
    "NEW is what happened since. Produce ONE updated dense line (max 280 "
    "chars, no newlines) that SUPERSEDES PREVIOUS by folding in NEW: what's "
    "been done, decided/learned, and open gotchas. Output ONLY that line — "
    "no preamble, no markdown, no quotes."
)


_SCRIBE_SINGLE_LIMIT = 350_000


_SCRIBE_RESULT_CAP = 2000


_SCRIBE_THIN_TEXT_CHARS = 120


_SCRIBE_ACTIVITY_PREFIXES = ('ACTION ', 'RESULT:', 'THINKING:')


_SCRIBE_REFUSAL_MARKERS = (
    "i don't see a transcript", "i do not see a transcript",
    "no transcript", "please paste", "paste the session",
    "paste the transcript", "share the transcript",
    "provide the transcript", "don't have access to",
    "didn't receive", "did not receive", "cannot see any transcript",
    "no session transcript", "there is no transcript",
)


def _scribe_stat(project_id, key, n=1):
    """Add n to a scribe-outcome counter (SPEC §8 telemetry). Best-effort;
    n<=0 is a no-op (no file touch)."""
    if n <= 0:
        return
    try:
        fp = DATA_DIR / f'{project_id}_scribe_stats.json'
        stats = {}
        if fp.exists():
            stats = json.loads(fp.read_text(encoding='utf-8') or '{}')
        stats[key] = int(stats.get(key, 0)) + n
        stats['_updated'] = now_iso()
        fp.write_text(json.dumps(stats, indent=2), encoding='utf-8')
    except Exception:
        pass


def _scribe_render_lines(lines):
    """Render an iterable of raw .jsonl text lines into the compact view.

    Shared core of _scribe_render_transcript (whole file) and
    _scribe_render_delta (Step 6, from a byte offset). Strips base64/image
    blocks, bulk-caps oversized tool_results, skips unparseable lines (so a
    stray leading fragment from a non-boundary offset is harmlessly ignored —
    the leading-partial safety net, SPEC §3.A.MID).
    """
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        msg = m.get('message') if isinstance(m.get('message'), dict) else None
        if not msg or not isinstance(msg.get('content'), list):
            continue
        mtype = m.get('type', '')
        for b in msg['content']:
            if not isinstance(b, dict):
                continue
            bt = b.get('type', '')
            if bt == 'text' and mtype == 'assistant':
                t = (b.get('text') or '').strip()
                if t:
                    out.append(f"ASSISTANT: {t}")
            elif bt == 'thinking':
                t = (b.get('thinking') or b.get('text') or '').strip()
                if t:
                    out.append(f"THINKING: {t[:2000]}")
            elif bt == 'tool_use':
                inp = b.get('input', {})
                try:
                    s = json.dumps(inp, ensure_ascii=False)
                except Exception:
                    s = str(inp)
                out.append(f"ACTION {b.get('name','?')}: {s[:400]}")
            elif bt == 'tool_result':
                c = b.get('content')
                if isinstance(c, list):
                    parts = []
                    for cb in c:
                        if isinstance(cb, dict) and cb.get('type') == 'text':
                            parts.append(cb.get('text', ''))
                        # image/base64 blocks intentionally dropped
                    c = '\n'.join(parts)
                elif not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False) if c else ''
                c = (c or '').strip()
                if not c:
                    continue
                if len(c) > _SCRIBE_RESULT_CAP:
                    half = _SCRIBE_RESULT_CAP // 2
                    c = f"{c[:half]}\n…[{len(c)-_SCRIBE_RESULT_CAP} chars elided]…\n{c[-half:]}"
                out.append(f"RESULT: {c}")
    return '\n'.join(out)


def _scribe_render_transcript(path):
    """Render the whole raw CLI .jsonl into the compact, full-sequence view."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        return _scribe_render_lines(fh)


def _scribe_render_delta(path, byte_offset):
    """Step 6: render ONLY the transcript bytes after `byte_offset`.

    Returns (rendered_text, new_byte_offset). new_byte_offset is the position
    immediately past the last complete newline consumed — it ONLY ever
    advances to a line boundary, so the next call's start is a clean line
    start (no leading-partial drop needed; an anomalous fragment would just
    fail json parse and be skipped by _scribe_render_lines). Trailing-partial
    rule: never consume past the last '\\n' (the agent may be mid-write). If
    `byte_offset` exceeds the file (rotation/truncation, SPEC S3-1) it resets
    to 0. If no complete new line is available, returns ('', byte_offset)
    unchanged (caller skips this checkpoint, retries next turn).
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return '', byte_offset
    if byte_offset > size:
        byte_offset = 0  # transcript rotated/truncated
    try:
        with open(path, 'rb') as fh:
            fh.seek(byte_offset)
            blob = fh.read()
    except OSError:
        return '', byte_offset
    last_nl = blob.rfind(b'\n')
    if last_nl < 0:
        return '', byte_offset  # no complete line yet
    consumed = blob[:last_nl].decode('utf-8', errors='replace')
    new_offset = byte_offset + last_nl + 1
    return _scribe_render_lines(consumed.split('\n')), new_offset


def _scribe_call(model, instruction, body):
    """One blocking `claude -p` call (prompt via stdin to dodge arg limits).

    Returns the model's text output, or raises on failure/timeout.
    Delegates to ClaudeRuntime.oneshot() — single source of truth.
    Callers that catch subprocess.TimeoutExpired should also catch RuntimeError
    since oneshot() normalises all failures to a None return which we raise here.
    """
    result = _agent_runtime.get_runtime('claude').oneshot(
        prompt=instruction,
        model=model,
        stdin_text=body,
        cwd=str(Path.home()),
    )
    if result is None:
        raise RuntimeError("scribe claude call failed (non-zero exit or timeout)")
    return result.text


def _extract_transcript_telemetry(path):
    """Read a JSONL transcript and extract cumulative token usage by model.

    Returns {'model': str, 'input_tokens': int, 'output_tokens': int,
             'cache_read_tokens': int, 'model_tokens': {model: total_tokens}}
    or {} on any failure. Never raises. Indicative, not billing-accurate.
    """
    if not path:
        return {}
    try:
        model_tokens = {}  # model -> {input, output}
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                msg = m.get('message') if isinstance(m.get('message'), dict) else None
                if not msg:
                    continue
                model = msg.get('model', '')
                usage = msg.get('usage')
                if not model or not isinstance(usage, dict):
                    continue
                if model not in model_tokens:
                    model_tokens[model] = {'input': 0, 'output': 0, 'cache_read': 0}
                model_tokens[model]['input'] += int(usage.get('input_tokens') or 0)
                model_tokens[model]['output'] += int(usage.get('output_tokens') or 0)
                model_tokens[model]['cache_read'] += int(
                    usage.get('cache_read_input_tokens') or 0)
        if not model_tokens:
            return {}
        dominant = max(model_tokens.items(),
                       key=lambda x: x[1]['input'] + x[1]['output'])[0]
        return {
            'model': dominant,
            'input_tokens': sum(v['input'] for v in model_tokens.values()),
            'output_tokens': sum(v['output'] for v in model_tokens.values()),
            'cache_read_tokens': sum(v['cache_read'] for v in model_tokens.values()),
            'model_tokens': {m: v['input'] + v['output']
                             for m, v in model_tokens.items()},
        }
    except Exception:
        return {}


def _scribe_extract(project, session):
    """Leg A scribe. Returns (entry_text, outcome_reason).

    entry_text is None when the caller must fall back to the legacy
    stdout-tail summary. Never raises. Dispatch-time incognito/housekeeping
    gate is asserted here too so Phase-2 mid-session triggers inherit it.
    """
    if not state.CONFIG.get('scribe_enabled', True):
        return None, 'disabled'
    if session.get('incognito') or session.get('housekeeping'):
        return None, 'gated'
    pid = project.get('id', '')
    pp = project.get('project_path', '')
    csid = session.get('claude_session_id', '')
    if not csid:
        return None, 'no_csid'
    tf = _find_transcript_file(pp, csid)
    if not tf:
        return None, 'no_transcript'
    with _scribe_lock:
        if pid in _scribing_projects:
            return None, 'busy'
        _scribing_projects.add(pid)
    try:
        try:
            transcript = _scribe_render_transcript(tf)
        except Exception:
            return None, 'parse_empty'
        model = state.CONFIG.get('scribe_model', '') or 'haiku'
        return _scribe_summarize_text(transcript, model)
    finally:
        with _scribe_lock:
            _scribing_projects.discard(pid)


def _scribe_summarize_text(text, model):
    """Core: rendered-transcript text → (one_line_summary, 'extracted') or
    (None, reason). Thin-transcript guard + single/map-reduce + refusal guard.
    No I/O, no locks — shared by _scribe_extract (whole transcript, completion
    path) and the Step-6 checkpoint worker (delta). Never raises.
    """
    _stripped = (text or '').strip()
    _has_activity = any(
        ln.startswith(_SCRIBE_ACTIVITY_PREFIXES)
        for ln in _stripped.splitlines())
    if not _has_activity and len(_stripped) < _SCRIBE_THIN_TEXT_CHARS:
        # No tool/think activity and only a trivial blip (aborted/no-op).
        # Caller falls back rather than persist a hallucinated reply.
        return None, 'parse_empty'
    try:
        if len(_stripped) <= _SCRIBE_SINGLE_LIMIT:
            out = _scribe_call(model, _SCRIBE_PROMPT, _stripped)
        else:
            chunks, cur, n = [], [], 0
            for ln in _stripped.split('\n'):
                cur.append(ln)
                n += len(ln) + 1
                if n >= _SCRIBE_SINGLE_LIMIT:
                    chunks.append('\n'.join(cur))
                    cur, n = [], 0
            if cur:
                chunks.append('\n'.join(cur))
            partials = []
            for i, ch in enumerate(chunks):
                try:
                    partials.append(_scribe_call(model, _SCRIBE_MAP_PROMPT, ch))
                except Exception as e:
                    _log(f"[scribe] map chunk {i + 1}/{len(chunks)} failed "
                         f"(model={model}, {len(ch)}c): {e}")
            if not partials:
                _log(f"[scribe] model_error: all {len(chunks)} map chunks failed "
                     f"(model={model})")
                return None, 'model_error'
            out = _scribe_call(
                model, _SCRIBE_REDUCE_PROMPT,
                '\n'.join(f"- {p}" for p in partials if p))
    except subprocess.TimeoutExpired as e:
        _log(f"[scribe] model_error: timeout (model={model}, "
             f"{len(_stripped)}c in): {e}")
        return None, 'model_error'
    except Exception as e:
        _log(f"[scribe] model_error: call failed (model={model}, "
             f"{len(_stripped)}c in): {e}")
        return None, 'model_error'
    out = (out or '').strip().replace('\n', ' ').strip()
    if not out:
        _log(f"[scribe] model_error: empty reply (model={model}, "
             f"{len(_stripped)}c in)")
        return None, 'model_error'
    if any(mk in out.lower() for mk in _SCRIBE_REFUSAL_MARKERS):
        _log(f"[scribe] model_refused (model={model}): {out[:120]}")
        return None, 'model_refused'
    return out[:300], 'extracted'


def _condense_integrity_check(mem_path, pre_mem, pre_wm, rc):
    """Post-condense safety net for MEMORY.md.

    A condense run is an external `claude` subprocess that rewrites MEMORY.md
    with the Write tool. If it is truncated mid-task (e.g. it hits --max-turns
    before the write step, the failure that motivated this guard) it can leave
    the file empty, drop the managed-region sentinels, nuke the curated index,
    or — worst — delete a `clayrune:wm:` watermark and lose a live session's
    progress. Compare the post-run file against the pre-run snapshot and decide:

      ('ok', ...)      file intact (or no pre-image to protect)
      ('heal', ...)    structure fine but live watermark(s) dropped — caller
                       re-injects them, preserving the agent's curation work
      ('restore', ...) hard corruption — caller rewrites `pre_mem` verbatim

    Returns (action, reason, status_kw). status_kw is merged into the per-
    project condense status so chronic turn-cap failures stay visible in
    telemetry instead of silently self-healing on the next trigger.
    """
    if pre_mem is None:
        # No pre-image captured — can only trust the exit code.
        if rc not in (0, None):
            return 'ok', f'agent exited {rc}', {
                'state': 'error', 'turn_cap': True,
                'error': f'condense agent exited {rc} (likely --max-turns); '
                         'no pre-image captured to verify integrity'}
        return 'ok', '', {}
    try:
        post = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
    except Exception as e:
        return 'restore', f'post-read failed ({e})', {
            'state': 'error',
            'error': f'MEMORY.md unreadable after condense ({e}); restored pre-image'}
    if not post.strip():
        return 'restore', 'empty after condense', {
            'state': 'error',
            'error': 'MEMORY.md empty after condense; restored pre-image'}

    if (_MEM_BEGIN in pre_mem and _MEM_END in pre_mem
            and not (_MEM_BEGIN in post and _MEM_END in post)):
        return 'restore', 'managed-region sentinels missing', {
            'state': 'error',
            'error': 'condense dropped the managed-region sentinels; restored pre-image'}

    pre_cur = _mem_split_full(pre_mem)[0]
    post_cur = _mem_split_full(post)[0]
    if len(pre_cur) > 200 and len(post_cur) < 0.25 * len(pre_cur):
        return 'restore', 'curated index lost >75%', {
            'state': 'error',
            'error': 'condense truncated the curated index (>75% lost); '
                     'restored pre-image'}

    post_wm = set(_mem_split_full(post)[2])
    missing_wm = [w for w in (pre_wm or []) if w not in post_wm]
    if missing_wm:
        if rc not in (0, None):
            kw = {'state': 'error', 'turn_cap': True,
                  'wm_repaired': len(missing_wm),
                  'error': f'condense agent exited {rc} (likely --max-turns) and '
                           f'dropped {len(missing_wm)} live-session watermark(s); '
                           're-injected, no progress lost'}
        else:
            kw = {'state': 'done', 'wm_repaired': len(missing_wm)}
        return 'heal', f'{len(missing_wm)} watermark(s) dropped', kw

    if rc not in (0, None):
        return 'ok', f'agent exited {rc}', {
            'state': 'error', 'turn_cap': True,
            'error': f'condense agent exited {rc} (likely --max-turns); '
                     'MEMORY.md integrity OK — no facts or watermarks lost'}
    return 'ok', '', {}


_CONDENSE_ACTIONS = ('keep', 'demote', 'fold')


_CONDENSE_ARCHIVE_TAIL_KB = 4


_CONDENSE_PLAN_PROMPT = (
    "You are the memory-condense decider (SPEC Leg C). You are NOT an agent: "
    "you have no tools, you do not write files. You receive a JSON object and "
    "you return ONLY a JSON object — no prose, no markdown fences.\n\n"
    "INPUT shape:\n"
    "  curated_headings: exact heading lines of the hand-curated pointer index\n"
    "  entries: [{id, text}] — raw machine-written `- [date] ...` session-log lines\n"
    "  archive_tail: recent already-archived lines (dedupe context only)\n"
    "  line_budget: target max lines for the whole auto-loaded file\n\n"
    "For EACH entry decide, by VALUE not recency:\n"
    "  • keep   — recent, not yet foldable; stays in the session log\n"
    "  • demote — no lasting value as a pointer; the raw line is moved to the\n"
    "             permanent archive (still searchable). NOTHING is erased.\n"
    "  • fold   — its durable insight belongs in the curated index. Provide\n"
    "             `fold_into` (an EXACT string from curated_headings) and\n"
    "             `pointer_line` (one new `- [...]` index line, single line,\n"
    "             no newline, must NOT contain the substring 'clayrune:'). The\n"
    "             raw entry is ALSO archived (fact preserved verbatim).\n\n"
    "Rules: never invent a heading; `fold_into` must match curated_headings\n"
    "verbatim. Prefer fold/demote enough that the file trends under\n"
    "line_budget, but never sacrifice a hard-won fact (paths, line numbers,\n"
    "symbol names, config keys, thresholds, gotchas) — those go to fold or\n"
    "demote, never 'keep-and-hope'. Entries you don't mention default to keep.\n\n"
    "OUTPUT exactly: {\"entry_decisions\":[{\"id\":\"..\",\"action\":\"keep|demote|fold\","
    "\"fold_into\":\"..\",\"pointer_line\":\"..\"}],\"curated_rewrite\":null}\n"
    "(`fold_into`/`pointer_line` only on fold entries; `curated_rewrite` must "
    "be null — wholesale curated re-authoring is not permitted in this mode.)"
)


def _condense_parse_json(raw):
    """Extract the JSON object from a model reply (tolerates ``` fences /
    leading prose). Returns dict or None."""
    s = (raw or '').strip()
    if s.startswith('```'):
        s = s.split('```', 2)[1] if s.count('```') >= 2 else s.strip('`')
        if s.lstrip().lower().startswith('json'):
            s = s.lstrip()[4:]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(s[i:j + 1])
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _validate_condense_payload(payload, valid_ids, valid_headings):
    """Schema + invariant gate, applied BEFORE the server writes anything.
    Returns (True, '') or (False, reason). Strictly pre-write: a reject leaves
    MEMORY.md untouched (no pre-image / restore needed)."""
    if not isinstance(payload, dict):
        return False, 'not_object'
    if payload.get('curated_rewrite') is not None:
        return False, 'curated_rewrite_forbidden_v1'
    decs = payload.get('entry_decisions')
    if not isinstance(decs, list):
        return False, 'entry_decisions_not_list'
    seen = set()
    for d in decs:
        if not isinstance(d, dict):
            return False, 'decision_not_object'
        did = d.get('id')
        if did not in valid_ids:
            return False, 'unknown_id'
        if did in seen:
            return False, 'duplicate_id'
        seen.add(did)
        act = d.get('action')
        if act not in _CONDENSE_ACTIONS:
            return False, 'bad_action'
        if act == 'fold':
            fi = d.get('fold_into')
            pl = d.get('pointer_line')
            if fi not in valid_headings:
                return False, 'fold_into_not_a_heading'
            if not isinstance(pl, str) or not pl.strip():
                return False, 'empty_pointer_line'
            if '\n' in pl or '\r' in pl:
                return False, 'multiline_pointer_line'
            if 'clayrune:' in pl:
                return False, 'pointer_line_synthesizes_machinery'
    return True, ''


def _condense_plan(project):
    """Assemble bounded read-only input, make ONE non-agentic model call, parse
    + validate. Returns (payload|None, reason, model_ms). Never raises."""
    t0 = _time.time()
    try:
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return None, 'no_memory_file', 0
        curated, entries, _wm = _mem_split_full(
            _mem_migrate(mem_path.read_text(encoding='utf-8')))
        if not entries:
            return None, 'noop', 0
        # Collect curated headings as fold targets, but skip any '#' line
        # inside a fenced code block (a shell comment / ATX-looking line in a
        # ``` fence is not a real section) — otherwise a pointer could be
        # folded into code. _condense_apply additionally requires the heading
        # to resolve UNIQUELY at apply time, else it downgrades to demote.
        valid_headings, _in_fence = [], False
        for ln in curated.splitlines():
            if ln.lstrip().startswith('```'):
                _in_fence = not _in_fence
                continue
            if not _in_fence and ln.lstrip().startswith('#'):
                valid_headings.append(ln.strip())
        in_entries, valid_ids = [], set()
        for e in entries:
            eid = _sha8(e)
            valid_ids.add(eid)
            in_entries.append({'id': eid, 'text': e})
        archive_tail = ''
        ap = _get_archive_path(project)
        if ap.exists():
            try:
                blob = ap.read_text(encoding='utf-8')
                archive_tail = blob[-_CONDENSE_ARCHIVE_TAIL_KB * 1024:]
            except Exception:
                pass
        body = json.dumps({
            'curated_headings': valid_headings,
            'entries': in_entries,
            'archive_tail': archive_tail,
            'line_budget': int(state.CONFIG.get('index_line_budget', 160) or 160),
        }, ensure_ascii=False)
        # Default to haiku, NOT sonnet. The structured condense is a one-shot
        # JSON call with no tools and a schema-validated reply — same shape as
        # Scribe, which already defaults to haiku. Sonnet's reasoning depth is
        # wasted here and routinely times out on 30KB+ stdin payloads (live:
        # 91 model_errors + 58 timeouts vs 5 successes before this default
        # was corrected). Users who want sonnet can still set condense_model
        # explicitly in Settings.
        model = state.CONFIG.get('condense_model', '') or 'haiku'
        pid = project.get('id', '')
        try:
            raw = _scribe_call(model, _CONDENSE_PLAN_PROMPT, body)
        except subprocess.TimeoutExpired as e:
            _log(f"[condense] {pid}: model_timeout (model={model}, "
                 f"{len(body)}c in, {len(entries)} entries): {e}")
            return None, 'model_timeout', int((_time.time() - t0) * 1000)
        except Exception as e:
            _log(f"[condense] {pid}: model_error (model={model}, "
                 f"{len(body)}c in, {len(entries)} entries): {e}")
            return None, 'model_error', int((_time.time() - t0) * 1000)
        ms = int((_time.time() - t0) * 1000)
        payload = _condense_parse_json(raw)
        if payload is None:
            _log(f"[condense] {pid}: parse_error after {ms}ms (model={model}) "
                 f"— reply head: {(raw or '')[:160]!r}")
            return None, 'parse_error', ms
        ok, why = _validate_condense_payload(
            payload, valid_ids, set(valid_headings))
        if not ok:
            _log(f"[condense] {pid}: rejected '{why}' after {ms}ms "
                 f"(model={model})")
            return None, why, ms
        return payload, 'ok', ms
    except Exception as e:
        # Static reason — keeps the colon-suffixed telemetry key bounded
        # (raw exception text must never become a _scribe_stats.json key).
        # Detail goes to the log + the bounded last-write-wins status field.
        _log(f"[condense] {project.get('id','')}: plan exception — {e}")
        return None, 'plan_exc', int((_time.time() - t0) * 1000)


def _condense_apply(project, payload):
    """Rebased, transactional apply under the SAME leaf lock the completion
    scribe + Step-6 use. Decisions are keyed by _sha8(entry); any decision
    whose entry vanished meanwhile (Step-6 fold / teardown / floor) is silently
    skipped. wm markers pass through untouched. Returns a stats dict."""
    pid = project.get('id', '')
    mem_path = _get_memory_path(project)
    hard_floor = int(state.CONFIG.get('index_line_hard_floor', 185) or 185)
    decs = {d['id']: d for d in payload.get('entry_decisions', [])}
    st = {'kept': 0, 'demoted': 0, 'folded': 0,
          'skipped_rebased': 0, 'fold_downgraded': 0, 'curated_lines': 0}
    with _get_mem_write_lock(pid):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        curated, entries, wm = _mem_split_full(_mem_migrate(existing))
        cur_lines = curated.splitlines()
        cur_norm = {ln.strip() for ln in cur_lines}
        present_ids = set()
        new_entries, overflow = [], []
        for e in entries:
            eid = _sha8(e)
            present_ids.add(eid)
            # Duplicate byte-identical entry lines hash to the same id, so one
            # decision intentionally applies to ALL of them. This is safe and
            # desirable: demote/fold route every copy verbatim to the
            # append-only archive (no fact lost) and collapse the noise; keep
            # is a per-copy no-op. _validate_condense_payload already rejects
            # duplicate ids in the decision LIST, so the model can't disagree
            # with itself across copies.
            d = decs.get(eid)
            act = d.get('action') if d else 'keep'
            if act == 'demote':
                overflow.append(e)
                st['demoted'] += 1
            elif act == 'fold':
                heading = d.get('fold_into')  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (mop)
                pl = d.get('pointer_line', '').strip()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (mop)
                hits = [k for k, ln in enumerate(cur_lines)
                        if ln.strip() == heading]
                if len(hits) != 1:
                    # Heading vanished, or is ambiguous (0 or >1 matches since
                    # plan time) — never misplace a pointer or lose the fact:
                    # demote the raw entry, skip the curated insert.
                    overflow.append(e)
                    st['fold_downgraded'] += 1
                    continue
                if pl and pl not in cur_norm:
                    cur_lines.insert(hits[0] + 1, pl)
                    cur_norm.add(pl)
                overflow.append(e)        # fact preserved verbatim in archive
                st['folded'] += 1
            else:
                new_entries.append(e)
                st['kept'] += 1
        # Decisions whose target entry is gone (concurrent Step-6 / teardown).
        st['skipped_rebased'] = sum(
            1 for did in decs if did not in present_ids)
        curated2 = '\n'.join(cur_lines)
        # Mechanical line floor backstop (same rule as _commit_managed_entry).
        while new_entries and len(_mem_compose(
                curated2, new_entries, wm).splitlines()) > hard_floor:
            overflow.append(new_entries.pop(0))
        # Post-apply curated size — a gauge (not additive) so soak can watch
        # the model-authored curated index for monotonic low-value drift
        # (additive-only fold has no mechanical eviction path until v2).
        st['curated_lines'] = len(cur_lines)
        _append_to_archive(project, overflow)
        _atomic_write_text(mem_path, _mem_compose(curated2, new_entries, wm))
    return st


def _run_structured_condense(project):
    """Orchestrator for condense_mode='structured'. Mirrors the agent path's
    status/lock discipline; the slow model call is OUTSIDE the leaf lock.
    Caller (_dispatch_condense) already holds the _condensing_projects guard
    and this MUST discard it. Never raises."""
    pid = project['id']
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)
    try:
        payload, reason, ms = _condense_plan(project)
        if payload is None:
            if reason in ('noop', 'no_memory_file'):
                _scribe_stat(pid, f'condense_{reason}')
                _set_condense_status(pid, state='done', model_ms=ms)
            else:
                _scribe_stat(pid, f'condense_rejected:{reason}')
                _set_condense_status(
                    pid, state='error', model_ms=ms,
                    error=f'structured condense not applied ({reason}); '
                          'MEMORY.md left untouched')
            return
        st = _condense_apply(project, payload)
        _scribe_stat(pid, 'condense_structured_ok')
        for k in ('kept', 'demoted', 'folded'):
            _scribe_stat(pid, f'condense_entries_{k}', st.get(k, 0))
        _scribe_stat(pid, 'condense_decisions_skipped_rebased',
                     st.get('skipped_rebased', 0))
        _scribe_stat(pid, 'condense_fold_downgraded',
                     st.get('fold_downgraded', 0))
        _set_condense_status(pid, state='done', model_ms=ms, **st)
        _log(f"[condense] {pid}: structured ok — "
             f"kept={st['kept']} demoted={st['demoted']} "
             f"folded={st['folded']} skipped_rebased={st['skipped_rebased']}")
    except Exception as e:
        _log(f"[condense] {pid}: structured error — {e}")
        _set_condense_status(pid, state='error', error=str(e))
    finally:
        _set_condense_status(pid, finished_at=now_iso(),
                             bytes_after=_condense_combined_bytes(project))
        with _condense_lock:
            if _condense_status.get(pid, {}).get('state') == 'running':
                _condense_status[pid]['state'] = 'done'
            _condensing_projects.discard(pid)


def _dispatch_condense(project):
    """Launch a housekeeping agent to condense memory + CLAUDE.md for a project."""
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return
        _condensing_projects.add(pid)
        _condense_triggered_at[pid] = _time.time()

    # Leg C executor selection. 'structured' (docs/CONDENSE_STRUCTURED_DESIGN.md)
    # replaces the free claude -p + Write agent below with one non-agentic JSON
    # call applied server-side. The structured runner owns the
    # _condensing_projects discard in its finally, same as the agent _run.
    if (state.CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        threading.Thread(target=_run_structured_condense,
                         args=(project,), daemon=True).start()
        return

    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    pp = project.get('project_path', '')

    # P2-1: mark condensation in-flight (bytes_before = pre-condense size).
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)

    # Check if CLAUDE.md exists and is large enough to warrant condensation
    claude_md_path = Path(pp) / 'CLAUDE.md' if pp else None
    claude_md_big = False
    if claude_md_path and claude_md_path.exists():
        try:
            claude_md_big = claude_md_path.stat().st_size > 15 * 1024  # > 15KB
        except OSError:
            pass

    budget = int(state.CONFIG.get('index_line_budget', 160) or 160)
    prompt_parts = [
        "You are a memory housekeeping agent (SPEC Leg C model tier). Your ONLY "
        "job is to curate the project context files so they stay concise and "
        "effective. You decide by VALUE, never by recency.\n",
        f"## MEMORY.md curation — target: the WHOLE file under {budget} LINES\n"
        f"(The harness only auto-loads ~200 lines; staying under {budget} keeps "
        f"headroom. This is a LINE budget, not a KB target.)\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        "3. MEMORY.md has two regions, treat them differently:\n"
        "   - CURATED region (everything ABOVE the "
        "`<!-- clayrune:managed:begin -->` sentinel): the hand-curated pointer "
        "index. You ARE permitted to compact THIS region (you are the only "
        "agent allowed to): merge overlapping pointers/sections covering the "
        "same subsystem, drop stale 'as of YYYY-MM-DD' notes clearly superseded "
        "by a later section, cut narration but keep the fact.\n"
        "   - MANAGED region (between `<!-- clayrune:managed:begin -->` and "
        "`<!-- clayrune:managed:end -->`, under `## Session Log`): raw "
        "machine-written session entries. For EACH entry decide, by value: "
        "(a) fold its durable insight into the matching curated pointer/topic "
        "then remove the raw entry; (b) if it has no lasting value, DEMOTE it "
        "(move it) to the archive; (c) keep it in the managed region only if "
        "it's recent and not yet foldable. Never keep/drop by recency alone.\n"
        "4. KEEP THE FORMAT: the rewritten file must still have the "
        "`<!-- clayrune:managed:begin -->` / `## Session Log` / "
        "`<!-- clayrune:managed:end -->` structure intact. The managed region "
        "may legitimately end up EMPTY after folding — that is fine; keep the "
        "sentinels and header. CRITICAL: any line beginning "
        "`<!-- clayrune:wm:` is a live-session watermark — PRESERVE IT "
        "VERBATIM, do not fold/move/delete/reformat it (deleting one loses a "
        "running session's progress and forces a re-scribe from zero).\n"
        "5. NEVER hard-delete a fact. The only permitted deletions are exact "
        "duplicates or an entry STRICTLY superseded by a newer one that wholly "
        "contains it. 'Not worth a curated slot' means DEMOTE to the archive "
        "(still searchable cold storage), never erase.\n"
        "6. DO NOT lose hard-won facts. Preserve verbatim: file paths, line "
        "numbers, function/class names, config keys, exact numeric thresholds, "
        "API signatures, command snippets, and any 'gotcha' warnings.\n"
        f"7. Append demoted/overflow entries to {archive_path} (create it if "
        f"needed). NEVER delete or truncate the archive — it is permanent "
        f"searchable cold storage (SPEC D3).\n"
        f"8. Write the curated result back to {mem_path}. Target under {budget} "
        f"lines; if after honest folding it is still slightly over, that is "
        f"acceptable — do NOT delete critical facts just to hit a number.\n",
    ]

    if claude_md_big:
        prompt_parts.append(
            f"\n## CLAUDE.md condensation — target under 15KB\n"
            f"9. Read {claude_md_path}\n"
            "10. This file contains project instructions and context that Claude CLI loads natively. "
            "Condense it while preserving ALL critical information:\n"
            "   - Keep all instructions, rules, and constraints verbatim.\n"
            "   - Merge duplicate/overlapping sections.\n"
            "   - Remove redundant examples, excessive formatting, and verbose explanations.\n"
            "   - Compress session logs / historical notes into brief summaries.\n"
            "   - Preserve code snippets, API references, and config patterns exactly.\n"
            f"11. Write the condensed result back to {claude_md_path}. Target under 15KB; do NOT "
            f"strip critical rules just to hit a number.\n"
        )

    prompt_parts.append(
        "\nBE TURN-EFFICIENT (you have a limited turn budget): read EVERY "
        "input file you need in your FIRST turn using parallel tool calls, "
        "do all the folding/demotion reasoning, then write each output file "
        "EXACTLY ONCE. Do not re-read a file you have already read. The write "
        "step is what matters — do not spend the whole budget exploring.\n"
        "\nDo NOT create any other files. Do NOT modify any code. Only touch the files listed above."
    )
    prompt = '\n'.join(prompt_parts)

    model = state.CONFIG.get('condense_model', '') or 'sonnet'
    # --max-turns 14 (was 5): the workload is read MEMORY.md + read archive
    # (+ optionally read CLAUDE.md) + fold/demote N entries + append archive
    # + rewrite MEMORY.md. 5 turns were routinely exhausted on the reads
    # alone, so the CLI exited 1 *before the write step* and the run was
    # flagged ERROR (it only "self-healed" because the next trigger retried).
    # The post-run integrity guard below makes a truncated run safe; this
    # gives it enough room to actually finish.
    cmd = [_resolve_claude(), '-p', prompt, '--model', model, '--max-turns', '14',
           '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions']

    cwd = pp if pp and Path(pp).is_dir() else str(Path.home())

    def _run():
        session_id = f'condense_{uuid.uuid4().hex[:8]}'
        # Pre-image snapshot for the post-run integrity guard. Captured here
        # (just before launch) so a truncated/botched run can never corrupt
        # MEMORY.md or lose a live-session watermark.
        try:
            pre_mem = mem_path.read_text(encoding='utf-8') if mem_path.exists() else None
        except Exception:
            pre_mem = None
        pre_wm = _mem_split_full(pre_mem)[2] if pre_mem else []
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Housekeeping (condense)', 'housekeeping',
                              session_id, pid, 'Memory condensation')

            session = {
                'proc': proc,
                'status': 'running',
                'task': 'Memory condensation',
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': pid,
                'mode': 'A',
                'housekeeping': True,
            }
            mgr = get_manager(pid)
            with mgr.lock:
                agent_sessions[session_id] = session
                mgr.session_ids.add(session_id)

            # Reuse existing stream reader (blocks until proc exits)
            _read_agent_stream(proc, session)

            # Post-run safety net: a truncated condense (e.g. --max-turns hit
            # before the write step) must never leave MEMORY.md corrupted or
            # drop a live-session watermark.
            rc = proc.returncode
            action, reason, kw = _condense_integrity_check(
                mem_path, pre_mem, pre_wm, rc)
            if action == 'restore':
                try:
                    mem_path.write_text(pre_mem, encoding='utf-8')  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (mop)
                    _log(f"[condense] {pid}: integrity FAIL ({reason}) — "
                         f"restored pre-image")
                except Exception as e:
                    _log(f"[condense] {pid}: RESTORE FAILED ({e}) — {reason}")
            elif action == 'heal':
                try:
                    cur, ent, wm = _mem_split_full(
                        mem_path.read_text(encoding='utf-8'))
                    have = set(wm)
                    for w in pre_wm:
                        if w not in have:
                            wm.append(w)
                            have.add(w)
                    mem_path.write_text(_mem_compose(cur, ent, wm),
                                        encoding='utf-8')
                    _log(f"[condense] {pid}: healed ({reason}) — re-injected "
                         f"dropped watermark(s), kept agent curation")
                except Exception as e:
                    # Heal failed — fall back to full restore to protect the
                    # load-bearing watermark over the agent's curation.
                    try:
                        mem_path.write_text(pre_mem, encoding='utf-8')  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (mop)
                    except Exception:
                        pass
                    _log(f"[condense] {pid}: heal FAILED ({e}) — restored "
                         f"pre-image")
                    kw = {'state': 'error',
                          'error': f'watermark heal failed ({e}); '
                                   'restored pre-image'}
            if kw:
                _set_condense_status(pid, **kw)
        except Exception as e:
            _log(f"[condense] error for {pid}: {e}")
            _set_condense_status(pid, state='error', error=str(e),
                                 finished_at=now_iso())
        finally:
            # P2-1: record outcome. bytes_after = post-condense size; a
            # still-'running' state means the body finished without raising.
            _set_condense_status(pid, finished_at=now_iso(),
                                 bytes_after=_condense_combined_bytes(project))
            with _condense_lock:
                if _condense_status.get(pid, {}).get('state') == 'running':
                    _condense_status[pid]['state'] = 'done'
                _condensing_projects.discard(pid)

    threading.Thread(target=_run, daemon=True).start()
