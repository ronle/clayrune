# Clayrune Modernization — Parallel Track Assignment (2026-06-09)

Companion to [`MODERNIZATION_PLAN.md`](MODERNIZATION_PLAN.md). The plan says *what* to do;
this says *who runs it in parallel without colliding*.

## The one principle: parallelize by FILE, not by phase

The whole refactor exists because parallel sessions collide on `server.py` and
`index.html`. So the split is brutally simple: **two agents may run at once only if
they never write the same file.** Concretely:

- **Every Phase 1 blueprint extraction edits `server.py`** (deletes its routes, adds one
  `register_blueprint`). Therefore the 13 blueprints **cannot** be fanned out to 13 agents —
  they serialize on `server.py`, and the plan deliberately orders them by ascending coupling
  (1.11 waits for shims battle-tested by 1.1–1.10; 1.12 is "do last"). One agent, in order.
- **Every Phase 3 module split edits `index.html` + `sw.js`.** Same story — the 8 frontend
  modules serialize. One agent, in order.
- These two files are **disjoint**, so the backend sequence and the frontend sequence run
  **fully in parallel**.

Net concurrency: **2 long-lived agents + 1 short tooling agent**. Not 13. This collapses the
plan's ~15–18 sessions toward ~10 wall-clock, almost entirely by overlapping the frontend track.

## Tracks

| Track | Agent | Scope (from plan) | Internal order |
|-------|-------|-------------------|----------------|
| **A — Backend** | long-lived | Phase 0 → Phase 1 (1.1→1.13) → Phase 2 obs folded in → Phase 5 tests | strictly sequential |
| **B — Frontend** | long-lived | Phase 3 (index.html → ES modules) | strictly sequential, **independent of A** |
| **C — Tooling** | short (½ session) | Phase 4 (pyright config + CI job + 1 CLAUDE.md line) | one-shot, then done |

> Track C can instead be Track A's second commit if you'd rather run only 2 agents. It's tiny.
> Recommended: run it as a brief standalone right after A's Phase 0 lands, so pyright lints `mc/`
> from the first blueprint onward.

## File ownership (the contract — do not cross these lines)

**Single-writer ownership eliminates merge conflicts.** Each path below has exactly one owner
for the duration of the effort.

