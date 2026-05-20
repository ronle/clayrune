# engulfing-analyst — Phase 0 Findings

Project name (scoped): **engulfing-analyst**
Date: 2026-05-18 20:30 ET · Author: Vector (local CR) · Status: Phase 0 PASSED, Phase 1 NOT started (awaiting Ron's go).
Full assessment: `engulfing-scanner/edge_analysis/engulfing_analyst_phase0/ASSESSMENT.md`
(`_` prefix on this filename is intentional — DATA_DIR exclusion rule, server.py:323 `DATA_DIR = _DATA_ROOT/data/projects`, load_projects skips `_`-prefixed entries.)

## 1. Headline diagnosis (30d, 2026-04-20→05-18, n=33,002 5min, 1:1 join)
The GCP agent = Gemini batch scorer (`signal_scorer.py` → `signal_ai_scores`).

| Verdict | n | WR % (all-outcomes resolved) | avg R |
|---|---|---|---|
| TAKE | 7,488 | 49.7 | +0.413 |
| WATCH | 13,774 | 49.1 | +0.407 |
| SKIP | 11,740 | 48.0 | +0.422 |

- Verdict is **non-discriminating**: 1.7pp TAKE−SKIP; avg-R **inverted** (SKIP > TAKE). Reproduces the 2026-05-08 V3 "AI anti-predictive" finding on a fresh window — no recovery.
- Prompt's own stated goal is "TAKE WR > 55%, SKIP WR < 40%" — **missed badly** (49.7 / 48.0).
- Only real signal is in the raw `ai_score` **tails**: 0–19 → ~41% WR (n≈2,257); 90–100 → ~52.5% (n≈2,575); flat noise 20–79. ~13pp best-vs-worst-decile spread destroyed by the 3-bucket collapse.

## 2. Patch surface — VERIFIED & CORRECTED
Ron's assumption was "Gemini prompt + threshold constants in signal_scorer.py." Correction after reading the code:

- **Repo/file:** `engulfing-dashboard` repo, `backend/signal_scorer.py` (deployed via `engulfing-dashboard:vN` Cloud Run image). Confirmed.
- **Primary surface = `SCORER_SYSTEM_PROMPT` string only.** It defines distribution targets (TAKE 15-25/WATCH 35-45/SKIP 30-45), the score→verdict band map (75-100 TAKE / 50-74 WATCH / 25-49 SKIP / 1-24 strong SKIP), and the WR goal. **The verdict is emitted directly by Gemini** (`_store_scores`: `verdict = sc.get("verdict","SKIP")`).
- **There are NO numeric threshold constants in Python.** Python only validates `1≤score≤100` and truncates verdict text. So a patch like "TAKE only at raw≥90 / hard-veto ≤19" CANNOT be a config-constant change — it requires **either** a prompt edit **or** a *new* Python re-bucket layer that does not exist today. Introducing that re-bucket layer is itself a Phase 2 patch-surface decision (deterministic, testable — preferable to prompt-only for the tail fix).
- **Secondary surface:** model/generation config (`gemini-2.5-flash`, `generation_config` sets only `response_mime_type` — temperature/top_p are at model default, i.e. unpinned sampling is an unmanaged tuning lever).
- No external weights/config keys/GCS objects/Cloud Function inline code involved. Single file, single repo.

## 3. Diagnostic-skill query-pattern spec (Phase 1 input, per Ron)
Codify into the `engulfing-diagnostic` skill:
- **From Phase 0 (proven):** DB-side aggregation only (GROUP BY / window fns, never raw rows); canonical join `signal_ai_scores ⋈ engulfing_scores ON (symbol, pattern_date AT TIME ZONE 'UTC'), timeframe='5min'` (1:1); verdict WR+avgR breakdown; raw-score decile (tail) calibration; **inverted-avg-R (SKIP avgR ≥ TAKE avgR) as the defect tell**; all-outcomes WR (win=target_1/2_hit, loss=stop_loss/expired, no_fill≈0, pending excluded).
- **4 additions for scheduled runs:** (a) **temporal-stability cuts** across weekly buckets (is the tail edge stable or drifting?); (b) **regime breakdown** (by SPY direction / vol regime); (c) **diff-from-last-report** (compare to prior `reports/<date>.md`, surface only deltas); (d) **"nothing new" is a valid output** — if no material change vs last report, say so and stop (token discipline, anti-noise).

## 4. Phase 1 — BUILT 2026-05-18 (analysis-only)
All artifacts created. No backend code (no Distiller) — Phase 1 needs none;
that is Phase 2, committee-gated per MAINTENANCE_PROTOCOL/SKILLS_CURATION.

| Artifact | Location | State |
|---|---|---|
| Project | `C:\Users\levir\Documents\Projects\engulfing-analyst` + MC id `engulfing-analyst` (`distiller_mode=off`) | created |
| MCP lockdown | `<proj>\.mcp.json` — ONLY `pg` server; no gcp_deploy/ops/logs; no repo_fs | created |
| Read-only enforcement | `<proj>\.claude\settings.json` — denies `pg_exec_ddl`/`pg_config_set`/`pg_config_get` + all gcp/deploy/docker/git-push/psql; allows `pg_query_ro` only | created |
| Domain context | `<proj>\CLAUDE.md` (identity, rails, GCP-agent def, Phase 0 baseline, regime/BD/tier context) | created |
| Seed memory | `~/.claude/projects/C--Users-levir-Documents-Projects-engulfing-analyst/memory/MEMORY.md` | created |
| Skill | `data/skills/builtin/engulfing-diagnostic/SKILL.md` (auto-installs next MC startup) — Phase 0 SQL + 4 additions | created |
| Schedule | Clayrune id `46420113`, cron `0 9 * * 0` (Sun 09:00 PT), next 2026-05-24 | **disabled** (inert until DB cred) |
| Reports dir | `data/projects/engulfing-analyst/reports/` | created (empty) |

Read-only is enforced **at the tool-permission layer** (agent physically
cannot invoke any write/DDL/deploy tool). The DB-role belt is templated but
NOT satisfied — see blocking item below.

## 5. Blocking item — RESOLVED 2026-05-18
Ron supplied the `readonly` role password. Verified before wiring:
- Auth as `readonly` on `fl3` succeeds; can read `signal_ai_scores` (33,132 rows).
- Genuinely read-only: `has_table_privilege` → SELECT yes; INSERT/UPDATE/DELETE
  **no** on `engulfing_scores`, no INSERT on `signal_ai_scores`, no UPDATE on
  `v3_auto_trades`; `rolsuper=false`, `rolbypassrls=false`, no role memberships.
- URL-encoded DSN round-trips (`=`→`%3D`, `}`→`%7D`); end-to-end `SELECT 1` OK.

`<proj>\.mcp.json` now carries the live read-only DSN. Schedule `46420113`
**ENABLED**; next run **2026-05-24 09:00 PT** (Sun). Defense-in-depth:
DB-role read-only **and** tool-permission deny both enforced. Separate-role
rail satisfied (NOT prod's `FR3_User`). **Phase 1 fully operational.**

## 6. Phases 2/3 (unchanged, not started)
Phase 2 blocked on Skills Curation backend steps 2-4 (only Step 1 `mc-distill`
shipped) AND requires committee review before backend code. Patch surface for
Phase 2 = `SCORER_SYSTEM_PROMPT` in `signal_scorer.py` (no threshold
constants exist) and/or a new deterministic Python re-bucket layer. Phase 3
needs an engulfing-side backtest harness — existence unverified.
