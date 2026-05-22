"""AgentRuntime — multi-provider abstraction for Mission Control.

This module is the single seam between MC and any underlying agent CLI
(claude-code, gemini, codex, ...). It lets MC drive any provider through
one uniform interface, with graceful degradation where capabilities differ.

**Current scope (feat/multi-provider-agents):**
- ClaudeRuntime: concrete implementation with all claude-specific logic
  lifted from server.py (_resolve_claude, _build_claude_flags, _find_transcript_file,
  JSONL parser). dispatch/followup/interrupt/stop delegate back to server.py via
  registered hooks to avoid circular imports (first-PR scope per design doc §9.1).
- GeminiRuntime: self-contained Gemini CLI driver (Mode A, synthetic followup).

Both share the AgentRuntime ABC, SessionHandle, and AgentEvent shapes.

See docs/MULTI_PROVIDER_DESIGN.md for the full architectural design.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time as _time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Literal, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess flags (mirror server.py — keep windows from popping up consoles)
# ─────────────────────────────────────────────────────────────────────────────


if sys.platform == 'win32':
    _POPEN_FLAGS = subprocess.CREATE_NO_WINDOW
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
else:
    _POPEN_FLAGS = 0
    _STARTUPINFO = None


# ─────────────────────────────────────────────────────────────────────────────
# Event types and dataclasses
# ─────────────────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    INIT = 'init'
    ASSISTANT_TEXT = 'assistant_text'
    THINKING = 'thinking'
    TOOL_USE = 'tool_use'
    TOOL_RESULT = 'tool_result'
    USER_MESSAGE = 'user_message'
    TURN_END = 'turn_end'
    USAGE = 'usage'
    RATE_LIMIT = 'rate_limit'
    AUTH_ERROR = 'auth_error'
    PLAN_REQUEST = 'plan_request'
    QUESTION = 'question'
    INTERRUPTED = 'interrupted'
    PROCESS_EXIT = 'process_exit'
    WARN = 'warn'
    ERROR = 'error'


@dataclass
class AgentEvent:
    type: EventType
    provider: str
    session_id: Optional[str]
    mc_session_id: str
    timestamp: str
    payload: Dict[str, Any] = field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None
    sequence: int = 0


@dataclass
class ProviderCapabilities:
    name: str
    display_name: str
    supports_mode_a: bool = True
    supports_mode_b: bool = False
    mode_b_kind: Literal['native', 'synthetic', 'none'] = 'none'
    default_mode: Literal['A', 'B'] = 'A'
    supports_session_resume: bool = False
    supports_mcp: bool = False
    supports_skills: bool = False
    supports_plan_mode: bool = False
    supports_ask_user_question: bool = False
    supports_streaming_text: bool = False
    emits_usage: bool = False
    emits_rate_limit: bool = False
    # Brief-specified fields (CapabilityFlags members)
    emits_cost: bool = False
    emits_num_turns: bool = False
    image_input: bool = False
    context_window: Optional[int] = None
    # Context injection
    context_injection: Literal['flag', 'file', 'prepend', 'read-file'] = 'prepend'
    context_file_name: Optional[str] = None
    oneshot_supported: bool = False


# Alias so caller code from the brief can use the name CapabilityFlags.
CapabilityFlags = ProviderCapabilities


@dataclass
class AuthState:
    status: Literal['ok', 'not_logged_in', 'invalid_api_key', 'unknown', 'not_installed']
    method: Optional[str] = None
    last_checked: str = ''
    error_text: Optional[str] = None


@dataclass
class HealthStatus:
    installed: bool
    binary_path: Optional[Path]
    version: Optional[str]
    auth_state: AuthState
    install_hint: str = ''
    diagnostic: str = ''


@dataclass
class OneshotResult:
    text: str
    raw: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    cost_usd: Optional[float] = None


@dataclass
class SessionHandle:
    """Opaque handle returned by `runtime.dispatch()`.

    Wraps the MC session dict in `agent_sessions` so callers can keep using
    the existing session-dict shape (`log_lines`, `status`, `proc`, ...).
    `session_dict` IS the entry in `agent_sessions` — mutations propagate.
    """

    mc_session_id: str
    provider: str
    mode: Literal['A', 'B']
    project_path: str
    project_id: str
    session_dict: Dict[str, Any]
    provider_session_id: Optional[str] = None
    started_at: str = ''
    capabilities: Optional[ProviderCapabilities] = None
    meta: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# AgentRuntime ABC
# ─────────────────────────────────────────────────────────────────────────────


class AgentRuntime(ABC):
    name: str = ''
    display_name: str = ''

    @abstractmethod
    def resolve_binary(self) -> Optional[Path]:
        """Return the absolute path to the provider's CLI binary, or a fallback
        string 'provider-name' that will FileNotFoundError on spawn."""
        ...

    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Probe install + auth state. May spawn the binary with --version."""
        ...

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return the CapabilityFlags for this provider."""
        ...

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False) -> List[str]:
        """Return [binary, *mode_flags] — the base command for a session.

        Callers extend with task-specific args (-p <task>, --append-system-prompt,
        -r <resume_id>, etc.). Providers that don't use flag-based configuration
        may return just [binary].

        Default: [str(resolve_binary())]. Override in every concrete subclass.
        """
        p = self.resolve_binary()
        return [str(p) if p else self.name]

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse a single output line from the provider CLI into a normalized AgentEvent.

        Returns None for empty or unrecognized lines. `event.raw` carries the
        original parsed JSON so callers can inspect provider-specific fields.

        Default: treats every non-empty line as ASSISTANT_TEXT. Override to
        implement provider-specific stream-json parsing.
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None
        return AgentEvent(
            type=EventType.ASSISTANT_TEXT, provider=self.name,
            session_id=None, mc_session_id=mc_session_id,
            timestamp=_now_iso(), payload={'text': line},
        )

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Return the path to the provider's on-disk session transcript, or None.

        For providers with no native transcript store (e.g. Gemini, Aider),
        return None. Callers gate Scribe / revive / history on the result.

        Default: None (no transcript store).
        """
        return None

    @abstractmethod
    def dispatch(self, **kwargs) -> SessionHandle:
        """Spawn a new agent session. Returns a SessionHandle immediately."""
        ...

    @abstractmethod
    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        """Send a follow-up message to a live session (Mode B stdin or respawn)."""
        ...

    @abstractmethod
    def interrupt(self, handle: SessionHandle) -> None:
        """Hard-kill the session process. Emits AgentEvent(type=INTERRUPTED)."""
        ...

    @abstractmethod
    def stop(self, handle: SessionHandle) -> None:
        """Gracefully stop the session. Mode A: same as interrupt."""
        ...

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        """Non-interactive single-turn call for Scribe / condense / summary.

        Returns None if the provider can't do a non-streaming call.
        Default: not supported.
        """
        return None

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        """Translate a non-zero exit code + recent output into a user-friendly hint.

        Returns None when nothing is recognizable (caller falls back to
        "exited with code N"). Providers override to add per-CLI patterns.
        """
        return None

    def auth_status(self) -> dict:
        """Return cached (cheap) auth state. No subprocess.

        Providers should maintain an in-memory cache updated by auth_probe() or
        by background session output parsing. Default: calls health_check() which
        may spawn a process — override for a truly cheap cached implementation.

        Response keys: ok (bool), status (str), method (str|None),
        error_text (str|None), last_checked (str|None).
        """
        try:
            h = self.health_check()
            st = h.auth_state
            return {
                'ok': st.status == 'ok',
                'status': st.status,
                'method': st.method,
                'error_text': st.error_text,
                'last_checked': st.last_checked,
            }
        except Exception as e:
            return {'ok': False, 'status': 'unknown', 'method': None,
                    'error_text': str(e), 'last_checked': _now_iso()}

    def auth_probe(self) -> dict:
        """Actively probe auth state (may spawn a process). Updates internal cache.

        Default: delegates to auth_status(). Providers that can cheaply verify
        creds without a full health_check() should override.
        """
        return self.auth_status()

    def auth_logout(self) -> dict:
        """Revoke / clear stored credentials.

        Default: not supported. Providers that have a programmatic logout command
        should override.
        """
        return {
            'ok': False,
            'error': f'{self.display_name} programmatic logout is not supported.',
        }


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


_RUNTIMES: Dict[str, AgentRuntime] = {}


def register_runtime(runtime: AgentRuntime) -> None:
    _RUNTIMES[runtime.name] = runtime


def get_runtime(name: str) -> AgentRuntime:
    if name not in _RUNTIMES:
        raise KeyError(f"unknown runtime: {name!r}")
    return _RUNTIMES[name]


def available_runtimes() -> List[AgentRuntime]:
    return list(_RUNTIMES.values())


def installed_runtimes() -> List[AgentRuntime]:
    out = []
    for r in _RUNTIMES.values():
        try:
            if r.health_check().installed:
                out.append(r)
        except Exception:
            pass
    return out


def default_runtime_name() -> str:
    return 'claude'


def runtime_for_project(project: Dict[str, Any]) -> AgentRuntime:
    name = (project or {}).get('provider') or default_runtime_name()
    if name not in _RUNTIMES:
        # Unknown provider on a project record — fall back silently to claude.
        name = default_runtime_name()
    return _RUNTIMES[name]


# ─────────────────────────────────────────────────────────────────────────────
# ClaudeRuntime — lifted logic + delegate hooks for dispatch
# ─────────────────────────────────────────────────────────────────────────────


# server.py registers its dispatch/followup/stop/interrupt hooks at startup.
# Doing it via this dict avoids the circular import (agent_runtime.py is
# imported by server.py, not the reverse) and keeps the existing claude code
# path byte-identical — we're adding a new entry point that calls back into it.
_CLAUDE_HOOKS: Dict[str, Callable] = {}


def register_claude_hooks(*,
                          resolve_binary: Callable,
                          health_check: Callable,
                          dispatch: Callable,
                          followup: Callable,
                          stop: Callable,
                          interrupt: Callable,
                          oneshot: Optional[Callable] = None,
                          auth_status: Optional[Callable] = None,
                          auth_probe: Optional[Callable] = None) -> None:
    """Called once from server.py at startup to wire ClaudeRuntime back into
    the legacy code path. ClaudeRuntime's dispatch/followup/interrupt/stop
    methods call these hooks so the claude path runs unchanged.
    """
    _CLAUDE_HOOKS['resolve_binary'] = resolve_binary
    _CLAUDE_HOOKS['health_check'] = health_check
    _CLAUDE_HOOKS['dispatch'] = dispatch
    _CLAUDE_HOOKS['followup'] = followup
    _CLAUDE_HOOKS['stop'] = stop
    _CLAUDE_HOOKS['interrupt'] = interrupt
    if oneshot:
        _CLAUDE_HOOKS['oneshot'] = oneshot
    if auth_status:
        _CLAUDE_HOOKS['auth_status'] = auth_status
    if auth_probe:
        _CLAUDE_HOOKS['auth_probe'] = auth_probe


# Claude Code's native transcript store lives here.
_CLAUDE_HOME = Path.home() / '.claude' / 'projects'

# Auth error sentinels emitted by claude CLI stderr.
# Mirrors _AUTH_ERROR_PATTERNS in server.py — keep in sync.
_CLAUDE_AUTH_PATTERNS: List[tuple] = [
    (re.compile(r'please\s+run\s*/login', re.I), 'not_logged_in'),
    (re.compile(r'not\s+logged\s+in', re.I), 'not_logged_in'),
    (re.compile(r'invalid\s+(?:api\s+)?key', re.I), 'invalid_api_key'),
    (re.compile(r'authentication_error', re.I), 'unknown'),
]


