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
- **Commit:** `82f5494` on `wt-1.7` (orchestrator merges).

## 1.8 — terminal_routes blueprint (2026-06-10)

- **What moved:** 258 lines (one contiguous region, splice-asserted: exactly
  6 routes + 8 defs, byte-faithful) → `mc/blueprints/terminal_routes.py`:
  **6 routes** (plan said 5): the 5 `/api/terminal/*` (launch/stream/stdin/
  stop/delete) + `/api/project/<id>/terminal/status` (terminal feature under
  the project prefix — same cohesion call as /api/presence in 1.2), the
  reader/kill helpers (`_read_terminal_stream`, `_kill_terminal_session`),
  and the TTY-shim spawn machinery (PYTHONPATH sitecustomize env wiring).
  `_TTY_SHIM_DIR` (derived from `_APP_DIR`) became a wired placeholder —
  the 1.7 SESSION_LABELS_PATH pattern.
- **Helper placement (the step's main boundary call):**
  `_register_process`/`_unregister_process` **STAY in server.py** — grep
  shows ~24 call sites across agent dispatch, hivemind workers/orchestrator,
  housekeeping, revival, and shutdown; the terminal call is 1 of them. They
  wire in and re-home at 1.12. Same verdict for `_kill_pid`/`_pid_is_alive`/
  `_kill_proc_background` (reaper + stream readers + agent stop) and
  `_launch_terminal_for_binary` (provider-auth popup, never touches
  terminal_sessions — NOT this family). **NO system_routes wire kwargs
  re-homed** (the brief's contingency didn't trigger); terminal stanza sits
  AFTER the system stanza, order-safe.
- **Seams:** `wire(load_project_fn, get_manager_fn, register_process_fn,
  unregister_process_fn, popen_flags, startupinfo, tty_shim_dir)`. Inbound
  shim (1): `_kill_terminal_session` — called by delete_project (1.11) and
  the atexit `_cleanup_terminals` hook. The hook itself STAYS in server.py:
  moving its `atexit.register` (line ~12605, pre-stanza) into the blueprint
  would reorder LIFO exit hooks vs scheduler/hivemind stops — behavior
  change. Both call sites resolve the shim global at call time, no
  import-order issue (ruff F821 clean).
- **AUTH-GATE FINDING (plan 1.8 acceptance said "re-verify the 403"):**
  `/api/terminal/launch` has NO route-private gate — its protection is the
  app-wide `local_auth_gate` (1.1): loopback + CF-tunneled exempt, all other
  peers need the LAN passcode cookie. The reject is **401 auth_required,
  not 403** (403 exists only inside the passcode login flow). Verified BOTH
  ways: unit test with `environ_base={'REMOTE_ADDR':'192.168.1.50'}` (401 +
  Popen-recorder proves no spawn, no session) AND live smoke POST from the
  real LAN interface 192.168.86.4 → 401 `{"error":"auth_required"}`. The
  plan's "403" is a misremembered status code; behavior unchanged.
- **Phase 5:** new `tests/test_terminal_routes.py` (16 tests): launch happy
  path with FakeProc-on-a-real-pipe (verbatim `_read_terminal_stream`
  exercised: feed→capture→EOF→completed→unregister) + `/api/processes`
  cross-blueprint check, 401 auth-reject + loopback-exempt twin, malformed
  ×5, stdin roundtrip/guards, stop/delete lifecycle + idempotency, status
  purge, SSE unknown-session. Patches `mc.blueprints.terminal_routes.*`
  attrs only (test-port rule); `subprocess` replaced by a recorder
  namespace ON THE MODULE so no real children spawn.
- **Cross-test pollution found:** `test_pid_reaper.py::
  test_persist_pid_ledger_roundtrip` clears `tracked_processes` and leaks
  a `{'proc': object()}` entry (no `.poll`) — full-suite-only 500 in
  `/api/processes` for any later caller. Fixed inside this step's fixture
  (clear-at-entry + snapshot/restore); the reaper test itself untouched
  (out of moves-only scope — flag for a cleanup commit).
- **Gates:** routes 210/210 (122 app-grep [121 real + the 1.7-noted f-string
  line] + 88 bp) ✓ · `import server` ✓ · ruff E9/F821 ✓ · pyright mc/ 0 (no
  new typing-debt tags needed — moved bodies are untyped-param Any) ✓ · full
  pytest exit 0 (codex env skip only) ✓ · isolated smoke (`MC_DATA_DIR=
  $TEMP\mc-smoke-18-*`, `MC_PORT=5377`, BOM-less config seed via
  `[IO.File]::WriteAllText` + seeded `data/projects/term-smoke.json`,
  taskkill-as-own-command): heartbeat 200 · loopback launch 200 + sid ·
  /api/processes shows `type=terminal alive=true` (cross-blueprint) ·
  terminal/status streams real ping output (reader thread live) ·
  enforcer-state `last_run=0` (seed parsed, no revocations) · LAN-peer
  launch 401 · `taskkill /T /F` killed server + tracked terminal child
  37208 + grandchildren (tree verified dead, port 5377 free, live :5199
  untouched) ✓
- **Commit:** `PENDING` on `wt-1.8` (orchestrator merges).

## 1.9 — guide_routes blueprint (2026-06-10)

- **What moved:** ~530 lines from FOUR regions → `mc/blueprints/guide_routes.py`:
  **5 routes**: `/api/guide/stream` (the plan-flagged 156-line SSE generator,
  moved WHOLE) + `/api/guide/ask` with all Claydo glue (`_claydo_cwd`,
  `_CLAYDO_NO_TOOLS_FLAGS`, `_claydo_recent_changelog`,
  `_claydo_prepare_context`), `/api/project/<id>/scribe-stats` (telemetry
  READ — route only), `/api/project/<id>/memory/search` (the 1.5
  splice-guard leftover; read-only retrieval), and
  `/api/walkthrough/sample-project` (+ `_clayrune_agent_rules` /
  `_clayrune_readme` seed helpers). The family is NOT contiguous — project
  CRUD, router-stats and backlog routes sit between the pieces; each region
  was boundary-asserted line-by-line before the cut.
- **Scoping calls (under-move wins):** (a) `_memory_search` STAYS in
  server.py — shared with the deterministic read floor in
  `_build_agent_context` (dispatch, 1.12) and walks `_get_memory_path`/
  `_get_archive_path`/`_mem_split`; the route wires it in. (b) The
  `/api/project/<id>/memory` GET/PUT/append trio (~11308, "Memory
  endpoints" section) NOT moved — separate editor-CRUD region, 1.11/1.12
  family. (c) `/api/router/stats` NOT moved (dispatch, per 1.5's note).
  (d) ALL Scribe/condense/checkpoint machinery untouched (CLAUDE.md
  lock+atomic discipline). (e) `_clayrune_api_reference`/
  `_clayrune_universal_capabilities` stay — they feed `_build_agent_context`,
  despite the `_clayrune_` name family. (f) No `/api/claydo*` routes exist;
  the "Claydo ask" stdin-stream blocks ARE `/api/guide/ask|stream` — moved.
- **Seams:** `wire(load_project_fn, save_project_fn, data_dir,
  memory_search_fn, resolve_claude_fn, popen_flags, startupinfo,
  server_dir)`. **New wired-placeholder case:** 5× `Path(__file__).parent`
  → wired `_SERVER_DIR` (evaluated in server.py — in the blueprint,
  `__file__` would resolve to mc/blueprints/ and silently break
  data/claydo + USER_GUIDE/CHANGELOG paths); 1× `CONFIG.get` →
  `state.CONFIG.get` (1.7 precedent). **Inbound shims: ZERO** — grep
  proved no other server.py caller of any moved name (only comment
  mentions, e.g. `_claydo_cwd` in the backfill heuristic's docstring).
- **Typing debt, explicit:** 5 tags
  `# pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.9)`
  on guide_stream's `proc.stdin/stdout/stderr` pipe access (stubs say
  `IO[str] | None`; PIPE guarantees non-None at runtime).
- **Phase 5:** new `tests/test_guide_routes.py` (25 tests): guide/ask happy
  (cmd flags + stdin stream-json + CLAUDE.md materialization) / history /
  malformed ×3 / guide-missing 500 / claude exit·timeout·missing; SSE
  stream happy (delta+done parse) / 400-is-JSON / spawn-fail + exit-fail
  SSE error events; scribe-stats empty·seeded·corrupt-500; memory/search
  passthrough + k-parse + 404 + missing-q; walkthrough create+seed-files
  (AGENT_RULES content proves _SERVER_DIR wiring) / idempotent / no-trample;
  401 auth-reject + loopback twins. Patches `mc.blueprints.guide_routes.*`
  only; recorder subprocess namespace — nothing real spawns. (No
  /api/processes touched → 1.8's pid-reaper fixture not needed.)
- **Gates:** routes 210/210 (117 app-grep [116 real + the 1.7-noted
  f-string line] + 93 bp) ✓ · `import server` ✓ · ruff E9/F821 ✓ · pyright
  mc/ 0 ✓ · full pytest exit 0 (codex env skip only) ✓ · isolated smoke
  (`MC_DATA_DIR=$TEMP\mc-smoke-19-*`, `MC_PORT=5377`, BOM-less seeds via
  `[IO.File]::WriteAllText`, taskkill-as-own-command): heartbeat 200 ·
  guide/ask {} → 400 question-required (route live, NO claude spawn) ·
  scribe-stats 200 returns seeded counters · memory/search 200 ·
  walkthrough 200 end-to-end (README/AGENT_RULES seeded in temp ws;
  AGENT_RULES references the worktree path = _SERVER_DIR wiring proven;
  clayrune.json in temp DATA_DIR) · enforcer-state last_run=0 ·
  system/loops lists session-label-enforcer + update-check (the latter
  only after its 60s boot delay — `state._UPDATE_CHECK_BOOT_DELAY_S`;
  poll past it before calling this check failed) · taskkill /T /F killed
  the tree, port 5377 free, live :5199 untouched ✓
- **Landmines for 1.10 (hivemind):** (a) main region ≈ 8278–9929 (9
  `# ── Hivemind…` sections) but **one straggler route lives outside it**:
  `/api/hivemind/<id>/runs` at ~10152 sits in the trigger-aware run-history
  section — the 28-route inventory assert must span both. (b) Outside-region
  `_hm_build_worker_context` hits at ~3008/3051 are DOCSTRING mentions only
  — no shim needed for it. (c) Real inbound touch points to shim/keep:
  `_start_hivemind_orchestrator()` (~12771 startup) +
  `_hm_reconcile_stale_on_startup()` (~12809 startup) +
  `atexit.register(_hivemind_orchestrator_stop.set)` (~11847 — the stop
  event already lives in mc/state; KEEP the atexit.register in server.py,
  1.8's LIFO-ordering lesson). (d) Cross-family wires to expect:
  `load_project` ×9, `get_manager` ×3, `_resolve_claude` ×3,
  `_register_process` ×3, `_read_agent_stream*` ×2 (dispatch),
  `_dispatch_agent_internal` ×1 (the orchestrator dispatches real agents —
  the deepest 1.12 coupling), `_log_agent_activity` ×2, `CONFIG.get` ×4
  (→ `state.CONFIG`), `now_iso` ×29 (mc.core). (e) hivemind state ×7 is
  already in mc/state since Phase 0 — import, don't re-declare.
- **Commit:** `PENDING` on `wt-1.9` (orchestrator merges).

## 1.10 — hivemind_routes blueprint + orchestrator-loop obs (2026-06-10)

- **What moved:** 1,698 lines from TWO regions (splice-asserted: exactly 28
  routes + 61 defs, byte-equality re-checked at cut time) →
  `mc/blueprints/hivemind_routes.py` (1,586 lines): **28 routes** — the 27
  of the main region (9 `# ── Hivemind…` sections: data layer, management +
  workstream CRUD, worker context builder & spawn, orchestrator CLI dispatch,
  message bus + SSE stream, knowledge base, escalation/intervention, server
  orchestrator loop) + the `/api/hivemind/<id>/runs` straggler from the
  trigger-aware run-history section (1.9's landmine (a) — confirmed, moved
  with its family). The `import shutil` inside hivemind_delete moved inline
  verbatim.
- **1.9 terrain note corrected:** `_dispatch_agent_internal` is **NOT used
  by this family** — the claimed ×1 is schedule_run_now's call site
  (~10092 pre-cut). Worker + orchestrator spawns Popen directly and use
  `_read_agent_stream`; nothing here wires the deep dispatcher. Verified
  counts: load_project ×4 call sites, get_manager ×3, _register_process ×3
  (one as `rt.dispatch(register_process=…)` kwarg), _read_agent_stream ×2,
  _resolve_claude ×2, _log_agent_activity ×2, CONFIG.get ×3 (brief said 4)
  → `state.CONFIG.get`, now_iso ×29 + time_ago ×1 + _log ×7 → mc.core.
- **Seams:** `wire(...)` = 13 fn slots (load_project [1.11]; get_manager,
  _register_process, _read_agent_stream, _resolve_claude,
  _sysprompt_file_args, _sysprompt_cleanup, _hide_windows_delayed [1.12];
  _log_agent_activity, _load_agent_log, _enrich_run_entries [agent-log/
  run-history family]; _clayrune_universal_capabilities,
  _clayrune_api_reference [feed _build_agent_context too — 1.12]) + 4
  const slots (PORT, _POPEN_FLAGS, _STARTUPINFO, hivemind_dir →
  HIVEMIND_DIR wired placeholder, its module-level `.mkdir` moved into
  wire() — 1.6/1.7 pattern). `agent_runtime` imported directly by the
  blueprint (top-level module, 1.3 precedent). State: imports 5 of the
  Phase-0 hivemind ×7 (orch set/lock, sse queues/lock, stop Event);
  `_hivemind_sessions`/`_hivemind_lock` are NOT used by this family (only
  system_routes' restart-blocker check reads them) — no dead imports.
- **Stanza placement (the 1.2 lesson, paid again):** the wire stanza sits at
  the STRAGGLER's site (post-schedule_runs), NOT at the main-region
  tombstone — `_enrich_run_entries` is defined between the two regions and
  wiring at the main site would NameError at import. Inbound shims (2):
  `_start_hivemind_orchestrator` + `_hm_reconcile_stale_on_startup`
  (startup call sites under `__main__` unchanged).
  `atexit.register(_hivemind_orchestrator_stop.set)` stays verbatim in
  server.py (LIFO exit-hook ordering — 1.8's lesson; the Event lives in
  mc/state.py since Phase 0).
- **Phase 2:** `_hivemind_orchestrator_loop` gains
  `obs.heartbeat('hivemind-orchestrator')` at the top of each iteration
  (1.6/1.7 precedent); live-verified in /api/system/loops at age 7.7s
  (10s tick, no boot delay).
- **Route-grep gotcha (new):** the blueprint docstring originally said
  "@app.route→@bp.route" — the literal `@bp.route` text inflated the
  inventory grep to 211. Reworded to "app-to-bp route-decorator swap".
  When documenting decorator swaps, never write the literal pattern.
- **Phase 5:** new `tests/test_hivemind_routes.py` (47 tests): create
  (inline-ws materialization / decompose-dispatch branch / malformed ×3 /
  404), list+filter, detail shape, update merge, start/pause/stop/delete
  lifecycle + archive + 404s, workstream CRUD + invalid-status 400, spawn
  claude-path (argv shape, worker-context content incl. wired clayrune
  feeders, ledger recorder, agent_sessions bookkeeping, ws flip) + spawn
  non-claude runtime-routing branch (fake `_agent_runtime` registry,
  context-prepend) + spawn 404s/400, handoff md + open-questions + artifact,
  bus post/finding_report/poll/history + SSE stream (real generator: push →
  data event → close → queue unregistered), knowledge synthesis GET/PUT/
  notify_only + decisions/findings/question-resolve, escalate/intervene/
  review/approve, runs straggler (role/ws filters, pagination, malformed
  params), `_hm_reconcile_stale_on_startup` direct (stale flip + paused
  untouched), 401 auth-reject + loopback twin. Patches
  `mc.blueprints.hivemind_routes.*` only; recorder `subprocess` AND
  `threading` namespaces (threads recorded, never run); fixture
  snapshot/clears/restores agent_sessions + _hivemind_orchestrating +
  _hivemind_sse_queues (1.8 pollution lesson). /api/processes untouched →
  pid-reaper fixture not needed.
- **SSE test gotcha (new):** werkzeug's test client pulls the generator's
  FIRST chunk during `.get()` (start_response trigger) — with an empty
  queue that pull blocks ~15s until the tick-50 `: heartbeat`. Fix: no-op
  the module's `_time.sleep` for that test and scan past comment chunks to
  the first `data:` event.
- **Smoke spawn-safety (new discipline for hivemind smokes):** the
  orchestrator loop auto-spawns REAL workers for any active hivemind with
  ready workstreams every 10s. The scratch project is seeded with a
  NONEXISTENT `project_path` so `_hm_auto_spawn_workers`' `is_dir()` check
  skips it — create/detail/runs/delete all exercise the family with zero
  real claude spawns (`/api/processes` returned `[]` throughout).
- **Gates:** routes 210/210 (89 app-grep [88 real + the 1.7-noted f-string
  line] + 121 bp; url_map arithmetic closes: 85 unconditional app + 121
  mc-bp + 2 marketing_preview + 1 static = 209 rules, the 3
  MC_REMOTE_LOCAL_MOCK conditionals register only under the env flag) ✓ ·
  `import server` + 28 hivemind rules in url_map ✓ · ruff E9/F821 ✓ ·
  pyright mc/ 0 (no new typing-debt tags needed) ✓ · full pytest exit 0
  (codex env skip only) ✓ · isolated smoke (`MC_DATA_DIR=$TEMP\
  mc-smoke-110-*`, `MC_PORT=5377`, BOM-less seeds via
  `[IO.File]::WriteAllText`, port-free asserts both ends,
  taskkill-as-own-command; NOTE: PS 5.1 Start-Process has no -Environment
  — boot via bash): heartbeat 200 · /api/hivemind/list 200 · create →
  detail → runs(200) → DELETE → 404 + `_archived/` move, all in the temp
  data dir (wired HIVEMIND_DIR proven) · system/loops shows
  hivemind-orchestrator (age 7.7s) + session-label-enforcer · prior
  blueprints 8/8 200 (local-auth/status, push vapid, skills, mcp,
  distiller loop-health, terminal status, remote enforcer-state, guide
  scribe-stats) · enforcer 0 revoked/no error · stderr error-free ·
  taskkill /T /F killed the tree, port 5377 free, live :5199 untouched ✓
- **server.py:** 11,414 → 9,986 lines (−1,698 deleted / +46 stanza+tombstone).
- **Landmines for 1.11 (projects — 48 routes, the core CRUD):**
  (a) Family fn homes in the NEW server.py: `load_project` :1589,
  `save_project` :1596, `load_projects` :1623 (the LOAD-BEARING sidecar
  suffix-exclusion lives here — `_agent_log.json`/`_scribe_stats.json`;
  `tests/test_load_projects_sidecar_exclusions.py` pins it),
  `delete_project` :1881 (route handler + deleter in one — its body calls
  the 1.8 inbound shim `_kill_terminal_session`). `update_project` (POST
  /api/project/<id>, create+update with auto-workspace) :1729.
  (b) Wire seams that re-home to the projects blueprint when it lands —
  EIGHT stanzas pass projects-family deps: guide :1702-1704
  (load+save+DATA_DIR), distiller :1935 (load+DATA_DIR), hivemind :8524
  (load), skills :9206 (load+load_projects), mcp :9220-21
  (load+save+DATA_DIR), push_mobile :10858 (load), system :10898-99
  (load+load_projects+DATA_DIR), terminal :10928 (load). remote +
  local_auth pass none.
  (c) DATA_DIR itself (`_DATA_ROOT / 'data' / 'projects'` :419 + mkdir) is
  the projects family's wired-placeholder candidate.
  (d) The plan's "48 /api/project" count includes routes that belong to
  OTHER families feature-wise (agent/* under the project prefix are 1.12;
  memory GET/PUT/append trio ~:9153-9178 is 1.11/1.12 per 1.9's scoping
  call (b); backlog/github/code-sync sit between the CRUD pieces) — expect
  the same boundary-assert discipline, the family is NOT contiguous.
  (e) `_project_live_agent` (status resolver) is dispatch-coupled — check
  its callers before assuming it travels with projects.
- **Commit:** `PENDING` on `wt-1.10` (orchestrator merges).

## 1.11 — project_routes blueprint (2026-06-10)

- **What moved:** 1,030 lines from NINE regions (byte-verified: all 927
  non-empty source lines re-found in the blueprint modulo the two documented
  mechanical rewrites — route-decorator swap + 3× `CONFIG.get` →
  `state.CONFIG.get`) → `mc/blueprints/project_routes.py` (1,209 lines):
  **32 routes** (plan said 48 — that count included /api/project-prefixed
  routes that already moved with their feature families [mcp-enabled 1.4,
  distiller ×2 1.5, terminal/status 1.8, scribe-stats + memory/search 1.9]
  or stay for 1.12 [the 11 agent/* routes + transcript/reconstruct/
  search-chats/conversations/plans]): `/api/projects`, POST + DELETE
  `/api/project/<id>`, generate_summary, import, backlog ×5 (incl. note),
  github ×4, code-sync ×5, attachments upload/delete +
  `/api/attachments/<name>` + `/api/serve-image`, rules ×4 (project +
  shared), the memory editor-CRUD trio, `/api/projects/order` +
  `/api/grid-layout`. Plus the store core (`load_project` / `save_project` /
  `load_projects` with the **LOAD-BEARING** `EXCLUDED_SIDECAR_SUFFIXES` +
  `_decorate_attachments`), `_project_live_agent` (callers checked: ONLY
  /api/projects — moved, reads agent_sessions from mc.state),
  `_log_agent_activity` (pure project-record activity_log writer — moved;
  ~25 dispatch/github call sites resolve the server.py inbound shim),
  `_append_note_to_backlog_item` (single caller = the note route), the
  upload-quota helpers, `_parse_changelog`, `_validate_project_path`,
  `_IMAGE_EXTS`.
- **Scoping calls (under-move wins, all callers grepped):** (a) memory
  GET/PUT/append trio MOVED — verified pure file-CRUD over
  `_get_memory_path` (wired in); it never touches `_commit_managed_entry` /
  `_get_mem_write_lock`, which stay in server.py untouched. (b)
  `_ensure_incognito_project` STAYS — project-record-shaped but its only 2
  callers are dispatch (/agent/dispatch + /agent/send) → 1.12's call. (c)
  `/api/agent/upload-image` + `_downscale_image_if_huge` STAY (agent-chat
  feature, 1.12) — they consume `_upload_limit`/`_incoming_file_size` via
  inbound shims. (d) `/api/router/stats` STAYS (dispatch telemetry, 1.5/1.9
  precedent). (e) transcript/reconstruct/search-chats/conversations/plans/
  recent-runs/usage STAY — transcript + agent-log machinery, not record
  CRUD. (f) schedule run-now/runs + schedules CRUD STAY (1.13). (g)
  `/api/config` ×2 STAY — global settings + dispatch-coupled
  (`_RESPAWN_TRIGGER_KEYS` flags live Mode B sessions). (h) browse ×2 +
  list-directory + create-folder STAY — generic FS pickers (PROJECTS_BASE
  shared with them). (i) settings/domains ×4 STAY (settings family). (j)
  `/assets/` serve_asset STAYS (app-shell static, _APP_DIR). (k)
  `_log_github_sync_activity` STAYS with the github_sync register() call.
  (l) `_memory_search` STAYS (1.9 decision, read-floor shared).
- **Seams:** `wire(data_dir, data_root, uploads_dir, projects_base,
  shared_rules_path, get_memory_path_fn, resolve_claude_fn, get_manager_fn,
  unregister_process_fn, popen_flags, startupinfo)` — 11 slots, ALL defined
  above the stanza, which sits at the CRUD-core tombstone (~:1473) ABOVE all
  8 re-homed seams (the brief's ordering requirement; deps checked:
  `_resolve_claude`:64, `_get_memory_path`:629, `get_manager`:1182,
  `_unregister_process`:1427 — no late dep needed). Path constants stay in
  server.py (DATA_DIR & co. still read by many families) — the 1.4/1.5
  wire-in pattern, NOT a placeholder move. The blueprint imports
  `github_sync`/`project_sync` DIRECTLY (top-level modules, 1.3 precedent;
  pure-defs modules, verified no import side effects; their `register()`
  wiring calls stay in server.py verbatim at the original positions — same
  module objects via sys.modules) and `_kill_terminal_session`
  CROSS-BLUEPRINT from terminal_routes (the 1.4/1.5
  `_resolve_project_path_or_400` precedent; called at request time only,
  after terminal wire()). State: agent_sessions, terminal_sessions,
  terminal_lock, _backlog_sync_lock imported from mc.state (mutated
  in-place only, never rebound).
- **EIGHT seams re-homed** (1.10's exact list, every fn slot now passes
  `_bp_projects.<fn>`): guide (load+save), distiller (load), hivemind
  (load + log_agent_activity), skills (load+load_projects), mcp
  (load+save), push_mobile (load), system (load+load_projects), terminal
  (load). The github/project-sync `register()` calls keep bare names —
  identical objects through the shims.
- **Inbound shims (8):** `load_project`, `save_project`, `load_projects`
  (dispatch/scheduler/scribe/condense callers — dozens),
  `EXCLUDED_SIDECAR_SUFFIXES` (tests read `server.<name>`),
  `_log_agent_activity` (~25 dispatch sites + `_log_github_sync_activity`),
  `_upload_limit` + `_incoming_file_size` (agent_upload_image) +
  `_project_attachment_usage` (test_p2_2 reads it).
- **Test ports: ZERO.** `test_load_projects_sidecar_exclusions.py` (the
  load-bearing pin) passes unmodified — it reads
  `server.EXCLUDED_SIDECAR_SUFFIXES`/`server.load_projects` through the
  shims, and its `del sys.modules['server']` re-import re-runs wire() so
  the blueprint re-binds to the fresh DATA_DIR. `test_auto_model_router.py`
  passes unmodified — its `s.DATA_DIR = bad` rebind test targets
  `_router_stat` (stays server-side); the exclusion tests read via shims.
  `test_p2_2_upload_quota.py` + `test_telemetry.py` pass unmodified (CONFIG
  in-place mutation flows through the `state.CONFIG` live alias; telemetry's
  DATA_DIR rebinds only feed server-side agent-log fns). 42/42 across the
  four files.
- **Emoji-escape fidelity gotcha (new):** generate_summary's prompt has a
  literal `⚽` ESCAPE in source; bash-heredoc layers collapse `\\u` on
  the way into helper scripts, silently turning fidelity checks/fixes into
  no-ops. Fix scripts built the needle with `chr(92)`. The byte-verify
  caught it.
- **Phase 5:** new `tests/test_project_routes.py` (35 tests): projects list
  (live_agent field + sidecar exclusion + asking>working priority), create
  (auto-workspace) / update (log_msg) / 400 / 409 duplicate-path,
  generate_summary happy via recorder subprocess + claude-missing 500 +
  timeout 504 + 404, delete full-cleanup (attachments + agent_log + session
  kill/unregister + terminal kill via patched module fn) + 404, backlog
  roundtrip + malformed ×5 + note append, github setup/status/disconnect +
  thread-recorded initial sync + 429-rate / 400, code-sync lifecycle + 429,
  attachment upload/serve/delete roundtrip + malformed ×3 + 413 cap,
  serve-image allowlist (200 uploads / 400 / 415 / 403 outside),
  import-from-changelog + malformed, rules roundtrip + invalid-path +
  shared, memory GET/PUT/append + malformed/404 ×5, order + grid-layout +
  OPTIONS 204, 401 LAN reject (handler proven not run) + loopback twin.
  Patches `mc.blueprints.project_routes.*` only; recorder `subprocess` +
  fake `_gh_sync`/`_proj_sync`/`threading` namespaces ON THE MODULE;
  fixture snapshots/clears/restores agent_sessions + terminal_sessions
  in-place (1.8 lesson). Windows gotcha: close the test-client response
  after GET /api/attachments before deleting the file (send_file holds the
  handle → WinError 32).
- **Gates:** routes 210/210 (57 app-grep [56 real + the 1.7-noted f-string
  line] + 153 bp; url_map 209 rules = 53 unconditional app + 153 mc-bp + 2
  marketing_preview + 1 static, the 3 mock-CP conditionals env-gated) ✓ ·
  `import server` + 32 project_routes rules in url_map ✓ · ruff E9/F821 ✓ ·
  pyright mc/ 0 (zero new typing-debt tags) ✓ · full pytest exit 0 — 591
  passed, 1 codex env skip ✓ · isolated smoke (`MC_DATA_DIR=$(mktemp -d)`,
  `MC_PORT=5377`, BOM-less config seed via bash printf, port-free asserts,
  taskkill /T /F): heartbeat 200 · GET /api/projects renders the seeded
  scratch project WITH live_agent field · POST create (auto-workspace
  materialized) → POST update (log_msg in activity_log) → DELETE → backlog
  404 · backlog GET/POST · rules/shared + memory + grid-layout 200 · prior
  blueprints 9/9 200 (local-auth/status, push vapid, skills, mcp, distiller
  loop-health, terminal status, remote sessions/enforcer-state
  [NOTE: full path is /api/remote/sessions/enforcer-state — the bare
  /api/remote/enforcer-state used in earlier entries' shorthand 404s],
  guide scribe-stats, hivemind list) · enforcer last_run=0/no error (seed
  parsed) · system/loops shows hivemind-orchestrator +
  session-label-enforcer + update-check · boot.log error-free · tree
  killed, port 5377 free, live :5199 untouched ✓
- **server.py:** 11,173 → 10,147 lines.
- **Landmines for 1.12 (agent_dispatch — orchestrator does it inline):**
  (a) Fn homes in the NEW server.py: providers/auth region 1739–2273
  (`/api/agent/upload-image`:1739 + `_downscale_image_if_huge`, providers
  :1813, provider auth :1908–2152, claude auth :2139–2152);
  `_build_agent_context`:2334; stream readers `_read_agent_stream`:2783 +
  `_read_agent_stream_b`:2967; `_revive_from_agent_log`:3694;
  `_log_agent_completion`:4305; `_dispatch_agent_internal`:5747; the agent
  route block 6072–7419 (dispatch/send/stream/followup:6377 [the 492-line
  one — move WHOLE]/stop/interrupt/session/plan-file/status/
  guardian-reset/log); plan-file pair :7225/:7247; transcript :7451 +
  reconstruct :7473; recent-runs :7667; search-chats :7859; conversations
  :7880; plans :8008; usage :8056. The memory/scribe/condense machinery
  interleaved through 2334–6071 (`_write_session_memory`:4094 etc.) is NOT
  dispatch — boundary-assert every cut.
  (b) MY wire slots 1.12 re-homes when those fns move:
  `resolve_claude_fn`, `get_manager_fn`, `unregister_process_fn` (also in
  system/terminal/hivemind stanzas). `get_memory_path_fn` does NOT re-home
  at 1.12 (scribe/condense stays).
  (c) When agent_upload_image moves: kill the `_upload_limit` /
  `_incoming_file_size` server shims by cross-importing from
  project_routes (keep `_project_attachment_usage` shim — test_p2_2 reads
  it off server).
  (d) `_ensure_incognito_project`:442 + INCOGNITO_PROJECT_ID go with
  dispatch (only callers: dispatch:~5770s + send). `p.get('_is_incognito_
  project')` checks are scattered through scribe/condense too — those are
  dict-key reads, not the fn.
  (e) `_log_agent_activity` callers in the dispatch family resolve the
  server shim today; 1.12 should cross-import from project_routes (the
  fn's home), not re-wire.
  (f) schedule_run_now:7517 calls `_dispatch_agent_internal` — the
  dispatch/scheduler boundary; decide whether the schedule routes ride
  with 1.12 or wait for 1.13 (they sit between transcript and recent-runs).
  (g) `/api/config` PUT flags `_needs_respawn` on live Mode B sessions —
  dispatch-coupled but left in server.py; agent_followup reads the flag.
  (h) The f-string doc line is now :2273 (inside
  `_clayrune_universal_capabilities`, which 1.10 wired into hivemind and
  1.12 will share via `_build_agent_context`) — grep-count arithmetic
  unchanged.
- **Commit:** `PENDING` on `wt-1.11` (orchestrator merges).

## PAUSED — Anthropic monthly spend limit hit (2026-06-10 ~00:30)

Both running workers died with "You've hit your monthly spend limit". All
MERGED work is safe and live; nothing in flight was lost that matters.

**State at pause:** backend 11/13 blueprints merged, server.py 10,147 lines,
routes 210/210, full pytest green. Frontend modules 1–9 merged, index.html
15,414 lines. All gates green at every merged step.

**To resume (in order):**
1. RELAUNCH 1.12 agent_dispatch worker — the full brief is in the orchestrator
   session transcript; key points: 1.11's landmine map (above) + the
   load-bearing stay-list (ALL scribe/condense/checkpoint/memory-write
   machinery stays; wire every call) + move agent_followup WHOLE.
   The dead worker's PARTIAL UNCOMMITTED work sits in
   `.claude/worktrees/agent-ad5012f1e01197183` (branch wt-1.12, no commit) —
   inspect for reusable splice scripts in its _scratch/, else discard.
2. Then 1.13 scheduler (small): loop + thread-start-once + CRUD + run-now
   `_dispatch_agent_internal` shim + obs.heartbeat('scheduler').
3. Then stragglers mop-up vs the <2,000 target.
4. Track B queue: search-past-chats 849 (module-10 worker died pre-work),
   MCP UI ~1128 (two segments), hivemind UI ~1294 (run-history boundary
   risk), palette 520, system-status 456, scheduler 377, update 360,
   provider settings ~630 — then the agent-panel/projects-grid core
   **store.js design checkpoint with Ron** (module 8's sizing + module 7/8
   bridge findings are the input).

## PAUSED by Ron (2026-06-10 morning) — resume at end of trading day

The post-resume relaunches of 1.12 + frontend module 10 died without commits
(limit likely re-tripped). Merged state unchanged: backend 11/13 (server.py
10,147), frontend 9 modules (index.html 15,414), all green and live. The
resumption runbook above (64e8d77) remains exact: relaunch 1.12 worker →
1.13 → mop-up; relaunch module 10 → mechanical queue → store.js checkpoint.

## 1.12 — agent_dispatch blueprint — COMPLETED (2026-06-10, orchestrator-finished)

Resumed by the orchestrator (no fresh worker). The dead 1.12 worker's
**uncommitted extraction was salvaged**, not redone: worktree
`agent-ad5012f1e01197183` (detached `c4db02b`) held a complete
`mc/blueprints/agent_routes.py` + spliced `server.py`. `c4db02b` is an
ancestor of tip `47d490e` and **`server.py` is byte-identical between them**
(zero commits touched it) → the splice applied to current tip with zero
drift. Transplanted the two files verbatim; everything below is validation +
finish work the worker died before doing.

- **What moved:** 33 routes (the whole agent dispatch/stream/followup/stop/
  interrupt/session/status/guardian-reset/log + transcript/reconstruct/
  recent-runs/conversations/plans/usage/router-stats + provider & claude auth
  + upload-image + plan-file families) → `mc/blueprints/agent_routes.py`
  (~5,830 lines). `agent_followup` moved WHOLE (the 492-line one).
- **Stay-list honored:** ALL scribe/condense/checkpoint/memory-write
  machinery stays in server.py and is late-bound via
  `wire(*, data_dir, …, scribe_call_fn, write_session_memory_fn,
  dispatch_condense_fn, …)` (28 slots). server.py calls `_bp_agent.wire(...)`
  then `register_blueprint(_bp_agent.bp)`.
- **Re-homing (the 1.11 landmine, point b):** functions whose HOME moved to
  agent_routes are re-exported back onto server.py as module-level aliases
  (`_resolve_claude`, `get_manager`, `_dispatch_agent_internal`,
  `_load_agent_log`, `_route_dispatch_model`, `_mcp_server_catalog`,
  `_resolve_project_mcp_config`, … ~33 names) so the staying machinery +
  scheduler (1.13's `_dispatch_agent_internal`) still resolve them; the other
  blueprints' `wire()` calls (hivemind/system/terminal/mcp) now pull their
  `get_manager_fn`/`resolve_claude_fn`/`register_process_fn` from `_bp_agent`.
- **Typing debt:** 19 verbatim-moved lines tripped pyright basic (the
  wire-slot `Optional[Callable]` None-default pattern → `reportOptionalCall`,
  plus tuple-vs-Response unions + Optional `.write/.flush/.pid`). Each tagged
  `# pyright: ignore[<rule>]  # moved-verbatim typing debt (1.12)` (the 1.2
  convention) — agent_routes.py back to **0 pyright errors** (remaining 23 =
  the pre-existing distiller/agent_runtime baseline).
- **Phase-5 test porting (the Phase-0 monkeypatch landmine, 8 failures fixed):**
  tests that patched `server.X` for functions/deps that moved now patch
  `mc.blueprints.agent_routes.X`:
  - `test_auth_routes.py` — `_launch_terminal_for_binary` (moved, not
    re-exported → server has no such attr).
  - `test_auto_model_router.py` — all 10 `_scribe_call` patches (the 4
    "passing" picks were silently making REAL classifier API calls that
    happened to agree; 2 fail-opens failed because the real classifier
    returned a valid token instead of raising/garbling — now deterministic).
  - `test_mcp_trim.py` — `_stub_catalog` helper + the inline `_boom` in
    `test_resolve_fails_open` (both patch `_mcp_server_catalog`).
  - `test_telemetry.py` — `/api/usage` globs `agent_routes.DATA_DIR`; the
    test's `server.DATA_DIR = data_dir` override now mirrored onto the
    blueprint.
- **New `tests/test_agent_routes.py` (8 tests):** registration parity (every
  one of the 33 routes is present AND owned by the `agent_routes` blueprint,
  pinned both ways so scope creep on re-merge is caught), read-only loopback
  smokes (providers/usage/router-stats/recent-runs prove wire() bound the
  global deps), and the app-wide LAN auth-gate contract (401 before handler).
- **Gates:** url_map **209 rules** unchanged (AST: 209 route decorators both
  sides; server.py 56→23, agent_routes 33; zero new duplicate paths, zero
  route loss) ✓ · `import server` + `agent_routes` registered (13 blueprints)
  ✓ · ruff E9/F821 clean ✓ · pyright agent_routes 0 errors ✓ · **full pytest
  green** (8 ported + 8 new) ✓ · boot smoke `MC_PORT=5377` heartbeat 200 +
  providers/usage/router-stats/recent-runs all 200, boot.log error-free, live
  :5199 untouched ✓
- **server.py:** 10,147 → **4,676 lines**.
- **Landmines for 1.13 (scheduler):** `_dispatch_agent_internal` is now a
  server.py alias of `_bp_agent._dispatch_agent_internal` — the scheduler's
  run-now path resolves it through that alias (no re-wire needed). The
  `/api/schedules*` CRUD routes (4) still sit in server.py (`get_schedules`/
  `create_schedule`/`update_schedule`/`delete_schedule`) — they move with
  1.13 along with `_scheduler_loop` + the thread-start-once guard +
  `obs.heartbeat('scheduler')`.
- **Commit:** `5e30819` on `refactor/backend` (this entry shipped in the 1.12
  commit; SHA backfilled at 1.13).

## 1.13 — scheduler_routes blueprint + scheduler-loop obs (2026-06-10)

- **What moved:** 685 source lines from FOUR regions (byte-verified: all 638
  non-blank source lines re-found in the blueprint at ≥ source multiplicity,
  modulo the two documented mechanical rewrites) →
  `mc/blueprints/scheduler_routes.py` (806 lines): **6 routes** — POST
  `/api/schedule/<id>/run-now` (schedule_run_now) + GET `/api/schedule/<id>/runs`
  (schedule_runs) + GET/POST `/api/schedules` (get_schedules/create_schedule) +
  PUT/DELETE `/api/schedules/<id>` (update_schedule/delete_schedule). Plus the
  whole `## ── Scheduled Tasks ──` section: cron parser
  (`_parse_cron_field`/`_next_cron_match`), `_compute_next_run`, the background
  `_scheduler_loop` — **which also drives GitHub auto-sync, code-sync
  auto-fetch, stale-session purge, and the process-tracker liveness sweep**, all
  moved verbatim with it (the brief understated the loop; it's multi-purpose) —
  `_start_scheduler` (daemon thread `'scheduler'`, start-once), the continuation
  helpers (`_latest_claude_sid_for_schedule`, `_latest_session_id_for_schedule`,
  `_newest_run_session_id_for_schedule`, `_scheduled_run_marker`,
  `_scheduled_continue`), and the schedules store (`_load_schedules`/
  `_save_schedules` — these live at server.py :739/:747, FAR from the rest;
  the brief's line hint was right and they'd have been missed without the AST
  free-name pass — F821 caught the omission on the first build).
  The family is NOT contiguous (R0 :739–749 store; R1 :2881–2981 run-now/runs;
  R2 :3347–3703 Scheduled-Tasks section; R3 :3706–3932 helpers+CRUD); each
  region was boundary-asserted line-by-line before the cut, and the splice
  re-asserted every first/last line. Layout in the blueprint is reordered to
  R0→R2→R3→R1 so module-level definitions precede their route-level uses
  (F821-clean); function bodies forward-reference freely (call-time lookup).
- **Wire seams (11 slots), derived by AST free-name extraction (not the brief's
  list — proven):** an AST walk of all 18 moved functions collected every
  `Name` load not bound locally; after subtracting builtins, intra-family defs,
  and the blueprint's own top imports, EXACTLY 14 free names remained →
  `app` (becomes `bp`), `_gh_sync`/`_proj_sync` (cross-imported directly — top-
  level modules, no Flask dep / no import side effects, 1.3/1.11 precedent;
  their `register()` wiring stays in server.py), and the **11 wire slots**:
  `schedules_path` (SCHEDULES_PATH — wired placeholder, the 1.7
  SESSION_LABELS_PATH pattern; nothing else reads it), `load_project_fn` +
  `load_projects_fn` + `log_agent_activity_fn` (projects family, 1.11), and the
  agent-dispatch family re-homed onto `_bp_agent` at 1.12 —
  `dispatch_agent_internal_fn` (run-now + cron dispatch), `load_agent_log_fn`
  (runs + continuation reads), `enrich_run_entries_fn` (runs response),
  `get_manager_fn` + `all_managers_fn` + `pid_is_alive_fn` +
  `revive_from_agent_log_fn` (the `_scheduled_continue` revive path + the loop's
  stale-session purge). State imports (mc.state, Phase 0): `_scheduler_stop`,
  `agent_sessions`, `terminal_sessions`, `terminal_lock`,
  `process_tracker_lock`, `tracked_processes`. core: `_log`, `now_iso`
  (`time_ago` NOT used → not imported). **Inbound shims / re-export aliases:
  ZERO** — grep proved the only server.py caller of any moved name outside the
  family is the startup `_start_scheduler()` site (rewritten to
  `_bp_sched._start_scheduler()`, the brief's instruction; `_start_hivemind_
  orchestrator` at the adjacent line stays its own server.py alias from 1.10).
- **Stanza placement:** wire()+register_blueprint sit at the R2 tombstone
  (~:3220) — after `_bp_projects` (:1047) and `_bp_agent` (import :1032,
  register :2817), before the startup block. `atexit.register(_scheduler_stop.
  set)` stays VERBATIM in server.py (the Event lives in mc/state.py since
  Phase 0; LIFO exit-hook ordering — the 1.8/1.10 lesson).
- **Phase 2:** `_scheduler_loop` gains `obs.heartbeat('scheduler')` as the
  first statement inside `while not _scheduler_stop.is_set():`, before the
  `try:` — once per iteration (1.6/1.7/1.10 placement). The ONLY intentional
  behavior addition; everything else byte-verbatim. Live-verified in
  /api/system/loops at age 1.5s (30s tick, no boot delay), alongside
  hivemind-orchestrator + session-label-enforcer.
- **Typing debt, explicit:** 3 tags
  `# pyright: ignore[reportOperatorIssue]  # moved-verbatim typing debt (1.13)`
  on `_scheduler_loop`'s `now - last_dt` (×2, github/code-sync blocks) and
  `ts < cutoff` (purge) — pyright flow-analysis sees `now`/`cutoff` as possibly
  Unbound because they're assigned inside the loop's `try:` scope; verbatim
  from server.py, zero behavior change. scheduler_routes.py back to 0 pyright
  errors (the pre-existing 23 distiller/agent_runtime baseline unchanged).
- **Phase 5:** new `tests/test_scheduler_routes.py` (19 tests): registration
  parity (all 6 routes present AND owned by the blueprint, pinned both ways) +
  blueprint-registered, GET /api/schedules empty + populated (name enrichment),
  POST create happy (201 + persisted) + malformed ×3 (400, nothing written),
  PUT update merge + 404, DELETE + 404, run-now via a `_DispatchRecorder`
  (asserts trigger_type/trigger_id metadata, last_run stamp, **NO real spawn**)
  + 404 + missing-project/task 400 + dispatch-failure 500, /runs pagination
  over a seeded agent_log (trigger filtering, two pages, total) + 404 +
  malformed-params-default, and the app-wide LAN auth-gate 401 (handler not
  reached). Patches `mc.blueprints.scheduler_routes.*` ONLY (test-port rule);
  SCHEDULES_PATH→tmp, load_project(s)/dispatch/agent-log→fakes-on-the-module,
  restored after. /api/processes untouched → no pid-reaper fixture needed.
- **Gates:** url_map **209 rules** unchanged (routes relocated, count
  preserved) + `scheduler_routes` registered (14 blueprints) ✓ · `import
  server` ✓ · ruff E9/F821 clean ✓ · pyright scheduler_routes 0 errors (full
  scope 23 = baseline) ✓ · **full pytest exit 0** — 618 passed, 1 codex env
  skip (619 collected; captured pytest's OWN exit code, not a pipe's) ✓ ·
  isolated smoke (`MC_DATA_DIR=_scratch/smoke113` [Windows-resolvable path so
  bash + Win-Python agree], `MC_PORT=5378`, BOM-less config seed,
  CLAUDE_SKIP_AUTH_CHECK=1, FROM THE WORKTREE): heartbeat 200 · /api/schedules
  200 `[]` · /api/system/loops shows `scheduler` fresh (age 1.5s, `last_ok`
  set — obs.heartbeat firing) next to hivemind-orchestrator +
  session-label-enforcer · `taskkill /T /F` killed the tree (3 procs), port
  5378 free, live :5199 untouched (PID 35552) ✓
- **server.py:** 4,676 → **4,010 lines** (git: 31 insertions / 697 deletions).
- **Surprises / notes:** (a) `_scheduler_loop` is far more than a scheduler —
  it's the catch-all 30s housekeeping loop (scheduler dispatch + github sync +
  code sync + session purge + process-tracker sweep). All moved verbatim; the
  obs heartbeat covers the whole loop's liveness. (b) The schedules store
  (`_load_schedules`/`_save_schedules`) sits ~2,600 lines above the rest of the
  family — the AST free-name pass (not the brief's enumerated list) is what
  guaranteed completeness; F821 would have caught a miss but the analysis
  caught it first. (c) ZERO inbound re-export aliases — unlike 1.12, nothing
  downstream in server.py calls a moved scheduler helper except the one startup
  thread-starter, which the brief already specified rewriting.
- **Commit:** PENDING on `wt-1.13` (orchestrator merges).
