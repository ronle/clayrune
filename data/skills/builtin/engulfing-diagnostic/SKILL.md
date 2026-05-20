---
name: engulfing-diagnostic
description: Diagnose the GCP Gemini engulfing scorer's performance over time (TAKE/WATCH/SKIP calls vs. outcomes) using read-only Postgres. TRIGGER when running the engulfing-analyst weekly schedule, or when a user in the engulfing-analyst project asks for a scorer-performance diagnosis / health check / "is the AI scorer adding edge". Analysis ONLY — never proposes or applies patches, never deploys, never writes to the DB. Codifies the Phase 0 validated query patterns + temporal-stability / regime / diff-from-last / nothing-new run discipline. Writes a dated report to data/projects/engulfing-analyst/reports/.
---

# Engulfing scorer diagnostic

You are diagnosing the **GCP engulfing agent** = the Gemini batch scorer
(`signal_scorer.py` → `signal_ai_scores`). Output is a report a human reads.
**Phase 1: analysis only. No patches. No deploy. Read-only DB.**

## Hard rails (do not violate)
- `mcp__pg__pg_query_ro` only. DDL/config/deploy tools are denied — never try.
- **DB-side aggregation only.** GROUP BY / window functions. Never `SELECT *`
  raw signal rows into context. If a cut needs row detail, aggregate it in SQL.
- Never auto-act on a finding. The report is the only deliverable.
- WR = **all-outcomes resolved**: WIN = `target_1_hit`/`target_2_hit`,
  LOSS = `stop_loss`/`expired`; `no_fill` ≈ 0 (ignore); `pending` excluded.
  Never report resolved-only WR (inflates ~20pp).

## Step 0 — connectivity gate
Run `SELECT 1`. If it fails because the DSN still contains
`__READONLY_PG_PASSWORD__`, STOP: write a one-line report saying the
read-only role credential is not yet wired, and exit. Do not seek another DSN.

## Step 1 — read the diff baseline
Read the most recent file in
`C:\Users\levir\Documents\_claude\mission-control\data\projects\engulfing-analyst\reports\`.
Extract its headline numbers (verdict WR/avgR table, tail deciles). You will
report **deltas vs. this**, not just absolutes.

## Step 2 — canonical query set (run all; 30-day window)

All queries use this join (1:1, validated Phase 0):
`signal_ai_scores sa ⋈ engulfing_scores es ON es.symbol=sa.symbol AND es.pattern_date = sa.pattern_date AT TIME ZONE 'UTC' AND es.timeframe='5min'`.

**A. Verdict discrimination + the defect tell**
```sql
WITH j AS (
  SELECT sa.verdict, es.outcome, es.r_multiple
  FROM signal_ai_scores sa
  JOIN engulfing_scores es ON es.symbol=sa.symbol
   AND es.pattern_date = sa.pattern_date AT TIME ZONE 'UTC' AND es.timeframe='5min'
  WHERE sa.pattern_date >= (now() AT TIME ZONE 'UTC')::date - INTERVAL '30 days')
SELECT verdict, COUNT(*) n,
  COUNT(*) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit')) wins,
  COUNT(*) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit','stop_loss','expired')) resolved,
  ROUND(100.0*COUNT(*) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit'))
        /NULLIF(COUNT(*) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit','stop_loss','expired')),0),1) wr_pct,
  ROUND(AVG(r_multiple) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit','stop_loss','expired')),3) avg_r
FROM j GROUP BY verdict ORDER BY CASE verdict WHEN 'TAKE' THEN 1 WHEN 'WATCH' THEN 2 ELSE 3 END;
```
**Defect tell:** flag if `avg_r(SKIP) >= avg_r(TAKE)` OR `wr(TAKE)-wr(SKIP) < 3pp`.
That means the verdict is non-discriminating (Phase 0 state). Report whether
this persists, narrows toward discrimination, or flips.

**B. Raw-score tail calibration** (the only place edge has lived)
```sql
WITH j AS (
  SELECT sa.ai_score, es.outcome FROM signal_ai_scores sa
  JOIN engulfing_scores es ON es.symbol=sa.symbol
   AND es.pattern_date = sa.pattern_date AT TIME ZONE 'UTC' AND es.timeframe='5min'
  WHERE sa.pattern_date >= (now() AT TIME ZONE 'UTC')::date - INTERVAL '30 days'
    AND es.outcome IN ('target_1_hit','target_2_hit','stop_loss','expired'))
SELECT width_bucket(ai_score,0,100,10)*10-10 score_floor, COUNT(*) n,
  ROUND(100.0*COUNT(*) FILTER (WHERE outcome IN ('target_1_hit','target_2_hit'))/COUNT(*),1) wr_pct
FROM j GROUP BY 1 ORDER BY 1;
```
Report the 0–19 band WR, the 90–100 band WR, and the best-minus-worst-decile
spread. Baseline: ~41% / ~52.5% / ~13pp.

## Step 3 — the four scheduled-run additions

**C. Weekly temporal-stability** — is any edge stable or drifting?
Same join; `GROUP BY date_trunc('week', sa.pattern_date), sa.verdict`; report
per-week n / wr_pct / avg_r for each verdict, and per-week WR of the 90–100
and 0–19 score bands. Call out any week whose verdict-WR or tail-WR is >1
band outside the trailing pattern (signal of scorer/regime change), and
distinguish that from normal weekly noise (small n weeks → say "inconclusive").

**D. Regime breakdown** — available proxies only (no SPY-regime join exists
read-only here; state that limitation). Cut Query A by, separately:
hour-of-day ET `EXTRACT(hour FROM es.pattern_date AT TIME ZONE 'America/New_York')`,
`es.direction` (bull/bear), and `es.trend_context`. Surface where the scorer
*does* vs. *does not* discriminate (e.g. is TAKE only meaningful in some hour
or direction?). Honor domain context: 9–10 ET is structurally weak; mid-day
confluence is negative — interpret accordingly, don't just rank cells.

**E. Diff-from-last-report.** Lead the report with a delta table: each
headline metric, last report → this report, Δ. If a metric moved, say
whether the move is inside weekly noise (per C) or a real shift.

**F. "Nothing new" is a valid, desired output.** If no headline metric moved
beyond weekly noise and no new defect/anomaly appeared, write a SHORT report:
the delta table (all ~flat), one line "no material change vs <date>", and
stop. Do not pad. Token discipline is a rule, not a preference.

## Step 4 — write the report
Path: `C:\Users\levir\Documents\_claude\mission-control\data\projects\engulfing-analyst\reports\<YYYY-MM-DD>.md`.
Structure:
1. **Delta vs last report** (table, top — Addition E).
2. **Verdict discrimination** (Query A table) + defect-tell verdict.
3. **Tail calibration** (Query B) + spread.
4. **Temporal stability** (Query C) — drift flags or "stable".
5. **Regime** (Query D) — where it discriminates / fails.
6. **Assessment** — 3-5 sentences: is the scorer adding edge? changed since
   last? Honest uncertainty where n is thin. NO patch recommendations
   (that is Phase 2, human-gated, out of scope for this skill).

Then update the analyst project's memory run log (one line: date, headline,
defect-tell state). Do not write anything else anywhere.
