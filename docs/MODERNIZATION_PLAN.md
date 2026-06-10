# Clayrune Modernization Plan — v1 (2026-06-09)
_Baseline: post-v2.0.2 (all inspection directives D1–D8 merged)._
_Goal: decompose the two monoliths (`server.py` 18,051 lines / 209 routes; `static/index.html` 25,162 lines), add an observability layer, and gate with type checking — **without any behavior change and without a rewrite**._

> Provenance: authored 2026-06-09 (uploaded as `agent_e5a9e80c17.md`), persisted here so it
> isn't orphaned in volatile `data/uploads/`. Execution split into parallel tracks is in
> [`MODERNIZATION_TRACKS.md`](MODERNIZATION_TRACKS.md) — read that for who-does-what.

## Why (one paragraph)
The bottleneck is no longer code quality; it's that parallel Claude Code sessions collide on two giant files, and background loops fail silently. Every step below is sized for **one agent session**, is **independently shippable**, and leaves the app fully working. If a step goes sideways, revert that one commit.

## Rules of engagement (apply to every phase)
1. **Moves only, never edits-while-moving.** A function relocates verbatim; behavior changes are separate commits.
2. **Route paths, methods, and JSON shapes are frozen.** `grep -c "@app.route\|@bp.route"` total must equal 209 before and after every step.
3. One blueprint (or one frontend module) per session/PR. Stage only the files listed, by explicit path.
4. After every step: `pytest -q`, `ruff check --select E9,F821 .`, then boot smoke test (**corrected endpoint** — `/api/system/health` does not exist; the real liveness route is `/api/system/heartbeat`, server.py:16938):
   `python server.py & sleep 4 && curl -sf localhost:<PORT>/api/system/heartbeat >/dev/null && echo SMOKE_OK`
5. Do not introduce an app factory, DI framework, or async rewrite. Plain Flask blueprints registered on the existing `app`.

---

# Phase 0 — Scaffolding (1 session)

Create the package that everything extracts into:
```
mc/
  __init__.py          # empty
  state.py             # shared globals + their locks (moved verbatim from server.py)
  core.py              # cross-cutting helpers: _log, time_ago, now_iso, path guards,
                       #   _is_loopback_request, json state load/save helpers
```
Procedure:
- Identify the ~44 module-level globals and their `threading.Lock`s in `server.py`; move declarations to `mc/state.py`; in `server.py` replace with `from mc import state` and rewrite references as `state.<name>` **only in the smallest set of functions needed to keep this step compiling** — the rest migrate as their blueprints move (use `from mc.state import X` shims at the top of server.py so existing bare names keep working: `globals().update(...)` is forbidden; explicit import-as lines only).
- Move the small pure helpers into `mc/core.py`; leave `from mc.core import time_ago, now_iso, ...` shims in `server.py` so nothing else changes.
- Add `mc/` to pyright scope (see Phase 4) from day one — new code starts clean.

Acceptance: route count 209, pytest green, smoke OK.
Commit: `refactor(scaffold): mc package with shared state + core helpers (no behavior change)`

# Phase 1 — Blueprint extraction (one per session, in this order)

Order = ascending coupling. Each step: create `mc/blueprints/<name>.py` with `bp = Blueprint(...)`, move the route handlers + their private helper functions (the `<family>_*` functions) verbatim, register in `server.py` with `app.register_blueprint(bp)`, delete the originals.

| # | Blueprint | Routes | Function family | Notes |
|---|-----------|--------|-----------------|-------|
| 1.1 | `local_auth` | 3 (`/api/local-auth`) + before_request hooks | `_local_auth_*` (~15 fn) | **Pilot** — smallest, and `tests/test_auth_routes.py` is its safety net. The two `before_request` handlers stay registered on `app` but their bodies move to this module. |
| 1.2 | `push_mobile` | 7 `/api/push` + 6 `/api/mobile-pair` | `push_*`, `mobile_*` | Self-contained FCM/webpush + pairing. |
| 1.3 | `skills` | 12 `/api/skills` | `skill*` glue | Thin — `skills.py` already holds the logic; routes are wrappers. |
| 1.4 | `mcp` | 6 `/api/mcp` | mcp glue | Same pattern; logic already in `mcp.py`/`mcp_installer.py`. |
| 1.5 | `distiller_routes` | 5 `/api/distiller` | distill glue | Logic already in `distiller.py`. |
| 1.6 | `system` | 11 `/api/system` + 3 `/api/processes` | `system_*` | Health/processes/settings adjacents. |
| 1.7 | `remote` | 12 `/api/remote` + `/_mc` | `remote_*`, `mc_*` | Pairs with `mc_remote/` package. |
| 1.8 | `terminal` | 5 `/api/terminal` | terminal fns | Includes the loopback-gated `launch`; re-verify the 403 behavior in acceptance. |
| 1.9 | `guide_scribe` | `/api/guide`, walkthrough | `scribe_*`, `guide_stream` | `guide_stream` is 156 lines — move verbatim, do not split. |
| 1.10 | `hivemind` | 28 `/api/hivemind` | `hm_*` + `hivemind_*` (~60 fn) | Biggest cohesive family (~2–3K lines). Budget a full session; includes `_hm_dispatch_orchestrator`. |
| 1.11 | `projects` | 48 `/api/project` | `get/save/load/update/delete_*` project fns | The core CRUD. Do after the registry shims in `mc/state.py` are battle-tested by 1.1–1.10. |
| 1.12 | `agent_dispatch` | 9 `/api/agent` + 3 `/api/claude` | `agent_*`, `_dispatch_*`, `_read_agent_stream*`, `_build_agent_context`, `_revive_from_agent_log` | **Most coupled — do last.** This is where the 492-line `agent_followup` lives. Move it whole; decomposition of that function is explicitly out of scope for this plan. |
| 1.13 | `scheduler` | schedule routes | `_scheduler_loop` + schedule CRUD | Move the loop + its start call; verify the thread still starts exactly once. |

