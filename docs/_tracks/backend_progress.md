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
- **Commit:** `e66ae29` on `refactor/backend`, merged to `local/opus-effort`.

## 1.1 — local_auth blueprint (pilot, 2026-06-10)

- **What moved:** the entire LAN passcode gate family (350 lines) →
  `mc/blueprints/local_auth.py`: 5 routes (3 `/api/local-auth/*` + 2 `/_mc/local-*`
  pages), 13 `_local_auth_*` helpers, `_render_local_auth_page`, constants, the
  `before_request` gate body (`local_auth_gate()`; thin wrapper stays on `app` at
  the SAME source position → hook order vs `_redirect_unlabeled_cf_session`
  unchanged). `_harden_secret_perms` → `mc/core.py` (cross-family, shimmed).
- **Seam:** `wire(local_auth_path=…, is_cf_tunneled_request=…)` late-binds the two
  unextracted deps (`_DATA_ROOT`; CF JWT machinery → moves home at 1.7). Wired
  types annotated; None defaults are import-time-only.
- **No shims needed:** zero callers of the family elsewhere in server.py (verified
  by grep); tests drive it via HTTP. Existing `test_auth_routes.py` covers
  provider auth, NOT this gate — so per Phase 5 added
  `tests/test_local_auth_routes.py` (7 tests: exempt host, LAN locked 401/302,
  no LAN bootstrap, set→login lifecycle incl. cookie-jar pitfall, throttle 429).
- **Gates:** routes 209/209 (204 app + 5 bp) ✓ · full pytest exit 0 ✓ · ruff
  E9/F821 ✓ · pyright mc/ 0 errors ✓ · smoke boot :5377 heartbeat 200 +
  `/api/local-auth/status` correct JSON + locked-page 302 ✓
- **Commit:** `276d3bd` on `refactor/backend`, merged to `local/opus-effort`.

## 1.2 — push_mobile blueprint (2026-06-10)

- **What moved:** 923 lines → `mc/blueprints/push_mobile.py`: 7 `/api/push` +
  6 `/api/mobile-pair` + `/api/presence` (presence exists solely as push
  focus-suppression → travels with push; **+1 route vs the plan table**, total
  still 209). VAPID/subscription store, FCM block, `_notify_push`,
  `_handle_push_signal`, presence touch/watch, mobile pairing + Path-B
  auto-pair/keystore/tokens.
- **Rebound globals done right:** `_fcm_app`/`_fcm_init_error` (the Phase-0
  deferred case) now live in `mc/state.py`; their `global` stmt dropped and all
  11 references rewritten to `state._fcm_*` — single live binding, no
  split-brain.
- **Seams:** `wire(data_root, load_project_fn, cf_session_nonce_fn,
  get_remote_provider_fn)` — projects family (1.11) + remote family (1.7) will
  re-home the last three. Only inbound shim: `_handle_push_signal` (2 stream-
  reader call sites; moves home at 1.12). Stanza placed with the 1.1 stanza
  AFTER `_cf_session_nonce_from_request`'s def (import-time NameError otherwise
  — ruff F821 caught it).
- **Typing debt, explicit:** 17 verbatim-moved lines tripped pyright basic
  (crypto union-narrowing + Optional `.get` chains); each tagged
  `# pyright: ignore[<rule>]  # moved-verbatim typing debt (1.2)` — greppable,
  zero behavior change. mc/ back to 0 errors.
- **Gates:** routes 209/209 ✓ · full pytest 0 ✓ · ruff E9/F821 ✓ · pyright mc/
  0 ✓ · smoke :5377 + route parity vs live :5199 (same VAPID key, same
  mobile-pair/presence responses) ✓
- **Commit:** `dfb4f86` on `refactor/backend`, merged to `local/opus-effort`.

## 1.3 — skills_routes blueprint (2026-06-10)

- **What moved:** 546 lines → `mc/blueprints/skills_routes.py`: 14 `/api/skills*`
  routes (plan table said 12 — git-import grew install/cancel since). Thin glue;
  `skills.py` keeps the logic (module named skills_routes to avoid shadowing it).
- **Seams:** `wire(load_project_fn, load_projects_fn, app_dir)` (projects family
  1.11 + `_APP_DIR` const). Blueprint imports top-level `skills`/`mcp` modules
  directly (allowed — they're not server.py).
- **Inbound shims (3):** `_install_builtin_skills` + `_install_builtin_mcps`
  (startup installers) and `_resolve_project_path_or_400` (shared request helper
  used by the MCP/distiller sections until 1.4/1.5 extract).
- **Gates:** routes 209/209 ✓ · full pytest 0 ✓ · ruff ✓ · pyright mc/ 0 ✓ ·
  smoke :5377 — /api/skills lists, /api/skills/search returns hits ✓
- **Commit:** `(fill13)` on `refactor/backend`, merged to `local/opus-effort`.