class ClaudeRuntime(AgentRuntime):
    """Concrete runtime for Anthropic's claude-code CLI.

    All claude-specific logic from server.py is lifted here:
    - resolve_binary()  ← _resolve_claude()
    - build_command()   ← _build_claude_flags()
    - parse_event()     ← _read_agent_stream JSONL parsing
    - transcript_path() ← _find_transcript_file()
    - oneshot()         ← _scribe_call()

    dispatch / write_followup / interrupt / stop delegate back to server.py
    via _CLAUDE_HOOKS to avoid circular imports (first-PR scope, design §9.1).
    """

    name = 'claude'
    display_name = 'Claude Code'

    # ── Path helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _encode_project_path(project_path: str) -> Optional[str]:
        """Encode a project path to Claude Code's directory naming convention.

        C:\\Users\\foo\\bar  →  C--Users-foo-bar
        /home/user/project   →  -home-user-project

        Mirrors _encode_project_path() in server.py.
        """
        if not project_path:
            return None
        try:
            resolved = str(Path(project_path).resolve())
        except Exception:
            return None
        return resolved.replace(':', '-').replace('\\', '-').replace('/', '-')

    # ── Binary resolution — lifted from _resolve_claude() in server.py ────────

    def resolve_binary(self) -> Optional[Path]:
        """Return the absolute path to the claude CLI binary.

        Mirrors _resolve_claude() in server.py exactly. Handles the Windows
        .exe-vs-.cmd orphan case and falls back to common install locations
        when the binary is not yet on PATH.

        Returns Path('claude') as last resort — callers that spawn will get
        a FileNotFoundError if claude is truly not installed.
        """
        found = shutil.which('claude')
        if found:
            if sys.platform == 'win32':
                # npm generates claude / claude.cmd / claude.ps1 but NEVER a
                # top-level claude.exe. A claude.exe sitting next to a claude.cmd
                # is a STALE ORPHAN — shutil.which/PATHEXT prefer .exe, so when
                # both co-exist, use the .cmd shim instead.
                p = Path(found)
                if p.suffix.lower() == '.exe':
                    sibling_cmd = p.with_suffix('.cmd')
                    try:
                        if sibling_cmd.exists():
                            return sibling_cmd
                    except Exception:
                        pass
            return Path(found)

        # Fallbacks for common install locations not yet on PATH (e.g. winget
        # adds claude to PATH for new shells, but the running server still has
        # the pre-install PATH).
        if sys.platform == 'win32':
            candidates = [
                Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd',
                Path(os.environ.get('USERPROFILE', '')) / '.claude' / 'bin' / 'claude.cmd',
                Path(os.environ.get('USERPROFILE', '')) / '.claude' / 'bin' / 'claude.exe',
                Path(os.environ.get('USERPROFILE', '')) / 'AppData' / 'Roaming' / 'npm' / 'claude.cmd',
            ]
        else:
            home = Path(os.environ.get('HOME', str(Path.home())))
            candidates = [
                home / '.claude' / 'bin' / 'claude',
                home / '.local' / 'bin' / 'claude',
                home / '.npm-global' / 'bin' / 'claude',
                Path('/usr/local/bin/claude'),
                Path('/opt/homebrew/bin/claude'),
            ]
        for c in candidates:
            try:
                if c.exists():
                    return c
            except Exception:
                pass
        return Path('claude')  # last resort — will FileNotFoundError on spawn

    def resolve_binary_str(self) -> str:
        """Return binary path as a string. Matches _resolve_claude() return type."""
        p = self.resolve_binary()
        return str(p) if p else 'claude'

    # ── Command builder — lifted from _build_claude_flags() in server.py ──────

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False) -> List[str]:
        """Return [binary, *flags]. Equivalent to _build_claude_flags() in server.py.

        Config values are passed explicitly (not read from server.py CONFIG) so
        this stays testable in isolation. Callers pass:
            model      = project.get('agent_model') or CONFIG.get('agent_model')
            max_turns  = CONFIG.get('agent_max_turns', 0)
            perm_mode  = CONFIG.get('agent_permission_mode', '')
            channels   = project.get('agent_channels') or CONFIG.get('agent_channels')
            remote_control = project.get('agent_remote_control') or CONFIG.get(...)
            streaming  = True for Mode B (--input-format stream-json)

        The returned list is [binary, '--print', '--verbose', ...] — callers extend
        with -p <task>, --append-system-prompt <ctx>, -r <csid>, etc.
        """
        cmd = [self.resolve_binary_str()]
        cmd.extend([
            '--print', '--verbose',
            '--output-format', 'stream-json',
            '--dangerously-skip-permissions',
        ])
        if streaming:
            cmd.extend(['--input-format', 'stream-json'])
        if model:
            cmd.extend(['--model', model])
        if max_turns and int(max_turns) > 0:
            cmd.extend(['--max-turns', str(int(max_turns))])
        if perm_mode:
            cmd.extend(['--permission-mode', perm_mode])
        if channels:
            cmd.extend(['--channels', channels])
        if remote_control:
            cmd.append('--remote-control')
        return cmd

    # ── JSONL event parser — lifted from _read_agent_stream in server.py ──────

    @staticmethod
    def _scan_auth_error(text: str) -> Optional[str]:
        """Return the reason code if `text` matches a claude auth-error sentinel."""
        for pat, reason in _CLAUDE_AUTH_PATTERNS:
            if pat.search(text):
                return reason
        return None

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse a single JSONL line from claude's --output-format stream-json.

        Returns a normalized AgentEvent, or None for empty / unrecognized lines.
        event.raw always carries the original parsed JSON dict so callers can
        inspect provider-specific fields without re-parsing (e.g. _capture_system_init,
        _handle_push_signal in server.py both read event.raw unchanged).

        For 'assistant' messages with mixed content blocks (text + tool_use),
        the primary event type reflects the first block; all blocks are in
        payload['blocks'] for callers that need them all.
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None

        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Non-JSON = stderr line (auth errors, install messages, etc.)
            reason = self._scan_auth_error(line)
            if reason:
                return AgentEvent(
                    type=EventType.AUTH_ERROR, provider='claude',
                    session_id=None, mc_session_id=mc_session_id,
                    timestamp=_now_iso(),
                    payload={'reason': reason, 'raw_line': line},
                )
            return AgentEvent(
                type=EventType.ASSISTANT_TEXT, provider='claude',
                session_id=None, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'text': line},
            )

        if not isinstance(msg, dict):
            return None

        msg_type = msg.get('type', '')
        session_id = msg.get('session_id')

        if msg_type == 'assistant':
            blocks: List[Dict[str, Any]] = []
            for block in msg.get('message', {}).get('content', []):
                if not isinstance(block, dict):
                    continue
                bt = block.get('type', '')
                if bt == 'text':
                    blocks.append({'type': 'text', 'text': block.get('text', '')})
                elif bt == 'tool_use':
                    blocks.append({
                        'type': 'tool_use',
                        'name': block.get('name', ''),
                        'input': block.get('input', {}),
                        'tool_use_id': block.get('id'),
                    })
                elif bt == 'thinking':
                    blocks.append({
                        'type': 'thinking',
                        'text': block.get('thinking') or block.get('text', ''),
                    })
            # Primary type: determined by the first content block
            primary_type = EventType.ASSISTANT_TEXT
            if blocks:
                first_bt = blocks[0].get('type', 'text')
                if first_bt == 'tool_use':
                    primary_type = EventType.TOOL_USE
                elif first_bt == 'thinking':
                    primary_type = EventType.THINKING
            return AgentEvent(
                type=primary_type, provider='claude',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'blocks': blocks}, raw=msg,
            )

        elif msg_type == 'result':
            return AgentEvent(
                type=EventType.TURN_END, provider='claude',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={
                    'usage': msg.get('usage'),
                    'cost_usd': msg.get('cost_usd'),
                    'num_turns': msg.get('num_turns'),
                    'rc': msg.get('result_code'),
                },
                raw=msg,
            )

        elif msg_type == 'system' and msg.get('subtype') == 'init':
            return AgentEvent(
                type=EventType.INIT, provider='claude',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={
                    'model': msg.get('model'),
                    'cli_version': msg.get('claude_code_version'),
                    'cwd': msg.get('cwd'),
                    'mcp_servers': msg.get('mcp_servers', []),
                    'tools': msg.get('tools', []),
                    'skills': msg.get('skills', []),
                    'agents': msg.get('agents', []),
                    'plugins': msg.get('plugins', []),
                    'slash_commands': msg.get('slash_commands', []),
                    'memory_paths': msg.get('memory_paths', []),
                    'permission_mode': msg.get('permissionMode'),
                    'fast_mode_state': msg.get('fast_mode_state'),
                    'api_key_source': msg.get('apiKeySource'),
                },
                raw=msg,
            )

        elif msg_type == 'rate_limit_event':
            ri = msg.get('rate_limit_info', {}) or {}
            return AgentEvent(
                type=EventType.RATE_LIMIT, provider='claude',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={
                    'status': ri.get('status'),
                    'resets_at': ri.get('resetsAt'),
                    'rate_limit_type': ri.get('rateLimitType'),
                    'overage_status': ri.get('overageStatus'),
                    'is_using_overage': ri.get('isUsingOverage'),
                    'overage_resets_at': ri.get('overageResetsAt'),
                },
                raw=msg,
            )

        elif msg_type == 'user':
            msg_content = msg.get('message', {}) or {}
            return AgentEvent(
                type=EventType.USER_MESSAGE, provider='claude',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={
                    'role': msg_content.get('role', ''),
                    'content': msg_content.get('content', ''),
                },
                raw=msg,
            )

        # Unknown / unhandled message type — return None (caller handles raw line)
        return None

    # ── Transcript path — lifted from _find_transcript_file() in server.py ────

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Locate the Claude Code transcript JSONL for a given session, or None.

        Mirrors _find_transcript_file() in server.py exactly.
        Checks both the canonical encoded path and the underscore→dash variant
        that Claude Code sometimes uses.

        Returns the Path if the file exists, None otherwise. Callers that need
        to build the path without existence-checking should use
        _build_transcript_path() instead.
        """
        if not session_id:
            return None
        encoded = self._encode_project_path(project_path)
        if not encoded:
            return None
        candidates = [_CLAUDE_HOME / encoded]
        encoded_alt = encoded.replace('_', '-')
        if encoded_alt != encoded:
            candidates.append(_CLAUDE_HOME / encoded_alt)
        for d in candidates:
            f = d / f'{session_id}.jsonl'
            try:
                if f.exists():
                    return f
            except OSError:
                continue
        return None

    def _build_transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Return the canonical .jsonl path without existence check.

        Mirrors _session_transcript_path() in server.py. Unlike transcript_path(),
        does NOT check whether the file exists and only uses the primary encoded
        variant. Use for callers that need the path before the file is created
        (e.g. size checks, watermark records).
        """
        if not session_id:
            return None
        encoded = self._encode_project_path(project_path)
        if not encoded:
            return None
        return _CLAUDE_HOME / encoded / f'{session_id}.jsonl'

    def list_sessions(self, project_path: str, limit: int = 5) -> List[Dict[str, Any]]:
        """List recent sessions by scanning the Claude transcript directory.

        Mirrors _recent_claude_transcripts() in server.py. Uses parse_event()
        so the user-text extraction stays consistent with the live stream reader.
        Checks both path-encoded variants (underscore and dash).

        Returns [{session_id, mtime, first_user, last_user, turns, size}]
        sorted by mtime desc, at most `limit` entries.
        """
        encoded = self._encode_project_path(project_path)
        if not encoded:
            return []
        candidates = [_CLAUDE_HOME / encoded]
        encoded_alt = encoded.replace('_', '-')
        if encoded_alt != encoded:
            candidates.append(_CLAUDE_HOME / encoded_alt)

        seen: set = set()
        files: List = []
        for d in candidates:
            try:
                if not d.exists():
                    continue
            except OSError:
                continue
            try:
                for f in d.glob('*.jsonl'):
                    if f.name in seen:
                        continue
                    seen.add(f.name)
                    try:
                        files.append((f, f.stat().st_mtime))
                    except OSError:
                        continue
            except OSError:
                continue
        files.sort(key=lambda x: x[1], reverse=True)
        files = files[:limit]

        results: List[Dict[str, Any]] = []
        for f, mtime in files:
            first_user = ''
            last_user = ''
            turns = 0
            try:
                with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                    for raw_line in fh:
                        ev = self.parse_event(raw_line)
                        if ev is None or ev.type != EventType.USER_MESSAGE:
                            continue
                        if ev.payload.get('role') != 'user':
                            continue
                        content = ev.payload.get('content', '')
                        if isinstance(content, list):
                            texts = [
                                str(b.get('text', ''))
                                for b in content
                                if isinstance(b, dict) and b.get('type') == 'text'
                            ]
                            text = ' '.join(t.strip() for t in texts if t).strip()
                        else:
                            text = str(content).strip() if content else ''
                        if not text:
                            continue
                        turns += 1
                        if not first_user:
                            first_user = text
                        last_user = text
            except Exception:
                pass
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            results.append({
                'session_id': f.stem,
                'mtime': mtime,
                'first_user': first_user[:300],
                'last_user': last_user[:300],
                'turns': turns,
                'size': size,
            })
        return results

    def parse_transcript_file(self, path: Path,
                              max_messages: int = 300) -> List[Dict[str, Any]]:
        """Parse a Claude JSONL transcript file into message dicts for display.

        Mirrors _parse_transcript_messages() in server.py. Uses parse_event()
        so the parsing logic stays consistent with the live stream reader. Thinking
        blocks are silently skipped to match the original behaviour.

        Returns [{role, text, tool?, timestamp}] where role is
        'user' | 'assistant' | 'tool_call'. On file/parse failure returns
        [{'role': 'error', 'text': '<reason>'}].
        """
        messages: List[Dict[str, Any]] = []
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                for raw_line in fh:
                    ev = self.parse_event(raw_line)
                    if ev is None:
                        continue
                    ts = (ev.raw or {}).get('timestamp', '')
                    if ev.type == EventType.USER_MESSAGE:
                        content = ev.payload.get('content', '')
                        if isinstance(content, list):
                            texts = [
                                str(b.get('text', ''))
                                for b in content
                                if isinstance(b, dict) and b.get('type') == 'text'
                            ]
                            text = ' '.join(t.strip() for t in texts if t).strip()
                        else:
                            text = str(content).strip() if content else ''
                        if text:
                            messages.append({'role': 'user', 'text': text[:5000],
                                             'timestamp': ts})
                    elif ev.type in (EventType.ASSISTANT_TEXT, EventType.TOOL_USE,
                                     EventType.THINKING):
                        for block in ev.payload.get('blocks', []):
                            btype = block.get('type', '')
                            if btype == 'text':
                                txt = str(block.get('text', '')).strip()
                                if txt:
                                    messages.append({'role': 'assistant',
                                                     'text': txt[:5000],
                                                     'timestamp': ts})
                            elif btype == 'tool_use':
                                messages.append({'role': 'tool_call',
                                                 'tool': block.get('name', ''),
                                                 'timestamp': ts})
                    if len(messages) >= max_messages:
                        break
        except Exception as e:
            return [{'role': 'error', 'text': f'Failed to parse transcript: {e}'}]
        return messages

    def memory_path(self, project_path: str) -> Optional[Path]:
        """Return the Claude Code native MEMORY.md path for a project.

        Mirrors _native_memory_path() in server.py. Returns the path even if
        the file doesn't exist yet (callers create it on first write).

        Checks both encoded variants and prefers whichever was modified most
        recently. Falls back to the canonical variant when neither exists.
        """
        encoded = self._encode_project_path(project_path)
        if not encoded:
            return None
        mem_path = _CLAUDE_HOME / encoded / 'memory' / 'MEMORY.md'
        encoded_alt = encoded.replace('_', '-')
        if encoded_alt != encoded:
            alt_path = _CLAUDE_HOME / encoded_alt / 'memory' / 'MEMORY.md'
            try:
                if alt_path.exists() and mem_path.exists():
                    if alt_path.stat().st_mtime > mem_path.stat().st_mtime:
                        return alt_path
                elif alt_path.exists():
                    return alt_path
            except OSError:
                pass
        return mem_path

    # ── Oneshot — lifted from _scribe_call() in server.py ────────────────────

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        """Non-interactive claude -p call for Scribe / condense / summary.

        Mirrors _scribe_call() in server.py. Prompt and optional stdin text are
        joined and piped via stdin to avoid the Windows 32 KB argv limit.
        Returns None on any error (timeout, non-zero exit, etc.).
        """
        instruction = (system_prompt + '\n\n' + prompt).strip() if system_prompt else prompt
        body = stdin_text or ''
        stdin_payload = f"{instruction}\n\n---TRANSCRIPT---\n{body}" if body else instruction

        cmd = [
            self.resolve_binary_str(), '-p',
            '--model', model or 'claude-haiku-4-5-20251001',
            '--max-turns', str(max(1, int(max_turns))),
            '--dangerously-skip-permissions',
        ]
        try:
            r = subprocess.run(
                cmd,
                input=stdin_payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=180,
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        if r.returncode != 0:
            return None
        return OneshotResult(text=(r.stdout or '').strip())

    # ── Health check ──────────────────────────────────────────────────────────

    def health_check(self) -> HealthStatus:
        fn = _CLAUDE_HOOKS.get('health_check')
        if fn:
            try:
                return fn()
            except Exception as e:
                return HealthStatus(
                    installed=False, binary_path=None, version=None,
                    auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                    install_hint='npm install -g @anthropic-ai/claude-code',
                    diagnostic=str(e),
                )
        # Fallback when hooks not registered (e.g. unit tests)
        p = self.resolve_binary()
        installed = bool(p) and (str(p) != 'claude' or bool(shutil.which('claude')))
        return HealthStatus(
            installed=bool(installed),
            binary_path=p if installed else None,
            version=None,
            auth_state=AuthState(status='unknown', last_checked=_now_iso()),
            install_hint='npm install -g @anthropic-ai/claude-code',
        )

    # ── Capabilities ──────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='claude',
            display_name='Claude Code',
            supports_mode_a=True,
            supports_mode_b=True,
            mode_b_kind='native',
            default_mode='B',
            supports_session_resume=True,
            supports_mcp=True,
            supports_skills=True,
            supports_plan_mode=True,
            supports_ask_user_question=True,
            supports_streaming_text=True,
            emits_usage=True,
            emits_rate_limit=True,
            emits_cost=True,
            emits_num_turns=True,
            image_input=True,
            context_window=200_000,
            context_injection='flag',
            context_file_name='CLAUDE.md',
            oneshot_supported=True,
        )

    # ── Dispatch delegates (keep byte-identical claude path) ──────────────────

    def dispatch(self, **kwargs) -> SessionHandle:
        fn = _CLAUDE_HOOKS.get('dispatch')
        if not fn:
            raise RuntimeError("ClaudeRuntime.dispatch: server.py hooks not registered. "
                               "Call register_claude_hooks() at server startup.")
        return fn(**kwargs)

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        fn = _CLAUDE_HOOKS.get('followup')
        if not fn:
            raise RuntimeError("ClaudeRuntime.write_followup: server.py hooks not registered.")
        return fn(handle, message, attachments=attachments)

    def interrupt(self, handle: SessionHandle) -> None:
        fn = _CLAUDE_HOOKS.get('interrupt')
        if not fn:
            raise RuntimeError("ClaudeRuntime.interrupt: server.py hooks not registered.")
        return fn(handle)

    def stop(self, handle: SessionHandle) -> None:
        fn = _CLAUDE_HOOKS.get('stop')
        if not fn:
            raise RuntimeError("ClaudeRuntime.stop: server.py hooks not registered.")
        return fn(handle)

    def oneshot_via_hook(self, **kwargs) -> Optional[OneshotResult]:
        """Call the server.py-registered oneshot hook (if any), else use self.oneshot()."""
        fn = _CLAUDE_HOOKS.get('oneshot')
        if fn:
            try:
                return fn(**kwargs)
            except Exception:
                return None
        return self.oneshot(**kwargs)

    def auth_status(self) -> dict:
        """Return the cached claude auth state dict (no subprocess).

        Delegates to the server.py-registered hook which returns _claude_auth_state.
        Response shape: {ok, reason, last_error_text, detected_at, last_probe_at} —
        byte-identical to the legacy /api/claude/auth-status response.
        """
        fn = _CLAUDE_HOOKS.get('auth_status')
        if fn:
            return fn()
        # Fallback when hooks not registered (e.g. unit tests).
        return {
            'ok': True,
            'reason': None,
            'last_error_text': None,
            'detected_at': None,
            'last_probe_at': None,
        }

    def auth_probe(self) -> dict:
        """Run claude -p ok to actively probe auth state. Updates the cache."""
        fn = _CLAUDE_HOOKS.get('auth_probe')
        if fn:
            return fn()
        return self.auth_status()


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _pid_is_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> None:
    if sys.platform == 'win32':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                           capture_output=True, timeout=10,
                           creationflags=_POPEN_FLAGS)
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# GeminiRuntime — self-contained Gemini CLI driver
# ─────────────────────────────────────────────────────────────────────────────


