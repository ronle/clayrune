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