| Path / glob | Owner | Notes |
|---|---|---|
| `server.py` | **A** | the serialization point; only A writes it |
| `mc/**` (state, core, obs, blueprints/*) | **A** | the entire new backend package |
| `tests/test_*_routes.py` | **A** | Phase 5 per-blueprint tests |
| `static/index.html` | **B** | the second serialization point; only B writes it |
| `static/js/**`, `static/css/**` (new) | **B** | extracted ES modules + styles |
| `static/sw.js` | **B** | cache-list + version bump per split |
| `tools/smoke/**` | **B** | if smoke fixtures need extending |
| `pyrightconfig.json` (new) | **C** | type-check scope |
| `requirements-dev.txt` | **C** | pin `pyright` |
| `.github/workflows/pyright.yml` (new) | **C** | CI gate; siblings already exist (`tests.yml`, `frontend-smoke.yml`) |
| `CLAUDE.md` | **C** | one policy line only; A/B do not touch it mid-effort |

### Shared files — the only collision risk, pre-resolved
- **`CHANGELOG.md`** — both A and B want it. Rule: **one entry per track, written at track close,
  not per step.** Per-step progress goes into the single-writer progress logs below. The 2–3
  final CHANGELOG entries merge once at the end; a both-added conflict resolves by keeping both.
- **Per-step / crash-recovery logging** goes to single-writer files (no collision, satisfies the
  "document for crash recovery" standing rule):
  - Track A → `docs/_tracks/backend_progress.md`
  - Track B → `docs/_tracks/frontend_progress.md`
  - Track C → (none needed; one commit)
  Each entry: step id, what moved, commit SHA, gate results (route count / smoke / pyright).

## Branch & merge strategy

- Integration branch = current **`local/opus-effort`**.
- Track A → `refactor/backend`; Track B → `refactor/frontend`; Track C → `refactor/tooling`.
- Each track merges its branch into `local/opus-effort` **after each green step** (per-step,
  not per-track — keeps drift small). Disjoint files ⇒ clean merges; `CHANGELOG.md` is the only
  possible conflict and only at track close.
- **Stage by explicit path** (standing repo rule — never `git add -A`). Each track stages only
  files inside its ownership rows above.

## Cross-track dependency gates (there is essentially one)

```
Track A  Phase 0 ──► (everything else in A)
                └──► Track C may start (pyright now lints a real mc/)
Track B  ── no dependency on A or C; start immediately ──►
```

- **C waits on A's Phase 0 commit.** Before `mc/` exists, pyright lints nothing; harmless but pointless.
- **B is free-running from minute one.** Zero coupling to backend work.
- **A's Phase 2 (observability) is NOT a separate parallel track** — its instrumentation edits
  `_scheduler_loop` / stream readers that live *inside* `server.py` until 1.12–1.13 extract them.
  So A creates `mc/obs.py` early, then instruments each loop **as it extracts that loop's blueprint**
  (stream readers during 1.12, scheduler loop during 1.13). The `/api/system/loops` route lands with
  the `system` blueprint (1.6) or later. This keeps all observability work inside A's `server.py`/`mc/`
  ownership — no cross-track edit.

## Per-track acceptance gates (run before every merge)

**Track A — after each blueprint step:**
```bash
grep -rc "@app.route\|@bp.route" server.py mc/ | awk -F: '{s+=$2} END{print s}'   # == 209
pytest -q
ruff check --select E9,F821 .
# boot smoke — CORRECTED endpoint (/api/system/health does NOT exist):
python server.py & sleep 4 && curl -sf localhost:5199/api/system/heartbeat >/dev/null && echo SMOKE_OK
```
> ⚠️ The source plan's smoke command curls `/api/system/health`, which 404s — `curl -sf` then
> fails and `SMOKE_OK` never prints. The real liveness route is **`/api/system/heartbeat`**
> (`server.py:16938`). Use that. (PORT resolves from `MC_PORT` env, default `5199` — `server.py:338`.)
> The smoke test boots a **throwaway** `python server.py`; it does **not** restart the live MC on
> :5199. Never restart the live server without Ron's explicit approval.

**Track B — after each module split:**
```bash
node tools/smoke/boot-smoke.mjs        # Playwright: dashboard SPA boots + grid renders (guards TDZ class)
node tools/smoke/bg-framing-check.mjs  # if the split touches framing
```
Plus manual: **hard-refresh** loads (SPA edits don't reach an open tab without a hard reload),
the feature works, **zero console errors**, and **`sw.js` version string bumped in the same commit**.

**Track C — once:**
```bash
pyright            # baseline; CI starts non-blocking (`|| true`), flip to blocking when clean
```

## Kickoff order

1. **A starts Phase 0** (scaffold `mc/state.py` + `mc/core.py`). Blocks nothing but C.
2. **B starts Phase 3 module 1** immediately, in parallel (smallest feature first — mirror plan order).
3. **C runs** right after A's Phase 0 merges (pyright config + CI + CLAUDE.md line), then retires.
4. **A proceeds 1.1 → 1.13**, folding Phase 2 obs in as each loop's blueprint is extracted.
5. Each track logs every step to its `docs/_tracks/*_progress.md`; CHANGELOG entries at track close.

## End-state acceptance (unchanged from plan, endpoint corrected)
```bash
wc -l server.py                                                                   # < 2000
grep -rc "@bp.route\|@app.route" mc/ server.py | awk -F: '{s+=$2} END{print s}'   # 209
pytest -q && pytest control_plane/tests -q
pyright                                                                            # 0 errors in scope
curl -sf localhost:5199/api/system/loops | python -m json.tool                    # all loops fresh
```

## Verified facts behind this split (checked against the repo 2026-06-09)
- `server.py` = 18,051 lines, **exactly 209 routes**, no `mc/` package yet, 0 blueprints today.
- `static/index.html` = 25,162 lines; `static/sw.js` present.
- `tests/test_auth_routes.py` exists → real safety net for the 1.1 pilot.
- CI gates already present: `.github/workflows/tests.yml` (Track A), `frontend-smoke.yml` (Track B).
- Liveness route is `/api/system/heartbeat` (server.py:16938); `/api/system/health` does **not** exist.
- `/api/system/loops` does not exist yet — Phase 2 creates it (expected).
