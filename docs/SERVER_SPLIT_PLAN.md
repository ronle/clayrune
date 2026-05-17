# server.py split — revised P1-1 plan (evidence-based)

Status: **BLOCKED, analysis-only.** Authored by the plan-v2 execution
agent 2026-05-17. This supersedes `IMPROVEMENT_PLAN_V2.md` §P1-1 where
they conflict (see flaws F1, F2 in `IMPROVEMENT_PLAN_V2_FLAWS.md`).

## Why this is blocked right now

`server.py` (12,541 lines) has **uncommitted WIP from Ron spanning 23
hunks across lines 231–12518** — measured, line-ending-normalized,
2026-05-17. The original plan assumed WIP was confined to a few
subsystems on the freeze list; it is not — it is spread over the whole
file. Splitting a file while it has pervasive uncommitted changes
guarantees conflict pain and risks silently dropping in-flight work.

**Hard prerequisite:** `git status server.py` is clean (Ron's WIP
committed or stashed) before *any* extraction PR. Re-measure WIP spread
before starting; this analysis is a snapshot.

## WIP heat map vs. extraction targets

Ron-WIP hunk starts (original line #): 231, 557, 740, 842, 1501, 2229,
2292, 2332, 3174, 3222, 3244, 3304, 3416, 3462, 3482, 3631, 3652, 3803,
3866, 6977, 8297, 11381, 11846. Mapped onto section banners:

| Proposed module | server.py section (line) | WIP nearby? | Freeze list? | Verdict |
|---|---|---|---|---|
| `marketing_preview.py` | Marketing-site preview (1041) | none | no | **Tier 1 — safest** |
| `process_tracker.py` | Process tracker (923) | none | no | **Tier 1** |
| `scheduler.py` | Scheduled Tasks (9216) | none | no | **Tier 1** |
| `terminal_sessions.py` | Terminal session mgmt (5505) | none | no | **Tier 1** |
| `transcript.py` | transcript helpers (~346–560) | **557** | no | Tier 2 — WIP overlap |
| `condense.py` | Memory condensation (863) | **842** | no | Tier 2 — WIP overlap |
| `agent_session.py` | Agent session tracking (748) | **740, 2229+** | no | Tier 3 — WIP-dense |
| `claydo.py` | Ask Claydo (1061) | 1501 adj. | **yes** (installer/Claydo) | Tier 3 — frozen |
| `hivemind.py` | Hivemind (5928–7527) | **6977** | **yes** (active use) | Tier 3 — frozen |
| `push.py` | Web push / FCM (10280–10900) | adj. 11381 | **yes** (May 16) | Tier 3 — frozen |
| `presence.py` | Dashboard presence (10299) | — | **yes** (May 16, *literally* HEAD) | Tier 3 — frozen |

### F1 resolved
`IMPROVEMENT_PLAN_V2.md` §P1-1 calls push/presence/hivemind/claydo "the
easiest extractions because they're already self-contained." They are
**self-contained but frozen** — the freeze list marks them active
development (presence is literally the HEAD commit). Self-containment ≠
safe to move. They are Tier 3, not first.

## ⚠️ CORRECTION (2026-05-17b) — banner proximity ≠ extractability (flaw F7)

The Tier table above classified modules by *section-banner vs.
WIP/freeze proximity*. It did **not** measure **call-site dispersion**,
and that turns out to be the property that actually governs a safe
verbatim extraction. Measured after Tier 1a shipped:

| Module | Shared mutable state | Call sites | Spread | Verdict |
|---|---|---|---|---|
| `marketing_preview` | none | 1 route, self-contained | 1 region | ✅ **true verbatim Tier 1 — DONE (`f3c083a`)** |
| `process_tracker` | `tracked_processes` dict + lock | `_register_process` ×17, `tracked_processes` ×13, `_unregister_process` ×13 | ~13 of 25 file regions | ❌ NOT verbatim — pervasive |
| `terminal_sessions` | `terminal_sessions` dict + lock | 18 + 7 refs | dispersed | ❌ NOT verbatim |
| `scheduler` | schedule state | `_load_schedules` ×8 | dispersed; `9216` banner is cron-parse utils, not endpoints | ❌ NOT verbatim |

Also: the "Process tracker" / "Terminal session tracking" banners are
immediately followed by **core** `load_project`/`save_project`/
`load_projects`/`time_ago` — the banner does not bound the section.

**Consequence.** Only `marketing_preview` was a clean
move-and-register. The other three are **deps-injection refactors**, not
moves: the module must own the state dict + lock and expose accessor
functions, and every one of the ~30+ dispersed call sites in server.py
(including agent-spawn and the guardian loop — code adjacent to frozen
subsystems) must be rewritten to call the module. That is a real
behavior-risking refactor per module, not a Tier-1 verbatim PR, and it
violates this plan's own "move code verbatim — no opportunistic edits"
rule if attempted as one.

## Revised Tier-1 definition

A module is **verbatim Tier-1** only if it has **(a) no shared mutable
module state** and **(b) all call sites within its own
banner-bounded region**. By that test, of the originally-listed Tier-1
set only `marketing_preview` qualified. The split as a low-risk
mechanical effort is therefore much smaller than the plan implied.

**Recommendation:** treat `process_tracker` / `terminal_sessions` /
`scheduler` as their own *designed* refactors (one per PR, deps-injection
contract written first, call sites migrated deliberately, smoke +
manual agent-spawn/guardian check), each gated on Ron's explicit
go-ahead because they touch the agent lifecycle. Do not auto-proceed.

## Tier 1-stateful (deferred — schedule as its own sprint)

`process_tracker`, `terminal_sessions`, `scheduler` are **not** Tier-1
verbatim moves (F7). They are *designed deps-injection refactors*, one
PR each, with Ron's explicit go-ahead — **not** auto-proceeded. Tracked
here so they can be scheduled as their own sprint later.

### The deps-injection pattern (same shape `github_sync.register()` proven)

Per module `X`:

1. New `X.py` **owns** the state: the dict + its `threading.Lock` move
   into the module as module globals.
2. `X.py` exposes a small typed API over that state — the *only* way
   the rest of the app touches it — plus a `register(app, deps)` that
   (a) registers the routes blueprint and (b) injects the server
   callbacks the module needs (`load_project`, `now_iso`,
   `agent_sessions` accessor, `_log_agent_activity`, …), exactly like
   `github_sync.register(...)` already does.
3. `server.py` keeps a thin re-export shim for the *call-side* names
   (e.g. `from process_tracker import register_process as _register_process`)
   so the ~30 existing call sites change by import, not by hand — this
   keeps the migration diff mechanical and reviewable.
4. Move the routes (`# ── … endpoints`) into the module blueprint.
5. Delete the old state + helpers from `server.py`.

The shim in step 3 is the key risk-reducer: call sites stay textually
identical; only the binding moves. The behavior-risking part is purely
that the state is now one object in one module — verified below.

### Per-module scope (measured 2026-05-17b)

| Module | State to move | Call-site load | Routes | Est. risk |
|---|---|---|---|---|
| `process_tracker` | `tracked_processes` dict + `process_tracker_lock` | `_register_process` ×17, `tracked_processes` ×13, `_unregister_process` ×13 — spread across agent spawn / revival / followup / housekeeping / **guardian loop** | Process Tracker endpoints | **High** — touches every agent-spawn path + guardian |
| `terminal_sessions` | `terminal_sessions` dict + `terminal_lock` | `terminal_sessions` ×18, `terminal_lock` ×7 — incl. the `/agent/status` log-line filter that references `terminal_sessions` | Terminal session mgmt endpoints | Medium-high |
| `scheduler` | `schedules`/`SCHEDULES`/`SCHEDULES_PATH` + `_save_schedules` + the `_scheduler` loop thread | `schedules` ×35, `_scheduler` ×8, `_save_schedules` ×6 | Scheduled Tasks endpoints | Medium (more self-contained than the other two, but 35 `schedules` refs) |

### Verification strategy (mandatory before each merges)

- `pytest -q` green (smoke import catches binding breakage immediately).
- **`process_tracker` only — manual smoke:** start a real agent, confirm
  it appears in Process Manager; let it finish, confirm it deregisters;
  trip the guardian (hung-session path) and confirm it still reaps via
  the moved state. The guardian + agent-spawn coupling is the single
  highest-risk surface in the whole split.
- `terminal_sessions`: open a terminal pop-out, run a command, confirm
  streaming + the `/agent/status` terminal-line filter still works.
- `scheduler`: create a once+interval schedule, confirm it fires and
  `run-now` works; restart and confirm persistence (`SCHEDULES_PATH`).
- Each PR: CHANGELOG entry (Done/Files/Rollback); rollback = revert the
  single PR (the re-export shim makes the revert clean; no schema).

Recommended order when scheduled: `scheduler` (most self-contained) →
`terminal_sessions` → `process_tracker` (do the guardian-coupled one
last, with the most manual verification).

## Revised execution order (when server.py is clean)

One PR per module, no behavior change, smoke test (`tests/test_smoke.py`)
green after each — it already imports `server` so a broken extraction
goes red immediately.

1. **Tier 1 (do these, in order):** `marketing_preview.py` →
   `process_tracker.py` → `scheduler.py` → `terminal_sessions.py`.
   No WIP overlap, not frozen, well-bounded by section banners. Each as
   a Flask blueprint; `register(app, deps)` injection pattern (same shape
   `github_sync.register()` already uses — proven).
2. **Tier 2 (only after Ron confirms the relevant WIP landed):**
   `transcript.py`, `condense.py`. Re-measure WIP first.
3. **Tier 3 (defer until the subsystem is not actively shipping):**
   `agent_session.py`, `hivemind.py`, `push.py`, `presence.py`,
   `claydo.py`. Each needs Ron's explicit go-ahead per the freeze rule.

Target after Tier 1: ~2,000–3,000 lines moved out, `server.py` still
imports identically, zero behavior change. Full ≤3,000-line target is a
Tier-2/3 outcome and is gated on the freeze lifting.

## Per-PR checklist (carry the existing CHANGELOG discipline)

- [ ] `git status server.py` clean before starting; WIP heat map re-measured
- [ ] New module + blueprint; `register(app, deps)` injection (no globals leak)
- [ ] Move code verbatim — no opportunistic edits in the same PR
- [ ] `pytest -q` green (smoke import + github_sync suites)
- [ ] CHANGELOG entry: Done / Files Changed / Rollback
- [ ] Rollback = revert the single PR; no persisted-state/schema change

## Rollback for this whole effort

Nothing here touches code yet. Tag `plan-v2-rollback-base` (= `8fab4a9`)
+ `_plan_v2_backups/<ts>/` remain the global anchors.
