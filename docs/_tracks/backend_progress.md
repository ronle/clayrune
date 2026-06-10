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
- **Commit:** `aa1fb6f` on `refactor/backend`, merged to `local/opus-effort`.

## 1.4 — mcp_routes blueprint (2026-06-10)

- **What moved:** 412 lines → `mc/blueprints/mcp_routes.py`: 10 routes (plan
  table said 6 — URL-install flow + per-project loadout grew after it): 8
  `/api/mcp*` + the 2 `/api/project/<id>/mcp-enabled` loadout routes
  (MCP-feature routes under /api/project/ — feature cohesion, same call as
  /api/presence in 1.2).
- **Seams:** `wire(load_project_fn, save_project_fn, data_dir,
  mcp_server_catalog_fn)` — `_mcp_server_catalog` STAYS in server.py (also
  feeds `_resolve_project_mcp_config` in dispatch; re-homes at 1.12).
  `_resolve_project_path_or_400` imported cross-blueprint from skills_routes.
- **Test port (landmine paid):** `test_mcp_trim._stub_endpoint_catalog`
  monkeypatched `server._mcp_server_catalog`; endpoint tests now patch
  `mc.blueprints.mcp_routes._mcp_server_catalog` (the dispatch-side stub stays
  on server). First real instance of the Phase-0 predicted test-port.
- **Gates:** routes 209/209 ✓ · full pytest 0 (22/22 mcp_trim after port) ✓ ·
  ruff ✓ · pyright mc/ 0 ✓ · smoke :5377 — /api/mcp lists, mcp-enabled returns
  loadout ✓
- **Commit:** `9da28cf` on `refactor/backend`, merged to `local/opus-effort`.

## 1.5 — distiller_routes blueprint (2026-06-10)

- **What moved:** 150 lines → `mc/blueprints/distiller_routes.py`: 7 routes
  (plan said 5 — loop-health + proposed-artifact landed after): 5
  `/api/distiller/*` + 2 `/api/project/<id>/distiller*` (feature cohesion).
  **Splice-guard win:** the source region also held `/api/router/stats` +
  `/api/project/<id>/memory/search` — the route-inventory assertion refused the
  cut until the boundary excluded them (dispatch/memory family; they stay for
  1.12/1.9).
- **Seams:** `wire(load_project_fn, data_dir)`; `_resolve_project_path_or_400`
  cross-imported from skills_routes; top-level `distiller`/`skills` imported
  directly.
- **Gates:** routes 209/209 ✓ · full pytest 0 ✓ · ruff ✓ · pyright mc/ 0 ✓ ·
  smoke :5377 — loop-health returns live alerts, distiller-stats returns
  counters ✓
- **Commit:** `ef07c04` on `refactor/backend`, merged to `local/opus-effort`.

## 1.6 — system_routes blueprint + Phase 2 obs (2026-06-10)

- **What moved:** 1,153 lines from TWO regions → `mc/blueprints/system_routes.py`:
  4 `/api/processes` (plan said 3 — cleanup grew) + 11 `/api/system` routes,
  system-status passive cache, restart machinery (incl. load-bearing
  `_get_active_restart_blockers`), update-check daemon loop.
- **Rebound globals retired:** `_LAST_SYSTEM_STATUS` (13 refs) +
  `_LAST_RESTART_TIME` (4 refs) → `mc/state.py` with `state.*` rewrites; the 2
  stream-reader touch points in server.py write `_mc_state._LAST_SYSTEM_STATUS`
  directly (a bare shim would have snapshotted the pre-rebind dict —
  split-brain). ALL FOUR Phase-0 deferred rebound globals are now migrated.
- **Phase 2 lands:** `mc/obs.py` (`log`/`heartbeat`/`snapshot` over
  `state.last_ok`) + **NEW route `GET /api/system/loops`** —
  **invariant 209 → 210** (plan-sanctioned addition; watcher + gates updated).
  update-check daemon instrumented; readers/scheduler instrument at 1.12/1.13.
- **Seams:** `wire(...)` carries 5 path/const slots + 9 fn slots (kill/pid/
  session helpers → 1.8/1.12, `_is_cf_tunneled_request` → 1.7,
  `_backfill_token_telemetry` → 1.12). Inbound shims: `_capture_system_init`
  (readers) + `_update_check_loop` (startup starter, position unchanged).
- **INCIDENT — stale smoke server:** a prior throwaway on :5377 survived its
  `kill` (git-bash kill unreliable on Windows) and silently ANSWERED the 1.3–1.5
  smokes. Killed; this step's isolated boot re-proved 1.3/1.4/1.5 registration
  (all 200). Kill discipline now `taskkill //PID //F` + port-free assert.
