# Seat 2 — Agent Behavior & Artifact Quality
> Review of `docs/AUTO_SCOPE_PROMOTION_COMMITTEE_BRIEF.md` §3 eligibility gate.
> Audited 2026-07-16 against the live artifact population (which the operator was
> actively draining during the audit — bucket locations are as-of ~20:30).

## Seat 2 — Agent behavior & artifact quality — RATIFY-WITH-CONDITIONS

The gate is dramatically stricter in practice than on paper: applied to every skill/preference artifact ever produced (93 instances audited across `_proposed/`, `_promoted/`, `_rejected/`, and the quarantine), it admits **zero artifacts as the disk stands today**, and its preference leg is an empirically null set — all ~50 unique preference fingerprints ever observed have `recurrence_count_exact = 1`, because `distiller.py` *generates* preferences at threshold 1 by design (`_aggregate_per_project`, line ~1119: "the human promotion step is the quality gate"). The artifacts the gate *would* have installed in a generous counterfactual (11 skills) are good; the real quality risks are two interactions the brief doesn't discuss — fingerprint-collision recurrence pooling, and the durable-yes fingerprint block silently starving distinct later skills — plus the fact that the gate's honest expected yield may be near zero, which undercuts the proposal's stated motivation (queue drain).

### Evidence — the artifact audit

