# Seat 4 — Config, Ops, Rollback, Cost — Auto-Scope Promotion Brief

> Reviewed: `docs/AUTO_SCOPE_PROMOTION_COMMITTEE_BRIEF.md` (§3 proposal), against
> `SKILLS_CURATION_PHASE4_SPEC_V2.md` §11 + committee synthesis (Seat 4 conditions
> D10–D13, I4–I5, Parent Cond 11 inheritance), `SKILLS_CURATION_PHASE5_AUTOMODE_ROLLBACK_SCOPE.md`,
> `distiller.py` (`_DEFAULTS_GLOBAL` l.175, `ENTRY_POINTS` l.512, `_distiller_should_proceed` l.522,
> `_within_cost_cap` l.1284, promotion/rejection block l.2432–2590), `server.py` CONFIG l.153–168,
> `skills.py` `write_skill`/`delete_skill` l.273–331, `tests/test_distiller_kill_switch.py`,
> `docs/MAINTENANCE_AUDIT_PROMPT.md`, `mc/blueprints/push_mobile.py` (Inbox timeline).

## Seat 4 — Config, ops, rollback, cost — RATIFY-WITH-CONDITIONS

The two-flag kill-switch design is right and cheap to wire — `auto_promote` is
already enumerated in `ENTRY_POINTS` and covered by the parametric master-kill and
mode-off tests, and `_cfg` reads live config so no restart is needed for any toggle.
But the brief's operability story is thinner than what this committee already
ratified: it names only "Skills panel badge + monthly audit" where Parent Cond 11
(inherited, binding: "Audit checklist is a *summary*, not the only discovery path";
"Discovery surface MUST ship in same commit") and the existing Phase 5 scope doc
specify an endpoint + filter + ledger + since-cursor digest + one-click and bulk
revert — and it is silent on flag-off semantics, collision safety, and what "revert"
provably restores. All closable; none require redesigning §3.

### Blockers

None.

### Conditions