- **INCIDENT — reaper foot-gun (root cause of today's mid-turn agent death):**
  a throwaway `server.py` sharing the LIVE data dir runs
  `_reap_prior_instance_strays()` → kills the live MC's registered claude.exe
  children (this very agent, rc=1 mid-turn → truthful red Blocked).
  **NEW SMOKE DISCIPLINE: every throwaway boot gets `MC_DATA_DIR=$(mktemp -d)`
  + `MC_PORT=5377`** — isolated ledger, no reaping, no shared state.
- **Gates:** routes 210/210 ✓ · full pytest 0 ✓ · ruff ✓ · pyright mc/ 0 ✓ ·
  isolated smoke: heartbeat + loops(200,`{}` pre-delay) + skills/mcp/distiller/
  processes/status/restart-status all 200 ✓
- **Commit:** `8ad36a7` on `refactor/backend`, merged to `local/opus-effort`.

## 1.7 — remote_routes blueprint (2026-06-10)

- **What moved:** ~1,000 lines from THREE regions → `mc/blueprints/remote_routes.py`:
  **16 routes** (plan said 12 + `/_mc`): 12 `/api/remote/*` + the 2 device-label
  pages (`/_mc/name-device`, POST `/api/_mc/session-label`) + the 2
  mc-tunnel/enrollment integration points (`/api/tunnel-handshake`,
  `/api/mc-callback` — same family: same section, same `_get_remote_provider`
  dep). Plus the mc_remote_iface provider-discovery glue (was top-of-server.py;
  import side-effect now fires at the blueprint import — registry is only read
  at request/loop time), session-labels store, CF JWT machinery
  (`_is_cf_tunneled_request` & co.), label enforcer + daemon loop,
  `_warmup_control_plane`, and the `_redirect_unlabeled_cf_session`
  before_request BODY (thin wrapper stays on `app` at the same position, after
  `_local_auth_gate` — hook order unchanged). **Splice-guard exclusion:** the
  `MC_REMOTE_LOCAL_MOCK` dev-only mock CP (`/v1/nonce`, `/v1/attest`,
  `/api/_mock/connect`) stays in server.py — it mocks the *cloud CP*, not this
  family, and registers conditionally on an env flag.
- **RE-HOMING (this step's extra):** three earlier wire() seams now pass
  `_bp_remote.*`: local_auth `is_cf_tunneled_request`, push_mobile
  `cf_session_nonce_fn` + `get_remote_provider_fn`, system_routes
  `is_cf_tunneled_request_fn`. Remote stanza placed ABOVE all three (the 1.2
  import-time NameError lesson). No rebound globals to migrate (verified: no
  `global` stmts in the moved text; `_ENFORCER_STATE`/`_enforcer_lock` already
  live in mc/state.py since Phase 0 — blueprint imports them; 2 CONFIG reads
  rewritten to `state.CONFIG`).
- **Seams:** `wire(session_labels_path=…)` — the SESSION_LABELS_PATH module
  constant became a wired placeholder (1.6 lesson). Inbound shims (2):
  `_session_label_enforcer_loop` + `_warmup_control_plane` (startup thread
  targets under `__main__`, call sites unchanged). Phase 2: the enforcer loop
  gains `obs.heartbeat('session-label-enforcer')`.
- **Counting note:** `grep -c "@app.route" server.py` includes ONE f-string doc
  line (~3645) — real decorators are 127 where grep says 128; the 210 invariant
  arithmetic is unaffected (the line predates Phase 0 and was always counted).
- **Typing debt, explicit:** 1 tag (`request.args.to_dict(flat=True)` —
  werkzeug stubs lack the `flat=True` overload) + `_CF_JWKS_CACHE: dict`
  annotation for the mixed ts/keys cache values.
- **Phase 5:** new `tests/test_remote_routes.py` (14 tests): status +
  enforcer-state shapes, name-device page, session-label 403-untunneled /
  400-malformed / persist + JWT-nonce fallback, retroactive-label parse paths,
  redirect-hook 302 / API-exempt / labeled-passthrough. CP-proxy and
  tunnel-mutating endpoints deliberately NOT hit (see incident).
- **INCIDENT — isolated smoke ≠ isolated enrollment (1.8 MUST reuse this):**
  `MC_DATA_DIR=<temp>` does NOT isolate remote identity — mc_remote stores it
  in the **OS keystore** (keyring, user-level), so a throwaway boot on an
  enrolled dev machine runs the label enforcer against the REAL control plane
  with an EMPTY temp label store (every CF session looks "unnamed").
  Guard: seed the temp dir's `config.json` with
  `{"auto_revoke_unnamed_sessions": false}` — and write it **BOM-less**:
  PS 5.1 `Set-Content -Encoding utf8` emits a BOM, `_load_config`'s bare
  `json.load` then fails *silently* and defaults apply (first smoke ran with
  the enforcer enabled; 0 revoked — CP listed no sessions; second smoke with
  `[IO.File]::WriteAllText(..., UTF8Encoding($false))` verified `last_run:0`,
  heartbeat still present). This exposure predates 1.7 — every prior
  `python server.py` smoke had it; 1.7 only names it.
- **Gates:** routes 210/210 (128 app-grep + 82 bp) ✓ · `import server` ✓ ·
  ruff E9/F821 ✓ · pyright mc/ 0 ✓ · full pytest exit 0 (codex env skip only)
  ✓ · isolated smoke ×2 (`MC_DATA_DIR=$TEMP\…`, `MC_PORT=5377`, taskkill +
  port-free asserts): heartbeat, remote/status, enforcer-state,
  /_mc/name-device, local-auth/status + push/vapid-public-key (both re-homed
  seams), system/loops shows `session-label-enforcer` ✓
- **Commit:** `PENDING` on `wt-1.7` (orchestrator merges).
