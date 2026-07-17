# AUTO_SCOPE_PROMOTION — Seat 3 assessment

> Reviewer scope: install/revert races, double-install, suppression ordering,
> crash windows, consumer-side TOCTOU, loop containment.
> Sources read end-to-end: `docs/AUTO_SCOPE_PROMOTION_COMMITTEE_BRIEF.md`,
> `distiller.py` (locks, `_commit_signals`/`_cold_archive_old_signals`,
> `_aggregate_per_project`, `_read_skill_stats_with_retry` walk,
> `_generate_and_write_artifact`, `mark_promoted`/`_relocate_proposed`/
> `_suppress_artifact`, `_installed_exact_fingerprints`),
> `mc/blueprints/distiller_routes.py`, `skills.py` (`write_skill`,
> `install_builtins`), v2.1 spec committee synthesis (prior Seat 3 conditions).

## Seat 3 — Concurrency & lifecycle — RATIFY-WITH-CONDITIONS

The proposal is concurrency-viable because the pieces it composes are already
disciplined (per-project `_distilling_projects` re-entrancy guard, per-project
semaphore, leaf-locked `_skill_stats.json`, atomic proposal writes) — but the
brief adds a SECOND writer to the artifact lifecycle without naming its
serialization domain, and its stated revert semantics contradict what
`mark_promoted` actually does on disk today (it writes a permanent
`decision: no` suppression on PROMOTE, so "uninstall = proposable again" is
currently false for anything that went through promote bookkeeping). Every
race below has a concrete fix; none requires redesign, so:
RATIFY-WITH-CONDITIONS.

### Blockers

None. All findings are closable as conditions.

### Conditions (RATIFY-WITH-CONDITIONS only)

**C1 — Name the serialization domain; use the proposal-dir rename as the
claim token. [in-design]**
Today the promote endpoint (`distiller_routes.py::post_distiller_promote`,
Flask request thread) calls `skills.write_skill` → `distiller.mark_promoted`
holding NO lock; the auto-installer will run on the distill daemon thread.
Two writers, no shared mutex → double-install and install-vs-reject
interleavings are unconstrained. `_get_skill_stats_lock` CANNOT be the wrapper:
it is a plain non-reentrant `threading.Lock`, and `mark_promoted` →
`_suppress_artifact` re-acquires it internally — wrapping the install sequence
in it reproduces the 2026-06-15 py-spy self-deadlock class byte for byte
(the warning is written into the lock's own docstring, distiller.py:318–322).
Instead, make `_relocate_proposed`'s `d.rename(dest)` the atomic ownership
claim and REVERSE the current ordering for both paths: **claim (rename dir out
of `_proposed/`) → install (`write_skill`) → bookkeeping (suppression/outbox)**.
`os.rename` is atomic on one filesystem; exactly one contender wins; the loser
gets `_is_within_proposed()` → None → clean 404/skip. This also makes a human
clicking promote/reject on a stale UI row fail safe (already verified:
`read_proposed_artifact` returns None once the dir has moved). The design must
state this ordering explicitly for the auto path AND retrofit the promote
endpoint, which today does write_skill FIRST and claims second.

**C2 — "Suppression wins" is not currently guaranteed; make it so, and ship a
reconciler for the contradictory state. [in-design + in-implementation]**
Interleaving that resurrects the `preference-1ba8d678` zombie: auto-installer
passes eligibility check #5 (`_is_suppressed` clean) → human clicks reject
(`reject_proposed`: suppression written, dir moved to `_rejected/`) →
auto-installer, already past its check, calls `write_skill` → artifact is
**installed + in `_rejected/` + suppressed** simultaneously. Nothing in the
brief's ordering prevents this. Required: (a) with C1's claim-first ordering
the window shrinks to nearly zero (the reject's relocate and the installer's
claim contend on the same rename — one loses cleanly), and the installer must
re-check `_is_suppressed` once more AFTER winning the claim, immediately
before `write_skill` — the same D6/Seat-3-Cond-5 TOCTOU re-check pattern
`_generate_and_write_artifact` already implements at distiller.py:1659–1673;
(b) regardless, ship a reconciler (startup, mirroring
`_reconcile_unscribed_sessions` posture) that scans installed `auto_authored`
skills and uninstalls/flags any whose exact fingerprint carries a suppression
with `source ∈ {ui_reject, auto_revert}`. The source discrimination is
load-bearing: `_suppress_artifact` writes `decision: 'no'` for BOTH promote
and reject (only `source` differs), so a reconciler keying on `decision`
alone would uninstall every legitimately promoted skill.

**C3 — Write down the artifact state machine and resolve the
suppression-on-install contradiction. [in-design]**
Enumerated states today: (1) `_proposed/` + not installed + no suppression
(queue); (2) `_promoted/` + installed + suppressed(`ui_promote`) — note:
**installed+suppressed is the NORMAL post-promote state**, not an anomaly;
(3) `_rejected/` + not installed + suppressed(`ui_reject`); (4) installed +
no record anywhere (legacy — the hole `_installed_exact_fingerprints` closed
in 5fb51fa). The 5fb51fa comment block (distiller.py:1481–1497) declares
"uninstalling an artifact makes its pattern proposable again (no permanent
mark)" — but that is only true for state (4); state (2) artifacts stay
suppressed forever after uninstall. The auto path must pick a side and the
design must say which:
- **Recommended:** auto-install writes NO suppression — the dynamic
  installed-fingerprint check IS the dedupe record; only the one-click
  REVERT writes suppression (`source: auto_revert`) + moves to `_rejected/`.
  Then: plain uninstall via the Skills panel = pattern proposable again
  (matches the 5fb51fa intent); revert = durable no. Consistent machine.
