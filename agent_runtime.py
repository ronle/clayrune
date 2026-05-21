"""AgentRuntime — multi-provider abstraction for Mission Control.

This module is the single seam between MC and any underlying agent CLI
(claude-code, gemini, codex, ...). It lets MC drive any provider through
one uniform interface, with graceful degradation where capabilities differ.

**Prototype scope (feat/multi-provider-agents):**
- ClaudeRuntime: a thin wrapper that delegates back to server.py's existing
  claude path via a registered hook. Provider 'claude' is the default; any
  project without an explicit provider runs through the legacy code path
  unchanged.
- GeminiRuntime: a self-contained implementation that drives the `gemini` CLI
  end-to-end (spawn / read / interrupt / followup) using its own reader
  thread. Mode A only (one process per turn). Followup re-spawns a fresh
  process — Gemini has no persistent stream-json mode.

The two share the `AgentRuntime` ABC, the `SessionHandle` shape, and the
event-callback contract. Server.py routes dispatch/followup/interrupt/stop
through a runtime when the project's provider is non-claude; claude
projects keep their legacy path.

See `docs/MULTI_PROVIDER_DESIGN.md` for the full architectural design.
"""

from __future__ import annotations

import json
import os
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
    context_injection: Literal['flag', 'file', 'prepend', 'read-file'] = 'prepend'
    context_file_name: Optional[str] = None
    oneshot_supported: bool = False


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
        ...

    @abstractmethod
    def health_check(self) -> HealthStatus:
        ...

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        ...

    @abstractmethod
    def dispatch(self, **kwargs) -> SessionHandle:
        ...

    @abstractmethod
    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        ...

    @abstractmethod
    def interrupt(self, handle: SessionHandle) -> None:
        ...

    @abstractmethod
    def stop(self, handle: SessionHandle) -> None:
        ...

    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: Optional[str] = None,
                cwd: Optional[str] = None) -> Optional[OneshotResult]:
        """Default: not supported. Providers override."""
        return None


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
# ClaudeRuntime — delegates to server.py via a registered hook
# ─────────────────────────────────────────────────────────────────────────────


# server.py registers its own dispatch/followup/stop/interrupt functions here
# at startup. Doing it this way avoids the circular import (agent_runtime.py
# is imported by server.py, not the reverse), and keeps the existing claude
# code path byte-identical (we're just adding a new entry point that calls
# back into it).
_CLAUDE_HOOKS: Dict[str, Callable] = {}


def register_claude_hooks(*,
                          resolve_binary: Callable,
                          health_check: Callable,
                          dispatch: Callable,
                          followup: Callable,
                          stop: Callable,
                          interrupt: Callable,
                          oneshot: Optional[Callable] = None) -> None:
    """Called once from server.py at startup to wire ClaudeRuntime back into
    the legacy code path. ClaudeRuntime's methods just call these hooks.
    """
    _CLAUDE_HOOKS['resolve_binary'] = resolve_binary
    _CLAUDE_HOOKS['health_check'] = health_check
    _CLAUDE_HOOKS['dispatch'] = dispatch
    _CLAUDE_HOOKS['followup'] = followup
    _CLAUDE_HOOKS['stop'] = stop
    _CLAUDE_HOOKS['interrupt'] = interrupt
    if oneshot:
        _CLAUDE_HOOKS['oneshot'] = oneshot


