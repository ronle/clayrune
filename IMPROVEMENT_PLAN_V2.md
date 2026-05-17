> # ⛔ SUPERSEDED — 2026-05-17
> This freeze-and-refactor plan is **closed**. Continuous shipping makes
> snapshot plans go stale (proven here: F2/F7 — the WIP map and Tier-1
> classification were obsolete within days). It is replaced by the
> standing **`docs/MAINTENANCE_PROTOCOL.md`** (new-subsystems-as-modules,
> opportunistic extraction, monthly sweep) — no more scheduled refactor
> sprints.
>
> **Outcome of v2 (all shipped to master):** P0-1..P0-7 github_sync
> correctness · P1-2 `_encode_project_path` · P1-4 KB refresh · P1-5 test
> scaffolding · P2-1 condense visibility · P2-2 upload quota · P2-3 log
> shim · P2-4 + N3 docs · N4 nul · Tier-1a `marketing_preview`.
> **Not done by design:** P1-1 full split (F1/F2/F7 → the stateful trio
> `process_tracker`/`terminal_sessions`/`scheduler` is now *opportunistic*
> per the protocol, not a sprint) · P3-1/N1 (plan-deferred) · N2 (Ron:
> keep, see N2a/N2b below). Full audit trail:
> `IMPROVEMENT_PLAN_V2_FLAWS.md`; deferred-extraction design:
> `docs/SERVER_SPLIT_PLAN.md`.
>
> Kept verbatim below for historical context. Do not execute from this
> document.

---

# Clayrune Improvement Plan — v2 (post-on-disk review)

**Source review:** Two-pass review on disk at `C:\Users\levir\Documents\_claude\mission-control` against the working tree as of 2026-05-16. Files read in full: `README.md`, `github_sync.py`, `.gitignore`, `CLAUDE_KB.md`, `docs/remote-access/01-architecture.md`, recent CHANGELOG (top 250 + bottom 300 lines), `RESUME_HERE.md` (first 120 lines). Files read partially: `server.py` (first ~1000 of ~13,500 lines via GitHub blob). Directory inventory verified for `mc_remote_iface/`, `mc_remote/`, `mc_tunnel/`, `mc_tty_shim/`, `control_plane/`, `src-tauri/`, `docs/`, `tools/`, `static/`.

**Changes vs v1:** P0-8 (CORS/tunnel) dropped — the remote-access architecture doc proves the design is correct (mc-tunnel forwards only to `127.0.0.1:5199`, hardcoded; CF Access + Worker path allowlist sit in front; CORS is a browser-side check that does not bound the security model). P1-4 (`mc_remote_iface` docs) downgraded to P2 because extensive `docs/remote-access/*.md` already exists; the residual is just the provider-interface contract. Two new items added: stale `CLAUDE_KB.md` refresh, and minimal test scaffolding for the main app.

---

## Active development — do not touch unless explicitly scoped

Ron is actively shipping in these areas (CHANGELOG entries within the last 7 days). The agent should leave these alone unless a task is specifically inside them.

- **Push / FCM / web-push / presence system** (May 14, 14b, 16) — `_presence_state`, `_handle_push_signal`, `_notify_push`, `_push_send_fcm`, the `/api/presence`, `/api/push/register-fcm` endpoints.
- **Activity feed redesign** (May 15) — `renderFeed`, `classifyFeedEvent`, `_buildAttentionList`, `_feedAgeBucket`, `_updateFeedAttentionBadge`.
- **AskUserQuestion pipeline** (May 14b, 15) — `pending_questions`, `question_id` UUIDs, `waiting_for_question` status, `_renderedQuestionIds`.
- **Modal Aero-Snap + Tile button + pin/unpin** (May 14b) — `_zoneRect`, `applySnap`, `mc_modal_prefs.snap`.
- **Mobile reconciliation + SSE recovery** (May 14b, 15) — `_reconcileAgentBuffer`, `_sendInFlight`, the `visibilitychange` fan-out.
- **Installer + Ask Claydo + walkthrough rewrite** (May 7) — `installer/`, `WT_STEPS`, the `__claydo` modal.
- **Hivemind global surface** — already shipped, in active use; `data/hiveminds/`, see `docs/HIVEMIND_SPEC.md`.

If any P0/P1 task below touches code adjacent to these, the agent must pause and ask before proceeding.