- If instead auto-install mirrors `mark_promoted` (suppression on install),
  then a user deleting an auto-installed skill from the Skills panel leaves a
  permanent, invisible `decision: no` they never chose — a silent-suppression
  trap. Reject this variant explicitly.
Also: revert operates on a dir that is no longer under `_proposed/` (it was
claimed/relocated at install time), and `_relocate_proposed` refuses anything
outside `_proposed/` by construction (`_is_within_proposed`). Revert needs its
own mover (`_promoted/`-or-equivalent → `_rejected/`) — specify it; do not
silently widen `_is_within_proposed`, which is a client-supplied-path
traversal guard on the HTTP endpoints.

**C4 — Atomic install write. [in-implementation]**
`skills.write_skill` lands SKILL.md via `skill_md.write_text(...)`
(skills.py:300) — plain truncate-and-write, not tmp+rename. Two consequences
for the auto path: (i) a CC session dispatching in that project can read a
torn/empty SKILL.md mid-write (the consumer reads the dir at process start —
see Ratifications — so the window is real, just narrow); (ii) a concurrent
human promote of a same-named skill interleaves two unserialized `write_text`
calls. The auto path must write via tmp+rename (`_atomic_write_text` is
already in distiller's toolbox), or `write_skill` itself gains an atomic mode.

**C5 — Never clobber a non-auto skill on name collision.
[in-implementation]**
The promote endpoint passes `overwrite=True`; if the auto path copies that, an
auto-installed skill whose model-generated `name` collides with an existing
user-authored project skill silently OVERWRITES the user's file — a
machine-initiated destructive write with no human in the loop. Auto-install
must refuse (or suffix) when the target dir exists and is not an
`auto_authored` artifact with the same `extraction_fingerprint_exact`;
telemetry-log the collision. (Interaction with `install_builtins` checksum
refresh is otherwise nil — builtins target `GLOBAL_SKILLS_DIR` only and the
auto path is project-scope only; a project skill shadowing a same-named
global/builtin is a pre-existing, surfaced behavior via
`shadowed_by_project`.)

**C6 — Pin the trigger point and the eligibility source of truth.
[in-design]**
The brief defines WHAT qualifies but never WHEN the installer runs. If it runs
inside the existing per-project pipeline (recommended: in
`_do_extract_aggregate` after the `_generate_and_write_artifact` loop, same
daemon thread), it inherits the `_distilling_projects` re-entrancy guard and
the D8 semaphore for free — two concurrent session-ends for the same project
cannot both reach it (the second is skipped at distiller.py:795–801), which
answers the double-install question structurally. If it is instead a sweep
over the standing 146-artifact queue (which the brief's phrasing "an artifact
in `data/skills/_proposed/<project_id>/...` is auto-installed iff" implies —
that's the only way the existing backlog ever auto-installs), the sweep needs
its own per-project mutex plus C1's rename claim. Related: eligibility #3
reads `recurrence_count_exact` from WHERE? Frontmatter is a generation-time
snapshot (preferences generate at `pref_min_rec=1`, so their frontmatter
almost never says ≥3); a live recompute must take `_get_skill_stats_lock` and
must tolerate signals having moved to the cold archive
(`_cold_archive_old_signals` keeps only 3×window in the hot file). Say which,
and if live, keep the recompute inside the existing RMW lock contract (§4.7)
without calling `_increment_counter` while holding the lock.

**C7 — Loop containment needs more than the exact-fingerprint check.
[in-design]**
The feedback loop (install → skill shapes next interactive session →
extraction emits similar signals → new proposal) is blocked for the SAME exact
fingerprint by `_installed_exact_fingerprints`, but that check is exact-only
(`_FP_LINE_RE` matches `extraction_fingerprint_exact`). A derivative phrase —
one modifier or verb synonym away — gets a fresh exact hash, and its
recurrence count is AMPLIFIED by the very sessions the installed skill is
steering, so the counter the eligibility gate trusts is no longer an
independent signal. Interactive-origin bounds the loop's clock rate (a human
must drive each evidence session) but not its direction. Require both:
(a) record `extraction_fingerprint_coarse` in installed frontmatter and emit
telemetry (structured log + counter) whenever a new candidate's COARSE
fingerprint matches an installed artifact's — derivative-family detection,
observe before deciding whether coarse should hard-block; (b) a per-project
auto-install rate cap (config key, small default, e.g. 2/day), same posture
as the cost cap — the containment backstop when (a)'s telemetry says the
family is sprawling.

**C8 — Crash-window recovery must be explicit. [in-implementation]**
Window A: crash after `write_skill`, before bookkeeping → installed + still in
`_proposed/` + no suppression. Generation-side re-proposal is already blocked
(installed-fingerprint check), and a sweep-based trigger retries idempotently
(`overwrite=True` on its own prior output per C5), but a generation-time-only
trigger leaves a permanent queue zombie a human may later REJECT — landing in
the C2 contradictory state via the crash path instead of the race path. The C2
reconciler must therefore also complete half-done installs: any `_proposed/`
dir whose exact fingerprint is already installed gets its bookkeeping finished
(relocate; no suppression per C3). Window B: crash after C1's claim-rename,
before `write_skill` → dir in the promoted-bucket, nothing installed, no
suppression; the pattern re-proposes after the D7 dedupe window (self-healing)
or the reconciler spots a bucketed dir with no matching installed fingerprint.
Both windows converge with the reconciler; neither accumulates unbounded
zombies. Test these two interleavings in `tests/test_distiller_safety.py`
alongside the existing rail tests.

### Ratifications

- **Double-install via concurrent distill daemons: structurally prevented**
  (conditional on C6 placing the trigger inside the pipeline). The
  `_distilling_guard`/`_distilling_projects` set (distiller.py:795–808) skips
  — not queues — a second concurrent run for the same project, and two
  different projects write to different `<project>/.claude/skills/` roots.
  The single-instance invariant (one MC per port) rules out cross-process
  contention.
- **Human action on an already-claimed artifact fails safe.** Once the
  installer renames the dir, `read_proposed_artifact` → `_is_within_proposed`
  returns None and the promote/reject endpoints 404 cleanly. No torn
  half-promote path exists in that direction.
- **Consumer-side TOCTOU is benign, with one documented latency caveat.** CC
  reads the skills loadout at process start; nothing in MC re-reads
  `<project>/.claude/skills/` mid-session (the read floor injects
  EXPLORATION.md content only, and `exploration_read_floor` is dispatch-time).
  A mid-session auto-install is invisible until the next dispatch — and under
  Mode B (the default), until the persistent process respawns, since skill
  installs are not in `_RESPAWN_TRIGGER_KEYS`. Fine; state it in the design so
  nobody "fixes" it later.
- **Kill-switch layering fits the existing single-gate discipline.** Adding
  `auto_install` to `ENTRY_POINTS` + the global `distiller_auto_scope_enabled`
  check inside `_distiller_should_proceed` (alongside the existing
  `cross_project_aggregate` special case) + per-project `distiller_mode ==
  'auto'` preserves parent Cond 10 (no call site inlines its own check).
  `distiller_mode` already exists in `_PCFG_DEFAULTS` and project records
  default it to `'proposed'` — dark-ship is real.
- **Prior Seat 3 conditions are not reopened.** The lock-free cross-project
  walk (D3 ii Option B) only READS `*_skill_stats.json` and the auto path adds
  no new writer shape to it; the D7 outbox, D8 semaphore, §4.7 RMW contract,
  and the torn-write recovery posture (`_read_skill_stats` preserves the file)
  are all untouched by the proposal — provided C1 keeps the install sequence
  OUT of `_get_skill_stats_lock` and C2's suppression writes go through the
  existing `_suppress_artifact` lock discipline.

### Out-of-scope but flagged

- **Pre-existing:** `mark_promoted` writing a `decision: 'no'` suppression on
  PROMOTE is semantically overloaded ("no" meaning both "rejected" and
  "already accepted") and is now half-contradicted by the 5fb51fa dynamic-check
  comment. C3 resolves it for the auto path; the human-promote path deserves
  the same cleanup in a follow-up (write `decision: 'promoted'` or drop the
  suppression in favor of the dynamic check + reconciler).
- **Pre-existing:** kind-drift bypasses revert durability — suppression is
  keyed `(exact, kind)`, so after revert-uninstall of a `kind=skill` artifact,
  the same pattern can re-propose as `kind=preference` (the dynamic check no
  longer blocks it once uninstalled). The 5fb51fa fix covered kind drift only
  while installed. Worth a cross-kind suppression option when revert intent is
  clearly "not this pattern."
- **Cosmetic:** after any relocate, `outbox[...]['last_proposed_path']` points
  at the old `_proposed/` path. Nothing consumes it today; annotate or refresh
  it during bookkeeping.
- `write_skill`'s non-atomic `write_text` (C4) also affects the existing HUMAN
  promote path and the skills editor endpoints — fixing it in `skills.py` once
  benefits all writers, not just the auto path.