After 1.13, `server.py` should be reduced to: app creation, blueprint registration, static serving, startup, and whatever resisted extraction. Target: **under 2,000 lines**.

# Phase 2 — Observability layer (folded into the backend track; see TRACKS doc)

1. `mc/obs.py`:
   - `log(subsystem: str, msg: str, level="info")` — single-line structured output (`[ts] [subsystem] msg`), wraps the existing `_log`.
   - `heartbeat(subsystem: str)` — records `last_ok[subsystem] = now` in `mc/state.py`.
2. Instrument every background loop (`_scheduler_loop`, stream readers, tunnel supervisor poll, any watchdog) with `heartbeat()` at the top of each successful iteration.
3. New route `GET /api/system/loops` → `{subsystem: {last_ok, age_seconds}}`. Frontend can later render staleness badges; not required now.
4. Apply the CLAUDE.md exception-swallowing policy opportunistically while touching these files (subprocess/file-I/O/network excepts gain a `log(...)`).

Acceptance: every long-lived thread appears in `/api/system/loops` within 2 minutes of boot.

# Phase 3 — Frontend decomposition (parallel track, one module per session)

No build step. Convert inline script to native ES modules:
```
static/
  index.html           # shell: markup + <script type="module" src="/static/js/main.js">
  js/main.js           # bootstrap + wiring only
  js/api.js            # all fetch() wrappers
  js/<feature>.js      # one per UI region: projects-grid, agent-panel, hivemind,
                       #   terminal, skills, settings, remote-pairing, walkthrough
  css/app.css          # extracted from inline <style>
```
Rules: extraction order mirrors Phase 1 (smallest feature first); shared mutable UI state goes in `js/store.js` (plain object + tiny pub/sub, no framework); the service worker (`sw.js`) cache list must be updated in the **same commit** as each file split, and bump its cache version string each time or PWA clients will serve stale shells.

Acceptance per step: hard-refresh loads, feature works, no console errors, `sw.js` version bumped.

# Phase 4 — Type checking gate (1 session, do early — right after Phase 0)

1. `pip install pyright` (or pin in requirements-dev.txt); add `pyrightconfig.json`:
   ```json
   { "include": ["mc", "agent_runtime.py", "db.py", "skills.py", "mcp.py", "distiller.py"],
     "typeCheckingMode": "basic", "reportMissingImports": false }
   ```
   `server.py` joins `include` only after Phase 1 shrinks it.
2. Add a CI job (alongside the pip-audit job from D8): `pyright --outputjson || true` initially; flip to blocking once the baseline is clean.
3. Policy line for CLAUDE.md: "New/moved modules under `mc/` must pass pyright basic."

# Phase 5 — Opportunistic test hardening (ongoing, not a project)

When a blueprint is extracted, add **one** request-level test file for it if none exists, covering: happy path, auth-rejected path, malformed-input path. Priority order if time-boxed: `terminal` (RCE-adjacent), `agent_dispatch`, `projects`, `local_auth` (exists), everything else best-effort.

---

## Sequencing summary
```
Session 1:  Phase 0 scaffold
Session 2:  Phase 4 pyright (cheap, locks in quality for all later moves)
Sessions 3–15: Phase 1 blueprints 1.1 → 1.13   (interleave Phase 2 after 1.1,
                Phase 3 anytime; both are independent tracks)
Ongoing:    Phase 5 per-blueprint tests
```
~15–18 agent sessions total. At no point is the app broken, and any two sessions working on *different* blueprints/modules no longer touch the same file — which is the point.

## Global acceptance (end state)
```bash
wc -l server.py                      # < 2000
grep -rc "@bp.route\|@app.route" mc/ server.py | awk -F: '{s+=$2} END{print s}'   # 209
pytest -q && pytest control_plane/tests -q
pyright                              # 0 errors in included scope
curl -sf localhost:<PORT>/api/system/loops | python3 -m json.tool   # all loops fresh
```