class ClaudeRuntime(AgentRuntime):
    name = 'claude'
    display_name = 'Claude Code'

    def resolve_binary(self) -> Optional[Path]:
        fn = _CLAUDE_HOOKS.get('resolve_binary')
        if not fn:
            return None
        try:
            return Path(fn())
        except Exception:
            return None

    def health_check(self) -> HealthStatus:
        fn = _CLAUDE_HOOKS.get('health_check')
        if fn:
            try:
                return fn()
            except Exception as e:
                return HealthStatus(installed=False, binary_path=None,
                                    version=None,
                                    auth_state=AuthState(status='unknown', last_checked=''),
                                    install_hint='', diagnostic=str(e))
        # Fallback: probe PATH directly
        bin_path = shutil.which('claude')
        return HealthStatus(
            installed=bool(bin_path),
            binary_path=Path(bin_path) if bin_path else None,
            version=None,
            auth_state=AuthState(status='unknown', last_checked=''),
            install_hint='npm install -g @anthropic-ai/claude-code',
        )

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
            context_injection='flag',
            context_file_name='CLAUDE.md',
            oneshot_supported=True,
        )

    def dispatch(self, **kwargs) -> SessionHandle:
        fn = _CLAUDE_HOOKS.get('dispatch')
        if not fn:
            raise RuntimeError("ClaudeRuntime not registered with server.py")
        return fn(**kwargs)

    def write_followup(self, handle, message, attachments=None):
        fn = _CLAUDE_HOOKS.get('followup')
        if not fn:
            raise RuntimeError("ClaudeRuntime not registered")
        return fn(handle, message, attachments=attachments)

    def interrupt(self, handle):
        fn = _CLAUDE_HOOKS.get('interrupt')
        if not fn:
            raise RuntimeError("ClaudeRuntime not registered")
        return fn(handle)

    def stop(self, handle):
        fn = _CLAUDE_HOOKS.get('stop')
        if not fn:
            raise RuntimeError("ClaudeRuntime not registered")
        return fn(handle)

    def oneshot(self, **kwargs):
        fn = _CLAUDE_HOOKS.get('oneshot')
        if not fn:
            return None
        try:
            return fn(**kwargs)
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# GeminiRuntime — self-contained Gemini CLI driver
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
        # Auth: gemini reads GEMINI_API_KEY or uses an oauth flow. We don't
        # actively probe — UI surfaces an "unknown" until a real call.
        auth_method = 'env:GEMINI_API_KEY' if os.environ.get('GEMINI_API_KEY') else None
        return HealthStatus(
            installed=True,
            binary_path=bin_path,
            version=version,
            auth_state=AuthState(status='unknown', method=auth_method,
                                 last_checked=_now_iso()),
            install_hint='',
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name='gemini',
            display_name='Gemini CLI',
            supports_mode_a=True,
            supports_mode_b=False,         # synthetic-B deferred
            mode_b_kind='none',
            default_mode='A',
            supports_session_resume=False, # no native resume; checkpoint only
            supports_mcp=False,            # MCP support deferred (config translation)
            supports_skills=False,         # Gemini has no Skills concept
            supports_plan_mode=False,
            supports_ask_user_question=False,
            supports_streaming_text=True,
            emits_usage=False,
            emits_rate_limit=False,
            context_injection='prepend',
            context_file_name='GEMINI.md',
            oneshot_supported=True,
        )

    # ── Dispatch ────────────────────────────────────────────────────────────

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

        mc_sid = mc_session_id or uuid.uuid4().hex[:12]
        # Build prompt with system_prompt prepended (Gemini reads GEMINI.md
        # in cwd; we still inline the MC system prompt so MEMORY/AGENT_RULES
        # ride along even if the project dir doesn't have a GEMINI.md).
        full_prompt = task
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n---\n\n{task}"

        cmd = [str(bin_path), '--prompt', full_prompt]
        if model:
            cmd.extend(['--model', model])

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
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

        # Build / reuse the session dict — MC owns it; this stays in
        # `agent_sessions[mc_sid]` and the reader thread mutates it in place.
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
        })

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

    # ── Reader thread ───────────────────────────────────────────────────────

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
                # Detect if another process replaced ours (followup respawn).
                if session.get('proc') is not proc:
                    break
                line = raw_line.rstrip('\n\r')
                if not line:
                    continue
                # Try JSON envelope first (--output-format stream-json), fall
                # back to plain text. Either way the line lands in log_lines
                # so the chat UI shows it.
                msg: Optional[Dict[str, Any]] = None
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        msg = parsed
                except (json.JSONDecodeError, ValueError):
                    pass

                if msg:
                    self._emit_from_json(msg, handle, _cb)
                else:
                    # Plain text — append directly to log
                    session['log_lines'].append(line)
                    session['last_output_time'] = _time.time()
                    ev = AgentEvent(
                        type=EventType.ASSISTANT_TEXT,
                        provider='gemini',
                        session_id=None,
                        mc_session_id=handle.mc_session_id,
                        timestamp=_now_iso(),
                        payload={'text': line},
                    )
                    _cb('on_assistant_text', ev)
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
                session['process_alive'] = False
                ev = AgentEvent(
                    type=EventType.PROCESS_EXIT,
                    provider='gemini',
                    session_id=None,
                    mc_session_id=handle.mc_session_id,
                    timestamp=_now_iso(),
                    payload={'rc': rc},
                )
                _cb('on_process_exit', ev)

    def _emit_from_json(self, msg: Dict[str, Any], handle: SessionHandle,
                        cb: Callable[[str, AgentEvent], None]) -> None:
        session = handle.session_dict
        mtype = msg.get('type') or msg.get('event') or ''
        text = msg.get('text') or msg.get('content') or ''

        # Best-effort normalization — Gemini's stream-json schema isn't
        # locked in yet, so we accept several shapes.
        if mtype in ('content', 'assistant', 'message', 'text') and text:
            session['log_lines'].append(str(text))
            session['last_output_time'] = _time.time()
            ev = AgentEvent(
                type=EventType.ASSISTANT_TEXT,
                provider='gemini',
                session_id=msg.get('session_id'),
                mc_session_id=handle.mc_session_id,
                timestamp=_now_iso(),
                payload={'text': str(text)},
                raw=msg,
            )
            cb('on_assistant_text', ev)
        elif mtype == 'tool_use':
            name = msg.get('name', '')
            session['log_lines'].append(f"[gemini tool: {name}]")
            session['last_output_time'] = _time.time()
        elif mtype in ('result', 'turn_end', 'done'):
            ev = AgentEvent(
                type=EventType.TURN_END,
                provider='gemini',
                session_id=msg.get('session_id'),
                mc_session_id=handle.mc_session_id,
                timestamp=_now_iso(),
                payload={'usage': msg.get('usage')},
                raw=msg,
            )
            cb('on_turn_end', ev)
        else:
            # Unknown envelope — surface the raw line so the user sees it
            session['log_lines'].append(json.dumps(msg, ensure_ascii=False))
            session['last_output_time'] = _time.time()

    # ── Followup / interrupt / stop ─────────────────────────────────────────

    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: Optional[List[str]] = None) -> None:
        """Synthetic follow-up: kill any live process and spawn fresh.

        Gemini has no native persistent stream-json mode, so each follow-up
        is a new process. Prior turn context lives in the session log; we
        prepend a short transcript-tail summary into the new prompt so the
        model has continuity.
        """
        session = handle.session_dict
        old_proc = session.get('proc')
        if old_proc and old_proc.poll() is None:
            _kill_pid(old_proc.pid)

        # Reconstruct minimal context from recent log_lines (last 4KB)
        log_lines = session.get('log_lines') or []
        tail = '\n'.join(log_lines[-30:])[-4000:]
        prior = f"[Prior turn excerpt for context only — do not re-execute]\n{tail}\n\n---\n\n" if tail else ""
        full_prompt = prior + message

        bin_path = self.resolve_binary()
        if not bin_path:
            session['log_lines'].append("[gemini binary missing — cannot continue]")
            session['status'] = 'error'
            session['last_status_change_time'] = _time.time()
            return

        cmd = [str(bin_path), '--prompt', full_prompt]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=handle.project_path,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
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
        # For Mode-A providers, stop == interrupt.
        self.interrupt(handle)

    # ── Oneshot ─────────────────────────────────────────────────────────────

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
        cmd = [str(bin_path), '--prompt', full]
        if model:
            cmd.extend(['--model', model])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=cwd, timeout=180,
                               creationflags=_POPEN_FLAGS,
                               startupinfo=_STARTUPINFO)
        except Exception:
            return None
        text = (r.stdout or '').strip()
        return OneshotResult(text=text, raw=None)


# ─────────────────────────────────────────────────────────────────────────────
# Auto-register the runtimes at import time
# ─────────────────────────────────────────────────────────────────────────────


register_runtime(ClaudeRuntime())
register_runtime(GeminiRuntime())