**C1 (in-design) — State flag-off semantics.** The brief must say what happens to
already-auto-installed artifacts when either flag is turned OFF: future installs
stop immediately; existing installs STAY (nothing is silently removed — silent
removal of a loaded skill is its own behavioral surprise); they remain badged,
ledgered, and visible in the discovery surface; removal is only via the revert
path (single or bulk). This matches the Phase 5 scope doc §1 ("Existing auto
skills stay until reverted") — adopt that sentence verbatim.

**C2 (in-implementation) — Gate branch + independence test.** Extend
`_distiller_should_proceed`: for `entry_point == 'auto_promote'`, require BOTH
`_cfg('distiller_auto_scope_enabled', False)` AND
`_pcfg(project, 'distiller_mode') == 'auto'` — mirroring the
`cross_project_aggregate`/`distiller_cross_project_enabled` branch at l.539. Add
tests mirroring `test_cross_project_kill_is_independent`: (a) auto kill flips only
`auto_promote`, other entry points unaffected; (b) `auto_promote` is False when
either flag is absent or off. The install call site must invoke the gate
immediately before the filesystem write, not only at aggregation start (config can
change mid-walk; toggles are live).

**C3 (in-implementation) — Dark-ship proof.** Two tests: (a) fresh-config test —
with no config keys set and a project record carrying `distiller_mode: 'auto'`,
`_distiller_should_proceed(pid, 'auto_promote')` returns False (the global default
False wins; a fresh install can never auto-install, per the BINDING
nothing-operator-specific rule — the enabling flag lives only in the operator's
`data/` config, never in a committed file); (b) default-table mirror test —
`distiller._DEFAULTS_GLOBAL['distiller_auto_scope_enabled'] is False` AND the
`server.py` CONFIG default is False, asserted against each other so the two tables
cannot drift on this key (drift precedent exists: `distiller_preference_min_recurrence`
is in server.py CONFIG l.166 but absent from `_DEFAULTS_GLOBAL`).

**C4 (in-design) — Ship the full Parent Cond 11 surface in the SAME commit.** The
brief's §3.2 ("badge + monthly audit") under-implements the inherited condition.
Required, per the ratified inheritance row (SPEC_V2 l.1408) and the Phase 5 scope
doc §2: `GET /api/distiller/auto-authored?[project_id=]&[since=]` (one query
answers "what did the system install this week and why"); Skills panel badge AND
filter; per-project `_auto_authored` ledger rows in `_skill_stats.json`
(name, `auto_installed_at`, fingerprint, recurrence, source_session, origin);
"N new since last review" cursor; one-click revert; bulk/panic revert (revert all
+ flip project back to `proposed`). The monthly audit entry is additive, not the
discovery path.

**C5 (in-design) — Real-time event per auto-install.** Monthly cadence is not
adequate for PREFERENCE-kind installs — an always-loaded behavioral instruction
(the exact artifact class of the 2026-07-11 incident) can steer every session in a
project for up to a month before a human looks. Each auto-install must emit an
event on the existing notification timeline (`mc/blueprints/push_mobile.py`
Inbox `log_inbox` path) — mandatory for `kind: preference`, recommended for
`kind: skill`. This is the "faster surface for the first N installs" answered
structurally: every install notifies, so N is irrelevant.

**C6 (in-implementation) — Collision guard, or revert destroys human work.**
`skills.write_skill` defaults `overwrite=True`. Auto-install MUST NOT overwrite an
existing skill dir unless its frontmatter carries `auto_authored: true`: pass
`overwrite=False` or pre-check, and on collision fall back to `_proposed/` and
bump `auto_install_refused:collision`. Without this, an auto-install can clobber a
human-authored skill of the same name and the revert path (`delete_skill`, hard
delete for project scope) then destroys the human's content — revert completeness
is unachievable.

**C7 (in-implementation) — Revert completeness defined testably.** "Restored" =
(a) `<project>/.claude/skills/` tree byte-identical to the pre-install snapshot;
(b) the uninstalled copy preserved under `data/skills/_auto_reverted/` (audit
trail, mirrors `_rejected/`, never silently deleted); (c) suppression record
`{exact}:{kind}` `decision: no`, `source: auto_revert` present in the owning
project's stats; (d) ledger row marked reverted, not deleted; (e) a subsequent
aggregation pass does NOT re-propose or re-install (`_is_suppressed` honored).
Note the asymmetry is intentional: the loadout is restored; the bookkeeping is
not — "no" stays durable (rail #3). One integration test covering (a)–(e).

**C8 (in-implementation) — No new DATA_DIR sidecar.** All new bookkeeping (ledger,
counters, cursor) lives inside the existing `_skill_stats.json` /
`_global_skill_stats.json` sidecars, already covered by
`EXCLUDED_SIDECAR_SUFFIXES`. If any new file is introduced under `data/projects/`,
it must join the constant and the existing regression test in the same commit
(LOAD-BEARING RULE, CLAUDE.md).

**C9 (in-implementation) — Observability minimum set.** (a) One structured log
line per install, mirroring the I4 cost-cap shape:
`distiller_auto_installed:project_id=<pid>:kind=<kind>:name=<name>:fingerprint_exact=<hex>:recurrence=<n>:origin=interactive`;
(b) counters `auto_installed:<kind>`, `auto_reverted`,
`auto_install_refused:<reason>` (reasons at minimum: `authority_recheck`,
`suppressed`, `origin_ineligible`, `collision`, `rate_cap`); (c) `loop_health()` /
`/api/distiller/loop-health` extended with the auto-install totals and the
`auto_reverted / auto_installed` ratio (the Phase 5 soak metric).

**C10 (in-design) — Rate bound per project per day.** Add
`distiller_auto_install_max_per_project_per_day` (global key, suggested default 3,
same naming family as the cost cap) so a recurrence storm cannot flood a loadout
between reviews; overflow falls back to `_proposed/` and bumps
`auto_install_refused:rate_cap`. The Phase 5 scope doc's panic button is the
recovery for the 2026-06-06 flood failure mode; this is the prevention. Cheap:
one counter read under the existing `_get_skill_stats_lock`.

### Ratifications

- **Two-flag layering is right.** Global master (default false) + per-project
  opt-in matches the proven `distiller_cross_project_enabled` precedent exactly;
  a per-project `auto` flip does nothing until the operator also arms the global
  flag, and the global flag alone arms nothing. Both are one dict lookup inside
  the single gate.
- **ENTRY_POINTS already covers it.** `auto_promote` is enumerated (distiller.py
  l.517, stubbed with gate enforced) and the parametric tests
  (`test_master_kill_switch_disables_all_entry_points`,
  `test_per_project_off_mode_disables_all_entry_points`) iterate the frozenset, so
  the new path inherits master-kill and mode-off coverage for free. No enumeration
  change needed — only the C2 branch and its independence test.
- **No-restart propagation holds.** `_cfg` → `_config_get` reads live CONFIG;
  `_pcfg` reads the project record; both toggles take effect on the next
  session-end with no restart, consistent with every existing distiller key.
- **Config hygiene.** `distiller_auto_scope_enabled` is named consistently with
  the family; it must land in all three places (`_DEFAULTS_GLOBAL`, server.py
  CONFIG block, `_CONFIG_EDITABLE_KEYS`) — covered by C3(b). Nothing
  operator-specific is committed: the enabling state is pure user config.
- **Cost posture is sound.** The installer is filesystem-only — zero model tokens,
  correctly outside `distiller_cost_cap_tokens_per_project_per_day` (which gates
  the cap-hit *upstream* at extraction/generation, so a cap-hit can never
  half-install). Eligibility re-checks are bounded: `_is_suppressed` is two
  sidecar reads (project + `_global`), `_authority_violation` re-check is one
  deterministic function call, both per-candidate within the already-debounced
  walk. New bookkeeping is a few keys in an existing locked, atomically-written
  sidecar. No new cost-cap key needed.
- **Suppression/revert reuse is the right shape.** Revert = `delete_skill` +
  `_suppress_artifact(source='auto_revert')` + `_auto_reverted/` relocation
  reuses ~80% existing mechanism (`_relocate_proposed`, `_suppress_artifact`,
  `mark_promoted`), including the durable-no fix that global rejections land in
  `_GLOBAL_SUPPRESSION_PID` — though auto-scope is project-only, so the owning-pid
  path always exists.

### Out-of-scope but flagged

- **PREFERENCE-kind inclusion contradicts the standing Phase 5 scope doc.**
  `SKILLS_CURATION_PHASE5_AUTOMODE_ROLLBACK_SCOPE.md` §1 locked "skill kind only —
  never exploration/preference/update" and D-A1's recorded leaning was "no" on
  preferences (always-loaded behavior, higher blast radius — and the 2026-07-11
  quarantine class was precisely PREFERENCE). The brief's §3.1(1) widens this
  without acknowledging the contradiction. Whether preferences belong in the auto
  surface at all is Seat 1/2 territory; from this seat: if they stay in, C5
  (per-install notification) and C10 (rate bound) are non-negotiable, and the two
  docs must be reconciled so there is one authoritative scope statement.
- **Default-table drift already exists.** `distiller_preference_min_recurrence`
  lives in server.py CONFIG (l.166) but not in `distiller._DEFAULTS_GLOBAL` —
  harmless today (call sites pass a fallback) but exactly the drift class C3(b)
  guards; consider a general mirror test over all `distiller_*` keys while adding
  the specific one.
- **Test-file naming drift.** distiller.py l.510 cites
  `tests/test_distiller_kill_switch_enumeration.py`; the file is
  `tests/test_distiller_kill_switch.py`. Cosmetic; fix the comment when touching
  the gate for C2.
- **Monthly audit prompt extension** (brief §3.2, build-order step 3): when the
  checklist section is added, remember `MAINTENANCE_AUDIT_PROMPT.md` and
  `MAINTENANCE_PROTOCOL.md` "must move together" (header of the former).
- **§3.4 unattended-tightening** (`trigger_type` set) is code-only, no new config
  key — fine from this seat; provenance-trust adequacy is Seat 1's call.
