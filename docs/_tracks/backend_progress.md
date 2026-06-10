# Track A — Backend progress log

Per-step crash-recovery log (MODERNIZATION_TRACKS.md). One entry per merged step.

## Phase 0 — scaffold (2026-06-10)

- **What moved:** `mc/` package created. `mc/state.py` = 43 shared globals/locks/events
  moved verbatim (agent_sessions, managers, mem-write locks + `_get_mem_write_lock`,
  condense/scribe, terminal/process tracker, claude-auth, provider-env, backlog-sync,
  checkpoint, hivemind ×7, scheduler/guardian stops, push/presence, enforcer,
  update-check, + `CONFIG` live alias). `mc/core.py` = pure helpers `_log`,
  `_LOG_LEVELS`, `_atomic_write_text`, `time_ago`, `now_iso`, `file_type`,
  `_is_loopback_request`. server.py keeps explicit `from mc.state/core import ...`
  shims so every bare-name reference is unchanged.
- **Deliberately left behind:** the 4 `global`-rebound names (`_fcm_app`,
  `_fcm_init_error`, `_LAST_SYSTEM_STATUS`, `_LAST_RESTART_TIME`) — shim-importing a
  rebound name would split-brain server.py vs mc.state. They migrate WITH their
  blueprints (push_mobile, system) when their rebind sites rewrite to `state.X`.
  Path constants (`_DATA_ROOT` etc.) + `_load_config` also stay until their users move.
- **Single permitted edit:** `_log` reads `state.CONFIG.get('log_level')`;
  server.py binds `state.CONFIG = CONFIG` right after `_load_config()`.
- **Phase-1 landmine to remember:** tests monkeypatch/mutate `server.<name>`
  (e.g. `srv._claude_auth_state.clear()`, `srv.tracked_processes[...]`) — in-place
  mutation flows through the shim alias fine, but any future `monkeypatch.setattr(server, name, ...)`
  REBINDS only server's attr, not `mc.state`'s. When a blueprint moves, port its
  tests to patch `mc.state.<name>`.
- **Gates:** routes 209/209 ✓ · pytest exit 0 (1 env skip: codex CLI absent) ✓ ·
  `ruff check --select E9,F821` clean ✓ · boot smoke `MC_PORT=5377` → heartbeat 200 ✓
  (NOTE: tracks-doc smoke command needs `MC_PORT=<free port>` — live MC owns :5199,
  a second bind fails; doc command corrected here.)
- **Commit:** (filled after commit)