def _sync_mcp_to_gemini_safe(project_path: str) -> Optional[Dict[str, Any]]:
    """Mirror MC's MCP server list into ~/.gemini/settings.json before a
    Gemini dispatch. Best-effort: any failure returns None and is logged
    via the caller's session log. Never blocks dispatch.
    """
    try:
        import mcp as _mcp_mod  # local import — avoids tight coupling
        return _mcp_mod.sync_to_gemini(project_path or None)
    except Exception as e:
        return {'error': str(e)}


def _log_mcp_sync_result(log_lines: List[str], result: Optional[Dict[str, Any]]) -> None:
    """Surface the MCP-sync outcome in the session log so the user can see
    what got plumbed through to Gemini."""
    if not result:
        return
    if result.get('error'):
        log_lines.append(f"[mcp-sync] failed (best-effort): {result['error']}")
        return
    added = result.get('added') or []
    skipped = result.get('skipped') or []
    removed = result.get('removed') or []
    if added:
        log_lines.append(f"[mcp-sync] available to Gemini: {', '.join(added)}")
    if skipped:
        log_lines.append(
            f"[mcp-sync] kept user-owned entries (not overwritten): "
            f"{', '.join(skipped)}")
    if removed:
        log_lines.append(
            f"[mcp-sync] cleared stale MC-managed entries: {', '.join(removed)}")