Population: 17 promoted SKILLs, ~45 promoted PREFERENCE instances (~37 unique fingerprints), 2 proposed SKILLs + 3 proposed PREFERENCEs (all drained to `_promoted`/`_rejected` during this audit), 4 rejected PREFERENCEs, 7 quarantined preferences. Gate columns applied per brief §3.1. "Would auto-install?" is evaluated two ways: **as-is** (today's frontmatter, fail-closed on missing origin) and **counterfactual** (assume generation-time origin stamping; brackets).

**Skills — all 17 with `recurrence_count_exact ≥ 3`, all project-scoped, none suppressed, all authority-clean:**

| Artifact (in `_promoted/` unless noted) | fp | rec_exact | origin | Auto-install as-is [counterfactual] | Good or bad | Note |
|---|---|---|---|---|---|---|
| validate-signal-hindsight-gate (06-12) | 94084ae5 | 3 | missing | NO [YES] | GOOD | Real procedure, concrete numbers, durable |
| validate-trading-signal-realistic-conditions (06-12) | 94084ae5 | 3 | missing | NO [**skipped** — fp already installed] | GOOD | Distinct skill, same fp — see Condition 2 |
| validate-signal-rigorous-gate (07-02) | 94084ae5 | 3 | missing | NO [**skipped** — fp already installed] | GOOD | Distinct skill (beta-neutral IC gate); human promoted it; auto-mode would have silently starved it |
| diagnose-silent-async-failure (06-18) | 19d238a0 | 4 | missing | NO [YES] | GOOD | Solid diagnostic playbook |
| committee-startup-diagnostics (06-18) | 19d238a0 | 3 | missing | NO [**skipped** — fp collision] | GOOD | Genuinely different skill sharing "diagnose-incident" fp |
| validate-trading-indicator-net-edge (06-18) | faabdbab | 3 | missing | NO [YES] | GOOD | |
| post-deploy-validation (06-19) | a0faa2e1 | 3 | missing | NO [YES] | GOOD | |
| validate-deployment-change (06-22) | a0faa2e1 | 3 | missing | NO [**skipped** — fp collision] | GOOD | Near-dupe of post-deploy-validation; skip is arguably correct here |
| diagnose-operational-stall (06-24) | 30235025 | 3 | missing | NO [YES] | GOOD | |
| gate-scheduled-maintenance-on-quota (06-26) | 88254aa6 | 3 | missing | NO [YES] | GOOD | Evidence sessions are scheduled maintenance runs — under §3.4 tightening this is unattended-origin |
| monitor-indicator (06-26) | 7771cd5b | 3 | missing | NO [YES] | GOOD | |
| diagnose-execution-quality-gap (06-29) | 985db7e4 | 3 | missing | NO [YES] | GOOD | |
| diagnose-silent-async-worker (06-29) | 9262cc55 | 3 | missing | NO [YES] | GOOD but REDUNDANT | Near-duplicate of diagnose-silent-async-failure under a *different* fp — both auto-install; loadout bloat a sensible human should have merged (and didn't) |
| audit-backlog (07-01) | d64e3350 | 3 | missing | NO [YES] | GOOD | Night-shift pattern — likely unattended under §3.4 |
| gate-threshold-admission (07-01) | 998ac23d | 3 | missing | NO [YES] | GOOD | |
| live-trade-override-price-guards (07-01) | 985db7e4 | 4 | missing | NO [**skipped** — fp collision] | GOOD, trading-risk-relevant | Distinct from diagnose-execution-quality-gap; silent starvation would have been costly |
| audit-doc (07-10, was `_proposed/mission_control/`, human-promoted 07-16) | 7cdb28b5 | 3 | **unattended** (backfilled) | **NO — origin blocks, permanently** | GOOD | The only rec≥3 skill with a real origin stamp — and it's blocked. Human path caught it; see yield finding below |
| validate-trading-threshold (07-09, was `_proposed/day_trading_engulfing_scanner/`) | 52a5dc26 | 3 | **unattended** (backfilled) | **NO — origin blocks** | not assessed | Second of only two origin-stamped rec≥3 skills: also unattended |

**Preferences — every instance in every bucket has `recurrence_count_exact = 1` → the gate admits NONE. Verified against the raw signal stores too: across all 14 `data/projects/*_skill_stats.json`, 50 unique preference-kind exact fingerprints, distinct-session recurrence distribution = {1: 50}. Not one preference has ever recurred even twice.** Notable rows (gate outcome NO for all):

| Artifact | rec_exact | Good or bad | Note |
|---|---|---|---|
| 6 quarantined authority prefs (26caa8ba, 2b70cbc6, 45494070, 497d0ce2, 916297ce, a465fd40) | 1 | BAD (authority class) | Recurrence≥3 alone would have blocked all six — real defense in depth behind the authority guard |
| preference-1ba8d678 "Keep local/opus-effort branch" (promoted ×3, rejected ×1, quarantined) | 1 | BAD — stale one-moment directive | Human promoted it three times; gate blocks it |
| preference-1ea4e648 "Ship the 3-month escalation in the scanner" (promoted 07-16) | 1 | BAD — a task, not a preference | Human promoted it today; gate blocks it |
| preference-94084ae5 variant "Trust intuition-backed strategy edges even when conventional testing doesn't validate them" (promoted 06-30) | 1 | BAD — anti-empirical, moment-specific; evidence quote is the *agent's own* apology ("the flaw is in *my* tests") | Same fp as the good "scrutinize the test" variant and as three SKILLs — see collision finding |
| preference-711853f4 "Claude agent is the regime governor at bootstrap…" | 1 | BAD — config snapshot as directive | Blocked |
| ~30 remaining promoted prefs (09e12b76, 65b77118, dbda9de7, 2df3ce14, b8345e3b ×3, b4e4b1bf ×4, …) | 1 | mostly GOOD | All blocked equally |
| rejected d027fab8 "Deploys require explicit user approval" (origin unattended) | 1 | fine but redundant | Blocked twice over (rec + origin) |

**Bottom line of the audit:** as-is auto-install count = **0**. Counterfactual = **11 skills installed, 0 harmful, 1 redundant near-dupe pair installed, 3 genuinely-distinct good skills silently starved by fp collision**. Under the §3.4 tightened origin rule the counterfactual collapses toward 0–3: the only two rec≥3 artifacts with real origin stamps are both unattended, 59 of 131 backfilled queue artifacts are unattended, and the heaviest rec≥3 producers (scheduled trading consultants, night-shift maintenance) are exactly the sessions §3.4 reclassifies as unattended.

### Blockers

None. Nothing in the observed population shows the gate admitting a harmful artifact.

### Conditions (RATIFY-WITH-CONDITIONS)

1. **Drop `preference` from the eligible kinds, or re-justify it against the null-set evidence (closable in design).** Preferences are generated at recurrence 1 by explicit design because "the human promotion step is the quality gate" (`_aggregate_per_project`); 50 of 50 observed preference fingerprints never recurred. The leg admits nothing today, and the *first* preference ever to hit rec 3 is disproportionately likely to be a vocabulary collision, not a restated preference: fp `a0faa2e13c42` demonstrably rendered two unrelated preference bodies ("Validate new configurations against historical data" on 06-09 and "Only commit strategy code to private repositories" on 06-11), and `_render_preference` pools evidence quotes from *all* same-fp signals — three unrelated user remarks sharing a verb-noun pair would be blended by a Haiku call into one standing behavioral instruction and installed with no human look. Keeping the leg buys zero yield and a nonzero collision tail; if kept, require the render to be validated against each evidence quote individually.
2. **Handle the fp-collision / durable-yes starvation interaction (closable in design).** `_generate_and_write_artifact`'s `skip_already_installed` keys on the exact fingerprint. Auto-install makes that block bind at machine speed: the first artifact on a collided fingerprint permanently suppresses later *distinct* skills on the same fp. The population shows this is not hypothetical — fp `94084ae5` produced three different validate-signal skills and fp `985db7e4` produced both diagnose-execution-quality-gap and live-trade-override-price-guards, all of which a human judged separately install-worthy. Minimum fix: when a candidate's fp is already auto-installed, route it to the human `_proposed/` queue instead of silently skipping (`skipped_installed` counter today is invisible to the operator).
3. **Install-time structural re-validation (closable in implementation).** The refusal pipeline has leaked junk to `_proposed/` twice before via new REFUSE phrasings (`_is_refusal` docstring; `tests/test_distiller_refusal.py`; the `distilled-969b3b91` body-=="REFUSE" artifact, 2026-06-05). Under `proposed` mode a leak lands in a human queue; under `auto` mode a future leak class lands directly in a project loadout. At install time, verify: frontmatter `name` + `description` present, body non-REFUSE-shaped, body length above a floor. One cheap function, same posture as re-running `_authority_violation`.
4. **State the expected yield honestly, and gate the ship on measured yield (closable in design).** With §3.4 (which this seat agrees must ship with the feature), the sessions that produce rec≥3 skills are mostly ineligible, and preferences are structurally ineligible (Condition 1). The proposal's motivation is queue drain (146 artifacts, oldest 44 days), but ~96% of that queue is EXPLORATIONs — which §3.1(1) rightly excludes. Run the eligibility gate retrospectively over 30 days of signal data with backfilled origin and publish the number of artifacts that would have auto-installed. If it is ~0, the feature is safe but does not solve the stated problem, and the brief should say what it is actually for (future-proofing the interactive path) rather than implying it relieves the queue.
5. **Recompute recurrence and suppression from live `_skill_stats.json` at install time, not from artifact frontmatter (closable in implementation).** `recurrence_count_exact` in frontmatter is a generation-time snapshot; the 30-day window decays underneath it, and suppression state changes (the operator rejected `dbda9de7` on 07-13 *after* promoting a same-fp sibling on 07-06). The brief's §3.1(5) implies a live check for suppression — make the same explicit for the recurrence leg.

### Ratifications

- **Recurrence ≥ 3 (exact) is a real quality filter for the bad-preference class.** Every observed bad shape — all six quarantined authority preferences, the stale branch directive (1ba8d678), the task-masquerading-as-preference (1ea4e648), the anti-empirical mood capture (94084ae5 06-30 variant), the config snapshot (711853f4) — sits at recurrence 1 and is blocked by the threshold alone, independent of the authority guard. Defense in depth here is genuine, not decorative.
- **Origin fail-closed did the right thing on the only two artifacts it could be tested on.** Both rec≥3 skill artifacts in the live queue (validate-trading-threshold, audit-doc) carried backfilled `origin: unattended` and would have been refused. audit-doc is a *good* skill the gate misses — acceptable, because the human promotion path still exists and in fact promoted it today; the gate is a subset selector, not a replacement.
- **The rec≥3 skill population is good.** The eleven counterfactual installs are concrete, durable diagnostic/validation playbooks with real evidence (several are live in loadouts and read well). Zero would have been harmful. The human gate's own record over the same population (promoted the "full autonomy" preferences, promoted 1ba8d678 three times, promoted a task as a preference *today*) confirms the brief's premise that the queue click is not where quality is enforced.
- **Excluding EXPLORATIONs from auto-install is correct** — 131 of 137 queued artifacts were explorations, they are question-shaped rather than loadout material, and the 2026-06-06 "install exploration as-is" experiment already demonstrated the junk-flood failure mode.

### Out-of-scope but flagged

- **Origin instability across instances of the same fingerprint** (Seat 1): the `_proposed/` copy of fp `da6fa7cb` (07-09) says `origin: unattended`; the `_promoted/` 07-16 copy of the same fingerprint says `origin: interactive`. If the same pattern can re-propose under a different origin, "ineligible forever" is not forever — the *fingerprint's* eligibility should be sticky, not the instance's.
- **Vocabulary misses under-count recurrence system-wide** (parent-design concern, not this gate's): OOV phrases are dropped entirely in `_normalize_signals`, contributing zero recurrence. Recorded miss volumes are material — mission_control 98 misses vs 290 recorded signals, daytrading 64 vs 108, clayrune_cloud 18 vs 6. Genuinely recurring off-vocabulary patterns can never reach 3; the threshold is effectively unbounded for them. This bounds auto-scope's yield well below its paper value and deserves a vocab-growth pass from the miss telemetry before yield is measured (Condition 4).
- **Duplicate re-proposal churn** (Seat 3): the 7-day outbox dedupe let identical-fp preferences resurface and get re-promoted repeatedly (b8345e3b ×3, b4e4b1bf ×4, 1ba8d678 promoted ×3 *and* rejected *and* quarantined). Auto-install converts this churn into repeated machine writes to the loadout; the install/revert lifecycle needs to be idempotent per fingerprint.
- **The human gate rubber-stamped in real time during this audit**: the operator promoted an unattended-origin skill and a task-shaped preference while I was reading the queue. This strengthens the brief's §1 argument that promotion-time human review is a consent ritual — but it equally means the "human path still catches what the gate misses" consolation in this report describes a low-scrutiny catch.