---

## Verified problem statements

For each item, the agent should still re-confirm against current code before changing it. Acceptance criteria from v1 unchanged unless noted.

### P0 — Correctness, data integrity, security (in `github_sync.py`)

`github_sync.py` is 11.5 KB / 317 LOC, unchanged since the open-source release prep in March (no CHANGELOG entries since). All seven concerns from v1 remain valid:

**P0-1.** Paginate `gh issue list` (currently `--limit 100`, no pagination → silent data loss on repos past 100 issues).

**P0-2.** Add `last_synced_state` snapshot on each backlog item for 3-way merge (currently GitHub-wins overwrites local edits silently).

**P0-3.** Stop redundant `gh issue close/reopen` on every sync (currently called for every linked item every cycle, regardless of state match).

**P0-4.** Handle deleted-on-GitHub issues (currently zombie `github_issue_number` → silent 404s forever).

**P0-5.** Symmetric sanitization (currently pull sanitizes, local doesn't → spurious "updated" cycles for any text with `<`, control chars).

**P0-6.** Push issue bodies (currently `--body ''` always).

**P0-7.** Throttle bulk pushes (currently N items = N sequential `gh issue create` inside the lock; 200 items locks sync for ~5min).

**Test infrastructure note:** P0-2 is not safe to ship without tests. There is **no `tests/` directory for the main app** — only `control_plane/tests/` exists, with a single `test_enroll.py`. See P1-5 below; consider doing P1-5 *before* P0-2.

### P0-8 — DROPPED

CORS/tunnel boundary: docs/remote-access/01-architecture.md §3.2 confirms mc-tunnel is hardcoded to `127.0.0.1:5199` and CF Access + Worker handle auth/path enforcement before requests reach Flask. The Origin echo in `add_cors_headers` is correct as designed.

---

### P1 — Architecture & maintainability

**P1-1. Split `server.py`** *(local file size now 516 KB — grew 32 KB beyond the GitHub copy reviewed in v1).* 

Same plan as v1: extract into `scheduler.py`, `terminal_sessions.py`, `condense.py`, `process_tracker.py`, `transcript.py`, `agent_session.py`, `marketing_preview.py` as Flask blueprints, one PR per module, no functional changes. Target ≤3,000 lines remaining in `server.py`.

**Additional modules to extract** (not in v1, surfaced by recent CHANGELOG):
- `push.py` — `_push_send_fcm`, `_notify_push`, `/api/push/*` routes.
- `presence.py` — `_presence_state`, `_presence_lock`, `_presence_touch`, `_is_being_watched`, `/api/presence`.
- `hivemind.py` — orchestrator/worker spawn, knowledge-base writes, escalation routes (whatever is currently in `server.py` rather than a dedicated module).
- `claydo.py` — `/api/guide/ask`, `/api/guide/stream`, USER_GUIDE.md → CLAUDE.md materialization in `data/claydo/`.

The push and presence systems are recent (this week); they're the easiest extractions because they're already self-contained.

**Sequencing:** do this *after* the github_sync P0 work — extracting modules while github_sync still has open correctness bugs makes the bug fixes riskier.

**P1-2. Extract `_encode_project_path` helper.** Same as v1.

**P1-3. Per-project `use_streaming_agent`.** Same as v1. Note global default is now `true` (Mode B); the per-project override still adds value for projects that need fresh-process isolation per turn.

**P1-4. Refresh stale `CLAUDE_KB.md`.** *(New.)* File header says "Updated: 2026-03-23" but content reflects state ~2 months old:
- Lists Hivemind as "next major feature" — it shipped weeks ago.
- Missing: skills system, MCP installer, push/FCM, presence, Ask Claydo, installer, Aero-Snap, scheduler reliability work, Claydo helper, native Android APK.
- Missing modules in "Key files" table: `skills.py`, `mcp.py`, `mcp_installer.py`, `mc_remote/`, `mc_tunnel/`, `control_plane/`, `mc_tty_shim/`.
- Lists `data/SHARED_RULES.md` as a key file — still accurate, but the file structure has grown significantly.

This is the file the CR agent itself reads on startup (per the `## Agent Session Rules` block). Stale content here means every agent session starts with the wrong mental model.

**Acceptance:**
- Header date updated to today.
- Architecture table includes every module currently in repo.
- "Active Backlog" reflects current state (not March's).
- "Recent Changelog Highlights" includes May entries.
- Hivemind section reframed from "next major feature" to "implemented feature; see HIVEMIND_SPEC.md for the implemented design."
- A new top section: "What changed since the last KB refresh" — a 5–10 line summary so future agents can skim diffs across refreshes.

This task should run *first*, before any P0 work, so the agent doing the P0 work has accurate context.

**P1-5. Minimal test scaffolding for main app.** *(New.)* 

Currently zero tests for `server.py`, `github_sync.py`, or `static/index.html`. `control_plane/tests/conftest.py` + `test_enroll.py` exist as a working pattern.

**Scope (deliberately small):**
- Create top-level `tests/` directory + `pytest.ini` (or `pyproject.toml` test config).
- Mirror the `conftest.py` shape from `control_plane/tests/` for shared fixtures: temp data dir, mock gh CLI, faked Claude CLI.
- One test per P0 fix in `github_sync.py` (so P0-1…P0-7 each ship with a passing regression test).
- One smoke test that imports `server.py` without starting Flask (catches import-time breakage in module extractions).
- Wire `pytest tests/` into the install scripts as an optional `--dev` step; don't gate normal install on test deps.

**Out of scope** (explicitly): selenium / playwright frontend tests, Tauri/pywebview integration tests, network tests against real GitHub or Cloudflare. The goal is a *safety net for refactoring*, not full coverage.

**Acceptance:**
- `pytest tests/` runs green from a fresh clone.
- Each P0 fix is paired with a test that fails on the unfixed code and passes after the fix.
- CI workflow under `.github/workflows/` runs tests on PR (if not already present).

---

### P2 — Robustness & UX

**P2-1. Memory condensation visibility.** Same as v1.

**P2-2. Per-project upload quota.** Same as v1.

**P2-3. Standardize log volume.** Same as v1.

**P2-4. Document `mc_remote_iface` provider contract.** *(Downgraded from P1-4 in v1.)*

The public-facing remote-access architecture is heavily documented in `docs/remote-access/` (12 files: user flow, architecture, attestation protocol, control plane API, abuse prevention, build pipeline, rollout, licensing, error codes, runbook, setup checklist). What's *not* documented is the contract surface inside `mc_remote_iface/__init__.py` + `mc_remote_iface/provider.py` itself — the methods a third-party provider would need to implement.

**Scope:** A single `mc_remote_iface/README.md` describing the registration mechanism, required methods (`enroll`, `attest`, `start_tunnel`, `stop_tunnel`, `status`, lifecycle hooks), config surface, and the `MC_DEV_REMOTE_STUB` env flag. Cross-link from main `README.md` and from `docs/remote-access/01-architecture.md` §3.1.

---

### P3 — Cleanup

**P3-1. Move marketing preview out of Flask.** Same as v1. The `clayrune.io` Cloudflare Pages setup is mentioned as a Ron-to-do in `RESUME_HERE.md` §0; once it's live, the in-Flask preview route can go.

---

## New items surfaced by full-tree review

These are not blocking but worth queuing.

**N1. `static/index.html` is 942.5 KB single-file.** This is the entire SPA. README explicitly says "no build step / pure ES modules" — that's a deliberate constraint, not an oversight. Don't suggest a bundler. *But* the file is past the comfortable size for in-place edits; a future task is to split it into multiple `<script type="module" src="...">` files served from `static/js/`, keeping the no-build constraint. Defer until after `server.py` split lands.

**N2. Tauri shell — keep, document, productionize later.** *(Ron decision 2026-05-17: keep in plan.)* `src-tauri/` is a working PoC at v0.1.0: spawns `python server.py` as a child, kills it on window close, has the stdout-deadlock fix. Not production-ready — hardcoded `python` (no venv resolution), 1500ms sleep instead of port-poll, no error UI if Flask fails, no PyInstaller story, no signed-installer pipeline, `productName` still says `mission-control`. Active desktop path remains pywebview/`app.py`.

**N2a. Document the parked state.** Add one-liner to root `README.md` under a "Desktop shells" section: pywebview = active, Tauri = parked alternative for a future smaller-binary / auto-updater / cross-platform path. Prevents contributor confusion about which is real.

**N2b. (Future sprint, not yet scheduled.) Productionize Tauri.** Rough scope, ~1–2 weeks single-dev:
- Replace hardcoded `python` with venv resolution + PyInstaller-frozen interpreter path (mirror `_resolve_claude()` pattern in `server.py`).
- Replace the 1500ms sleep with port-poll on `127.0.0.1:5199` + timeout + error UI.
- Update `productName` to `clayrune` and bump version.
- Cross-platform bundle pipeline: `.msi` (Windows, code-signed), `.dmg` (macOS, notarized), `.deb`/`.AppImage` (Linux).
- Auto-updater wiring (Tauri's built-in updater pointed at `clayrune.io/releases/`).
- System-tray integration (minimize-to-tray, quick-restart-server menu).
- Decide single-binary (embed Python via PyOxidizer or ship portable Python) vs require-system-Python install path.
Trigger this sprint when (a) Windows pywebview path hits a real blocker, (b) macOS/Linux demand becomes concrete, or (c) installer-size / auto-update friction surfaces in user feedback. Until then: parked, documented, untouched.

**N3. `mc_remote/PROPRIETARY.md` + `mc_tunnel/PROPRIETARY.md`.** Both modules are *in* the public repo but carry proprietary license notices. This is a source-available-but-not-MIT split. Either (a) move these to a separate private repo and submodule-link, or (b) clarify in main `README.md` that the repo is MIT-except-for-these-two-directories. Current state risks license confusion for contributors.

**N4. `nul` file in repo root (132 B).** Looks like a Windows accident (`> nul` redirect creating an actual file). Delete + add to `.gitignore`.

**N5. `data/` contains generated artifacts mixed with source.** `data/projects/.gitkeep` and `data/uploads/.gitkeep` are correctly the only committed items; user data is gitignored. But `data/SHARED_RULES.md` is *also* user content and *is* checked in (via the path in `config.json`). Either move the canonical `SHARED_RULES.md` to repo root or `templates/`, or document why it lives under `data/`.

---

## Suggested execution order (revised)

1. **Sprint 0:** P1-4 (refresh `CLAUDE_KB.md`) — single PR, ~1 hour, fixes the stale agent context that affects every subsequent task.
2. **Sprint 1:** P1-5 (minimal test scaffolding) — prerequisite for safe github_sync work.
3. **Sprint 2:** P0-1 → P0-7 — `github_sync.py` correctness, each paired with a test.
4. **Sprint 3:** P1-2 (helper extraction), P1-3 (per-project streaming mode), N4 (`nul` cleanup) — small, low-risk.
5. **Sprint 4:** P1-1 (`server.py` split) — 11 small PRs over a week.
6. **Sprint 5:** P2-1, P2-2, P2-3 — UX polish.
7. **Sprint 6:** P2-4 (`mc_remote_iface` README), N3 (license clarification).
8. **Sprint 7:** P3-1, N1, N2 as time permits.

---

## Open questions for Ron

1. **Tauri status** (N2) — actively planned or parked? Affects whether to keep `src-tauri/`.
2. **mc_remote / mc_tunnel licensing** (N3) — keep in main repo or split out?
3. **Free-tier limits** for the control plane (per `docs/remote-access/01-architecture.md` §11 open question #2) — does this need to be resolved before the install rollout?
4. **Mobile reconciliation regressions** — the May 14b–15 mobile work suggests the SSE / IME / touch surface is fragile. Is there a phone you can keep in a stable test harness, or is this still ad-hoc?

---

## Notes for the CR agent

- This plan was authored from C:\Users\levir\Documents\_claude\mission-control at master after the May 16 push-policy commit. Re-verify against `git log -10` before starting.
- Sprint 0 (`CLAUDE_KB.md` refresh) gives every subsequent sprint accurate context. Do it first.
- Sprint 1 (tests) before any github_sync changes. P0-2 (3-way merge) is the riskiest correctness fix in the plan; do not ship it without paired tests.
- Each task has explicit acceptance criteria. Do not mark a task done until the criterion is met.
- If a task as written conflicts with code I couldn't see in this review, document the conflict in this file as `[reviewer-error: <explanation>]` and propose a revised version for user approval.
- Continue the existing CHANGELOG.md discipline — each PR gets a dated entry with Done / Files Changed / Rollback notes.
- The `Agent Session Rules` block in `CLAUDE_KB.md` is mandatory; follow it on every session.