class GeminiRuntime(AgentRuntime):
    """Driver for Google's `gemini` CLI.

    Mode A only (synthetic per-turn). Each `dispatch()` / `write_followup()`
    spawns a fresh `gemini` process. Output is streamed line-by-line into
    `session['log_lines']`. Gemini's `--output-format stream-json` is used
    when available; falls back to plain-text otherwise.

    Followup uses `--checkpoint` for cross-turn continuity if the CLI
    version supports it; otherwise the runtime prepends a one-line summary
    of the prior turn to the new prompt as context.

    Tools/MCP/Skills are NOT injected — Gemini's tool model differs from
    claude's. The prototype validates the end-to-end loop on text-only
    turns; advanced features ride on a follow-up PR.
    """

    name = 'gemini'
    display_name = 'Gemini CLI'

    _bin_cache: Optional[str] = None

    def __init__(self) -> None:
        self._auth_cache: dict = {
            'ok': True,
            'status': 'unknown',
            'method': None,
            'error_text': None,
            'last_checked': None,
        }
        self._auth_lock = threading.Lock()

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            return Path(self._bin_cache) if self._bin_cache else None
        found = shutil.which('gemini')
        if not found and sys.platform == 'win32':
            for c in [
                Path(os.environ.get('APPDATA', '')) / 'npm' / 'gemini.cmd',
                Path(os.environ.get('USERPROFILE', '')) / 'AppData' / 'Roaming' / 'npm' / 'gemini.cmd',
            ]:
                try:
                    if c.exists():
                        found = str(c)
                        break
                except Exception:
                    pass
        elif not found:
            home = Path(os.environ.get('HOME', str(Path.home())))
            for c in [
                home / '.local' / 'bin' / 'gemini',
                home / '.npm-global' / 'bin' / 'gemini',
                Path('/usr/local/bin/gemini'),
                Path('/opt/homebrew/bin/gemini'),
            ]:
                try:
                    if c.exists():
                        found = str(c)
                        break
                except Exception:
                    pass
        self._bin_cache = found or ''
        return Path(found) if found else None

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False) -> List[str]:
        bin_path = self.resolve_binary()
        cmd = [str(bin_path) if bin_path else 'gemini',
               '--output-format', 'stream-json', '--yolo']
        if model:
            cmd.extend(['--model', model])
        return cmd

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse a single output line from gemini's --output-format stream-json."""
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None

        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not a dict')
        except (json.JSONDecodeError, ValueError):
            return AgentEvent(
                type=EventType.ASSISTANT_TEXT, provider='gemini',
                session_id=None, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'text': line},
            )

        mtype = msg.get('type') or msg.get('event') or ''
        role = msg.get('role') or ''
        text = msg.get('text') or msg.get('content') or ''
        session_id = msg.get('session_id')

        # init envelope — emitted once at stream start; carries no agent
        # output. Surfaced as INIT so the reader can consume it silently.
        if mtype == 'init':
            return AgentEvent(
                type=EventType.INIT, provider='gemini',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={}, raw=msg,
            )

        # 'message' events carry a `role`. Gemini echoes the input prompt
        # back as a role:"user" message — that is NOT agent output and must
        # not reach the chat. Only role:"assistant" messages are real text.
        if mtype == 'message':
            if role == 'user':
                return AgentEvent(
                    type=EventType.USER_MESSAGE, provider='gemini',
                    session_id=session_id, mc_session_id=mc_session_id,
                    timestamp=_now_iso(), payload={'text': str(text)}, raw=msg,
                )
            if text:
                return AgentEvent(
                    type=EventType.ASSISTANT_TEXT, provider='gemini',
                    session_id=session_id, mc_session_id=mc_session_id,
                    timestamp=_now_iso(), payload={'text': str(text)}, raw=msg,
                )
            return None

        if mtype in ('content', 'assistant', 'text') and text:
            return AgentEvent(
                type=EventType.ASSISTANT_TEXT, provider='gemini',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'text': str(text)}, raw=msg,
            )
        elif mtype == 'tool_use':
            return AgentEvent(
                type=EventType.TOOL_USE, provider='gemini',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'blocks': [{'type': 'tool_use', 'name': msg.get('name', ''),
                                     'input': msg.get('input', {}), 'tool_use_id': None}]},
                raw=msg,
            )
        elif mtype == 'tool_result':
            # tool_id looks like "<tool_name>-<digits>-<digits>"; strip the
            # two trailing numeric id segments to recover the tool name.
            tool_id = msg.get('tool_id') or ''
            tname = (tool_id.rsplit('-', 2)[0] if tool_id.count('-') >= 2
                     else (tool_id or msg.get('name') or ''))
            return AgentEvent(
                type=EventType.TOOL_RESULT, provider='gemini',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'name': tname, 'status': msg.get('status') or ''},
                raw=msg,
            )
        elif mtype in ('result', 'turn_end', 'done'):
            return AgentEvent(
                type=EventType.TURN_END, provider='gemini',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'usage': msg.get('usage') or msg.get('stats'),
                         'cost_usd': None, 'num_turns': None},
                raw=msg,
            )
        # Unknown — return None (caller drops it)
        return None

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Gemini has no native transcript store — return None."""
        return None

    def _gemini_auth_state(self) -> tuple:
        """Best-effort Gemini auth detection — no API call, no subprocess.

        Order of evidence:
          1. GEMINI_API_KEY env var → API-key auth.
          2. Cached OAuth credentials at ~/.gemini/oauth_creds.json. A
             refresh_token (or access_token) present = signed in — even when
             the short-lived access_token has expired, the CLI refreshes it.
             The active Google account is read from google_accounts.json for
             a friendlier label.

        Returns (status, method, error_text) where status is one of
        'ok' | 'not_logged_in'.
        """
        if os.environ.get('GEMINI_API_KEY'):
            return ('ok', 'env:GEMINI_API_KEY', None)
        try:
            home = (os.environ.get('USERPROFILE') or os.environ.get('HOME')
                    or str(Path.home()))
            gdir = Path(home) / '.gemini'
            creds = gdir / 'oauth_creds.json'
            if creds.is_file():
                data = json.loads(creds.read_text(encoding='utf-8'))
                if data.get('refresh_token') or data.get('access_token'):
                    email = ''
                    accts = gdir / 'google_accounts.json'
                    if accts.is_file():
                        try:
                            email = (json.loads(accts.read_text(encoding='utf-8'))
                                     .get('active') or '')
                        except Exception:
                            email = ''
                    return ('ok', f'oauth ({email})' if email else 'oauth', None)
        except Exception:
            pass
        return ('not_logged_in', None,
                'Not signed in. Set GEMINI_API_KEY in Provider Settings, or '
                'click "Launch terminal login" to sign in with Google.')

    def health_check(self) -> HealthStatus:
        bin_path = self.resolve_binary()
        if not bin_path:
            return HealthStatus(
                installed=False,
                binary_path=None,
                version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint='npm install -g @google/gemini-cli',
            )
        version = None
        try:
            r = subprocess.run([str(bin_path), '--version'],
                               capture_output=True, text=True, timeout=10,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            version = (r.stdout or r.stderr or '').strip().splitlines()[0] if (r.stdout or r.stderr) else None
        except Exception as e:
            return HealthStatus(installed=True, binary_path=bin_path, version=None,
                                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                                install_hint='', diagnostic=str(e))
        auth_status, auth_method, _err = self._gemini_auth_state()
        return HealthStatus(
            installed=True,
            binary_path=bin_path,
            version=version,
            auth_state=AuthState(status=auth_status, method=auth_method,
                                 last_checked=_now_iso()),
            install_hint='',
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='gemini',
            display_name='Gemini CLI',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            # --resume <id|latest> confirmed in gemini v0.20.0 --help output
            supports_session_resume=True,
            # mcpServers config section confirmed in gemini docs + capability matrix
            supports_mcp=True,
            supports_skills=False,
            supports_plan_mode=False,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=False,
            emits_rate_limit=False,
            emits_cost=False,
            emits_num_turns=False,
            image_input=False,
            context_window=None,
            context_injection='prepend',
            context_file_name='GEMINI.md',
            oneshot_supported=True,
        )

    # ── Auth ──────────────────────────────────────────────────────────────────

    def auth_status(self) -> dict:
        """Return cached Gemini auth state (no subprocess)."""
        with self._auth_lock:
            return dict(self._auth_cache)

    def auth_probe(self) -> dict:
        """Check Gemini auth without an API call.

        Gemini CLI authenticates via GEMINI_API_KEY or a cached Google OAuth
        login (~/.gemini/oauth_creds.json). Both are detected — see
        `_gemini_auth_state`.
        """
        bin_path = self.resolve_binary()
        if not bin_path:
            state: dict = {
                'ok': False, 'status': 'not_installed', 'method': None,
                'error_text': 'gemini CLI not installed — run: npm install -g @google/gemini-cli',
                'last_checked': _now_iso(),
            }
        else:
            status, method, error_text = self._gemini_auth_state()
            state = {
                'ok': status == 'ok', 'status': status, 'method': method,
                'error_text': error_text, 'last_checked': _now_iso(),
            }
        with self._auth_lock:
            self._auth_cache.update(state)
        return state

    # ── Dispatch ──────────────────────────────────────────────────────────────

    # Claude-Code-only instructions that confuse a weaker model — each names
    # a tool (EnterPlanMode / ExitPlanMode / AskUserQuestion) Gemini lacks.
    # Matched as line prefixes; each capability is a single line in the
    # assembled --- SYSTEM --- block.
    _CLAUDE_ONLY_PREFIXES = (
        'IMPORTANT — Plan Mode:',
        'Questions: When you need to ask the user',
    )

    def _slim_system_prompt(self, system_prompt: str) -> str:
        """Trim the generic (Claude-Code-shaped) system prompt to what
        actually helps Gemini.

        The full prompt has two problems for Gemini:
          * Bloat — the ~100-endpoint CLAYRUNE API REFERENCE block is large
            and rarely needed; Gemini can discover endpoints by curling the
            server. Anthropic's prompt cache made that block free for
            Claude; for Gemini it is dead weight on every call.
          * Claude-only noise — instructions naming EnterPlanMode /
            ExitPlanMode / AskUserQuestion refer to tools Gemini does not
            have, and a stale project-level "Current task:" line has been
            observed to derail it onto unrelated work.

        Each transformation is independent and a no-op when its anchor is
        absent, so a future change to the prompt format degrades gracefully
        (less slimming) rather than corrupting the prompt.
        """
        if not system_prompt:
            return system_prompt
        text = system_prompt

        # 1. Collapse the API REFERENCE block to a one-line pointer. It runs
        #    from its header to the next recognizable section start.
        m = re.search(r'\n*--- CLAYRUNE API REFERENCE ---\n', text)
        if m:
            after = text[m.end():]
            nxt = re.search(
                r'\n(--- [^\n]+ ---\n|Recent activity:|Recent conversations|Current task:)',
                after)
            tail = after[nxt.start():] if nxt else ''
            pm = re.search(r'localhost:(\d+)', text)
            host = (f"http://localhost:{pm.group(1)}" if pm
                    else "the Clayrune server")
            text = (text[:m.start()].rstrip()
                    + "\n\n--- CLAYRUNE API ---\n"
                    + f"Clayrune's HTTP API is on {host}. To discover "
                    + "endpoints, curl GET / or grep server.py for @app.route."
                    + (('\n' + tail) if tail else ''))

        # 2. Drop Claude-only tool instructions (each is a single line).
        text = '\n'.join(
            ln for ln in text.split('\n')
            if not any(ln.lstrip().startswith(p)
                       for p in self._CLAUDE_ONLY_PREFIXES))

        # 3. Drop the stale project-level "Current task:" line — it is NOT
        #    the dispatched task (that is appended after a --- separator).
        text = re.sub(r'(?m)^Current task:.*$', '', text)

        return text.strip()

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        bin_path = self.resolve_binary()
        if not bin_path:
            raise RuntimeError("gemini CLI not installed — run: npm install -g @google/gemini-cli")

        mcp_sync = _sync_mcp_to_gemini_safe(project_path)

        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        # Slim the Claude-Code-shaped prompt down for Gemini (see
        # _slim_system_prompt). The slimmed text is also what gets stashed as
        # `_system_prompt`, so per-turn respawns reuse the trimmed version.
        slim_prompt = self._slim_system_prompt(system_prompt)
        full_prompt = task
        if slim_prompt:
            full_prompt = f"{slim_prompt}\n\n---\n\n{task}"

        cmd = self.build_command(model=model)
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=project_path,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
        self._write_prompt_async(proc, full_prompt, mc_sid)

        if session_dict is None:
            session_dict = {}
        session_dict.update({
            'proc': proc,
            'status': 'running',
            'task': task,
            'log_lines': session_dict.get('log_lines') or [],
            'started_at': session_dict.get('started_at') or _now_iso(),
            'session_id': mc_sid,
            'project_id': project_id,
            'mode': 'A',
            'process_alive': True,
            'last_output_time': _time.time(),
            'last_status_change_time': _time.time(),
            'provider': 'gemini',
            'incognito': bool(incognito),
            '_dispatch_time': _time.time(),
            '_system_prompt': slim_prompt or '',
        })
        _log_mcp_sync_result(session_dict['log_lines'], mcp_sync)

        handle = SessionHandle(
            mc_session_id=mc_sid,
            provider='gemini',
            mode='A',
            project_path=project_path,
            project_id=project_id,
            session_dict=session_dict,
            started_at=session_dict['started_at'],
            capabilities=self.capabilities(),
            meta={'callbacks': callbacks or {}},
        )

        if register_process:
            try:
                register_process(proc, 'Agent (gemini Mode A)', 'agent',
                                 mc_sid, project_id, task[:80])
            except Exception:
                pass

        t = threading.Thread(target=self._read_stream, args=(proc, handle),
                             daemon=True, name=f'gemini-reader-{mc_sid[:8]}')
        t.start()
        return handle

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        s = (log_tail or '').lower()
        if 'command line is too long' in s:
            return ("Your prompt + project context was too large for Windows "
                    "to send to Gemini. This shouldn't happen anymore after "
                    "the stdin-pipe fix — if you see this, restart MC and "
                    "make sure you're on the latest build.")
        if any(p in s for p in (
                'not authenticated', 'unauthorized', 'auth error',
                'invalid api key', 'api_key_invalid', 'gemini_api_key',
                'please log in', 'please sign in', 'login required',
                'oauth')):
            return ("Gemini isn't logged in. Either set GEMINI_API_KEY in "
                    "Settings → Agent Providers, or click \"Launch terminal "
                    "login\" there to complete Google OAuth.")
        if any(p in s for p in (
                'enoent', "is not recognized as", 'no such file',
                'cannot find the path', 'command not found')):
            return ("Gemini CLI isn't installed (or isn't on PATH). Run: "
                    "npm install -g @google/gemini-cli")
        if any(p in s for p in ('quota', 'rate limit', 'too many requests',
                                ' 429', 'resource_exhausted')):
            return ("Gemini rate limit / quota exceeded. Wait a minute and "
                    "try again, or check your Google AI Studio quota.")
        if any(p in s for p in (
                'econnrefused', 'enetunreach', 'getaddrinfo',
                'fetch failed', 'connection refused', 'network error',
                'timed out', 'etimedout')):
            return ("Network problem reaching Google. Check your internet "
                    "connection, corporate proxy, or VPN.")
        if any(p in s for p in ('permission denied', 'eacces')):
            return ("Gemini was denied access to a file in the project "
                    "folder. Check the folder's permissions.")
        if rc != 0:
            return ("Gemini exited with code {0}. Common causes: not logged "
                    "in, network blocked, prompt too big. Scroll up for the "
                    "raw error text.".format(rc))
        return None

    def _write_prompt_async(self, proc: subprocess.Popen, prompt: str,
                            mc_sid: str) -> None:
        def _send() -> None:
            try:
                if proc.stdin:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True,
                         name=f'gemini-stdin-{mc_sid[:8]}').start()

    def _read_stream(self, proc: subprocess.Popen, handle: SessionHandle) -> None:
        session = handle.session_dict
        cbs = handle.meta.get('callbacks', {})

        def _cb(name: str, ev: AgentEvent) -> None:
            fn = cbs.get(name)
            if fn:
                try:
                    fn(ev, session)
                except Exception as e:
                    session['log_lines'].append(f"[callback {name} error: {e}]")

        try:
            for raw_line in proc.stdout:
                if session.get('proc') is not proc:
                    break
                line = raw_line.rstrip('\n\r')
                if not line:
                    continue

                # With --output-format stream-json every real event is a
                # JSON object. Non-JSON lines are CLI startup chatter (the
                # YOLO banner, "Loaded cached credentials", the [STARTUP]
                # profiler dump) merged in from stderr — drop them from the
                # chat, but keep a rolling tail so explain_exit_error() can
                # still classify a crash (real errors land on stderr too).
                if not line.lstrip().startswith('{'):
                    tail_buf = session.setdefault('_gemini_stderr_tail', [])
                    tail_buf.append(line)
                    if len(tail_buf) > 40:
                        del tail_buf[:-40]
                    continue

                ev = self.parse_event(line, handle.mc_session_id)
                if ev and ev.type == EventType.ASSISTANT_TEXT:
                    session['log_lines'].append(ev.payload.get('text', line))
                    session['last_output_time'] = _time.time()
                    _cb('on_assistant_text', ev)
                elif ev and ev.type == EventType.TOOL_USE:
                    blocks = ev.payload.get('blocks', [])
                    name = blocks[0].get('name', '') if blocks else ''
                    session['log_lines'].append(
                        f"[gemini tool: {name}]" if name else "[gemini tool call]")
                    session['last_output_time'] = _time.time()
                elif ev and ev.type == EventType.TOOL_RESULT:
                    nm = ev.payload.get('name') or 'tool'
                    st = ev.payload.get('status') or ''
                    session['log_lines'].append(
                        f"[gemini tool result: {nm}{(' — ' + st) if st else ''}]")
                    session['last_output_time'] = _time.time()
                elif ev and ev.type == EventType.TURN_END:
                    _cb('on_turn_end', ev)
                # INIT, USER_MESSAGE (the prompt echo) and unrecognized
                # envelopes (ev is None) are consumed silently — they carry
                # no agent output.
        except Exception as e:
            if session.get('proc') is proc:
                session['log_lines'].append(f"[stream error: {e}]")
        finally:
            try:
                rc = proc.wait()
            except Exception:
                rc = -1
            if session.get('proc') is proc:
                if session.get('status') == 'running':
                    session['status'] = 'completed' if rc == 0 else 'error'
                    session['last_status_change_time'] = _time.time()
                    if rc != 0:
                        session['log_lines'].append(f"[gemini exited with code {rc}]")
                        try:
                            # Include the dropped non-JSON stderr tail —
                            # gemini's real error text lands there, not in
                            # the JSON event stream.
                            tail = '\n'.join(
                                (session.get('_gemini_stderr_tail') or [])[-30:]
                                + session['log_lines'][-30:])
                            hint = self.explain_exit_error(rc, tail)
                        except Exception:
                            hint = None
                        if hint:
                            session['log_lines'].append(f"[hint] {hint}")
                session['process_alive'] = False
                exit_ev = AgentEvent(
                    type=EventType.PROCESS_EXIT,
                    provider='gemini',
                    session_id=None,
                    mc_session_id=handle.mc_session_id,
                    timestamp=_now_iso(),
                    payload={'rc': rc},
                )
                _cb('on_process_exit', exit_ev)

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)

        full_prompt = _compose_respawn_prompt(session, message)

        bin_path = self.resolve_binary()
        if not bin_path:
            session['log_lines'].append("[gemini binary missing — cannot continue]")
            session['status'] = 'error'
            session['last_status_change_time'] = _time.time()
            return

        mcp_sync = _sync_mcp_to_gemini_safe(handle.project_path)
        _log_mcp_sync_result(session['log_lines'], mcp_sync)

        cmd = self.build_command()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=handle.project_path,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
        self._write_prompt_async(proc, full_prompt, handle.mc_session_id)
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()

        t = threading.Thread(target=self._read_stream, args=(proc, handle),
                             daemon=True, name=f'gemini-reader-{handle.mc_session_id[:8]}')
        t.start()

    def interrupt(self, handle: SessionHandle) -> None:
        session = handle.session_dict
        proc = session.get('proc')
        if proc and proc.poll() is None:
            _kill_pid(proc.pid)
        session['status'] = 'stopped'
        session['last_status_change_time'] = _time.time()
        session['process_alive'] = False
        session['log_lines'].append('[interrupted]')

    def stop(self, handle: SessionHandle) -> None:
        self.interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        bin_path = self.resolve_binary()
        if not bin_path:
            return None
        full = (system_prompt + '\n\n' + prompt) if system_prompt else prompt
        if stdin_text:
            full = f"{full}\n\n---\n\n{stdin_text}"
        cmd = [str(bin_path)]
        if model:
            cmd.extend(['--model', model])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               input=full,
                               cwd=cwd, timeout=180,
                               encoding='utf-8', errors='replace',
                               creationflags=_POPEN_FLAGS,
                               startupinfo=_STARTUPINFO)
        except Exception:
            return None
        text = (r.stdout or '').strip()
        return OneshotResult(text=text, raw=None)


# ─────────────────────────────────────────────────────────────────────────────
# Shared Mode-A dispatch helper (reused by Codex, OpenCode, Goose, Aider, Kiro)
# ─────────────────────────────────────────────────────────────────────────────


def _compose_respawn_prompt(session: Dict[str, Any], message: str, *,
                            tail_lines: int = 30, tail_chars: int = 4000) -> str:
    """Build a followup prompt for Mode-A providers that respawn per turn.

    Every provider except claude gets a fresh process for each turn — there is
    no persistent session. Without re-injecting the dispatch-time system
    context (MEMORY / AGENT_RULES / CLAYRUNE_API), the agent loses all project
    context after turn 1 — the "amnesia" bug. This re-prepends that context
    (stored on the session dict as `_system_prompt` at dispatch), then appends
    a tail of the prior turn's output for conversational continuity, then the
    new user message.
    """
    sys_prompt = (session.get('_system_prompt') or '').strip()
    log_lines = session.get('log_lines') or []
    tail = '\n'.join(log_lines[-tail_lines:])[-tail_chars:].strip()
    parts: List[str] = []
    if sys_prompt:
        parts.append(sys_prompt)
    if tail:
        parts.append(
            "[Prior turn excerpt for context only — do not re-execute]\n" + tail)
    parts.append(message)
    return '\n\n---\n\n'.join(parts)


def _mode_a_dispatch(runtime: 'AgentRuntime',
                     cmd: List[str],
                     full_prompt: str,
                     project_path: str,
                     project_id: str,
                     task: str,
                     mc_session_id: str,
                     session_dict: Optional[Dict[str, Any]],
                     incognito: bool,
                     env_extra: Optional[Dict[str, str]],
                     callbacks: Optional[Dict[str, Callable]],
                     register_process: Optional[Callable],
                     prompt_via_stdin: bool = True,
                     system_prompt: str = '') -> SessionHandle:
    """Spawn a Mode-A provider process and return a SessionHandle.

    `cmd` is the full command list. When `prompt_via_stdin` is True the prompt
    is written to the process stdin; otherwise it must already be embedded in
    `cmd` by the caller.

    `system_prompt` is stashed on the session dict as `_system_prompt` so
    `write_followup` can re-inject it on every per-turn respawn — see
    `_compose_respawn_prompt`.
    """
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if prompt_via_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=project_path,
        text=True,
        encoding='utf-8',
        errors='replace',
        env=env,
        creationflags=_POPEN_FLAGS,
        startupinfo=_STARTUPINFO,
    )

    if prompt_via_stdin:
        def _send() -> None:
            try:
                if proc.stdin:
                    proc.stdin.write(full_prompt)
                    proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True,
                         name=f'{runtime.name}-stdin-{mc_session_id[:8]}').start()

    if session_dict is None:
        session_dict = {}
    session_dict.update({
        'proc': proc,
        'status': 'running',
        'task': task,
        'log_lines': session_dict.get('log_lines') or [],
        'started_at': session_dict.get('started_at') or _now_iso(),
        'session_id': mc_session_id,
        'project_id': project_id,
        'mode': 'A',
        'process_alive': True,
        'last_output_time': _time.time(),
        'last_status_change_time': _time.time(),
        'provider': runtime.name,
        'incognito': bool(incognito),
        '_dispatch_time': _time.time(),
        '_system_prompt': system_prompt or '',
    })

    handle = SessionHandle(
        mc_session_id=mc_session_id,
        provider=runtime.name,
        mode='A',
        project_path=project_path,
        project_id=project_id,
        session_dict=session_dict,
        started_at=session_dict['started_at'],
        capabilities=runtime.capabilities(),
        meta={'callbacks': callbacks or {}},
    )

    if register_process:
        try:
            register_process(proc, f'Agent ({runtime.name} Mode A)', 'agent',
                             mc_session_id, project_id, task[:80])
        except Exception:
            pass

    t = threading.Thread(target=_mode_a_reader,
                         args=(proc, handle, runtime),
                         daemon=True,
                         name=f'{runtime.name}-reader-{mc_session_id[:8]}')
    t.start()
    return handle


def _mode_a_reader(proc: subprocess.Popen, handle: SessionHandle,
                   runtime: 'AgentRuntime') -> None:
    """Generic stdout reader for Mode-A providers. Uses runtime.parse_event()."""
    session = handle.session_dict
    cbs = handle.meta.get('callbacks', {})

    def _cb(name: str, ev: AgentEvent) -> None:
        fn = cbs.get(name)
        if fn:
            try:
                fn(ev, session)
            except Exception as e:
                session['log_lines'].append(f"[callback {name} error: {e}]")

    try:
        for raw_line in proc.stdout:
            if session.get('proc') is not proc:
                break
            line = raw_line.rstrip('\n\r')
            if not line:
                continue
            ev = runtime.parse_event(line, handle.mc_session_id)
            if ev is None:
                session['log_lines'].append(line)
                session['last_output_time'] = _time.time()
            elif ev.type == EventType.ASSISTANT_TEXT:
                session['log_lines'].append(ev.payload.get('text', line))
                session['last_output_time'] = _time.time()
                _cb('on_assistant_text', ev)
            elif ev.type == EventType.TOOL_USE:
                blocks = ev.payload.get('blocks', [])
                tname = blocks[0].get('name', '') if blocks else ''
                session['log_lines'].append(f"[{runtime.name} tool: {tname}]")
                session['last_output_time'] = _time.time()
            elif ev.type == EventType.INIT:
                session.setdefault('provider_session_id',
                                   ev.payload.get('session_id') or
                                   ev.payload.get('thread_id'))
                _cb('on_init', ev)
            elif ev.type == EventType.TURN_END:
                _cb('on_turn_end', ev)
            elif ev.type in (EventType.ERROR, EventType.AUTH_ERROR):
                session['log_lines'].append(
                    f"[{runtime.name} error] {ev.payload.get('text', line)}")
                session['last_output_time'] = _time.time()
            else:
                raw_text = (ev.payload.get('text') or
                            (json.dumps(ev.payload) if ev.payload else line))
                if raw_text:
                    session['log_lines'].append(raw_text)
                    session['last_output_time'] = _time.time()
    except Exception as e:
        if session.get('proc') is proc:
            session['log_lines'].append(f"[stream error: {e}]")
    finally:
        try:
            rc = proc.wait()
        except Exception:
            rc = -1
        if session.get('proc') is proc:
            if session.get('status') == 'running':
                session['status'] = 'completed' if rc == 0 else 'error'
                session['last_status_change_time'] = _time.time()
                if rc != 0:
                    session['log_lines'].append(
                        f"[{runtime.name} exited with code {rc}]")
                    try:
                        tail = '\n'.join(session['log_lines'][-30:])
                        hint = runtime.explain_exit_error(rc, tail)
                    except Exception:
                        hint = None
                    if hint:
                        session['log_lines'].append(f"[hint] {hint}")
            session['process_alive'] = False
            exit_ev = AgentEvent(
                type=EventType.PROCESS_EXIT,
                provider=runtime.name,
                session_id=None,
                mc_session_id=handle.mc_session_id,
                timestamp=_now_iso(),
                payload={'rc': rc},
            )
            _cb('on_process_exit', exit_ev)


def _mode_a_interrupt(handle: SessionHandle) -> None:
    session = handle.session_dict
    proc = session.get('proc')
    if proc and proc.poll() is None:
        _kill_pid(proc.pid)
    session['status'] = 'stopped'
    session['last_status_change_time'] = _time.time()
    session['process_alive'] = False
    session['log_lines'].append('[interrupted]')


# ─────────────────────────────────────────────────────────────────────────────
# CodexRuntime — OpenAI Codex CLI (codex exec --json)
# ─────────────────────────────────────────────────────────────────────────────


class CodexRuntime(AgentRuntime):
    """Driver for OpenAI's `codex` CLI.

    Invokes `codex exec [PROMPT] --json --dangerously-bypass-approvals-and-sandbox`
    for Mode A (non-interactive) operation. Output is JSONL with typed events:
    thread.started, turn.started, item.completed, turn.completed, error, turn.failed.

    When `codex` is not on PATH (common — npm installs it to a non-standard location),
    falls back to `npx @openai/codex` so operators can still use it without a full
    PATH fix. health_check().installed reflects whichever path resolves.

    Session resume: `codex exec resume --last` or `codex exec resume <SESSION_ID>`.
    Session files: ~/.codex/sessions/<thread_id>/ (created on first run with auth).
    """

    name = 'codex'
    display_name = 'Codex CLI'

    _bin_cache: Optional[str] = None
    _npx_fallback: bool = False

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            if self._bin_cache == '__npx__':
                return None
            return Path(self._bin_cache) if self._bin_cache else None

        found = shutil.which('codex')
        if found:
            self._bin_cache = found
            self._npx_fallback = False
            return Path(found)

        if sys.platform == 'win32':
            candidates = [
                Path(os.environ.get('APPDATA', '')) / 'npm' / 'codex.cmd',
                Path(os.environ.get('USERPROFILE', '')) / '.npm-global' / 'bin' / 'codex.cmd',
                Path(os.environ.get('USERPROFILE', '')) / 'AppData' / 'Roaming' / 'npm' / 'codex.cmd',
            ]
        else:
            home = Path.home()
            candidates = [
                home / '.npm-global' / 'bin' / 'codex',
                home / '.local' / 'bin' / 'codex',
                Path('/usr/local/bin/codex'),
                Path('/opt/homebrew/bin/codex'),
            ]
        for c in candidates:
            try:
                if c.exists():
                    self._bin_cache = str(c)
                    self._npx_fallback = False
                    return c
            except Exception:
                pass

        # Fall back to npx if npm is available
        if shutil.which('npx'):
            self._bin_cache = '__npx__'
            self._npx_fallback = True
            return None

        self._bin_cache = ''
        self._npx_fallback = False
        return None

    def _cmd_prefix(self) -> List[str]:
        """Return [codex] if binary found, [npx, --yes, @openai/codex] otherwise."""
        p = self.resolve_binary()
        if p:
            return [str(p)]
        if self._npx_fallback:
            return ['npx', '--yes', '@openai/codex']
        return ['codex']  # will FileNotFoundError on spawn

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False,
                      resume_id: str = '') -> List[str]:
        """Return the codex exec command for non-interactive use.

        Flags verified against codex 0.133.0 `codex exec --help`:
          exec [PROMPT]                    -- non-interactive; reads prompt from stdin
          --json                           -- JSONL event stream to stdout
          --dangerously-bypass-approvals-and-sandbox -- skip all prompts (CI use)
          -m / --model MODEL               -- override model
          -C / --cd DIR                    -- working dir (set by Popen cwd, not here)
          exec resume --last               -- resume most recent session
          exec resume SESSION_ID           -- resume specific session by thread_id
        """
        prefix = self._cmd_prefix()
        if resume_id:
            cmd = prefix + ['exec', 'resume']
            if resume_id.lower() == 'last':
                cmd.append('--last')
            else:
                cmd.append(resume_id)
            cmd.append('--json')
        else:
            cmd = prefix + ['exec', '--json',
                            '--dangerously-bypass-approvals-and-sandbox']
        if model:
            cmd.extend(['-m', model])
        return cmd

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse a JSONL event from `codex exec --json`.

        Observed event types (codex 0.133.0, live-tested on this machine):
          thread.started  → INIT (payload: thread_id)
          turn.started    → None (internal; suppressed)
          item.completed  → ASSISTANT_TEXT or TOOL_USE (based on item.type)
          turn.completed  → TURN_END
          thread.completed→ TURN_END
          error           → ERROR
          turn.failed     → ERROR
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None

        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not a dict')
        except (json.JSONDecodeError, ValueError):
            return AgentEvent(
                type=EventType.ASSISTANT_TEXT, provider='codex',
                session_id=None, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'text': line},
            )

        etype = msg.get('type', '')
        session_id = msg.get('thread_id') or msg.get('session_id')

        if etype == 'thread.started':
            return AgentEvent(
                type=EventType.INIT, provider='codex',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'thread_id': session_id, 'session_id': session_id},
                raw=msg,
            )
        elif etype == 'turn.started':
            return None  # internal; suppress
        elif etype == 'item.completed':
            item = msg.get('item') or {}
            item_type = item.get('type', '')
            if item_type == 'message':
                content = item.get('content') or []
                text_parts = []
                tool_blocks = []
                for block in (content if isinstance(content, list) else []):
                    if isinstance(block, dict):
                        btype = block.get('type', '')
                        if btype in ('output_text', 'text'):
                            text_parts.append(block.get('text', ''))
                        elif btype == 'tool_use':
                            tool_blocks.append({
                                'type': 'tool_use',
                                'name': block.get('name', ''),
                                'input': block.get('input', {}),
                                'tool_use_id': block.get('id'),
                            })
                if tool_blocks:
                    return AgentEvent(
                        type=EventType.TOOL_USE, provider='codex',
                        session_id=session_id, mc_session_id=mc_session_id,
                        timestamp=_now_iso(),
                        payload={'blocks': tool_blocks},
                        raw=msg,
                    )
                text = '\n'.join(text_parts).strip()
                if text:
                    return AgentEvent(
                        type=EventType.ASSISTANT_TEXT, provider='codex',
                        session_id=session_id, mc_session_id=mc_session_id,
                        timestamp=_now_iso(),
                        payload={'text': text},
                        raw=msg,
                    )
            elif item_type in ('function_call', 'computer_call', 'tool_call'):
                return AgentEvent(
                    type=EventType.TOOL_USE, provider='codex',
                    session_id=session_id, mc_session_id=mc_session_id,
                    timestamp=_now_iso(),
                    payload={'blocks': [{'type': 'tool_use',
                                        'name': item.get('name', item_type),
                                        'input': (item.get('arguments') or
                                                  item.get('input', {})),
                                        'tool_use_id': item.get('id')}]},
                    raw=msg,
                )
            return None
        elif etype in ('turn.completed', 'thread.completed'):
            return AgentEvent(
                type=EventType.TURN_END, provider='codex',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'usage': msg.get('usage'), 'cost_usd': msg.get('cost_usd'),
                         'num_turns': None, 'rc': 0},
                raw=msg,
            )
        elif etype in ('error', 'turn.failed'):
            err_msg = (msg.get('message') or
                       (msg.get('error') or {}).get('message', '') or
                       str(msg))
            return AgentEvent(
                type=EventType.ERROR, provider='codex',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'text': err_msg},
                raw=msg,
            )
        return None

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Codex stores sessions in ~/.codex/sessions/<thread_id>/transcript.jsonl."""
        if not session_id:
            return None
        p = Path.home() / '.codex' / 'sessions' / session_id
        try:
            if p.exists():
                transcript = p / 'transcript.jsonl'
                if transcript.exists():
                    return transcript
                return p
        except OSError:
            pass
        return None

    def health_check(self) -> HealthStatus:
        p = self.resolve_binary()
        is_npx = self._npx_fallback
        installed = bool(p) or is_npx
        if not installed:
            return HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint='npm install -g @openai/codex',
            )
        version = None
        try:
            cmd = self._cmd_prefix() + ['--version']
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            raw = (r.stdout or r.stderr or '').strip()
            version = raw.splitlines()[0] if raw else None
        except Exception as e:
            return HealthStatus(
                installed=True, binary_path=p, version=None,
                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                diagnostic=str(e),
                install_hint='npm install -g @openai/codex',
            )
        has_key = bool(os.environ.get('CODEX_API_KEY') or os.environ.get('OPENAI_API_KEY'))
        auth_method = None
        if os.environ.get('CODEX_API_KEY'):
            auth_method = 'env:CODEX_API_KEY'
        elif os.environ.get('OPENAI_API_KEY'):
            auth_method = 'env:OPENAI_API_KEY'
        return HealthStatus(
            installed=True,
            binary_path=p,
            version=version,
            auth_state=AuthState(
                status='ok' if has_key else 'unknown',
                method=auth_method,
                last_checked=_now_iso(),
            ),
            install_hint='npm install -g @openai/codex' if is_npx else '',
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='codex',
            display_name='Codex CLI',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=True,
            supports_mcp=True,
            supports_skills=False,
            supports_plan_mode=True,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=True,
            emits_rate_limit=False,
            emits_cost=True,
            emits_num_turns=False,
            image_input=True,
            context_window=None,
            context_injection='file',
            context_file_name='AGENTS.md',
            oneshot_supported=True,
        )

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        if not self.resolve_binary() and not self._npx_fallback:
            raise RuntimeError("codex CLI not installed — run: npm install -g @openai/codex")

        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        cmd = self.build_command(model=model, resume_id=resume_id or '')
        full_prompt = task
        if system_prompt and not resume_id:
            # AGENTS.md hierarchy is primary; prepend to task as a quick override.
            full_prompt = f"{system_prompt}\n\n---\n\n{task}"

        return _mode_a_dispatch(
            self, cmd, full_prompt, project_path, project_id, task,
            mc_sid, session_dict, incognito, env_extra, callbacks,
            register_process, prompt_via_stdin=True,
            system_prompt=system_prompt,
        )

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)
        full_prompt = _compose_respawn_prompt(session, message)
        mc_sid = handle.mc_session_id
        cmd = self.build_command()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=handle.project_path,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )

        def _send() -> None:
            try:
                if proc.stdin:
                    proc.stdin.write(full_prompt)
                    proc.stdin.close()
            except Exception:
                pass
        threading.Thread(target=_send, daemon=True,
                         name=f'codex-stdin-{mc_sid[:8]}').start()
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()
        threading.Thread(target=_mode_a_reader, args=(proc, handle, self),
                         daemon=True, name=f'codex-reader-{mc_sid[:8]}').start()

    def interrupt(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def stop(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        if not self.resolve_binary() and not self._npx_fallback:
            return None
        full = (system_prompt + '\n\n' + prompt).strip() if system_prompt else prompt
        if stdin_text:
            full = f"{full}\n\n---\n\n{stdin_text}"
        cmd = self._cmd_prefix() + ['exec', '--json',
                                    '--dangerously-bypass-approvals-and-sandbox']
        if model:
            cmd.extend(['-m', model])
        try:
            r = subprocess.run(
                cmd, input=full,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True, encoding='utf-8', errors='replace',
                timeout=180,
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        last_text = ''
        for raw_line in (r.stdout or '').splitlines():
            ev = self.parse_event(raw_line)
            if ev and ev.type == EventType.ASSISTANT_TEXT:
                last_text = ev.payload.get('text', last_text)
        return OneshotResult(text=last_text or (r.stdout or '').strip())

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        s = (log_tail or '').lower()
        if any(p in s for p in ('not authenticated', 'invalid api key',
                                'unauthorized', 'auth_error',
                                'login required', 'chatgpt')):
            return ("Codex isn't authenticated. Set CODEX_API_KEY or OPENAI_API_KEY "
                    "in Settings → Agent Providers, or run: codex login")
        if any(p in s for p in ('enoent', 'command not found', 'no such file',
                                'cannot find the path', "is not recognized")):
            return "Codex CLI not found. Run: npm install -g @openai/codex"
        if any(p in s for p in ('quota', 'rate limit', '429', 'too many requests')):
            return "Codex rate limit hit. Wait a minute and try again."
        if rc != 0:
            return f"Codex exited with code {rc}. Check auth and model name."
        return None


# ─────────────────────────────────────────────────────────────────────────────
# OpenCodeRuntime — opencode run --format json (nd-JSON)
# ─────────────────────────────────────────────────────────────────────────────


class OpenCodeRuntime(AgentRuntime):
    """Driver for opencode-ai CLI (opencode run --format json).

    Invokes `opencode run --format json "<prompt>"` for non-interactive use.
    Output is newline-delimited JSON (nd-JSON).

    Session resume: `--continue` (last) or `--session <SESSION_ID>`.
    MCP: config via opencode's settings file.
    Plan mode: experimental (OPENCODE_EXPERIMENTAL_PLAN_MODE=1 env var).
    """

    name = 'opencode'
    display_name = 'OpenCode'

    _bin_cache: Optional[str] = None

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            return Path(self._bin_cache) if self._bin_cache else None

        found = shutil.which('opencode')
        if not found:
            if sys.platform == 'win32':
                candidates = [
                    Path(os.environ.get('APPDATA', '')) / 'npm' / 'opencode.cmd',
                    Path(os.environ.get('USERPROFILE', '')) / '.npm-global' / 'bin' / 'opencode.cmd',
                    Path(os.environ.get('LOCALAPPDATA', '')) / 'opencode' / 'bin' / 'opencode.exe',
                ]
            else:
                home = Path.home()
                candidates = [
                    home / '.local' / 'bin' / 'opencode',
                    home / '.npm-global' / 'bin' / 'opencode',
                    Path('/usr/local/bin/opencode'),
                    Path('/opt/homebrew/bin/opencode'),
                ]
            for c in candidates:
                try:
                    if c.exists():
                        found = str(c)
                        break
                except Exception:
                    pass
        self._bin_cache = found or ''
        return Path(found) if found else None

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False,
                      resume_id: str = '') -> List[str]:
        """Return the opencode run command for non-interactive use.

        Flags from opencode CLI docs:
          run --format json "prompt"  -- nd-JSON event stream
          --continue / -c             -- resume last session
          --session / -s SESSION_ID  -- resume specific session
          --model MODEL               -- override model
        """
        bin_path = self.resolve_binary()
        cmd = [str(bin_path) if bin_path else 'opencode', 'run', '--format', 'json']
        if resume_id:
            if resume_id.lower() in ('last', 'latest'):
                cmd.append('--continue')
            else:
                cmd.extend(['--session', resume_id])
        if model:
            cmd.extend(['--model', model])
        return cmd

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse nd-JSON from `opencode run --format json`.

        OpenCode nd-JSON event shapes:
          {"type":"session","properties":{"id":"..."}} → INIT
          {"type":"message","role":"assistant","content":[...]} → ASSISTANT_TEXT / TOOL_USE
          {"type":"tool","name":"...","input":{...}} → TOOL_USE
          {"type":"done","info":{...}} → TURN_END
          {"type":"error","error":{"message":"..."}} → ERROR
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None

        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not a dict')
        except (json.JSONDecodeError, ValueError):
            return AgentEvent(
                type=EventType.ASSISTANT_TEXT, provider='opencode',
                session_id=None, mc_session_id=mc_session_id,
                timestamp=_now_iso(), payload={'text': line},
            )

        etype = msg.get('type', '')
        props = msg.get('properties') or {}
        session_id = props.get('id') or msg.get('session_id') or msg.get('id')

        if etype == 'session':
            return AgentEvent(
                type=EventType.INIT, provider='opencode',
                session_id=session_id, mc_session_id=mc_session_id,
                timestamp=_now_iso(),
                payload={'session_id': session_id,
                         'model': props.get('model') or props.get('modelID')},
                raw=msg,
            )
        elif etype == 'message':
            if msg.get('role') != 'assistant':
                return None
            content = msg.get('content') or []
            texts, tool_blocks = [], []
            for block in (content if isinstance(content, list) else []):
                if not isinstance(block, dict):
                    continue
                btype = block.get('type', '')
                if btype == 'text':
                    texts.append(block.get('text', ''))
                elif btype == 'tool_use':
                    tool_blocks.append({'type': 'tool_use',
                                       'name': block.get('name', ''),
                                       'input': block.get('input', {}),
                                       'tool_use_id': block.get('id')})
            if tool_blocks:
                return AgentEvent(type=EventType.TOOL_USE, provider='opencode',
                                  session_id=session_id, mc_session_id=mc_session_id,
                                  timestamp=_now_iso(),
                                  payload={'blocks': tool_blocks}, raw=msg)
            text = '\n'.join(texts).strip()
            return (AgentEvent(type=EventType.ASSISTANT_TEXT, provider='opencode',
                               session_id=session_id, mc_session_id=mc_session_id,
                               timestamp=_now_iso(),
                               payload={'text': text}, raw=msg)
                    if text else None)
        elif etype == 'tool':
            return AgentEvent(type=EventType.TOOL_USE, provider='opencode',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'blocks': [{'type': 'tool_use',
                                                  'name': msg.get('name', ''),
                                                  'input': msg.get('input', {}),
                                                  'tool_use_id': None}]}, raw=msg)
        elif etype in ('done', 'finish', 'complete', 'end'):
            info = msg.get('info') or {}
            return AgentEvent(type=EventType.TURN_END, provider='opencode',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'usage': info.get('usage') or info,
                                      'cost_usd': info.get('cost'),
                                      'num_turns': None, 'rc': 0}, raw=msg)
        elif etype == 'error':
            err = msg.get('error') or {}
            err_msg = (err.get('message') or str(err)) if isinstance(err, dict) else str(msg)
            return AgentEvent(type=EventType.ERROR, provider='opencode',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'text': err_msg}, raw=msg)
        text_val = msg.get('text') or msg.get('content') or ''
        if text_val and isinstance(text_val, str):
            return AgentEvent(type=EventType.ASSISTANT_TEXT, provider='opencode',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'text': text_val}, raw=msg)
        return None

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """OpenCode stores sessions in ~/.local/share/opencode/sessions/<id>/messages.json"""
        if not session_id:
            return None
        if sys.platform == 'win32':
            base = Path(os.environ.get('LOCALAPPDATA', '')) / 'opencode' / 'sessions'
        else:
            base = Path.home() / '.local' / 'share' / 'opencode' / 'sessions'
        p = base / session_id / 'messages.json'
        try:
            if p.exists():
                return p
        except OSError:
            pass
        return None

    def health_check(self) -> HealthStatus:
        bin_path = self.resolve_binary()
        if not bin_path:
            return HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint='curl -fsSL https://opencode.ai/install | bash  # or: npm install -g opencode-ai',
            )
        version = None
        try:
            r = subprocess.run([str(bin_path), '--version'],
                               capture_output=True, text=True, timeout=10,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            raw = (r.stdout or r.stderr or '').strip()
            version = raw.splitlines()[0] if raw else None
        except Exception as e:
            return HealthStatus(installed=True, binary_path=bin_path, version=None,
                                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                                diagnostic=str(e))
        return HealthStatus(installed=True, binary_path=bin_path, version=version,
                            auth_state=AuthState(status='unknown', last_checked=_now_iso()))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='opencode',
            display_name='OpenCode',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=True,
            supports_mcp=True,
            supports_skills=False,
            supports_plan_mode=False,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=True,
            emits_rate_limit=False,
            emits_cost=True,
            emits_num_turns=False,
            image_input=False,
            context_window=None,
            context_injection='prepend',
            context_file_name=None,
            oneshot_supported=True,
        )

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        if not self.resolve_binary():
            raise RuntimeError(
                "opencode not installed — "
                "run: curl -fsSL https://opencode.ai/install | bash")
        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        full_prompt = task
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{task}"
        cmd = self.build_command(model=model, resume_id=resume_id or '') + [full_prompt]
        return _mode_a_dispatch(
            self, cmd, full_prompt, project_path, project_id, task,
            mc_sid, session_dict, incognito, env_extra, callbacks,
            register_process, prompt_via_stdin=False,
            system_prompt=system_prompt,
        )

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)
        full_prompt = _compose_respawn_prompt(session, message,
                                              tail_lines=20, tail_chars=3000)
        cmd = self.build_command() + [full_prompt]
        mc_sid = handle.mc_session_id
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=handle.project_path,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()
        threading.Thread(target=_mode_a_reader, args=(proc, handle, self),
                         daemon=True, name=f'opencode-reader-{mc_sid[:8]}').start()

    def interrupt(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def stop(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        bin_path = self.resolve_binary()
        if not bin_path:
            return None
        full = (system_prompt + '\n\n' + prompt).strip() if system_prompt else prompt
        if stdin_text:
            full = f"{full}\n\n---\n\n{stdin_text}"
        cmd = [str(bin_path), 'run', '--format', 'json']
        if model:
            cmd.extend(['--model', model])
        cmd.append(full)
        try:
            r = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True, encoding='utf-8', errors='replace',
                timeout=180, creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        last_text = ''
        for raw_line in (r.stdout or '').splitlines():
            ev = self.parse_event(raw_line)
            if ev and ev.type == EventType.ASSISTANT_TEXT:
                last_text = ev.payload.get('text', last_text)
        return OneshotResult(text=last_text or (r.stdout or '').strip())


# ─────────────────────────────────────────────────────────────────────────────
# GooseRuntime — Block/AAIF goose CLI (stream-json)
# ─────────────────────────────────────────────────────────────────────────────


class GooseRuntime(AgentRuntime):
    """Driver for Block/AAIF `goose` CLI (Rust binary).

    Non-interactive: `goose run --no-session --output-format stream-json "prompt"`.
    System prompt: `--system "text"` flag (unique; no CLAUDE.md/AGENTS.md equivalent).
    Session resume: `goose session --resume` (SQLite-backed since v1.10.0).
    MCP: 70+ extensions via goose configure or YAML config.
    Plan mode: `/plan` slash command (interactive only — not headless-compatible).
    """

    name = 'goose'
    display_name = 'Goose'

    _bin_cache: Optional[str] = None

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            return Path(self._bin_cache) if self._bin_cache else None
        found = shutil.which('goose')
        if not found:
            home = Path.home()
            if sys.platform == 'win32':
                candidates = [
                    Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'goose' / 'goose.exe',
                    home / '.local' / 'bin' / 'goose.exe',
                ]
            else:
                candidates = [
                    home / '.local' / 'bin' / 'goose',
                    home / 'bin' / 'goose',
                    Path('/usr/local/bin/goose'),
                    Path('/opt/homebrew/bin/goose'),
                ]
            for c in candidates:
                try:
                    if c.exists():
                        found = str(c)
                        break
                except Exception:
                    pass
        self._bin_cache = found or ''
        return Path(found) if found else None

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False,
                      system_prompt: str = '') -> List[str]:
        """Return the goose run command for headless use.

        Flags verified against goose docs:
          run "prompt"                    -- non-interactive
          --no-session                    -- don't persist session
          --output-format stream-json     -- JSONL event stream
          --system TEXT                   -- system instructions (unique to goose)
          --model PROVIDER/MODEL          -- override provider/model
        """
        bin_path = self.resolve_binary()
        cmd = [str(bin_path) if bin_path else 'goose', 'run',
               '--no-session', '--output-format', 'stream-json']
        if system_prompt:
            cmd.extend(['--system', system_prompt])
        if model:
            cmd.extend(['--model', model])
        return cmd

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse JSONL from `goose run --output-format stream-json`.

        Goose stream-json events (shape mirrors gemini/claude):
          {"type":"init","session_id":"..."} → INIT
          {"type":"message","role":"assistant","content":[...]} → ASSISTANT_TEXT / TOOL_USE
          {"type":"tool_use","name":"...","input":{...}} → TOOL_USE
          {"type":"result","usage":{...}} → TURN_END
          {"type":"error","message":"..."} → ERROR
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not a dict')
        except (json.JSONDecodeError, ValueError):
            return AgentEvent(type=EventType.ASSISTANT_TEXT, provider='goose',
                              session_id=None, mc_session_id=mc_session_id,
                              timestamp=_now_iso(), payload={'text': line})

        etype = msg.get('type', '')
        session_id = msg.get('session_id') or msg.get('id')

        if etype == 'init':
            return AgentEvent(type=EventType.INIT, provider='goose',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'session_id': session_id,
                                      'model': msg.get('model')}, raw=msg)
        elif etype in ('message', 'assistant'):
            if msg.get('role', 'assistant') not in ('assistant', ''):
                return None
            content = msg.get('content') or []
            texts, tool_blocks = [], []
            for block in (content if isinstance(content, list) else []):
                if not isinstance(block, dict):
                    continue
                btype = block.get('type', '')
                if btype == 'text':
                    texts.append(block.get('text', ''))
                elif btype == 'tool_use':
                    tool_blocks.append({'type': 'tool_use',
                                       'name': block.get('name', ''),
                                       'input': block.get('input', {}),
                                       'tool_use_id': block.get('id')})
            if tool_blocks:
                return AgentEvent(type=EventType.TOOL_USE, provider='goose',
                                  session_id=session_id, mc_session_id=mc_session_id,
                                  timestamp=_now_iso(),
                                  payload={'blocks': tool_blocks}, raw=msg)
            text = '\n'.join(texts).strip() or str(msg.get('content', ''))
            return (AgentEvent(type=EventType.ASSISTANT_TEXT, provider='goose',
                               session_id=session_id, mc_session_id=mc_session_id,
                               timestamp=_now_iso(), payload={'text': text}, raw=msg)
                    if text else None)
        elif etype == 'tool_use':
            return AgentEvent(type=EventType.TOOL_USE, provider='goose',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'blocks': [{'type': 'tool_use',
                                                  'name': msg.get('name', ''),
                                                  'input': msg.get('input', {}),
                                                  'tool_use_id': msg.get('id')}]}, raw=msg)
        elif etype in ('result', 'done', 'finish', 'complete', 'turn_end'):
            return AgentEvent(type=EventType.TURN_END, provider='goose',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'usage': msg.get('usage'), 'cost_usd': None,
                                      'num_turns': None, 'rc': 0}, raw=msg)
        elif etype == 'error':
            return AgentEvent(type=EventType.ERROR, provider='goose',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'text': msg.get('message', str(msg))}, raw=msg)
        text_val = msg.get('text') or msg.get('content') or ''
        if text_val and isinstance(text_val, str):
            return AgentEvent(type=EventType.ASSISTANT_TEXT, provider='goose',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(), payload={'text': text_val}, raw=msg)
        return None

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Goose uses SQLite (v1.10.0+). Legacy JSONL: ~/.config/goose/sessions/<id>.jsonl"""
        if not session_id:
            return None
        if sys.platform == 'win32':
            base = Path(os.environ.get('APPDATA', '')) / 'goose' / 'sessions'
        else:
            base = Path.home() / '.config' / 'goose' / 'sessions'
        p = base / f'{session_id}.jsonl'
        try:
            if p.exists():
                return p
        except OSError:
            pass
        return None

    def health_check(self) -> HealthStatus:
        bin_path = self.resolve_binary()
        if not bin_path:
            return HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint=(
                    'curl -fsSL https://github.com/aaif-goose/goose/releases/'
                    'download/stable/download_cli.sh | CONFIGURE=false bash'),
            )
        version = None
        try:
            r = subprocess.run([str(bin_path), '--version'],
                               capture_output=True, text=True, timeout=10,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            raw = (r.stdout or r.stderr or '').strip()
            version = raw.splitlines()[0] if raw else None
        except Exception as e:
            return HealthStatus(installed=True, binary_path=bin_path, version=None,
                                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                                diagnostic=str(e))
        auth_method = None
        for env_var in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GOOGLE_API_KEY'):
            if os.environ.get(env_var):
                auth_method = f'env:{env_var}'
                break
        return HealthStatus(
            installed=True, binary_path=bin_path, version=version,
            auth_state=AuthState(status='ok' if auth_method else 'unknown',
                                 method=auth_method, last_checked=_now_iso()),
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='goose',
            display_name='Goose',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=True,
            supports_mcp=True,
            supports_skills=False,
            supports_plan_mode=False,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=False,
            emits_rate_limit=False,
            emits_cost=False,
            emits_num_turns=False,
            image_input=False,
            context_window=None,
            context_injection='flag',
            context_file_name=None,
            oneshot_supported=True,
        )

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        if not self.resolve_binary():
            raise RuntimeError(
                "goose not installed — run: "
                "curl -fsSL https://github.com/aaif-goose/goose/releases/"
                "download/stable/download_cli.sh | CONFIGURE=false bash")
        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        cmd = self.build_command(model=model, system_prompt=system_prompt)
        cmd.append(task)
        return _mode_a_dispatch(
            self, cmd, task, project_path, project_id, task,
            mc_sid, session_dict, incognito, env_extra, callbacks,
            register_process, prompt_via_stdin=False,
            system_prompt=system_prompt,
        )

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)
        full_prompt = _compose_respawn_prompt(session, message,
                                              tail_lines=20, tail_chars=3000)
        cmd = self.build_command() + [full_prompt]
        mc_sid = handle.mc_session_id
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=handle.project_path,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()
        threading.Thread(target=_mode_a_reader, args=(proc, handle, self),
                         daemon=True, name=f'goose-reader-{mc_sid[:8]}').start()

    def interrupt(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def stop(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        bin_path = self.resolve_binary()
        if not bin_path:
            return None
        full = prompt
        if stdin_text:
            full = f"{prompt}\n\n---\n\n{stdin_text}"
        cmd = [str(bin_path), 'run', '--no-session', '--output-format', 'stream-json']
        if system_prompt:
            cmd.extend(['--system', system_prompt])
        if model:
            cmd.extend(['--model', model])
        cmd.append(full)
        try:
            r = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True, encoding='utf-8', errors='replace',
                timeout=180, creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        last_text = ''
        for raw_line in (r.stdout or '').splitlines():
            ev = self.parse_event(raw_line)
            if ev and ev.type == EventType.ASSISTANT_TEXT:
                last_text = ev.payload.get('text', last_text)
        return OneshotResult(text=last_text or (r.stdout or '').strip())

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        s = (log_tail or '').lower()
        if any(p in s for p in ('api key', 'unauthorized', 'not configured',
                                'missing provider', 'no provider')):
            return ("Goose needs a provider configured. Run: goose configure  "
                    "or set OPENAI_API_KEY / ANTHROPIC_API_KEY in Settings → Agent Providers.")
        if rc != 0:
            return f"Goose exited with code {rc}. Run 'goose configure' to check provider setup."
        return None


# ─────────────────────────────────────────────────────────────────────────────
# AiderRuntime — aider-chat (plain text; Tier 2)
# ─────────────────────────────────────────────────────────────────────────────


class AiderRuntime(AgentRuntime):
    """Driver for aider (aider-chat Python package).

    Plain text output — no JSON events. Each output line is ASSISTANT_TEXT.
    Non-interactive: `aider --message "prompt" --no-stream --yes --no-auto-commits`.
    System prompt injection: `--read <file>` (read-only context file).
    Session resume: Not supported (stateless per invocation).
    MCP: Not supported natively.
    Plan mode: `--dry-run` previews changes without applying.
    Transcript: .aider.chat.history.md in project root.
    """

    name = 'aider'
    display_name = 'Aider'

    _bin_cache: Optional[str] = None

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            return Path(self._bin_cache) if self._bin_cache else None
        found = shutil.which('aider')
        if not found:
            home = Path.home()
            if sys.platform == 'win32':
                candidates = [
                    home / 'AppData' / 'Local' / 'Programs' / 'Python' / 'Python312' / 'Scripts' / 'aider.exe',
                    home / 'AppData' / 'Roaming' / 'Python' / 'Python312' / 'Scripts' / 'aider.exe',
                    home / '.local' / 'bin' / 'aider.exe',
                ]
            else:
                candidates = [
                    home / '.local' / 'bin' / 'aider',
                    home / '.venv' / 'bin' / 'aider',
                    Path('/usr/local/bin/aider'),
                ]
            for c in candidates:
                try:
                    if c.exists():
                        found = str(c)
                        break
                except Exception:
                    pass
        self._bin_cache = found or ''
        return Path(found) if found else None

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False,
                      dry_run: bool = False) -> List[str]:
        """Return aider base command for non-interactive use.

        Flags from aider docs:
          --no-stream               -- disable streaming (cleaner stdout capture)
          --yes / -y                -- auto-accept all changes
          --no-auto-commits         -- don't commit after changes
          --dry-run                 -- preview without applying (plan-mode equivalent)
          --model MODEL             -- override model
        Prompt is passed via `--message TEXT` by the caller (not positional).
        """
        bin_path = self.resolve_binary()
        cmd = [str(bin_path) if bin_path else 'aider',
               '--no-stream', '--yes', '--no-auto-commits']
        if model:
            cmd.extend(['--model', model])
        if dry_run:
            cmd.append('--dry-run')
        return cmd

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Aider is plain text only. Every non-empty line → ASSISTANT_TEXT."""
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None
        return AgentEvent(
            type=EventType.ASSISTANT_TEXT, provider='aider',
            session_id=None, mc_session_id=mc_session_id,
            timestamp=_now_iso(), payload={'text': line},
        )

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Aider writes .aider.chat.history.md in the project working directory."""
        if not project_path:
            return None
        p = Path(project_path) / '.aider.chat.history.md'
        try:
            if p.exists():
                return p
        except OSError:
            pass
        return None

    def health_check(self) -> HealthStatus:
        bin_path = self.resolve_binary()
        if not bin_path:
            return HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint='pip install aider-chat  # or: uv tool install aider-chat',
            )
        version = None
        try:
            r = subprocess.run([str(bin_path), '--version'],
                               capture_output=True, text=True, timeout=15,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            raw = (r.stdout or r.stderr or '').strip()
            version = raw.splitlines()[0] if raw else None
        except Exception as e:
            return HealthStatus(installed=True, binary_path=bin_path, version=None,
                                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                                diagnostic=str(e))
        auth_method = None
        for env_var in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GEMINI_API_KEY'):
            if os.environ.get(env_var):
                auth_method = f'env:{env_var}'
                break
        return HealthStatus(
            installed=True, binary_path=bin_path, version=version,
            auth_state=AuthState(status='ok' if auth_method else 'unknown',
                                 method=auth_method, last_checked=_now_iso()),
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='aider',
            display_name='Aider',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=False,
            supports_mcp=False,
            supports_skills=False,
            supports_plan_mode=True,
            supports_ask_user_question=False,
            supports_streaming_text=False,
            emits_usage=False,
            emits_rate_limit=False,
            emits_cost=False,
            emits_num_turns=False,
            image_input=False,
            context_window=None,
            context_injection='file',
            context_file_name='.aider.conf.yml',
            oneshot_supported=True,
        )

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        if not self.resolve_binary():
            raise RuntimeError("aider not installed — run: pip install aider-chat")
        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        cmd = self.build_command(model=model)
        if system_prompt:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.md',
                                              prefix='mc_aider_ctx_',
                                              delete=False, encoding='utf-8')
            tmp.write(system_prompt)
            tmp.close()
            cmd.extend(['--read', tmp.name])
        cmd.extend(['--message', task])
        return _mode_a_dispatch(
            self, cmd, task, project_path, project_id, task,
            mc_sid, session_dict, incognito, env_extra, callbacks,
            register_process, prompt_via_stdin=False,
            system_prompt=system_prompt,
        )

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)
        cmd = self.build_command()
        # Re-inject the dispatch-time system context via --read (aider keeps
        # --read files in context). The dispatch temp file isn't reused across
        # turns, so write a fresh one from the stashed _system_prompt.
        sys_prompt = (session.get('_system_prompt') or '').strip()
        if sys_prompt:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.md',
                                              prefix='mc_aider_ctx_',
                                              delete=False, encoding='utf-8')
            tmp.write(sys_prompt)
            tmp.close()
            cmd.extend(['--read', tmp.name])
        log_lines = session.get('log_lines') or []
        tail = '\n'.join(log_lines[-20:])[-3000:].strip()
        msg = (f"[Prior turn excerpt for context only — do not re-execute]\n"
               f"{tail}\n\n---\n\n{message}") if tail else message
        cmd.extend(['--message', msg])
        mc_sid = handle.mc_session_id
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=handle.project_path,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()
        threading.Thread(target=_mode_a_reader, args=(proc, handle, self),
                         daemon=True, name=f'aider-reader-{mc_sid[:8]}').start()

    def interrupt(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def stop(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        bin_path = self.resolve_binary()
        if not bin_path:
            return None
        cmd = self.build_command(model=model)
        if system_prompt:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.md',
                                              prefix='mc_aider_ctx_',
                                              delete=False, encoding='utf-8')
            tmp.write(system_prompt)
            if stdin_text:
                tmp.write(f"\n\n---\n\n{stdin_text}")
            tmp.close()
            cmd.extend(['--read', tmp.name])
        cmd.extend(['--message', prompt])
        try:
            r = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True, encoding='utf-8', errors='replace',
                timeout=180, creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        return OneshotResult(text=(r.stdout or '').strip())

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        s = (log_tail or '').lower()
        if any(p in s for p in ('api key', 'authentication', 'unauthorized')):
            return ("Aider needs an API key. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                    "or GEMINI_API_KEY in Settings → Agent Providers.")
        if any(p in s for p in ('no module named', 'importerror')):
            return "Aider dependency missing. Try: pip install --upgrade aider-chat"
        if rc != 0:
            return f"Aider exited with code {rc}. Check the API key and model name."
        return None


# ─────────────────────────────────────────────────────────────────────────────
# KiroRuntime — AWS Kiro CLI (JSON-RPC 2.0 ACP; Tier 2)
# ─────────────────────────────────────────────────────────────────────────────


class KiroRuntime(AgentRuntime):
    """Driver for AWS Kiro CLI (kiro-cli).

    Headless mode: `kiro-cli --no-interactive "task" --trust-all-tools`.
    ACP mode (kiro-cli acp) uses JSON-RPC 2.0 over stdin/stdout — reserved
    for future full session lifecycle support.

    Auth: KIRO_API_KEY env var (requires paid subscription for headless use).
    MCP: --require-mcp-startup for CI.
    """

    name = 'kiro'
    display_name = 'Kiro'

    _bin_cache: Optional[str] = None

    def resolve_binary(self) -> Optional[Path]:
        if self._bin_cache is not None:
            return Path(self._bin_cache) if self._bin_cache else None
        for name_candidate in ('kiro-cli', 'kiro'):
            found = shutil.which(name_candidate)
            if found:
                self._bin_cache = found
                return Path(found)
        home = Path.home()
        if sys.platform == 'win32':
            candidates = [
                home / '.kiro' / 'bin' / 'kiro-cli.exe',
                home / '.local' / 'bin' / 'kiro-cli.exe',
            ]
        else:
            candidates = [
                home / '.kiro' / 'bin' / 'kiro-cli',
                home / '.local' / 'bin' / 'kiro-cli',
                Path('/usr/local/bin/kiro-cli'),
            ]
        for c in candidates:
            try:
                if c.exists():
                    self._bin_cache = str(c)
                    return c
            except Exception:
                pass
        self._bin_cache = ''
        return None

    def build_command(self, *, model: str = '', max_turns: int = 0,
                      streaming: bool = False, perm_mode: str = '',
                      channels: str = '', remote_control: bool = False) -> List[str]:
        """Return kiro-cli headless command.

        Flags from kiro CLI docs:
          --no-interactive          -- headless (no TTY required)
          --trust-all-tools         -- auto-approve all tool calls
          --trust-tools read,grep   -- selective tool trust
        """
        bin_path = self.resolve_binary()
        return [str(bin_path) if bin_path else 'kiro-cli',
                '--no-interactive', '--trust-all-tools']

    def parse_event(self, raw_line: str, mc_session_id: str = '') -> Optional[AgentEvent]:
        """Parse kiro-cli headless output (JSON-RPC 2.0 notifications + plain text).

        JSON-RPC 2.0 notifications omit 'id'; requests include 'id'; responses
        include 'result' or 'error'. In headless mode, kiro emits a mix.
        """
        line = raw_line.rstrip('\n\r') if raw_line else ''
        if not line:
            return None
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError('not a dict')
        except (json.JSONDecodeError, ValueError):
            return AgentEvent(type=EventType.ASSISTANT_TEXT, provider='kiro',
                              session_id=None, mc_session_id=mc_session_id,
                              timestamp=_now_iso(), payload={'text': line})

        error = msg.get('error')
        result = msg.get('result')
        method = msg.get('method', '')
        params = msg.get('params') or {}
        session_id = params.get('session_id') or (result or {}).get('session_id') if isinstance(result, dict) else None

        if error:
            err_msg = (error.get('message', str(error))
                       if isinstance(error, dict) else str(error))
            return AgentEvent(type=EventType.ERROR, provider='kiro',
                              session_id=session_id, mc_session_id=mc_session_id,
                              timestamp=_now_iso(), payload={'text': err_msg}, raw=msg)

        if method == '_kiro.dev/metadata':
            text = params.get('text') or params.get('content', '')
            return (AgentEvent(type=EventType.ASSISTANT_TEXT, provider='kiro',
                               session_id=session_id, mc_session_id=mc_session_id,
                               timestamp=_now_iso(),
                               payload={'text': str(text)}, raw=msg)
                    if text else None)
        elif method in ('session/new', 'session/load'):
            sid = (result or {}).get('session_id') or session_id if isinstance(result, dict) else session_id
            return AgentEvent(type=EventType.INIT, provider='kiro',
                              session_id=sid, mc_session_id=mc_session_id,
                              timestamp=_now_iso(),
                              payload={'session_id': sid}, raw=msg)
        elif method == 'session/prompt' and isinstance(result, dict):
            content = result.get('content') or result.get('text', '')
            return (AgentEvent(type=EventType.ASSISTANT_TEXT, provider='kiro',
                               session_id=session_id, mc_session_id=mc_session_id,
                               timestamp=_now_iso(),
                               payload={'text': str(content)}, raw=msg)
                    if content else None)

        if isinstance(result, dict):
            text = result.get('content') or result.get('text', '')
            if text:
                return AgentEvent(type=EventType.ASSISTANT_TEXT, provider='kiro',
                                  session_id=session_id, mc_session_id=mc_session_id,
                                  timestamp=_now_iso(),
                                  payload={'text': str(text)}, raw=msg)
        return None

    def transcript_path(self, project_path: str, session_id: str) -> Optional[Path]:
        """Kiro stores sessions via ACP. No flat transcript file."""
        return None

    def health_check(self) -> HealthStatus:
        bin_path = self.resolve_binary()
        if not bin_path:
            return HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=AuthState(status='not_installed', last_checked=_now_iso()),
                install_hint='curl -fsSL https://cli.kiro.dev/install | bash',
            )
        version = None
        try:
            r = subprocess.run([str(bin_path), '--version'],
                               capture_output=True, text=True, timeout=10,
                               creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
            raw = (r.stdout or r.stderr or '').strip()
            version = raw.splitlines()[0] if raw else None
        except Exception as e:
            return HealthStatus(installed=True, binary_path=bin_path, version=None,
                                auth_state=AuthState(status='unknown', last_checked=_now_iso()),
                                diagnostic=str(e))
        has_key = bool(os.environ.get('KIRO_API_KEY'))
        return HealthStatus(
            installed=True, binary_path=bin_path, version=version,
            auth_state=AuthState(
                status='ok' if has_key else 'unknown',
                method='env:KIRO_API_KEY' if has_key else None,
                last_checked=_now_iso(),
                error_text=(None if has_key else
                            'KIRO_API_KEY not set. Requires paid Kiro subscription for headless use.'),
            ),
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='kiro',
            display_name='Kiro',
            supports_mode_a=True,
            supports_mode_b=False,
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=False,
            supports_mcp=True,
            supports_skills=False,
            supports_plan_mode=False,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=False,
            emits_rate_limit=False,
            emits_cost=False,
            emits_num_turns=False,
            image_input=False,
            context_window=None,
            context_injection='prepend',
            context_file_name=None,
            oneshot_supported=True,
        )

    def dispatch(self, *,
                 project_path: str,
                 task: str,
                 system_prompt: str = '',
                 resume_id: str = '',
                 mode: Literal['A', 'B'] = 'A',
                 model: str = '',
                 max_turns: Optional[int] = None,
                 incognito: bool = False,
                 env_extra: Optional[Dict[str, str]] = None,
                 callbacks: Optional[Dict[str, Callable]] = None,
                 housekeeping: bool = False,
                 mc_session_id: Optional[str] = None,
                 session_dict: Optional[Dict[str, Any]] = None,
                 project_id: str = '',
                 register_process: Optional[Callable] = None,
                 **_extra) -> SessionHandle:
        if not self.resolve_binary():
            raise RuntimeError(
                "kiro-cli not installed — run: "
                "curl -fsSL https://cli.kiro.dev/install | bash")
        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        cmd = self.build_command()
        full_prompt = task
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{task}"
        cmd.append(full_prompt)
        return _mode_a_dispatch(
            self, cmd, full_prompt, project_path, project_id, task,
            mc_sid, session_dict, incognito, env_extra, callbacks,
            register_process, prompt_via_stdin=False,
            system_prompt=system_prompt,
        )

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)
        full_prompt = _compose_respawn_prompt(session, message,
                                              tail_lines=20, tail_chars=3000)
        cmd = self.build_command() + [full_prompt]
        mc_sid = handle.mc_session_id
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=handle.project_path,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        session['proc'] = proc
        session['status'] = 'running'
        session['process_alive'] = True
        session['last_output_time'] = _time.time()
        session['last_status_change_time'] = _time.time()
        threading.Thread(target=_mode_a_reader, args=(proc, handle, self),
                         daemon=True, name=f'kiro-reader-{mc_sid[:8]}').start()

    def interrupt(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def stop(self, handle: SessionHandle) -> None:
        _mode_a_interrupt(handle)

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        bin_path = self.resolve_binary()
        if not bin_path:
            return None
        full = (system_prompt + '\n\n' + prompt).strip() if system_prompt else prompt
        if stdin_text:
            full = f"{full}\n\n---\n\n{stdin_text}"
        cmd = self.build_command() + [full]
        try:
            r = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=cwd or str(Path.home()),
                text=True, encoding='utf-8', errors='replace',
                timeout=180, creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception:
            return None
        last_text = ''
        for raw_line in (r.stdout or '').splitlines():
            ev = self.parse_event(raw_line)
            if ev and ev.type == EventType.ASSISTANT_TEXT:
                last_text = ev.payload.get('text', last_text)
        return OneshotResult(text=last_text or (r.stdout or '').strip())

    def explain_exit_error(self, rc: int, log_tail: str) -> Optional[str]:
        s = (log_tail or '').lower()
        if any(p in s for p in ('api key', 'unauthorized', 'kiro_api_key',
                                'paid subscription', 'pro plan')):
            return ("Kiro requires a paid subscription for headless use. "
                    "Set KIRO_API_KEY in Settings → Agent Providers.")
        if rc != 0:
            return f"Kiro exited with code {rc}. Verify KIRO_API_KEY is set."
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Auto-register the runtimes at import time
# ─────────────────────────────────────────────────────────────────────────────


register_runtime(ClaudeRuntime())
register_runtime(GeminiRuntime())
register_runtime(CodexRuntime())
register_runtime(OpenCodeRuntime())
register_runtime(GooseRuntime())
register_runtime(AiderRuntime())
register_runtime(KiroRuntime())
