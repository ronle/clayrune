# Clayrune — Claude Code project notes

## Commit discipline — stay scoped to your own session (added 2026-06-08)

When asked to commit "the work we did," stage **only the files you edited this
session, by explicit path** (`git add <path> …`):

- **Never** `git add -A`, `git add .`, or `git commit -a`. Name the paths.
- **Don't sweep, don't narrate.** The working tree always carries unrelated
  dirty files — other MC-managed projects' data under `data/projects/`,
  backups, `_scratch/`, mobile/store assets. Don't stage them; don't list them
  back. A commit report names only what you committed. Enumerate other dirty
  files **only** if the user asks "what else is uncommitted?"
- Scratch/throwaway artifacts go in **`_scratch/`** (gitignored), not in
  `tools/`, `docs/`, or `data/`.
- Pairs with the standing rule to commit your own completed work without asking
  — but *only your own*.

`.gitignore` enforces the structural half: backups (`*.bak`/`*.broken`),
runtime (`data/mc_child_pids.json`, `data/skills/_proposed/`), `_scratch/`,
`tools/_*` scratch, served build artifacts, and mobile-app assets are all
untracked so they never reach a commit candidate.

## macOS code-signing & notarization (added 2026-06-04)

The Mac `.app` is now **signed (Developer ID) + notarized + stapled** so fresh
downloads open without the Gatekeeper "Apple could not verify… Move to Trash"
block. This reverses [[feedback-no-paid-code-signing]] **for the Mac app only**
(the Rust `mc-tunnel` moat is unaffected). Ron enrolled in the Apple Developer
Program 2026-06-03; first signed build done 2026-06-04.

- **Per release:** `pyinstaller build-macos.spec --noconfirm` →
  `tools/notarize-macos.sh` → upload the resulting `MissionControl-macOS.zip`.
- **CRITICAL:** `build-macos.yml` auto-attaches an *unsigned* zip to every
  release. You MUST replace it with the script's output or users still hit the
  warning. (CI signing is a deferred follow-up.)
- Identity: `Developer ID Application: Ron Levy (ZN4RFW9K5T)`; Team ID
  `ZN4RFW9K5T`; notarytool keychain profile `clayrune-notary`. Bundle id
  `io.clayrune.app`.
- Full playbook + gotchas: `docs/MACOS_NOTARIZATION.md`. Gotcha headline:
  `codesign --verify` passes on PyInstaller's ad-hoc sig (false "valid") — only
  the `Authority=` line from `codesign -dvv` proves Developer ID signing took.

## Video attachments — use the frame extractor

Claude (this model) doesn't read videos natively. When the user attaches an
`.mp4` / `.mov` / `.webm` / `.avi` / `.mkv` file in this repo (typically under
`data/uploads/agent_*.mp4`), do this **before** trying to describe it:

```bash
tools/extract-frames.sh <path-to-video>
```

That writes `<basename>_frames/frame_001.png ... frame_NNN.png` next to the
video. Read those PNGs with the Read tool to actually see the content.

Defaults: 2 fps, capped at 24 frames. Override for longer / more detailed
clips: `tools/extract-frames.sh video.mp4 4 48` (4 fps, up to 48 frames).

ffmpeg must be installed (`winget install Gyan.FFmpeg` / `apt install ffmpeg`
/ `brew install ffmpeg`). The script tells the user how to install if missing.

## Showing the user an image in chat

To display an image to the user, **output its absolute path on its own line** —
the agent-chat renderer (`formatAgentText` → `/api/serve-image`) turns it into an
inline thumbnail (click to enlarge). The file must resolve under the repo root,
`data/uploads/`, or a registered project path (the `/api/serve-image` allowlist).
**Markdown `![](...)` does NOT render** — the generic "your output is GitHub
markdown in a terminal" framing is misleading for MC's web chat. Full detail +
gotchas: memory `reference-show-image-in-chat`.

## Live test environments

Two VMs are kept clean for end-to-end install testing:
- Windows 11 Home VM
- Ubuntu 22.04 VM

Both validated `c34cf44` clean. Re-test on a fresh snapshot if you change
anything in `installer/`.

## Skills (Anthropic-format) — management surface

Clayrune ships a Skills surface (sidebar entry above Backlog, project-modal
three-dot menu entry) that manages skills CC reads from `~/.claude/skills/`
and `<project_path>/.claude/skills/`. Five built-ins ship under
`data/skills/builtin/` and install once on startup with checksum-based
update preservation (`skills.install_builtins`). User edits to a managed
built-in are preserved across updates.

To add a new built-in: drop a folder under `data/skills/builtin/<name>/`
with a `SKILL.md` (and optional `scripts/`, `references/`). On next MC
startup `_install_builtin_skills()` will install it. Bump the source file
to push an update — checksum drift triggers re-install for users who
haven't modified their copy.

Backend: `skills.py` module + `# ── Skills endpoints` section in
`server.py`. Frontend: `// ── Skills (global + per-project ...)` section
in `static/index.html`. Architecture and rollback recipe in CHANGELOG
`[2026-05-10]`.

## memsearch — cross-session persistent memory layer (added 2026-05-14)

Claude Code has the `memsearch` plugin installed (Zilliz, MIT, v0.4.2+).
It gives sessions persistent semantic recall across conversations without
external services — markdown files at `.memsearch/memory/<YYYY-MM-DD>.md`
are the source of truth, Milvus Lite at `.memsearch/milvus.db` is a
rebuildable vector index, embeddings via local ONNX bge-m3 (no API key,
no daemon, no Docker).

**At task start** (especially for non-trivial work in this repo): use the
plugin's memory-recall skill / query memsearch for the topic *before*
starting. Memory files at `~/.claude/projects/C--Users-levir-Documents--claude-mission-control/memory/`
are still the curated stable index ([[feedback-grep-memory-dir]]); memsearch
holds the fluid auto-captured context (decisions, debugging notes,
what-was-tried). The two are complementary, not redundant.

**Storage**: per-project (each MC project gets its own `.memsearch/`).
Both the memory dir and the index are gitignored. To wipe and rebuild
from scratch: `rm -rf .memsearch && memsearch index --force`.

## Memory system — server-side Scribe + Leg 0 (added 2026-05-17)

Headless project agents get cross-session memory via a **server-side
pipeline** in `server.py`, not via plugins. Full design + committee review:
`docs/MEMORY_SYSTEM_SPEC.md`. The shape:

- **Leg 0 format**: a project's `MEMORY.md` = a *curated* pointer index on
  top + a sentinel-delimited *managed region* below
  (`<!-- clayrune:managed:begin/end -->`, `## Session Log`). The curated
  region is human/condense-owned and byte-preserved; only the condense model
  tier may rewrite it. Machinery touches the managed region only. Helpers:
  `_mem_split` / `_mem_compose` / `_mem_migrate` (idempotent, additive).
- **Scribe** (`_scribe_extract` → shared `_write_session_memory`): on
  session end, reads the CLI's on-disk `.jsonl` (the only full-fidelity
  source — MC's in-memory `log_lines` drops tool results & thinking),
  cheap-model-summarizes one line, falls back to the stdout tail on any
  failure. Telemetry: `/api/project/<id>/scribe-stats`.
- **Retrieval**: `/api/project/<id>/memory/search`, a deterministic
  read-floor injected in `_build_agent_context`, and the `mc-memory-search`
  built-in skill.
- **Trim**: line-keyed lossless mechanical floor + the condense model tier
  (`index_line_budget`/`index_line_hard_floor`); the archive is **permanent
  searchable cold storage — never delete/truncate it**.
- **Fix B**: `_reconcile_unscribed_sessions` at startup closes the
  hard-MC-kill gap; first encounter baseline-stamps history `scribed:true`
  without scribing it.

**LOAD-BEARING RULE — DATA_DIR pollution.** `DATA_DIR` (`data/projects/`)
is the project-records dir; `load_projects()` treats every `*.json` there
as a project. Anything else written into `DATA_DIR` (telemetry, sidecars)
**MUST be suffix-excluded in `load_projects()`** (it already excludes
`_agent_log.json` and `_scribe_stats.json`). A stray file there becomes a
malformed "project" and 500s `_get_active_restart_blockers` → both restart
endpoints. New per-session/sidecar state belongs OUTSIDE `DATA_DIR`.

**Mode B caveat.** With `use_streaming_agent` (global default) the
persistent process doesn't exit per turn, so the session-end Scribe fires at
*teardown*, not per turn. Step 6 mid-session checkpointing (SPEC §3.A.MID) is
the fix — **implemented (commit `9683996`), offline- AND live-validated
end-to-end (2026-05-18), and currently ENABLED** on this deployment
(`scribe_checkpoint_enabled=true`, `scribe_checkpoint_kb=8`). It ships
default-off in code; revert with `scribe_checkpoint_enabled=false` (a
Settings toggle, no restart). So Mode-B sessions now DO capture per-turn,
not only at teardown. When working on memory code: the Step-6
`<!-- clayrune:wm:<sid> … -->` watermark markers are load-bearing — never
strip them. The MEMORY.md write discipline is **leaf-locked + atomic**:
`_commit_managed_entry` (completion, checkpoint, reconcile) is one such
writer; `_condense_apply` (structured Leg C, `condense_mode=structured`) is a
co-equal second one — both take the SAME per-project `_get_mem_write_lock`,
both write via `_atomic_write_text`, and both route archive overflow through
the shared `_append_to_archive`. Any new MEMORY.md mutation MUST follow that
same lock+atomic+shared-archive discipline (do not add an unlocked or
non-atomic writer). The legacy `condense_mode=agent` path is the exception
that proves the rule — it writes from a subprocess outside the lock, which is
exactly why it needs the `_condense_integrity_check` heal/restore guard.

**Rollback**: `scribe_enabled=false` reverts to the legacy stdout-tail
write; `scribe_reconcile_enabled=false` disables startup reconcile.


## Skills Curation — design + Step 1 shipped (added 2026-05-18)

A self-improving skills layer on top of the existing Skills surface. Design:
`docs/SKILLS_CURATION_DESIGN.md`. Step 1 ships as the `mc-distill` built-in
skill (auto-installs on startup); backend Distiller / telemetry / dispatch
hint are deferred pending committee review.

**Principles (firm):**
- **MC owns, agent proposes, human promotes.** Skills the agent invents do
  not enter the loadout without explicit human approval (or, in opt-in
  `auto` mode, without project-local scoping that the user can revert).
- **Three per-project modes** (`distiller_mode`): `off` (no proposing),
  `proposed` (writes to `data/skills/_proposed/<sid>/` for UI review), or
  `auto` (writes directly to `<project>/.claude/skills/` with
  `auto_authored: true`, surfaced in the monthly audit). User-controlled
  per project; no `production` flag, no system-imposed rules — the user is
  trusted to choose.
- **Authored skills only.** Explicit named SKILL.md artifacts; no
  "learned behavior" drift in MEMORY.md's curated section (out of scope —
  blurs the curated/managed boundary and is hard to roll back).
- **Auto-authored skills are project-local only.** Never written to
  `~/.claude/skills/`. Global promotion is always a deliberate user
  action.
- **Distiller is best-effort, never load-bearing.** Failure to distill
  never breaks a session, never breaks Scribe, never blocks completion
  logging. Same posture as Scribe's thin/refusal guards.

**Three trigger paths** (only #1 currently shipped):
1. **Conversational push (SHIPPED).** The `mc-distill` skill empowers the
   agent to surface a candidate proposal inline at a natural breakpoint
   (end of task, after commit, wrap-up). Format:
   `Noticed a pattern: <X>. Observed <N> times. Bottle? [Yes / Later / No]`.
   Hard rules: recurrence ≥ 2, specificity (one-line name + concrete
   observations), one-proactive-push-per-session max, never mid-task. Same
   skill also handles the explicit `/distill` invocation path.
2. **Silent Distiller (NOT BUILT).** Cheap-model proposer at session end,
   parallel to Scribe — reuses `_scribe_render_transcript` and the
   `_scribe_call` wrapper. Writes to `_proposed/<sid>/` (or, in `auto`
   mode, directly to project skills). Catches *cross-session* recurrence
   the in-session agent can't see.
3. **Dispatch skill-relevance hint (NOT BUILT).** Top-K skills injected
   into the read-floor at dispatch. v1 = keyword scoring via the existing
   `/api/skills/search` endpoint (used by `mc-skill-broker`); v2 = bge-m3
   semantic similarity when/if Step 7 ships.

**`No` from a conversational push** writes a suppression marker to
`_skill_stats.json` (when telemetry ships) so the silent Distiller does
not re-propose the same pattern in the same session. `Later` is **not**
consent — only `Yes` writes anything.

**LOAD-BEARING RULE — DATA_DIR pollution (same as Memory System).**
`data/projects/<id>/_skill_stats.json` (when telemetry ships) MUST be
suffix-excluded in `load_projects()` — same rule as `_agent_log.json` and
`_scribe_stats.json`.

**Build order** (`docs/SKILLS_CURATION_DESIGN.md` "Recommended build order"):
1. `mc-distill` skill — SHIPPED 2026-05-18.
2. Skill-use telemetry (`_skill_stats.json`).
3. Audit checklist extension in `docs/MAINTENANCE_AUDIT_PROMPT.md`.
4. Distiller (`proposed` mode only) — `distiller.py` module, hooks into
   the Scribe trigger.
5. `auto` mode — after `proposed` is real and proposal quality has been
   observed in practice.
6. Dispatch skill hint (v1 keyword).
7. Dispatch skill hint (v2 bge-m3) — if/when Step 7 lands.

Steps 2–7 require **committee review against the design doc before any
code lands** — same discipline as Memory System Step 6 (`MEMORY_SYSTEM_SPEC.md`
§3.A.MID) and Leg C structured condense (CHANGELOG `[2026-05-18e]`
committee review block).

## Skills Curation — Phase 4 promoted, Phase 1 softened (added 2026-05-27)

**Build order revised.** The original v2 design ordered telemetry (Phase 2)
before the silent Distiller (Phase 4). A 2026-05-27 diagnostic against
`~/.claude/projects/**/*.jsonl` showed **zero proactive Phase 1 fires
across 1,199 sessions in 9 days** post-ship (`mc-distill` loaded in ~99%
of those sessions). The committee's soak-gate Condition #14 collected its
evidence: the in-session-recurrence bar is structurally incompatible with
single-task sessions, so telemetry that counts Phase 1 fires would be
silent. Phase 4 (cross-session observer) is promoted to the next thing
built; Phase 2's substrate (`_skill_stats.json`, locks, fingerprint
normalization, kill-switch gate) folds into Phase 4's implementation.

**Spec:** `docs/SKILLS_CURATION_PHASE4_SPEC.md` (DRAFT v1.1, 2026-05-27).
Companion to the parent `docs/SKILLS_CURATION_DESIGN.md` (still
authoritative for the load-bearing rules and Conditions 1–11 from the
2026-05-19 committee). When reading the parent doc's "Recommended build
order," **defer to v1.1**: Phase 4 is next, not Phase 2.

**Locked decisions (v1.1):**
- Recurrence threshold default = `3`.
- Cross-project scoping: per-project default for proposal generation
  (blast-radius safety) + a "cross-project candidates" surface that
  *notifies* but does not auto-write (operator-level patterns are
  findable without leaking).
- Extraction prompt is "what topics did this session touch?" — narrow
  objective question; the cross-session aggregator does the judging.
  This is the inverse of Phase 1's "is this worth bottling?" framing.

**Phase 1 softening (parallel SKILL.md edit).** `mc-distill` SKILL.md
softened on disk 2026-05-27 ahead of Phase 4 backend code:
- Dropped `recurrence ≥ 2 within session`
- Dropped `once per session, max`
- Reversed `err toward asking less` disposition
- Kept natural-breakpoint + specificity + no-duplicates bars
The source file is at `data/skills/builtin/mc-distill/SKILL.md`; the
propagated copy at `~/.claude/skills/mc-distill/SKILL.md` refreshes via
`_install_builtin_skills()` checksum drift on next MC restart. **Until
restart, agents still see the old hard rules.**

**Pending (in order):**
1. MC restart (operator approval required per
   [[feedback-server-restart-approval]]) — propagates softening.
2. Committee review against v1.1 spec — same four-seat structure as
   parent (pattern-integrity / agent-behavior / concurrency / config-
   ops). No backend code lands until ratification + condition closure.
3. Phase 4 build — `distiller.py` module + hooks into
   `_write_session_memory` + two server endpoints + DATA_DIR exclusion
   regression test. ~400–600 LOC. Single bundled PR recommended.

**Resumability anchors** (read these when picking this up later):
- `docs/SKILLS_CURATION_PHASE4_SPEC.md` — current authoritative spec
- `docs/SKILLS_CURATION_DESIGN.md` — parent design + Conditions 1–11
- `docs/SKILLS_CURATION_COMMITTEE_BRIEF.md` — pattern for the v1.1 brief

## Learning definition locked + build order revised (added 2026-05-27)

After Phase 4 v1.1 committee returned RATIFY-WITH-CONDITIONS (no blockers,
14 must-fix-in-design), Ron pushed back on the framing ("we're not
defining the right things here"). A working definition of "learning" was
then locked in conversation. See `~/.claude/projects/C--Users-levir-Documents--claude-mission-control/memory/decision_learning_definition.md`
for the full text. Headline:

> **Learning is when the agent's effective behavior changes over time,
> driven by experience, without the human having to type the change.**

Experience includes own past sessions AND proactive external exploration.
Targets include codebase + work + user + agent itself. Default scope is
cross-project (narrowed only when intrinsically project-specific) with
dual-checkpoint tagging (extraction-time + promotion-time). Feedback
signal is RELAXED: human review at promotion is sufficient.

**Phase 4 v1.1 spec is now reference-only.** Mechanism details (Scribe
trigger, lock pattern, fingerprint approach, kill switch, atomic-write
discipline) remain valid as building blocks for the v2 design.

**v2 spec DRAFTED 2026-05-27:** `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md`.
Incorporates all five shifts (cross-project default,
external-exploration retention as first-class artifact,
proactive-exploration disposition, user-facing learning,
dual-checkpoint scope tagging). Closes all 14 v1.1 must-fix-in-design
conditions. Settles the three open questions: (a) restore mc-distill's
once-per-session cap, (b) closed-vocabulary Stage 1 + near-miss
telemetry (NOT embeddings, NOT bag-of-tokens), (c) mc-distill §Tone
removed and §Procedure step 2 rewritten so the SKILL.md is end-to-end
internally consistent. Introduces a four-artifact model
(SKILL/UPDATE/EXPLORATION/PREFERENCE) with one `_proposed/` writer.

**v2 RATIFIED-WITH-CONDITIONS 2026-05-27** by 4-seat committee
(synthesis appended to v2 spec under `## Committee review (2026-05-27)`).
Unanimous, no blockers. 14 must-fix-in-design + 5 must-fix-in-
implementation + 2 soak-gate conditions. Strongest cross-seat
convergences: EXPLORATION.md needs per-session cap (Seats 2+3); cross-
artifact suppression must key on (fingerprint, kind) (Seats 2+3);
cross-project recurrence composition rule unspecified (Seats 2+3+4);
cross-project aggregation lock+cost discipline punted (Seats 1+3+4);
closed-vocab lists under-fitted to this codebase + subsystem terms
in wrong slot (Seat 1); in-session push fingerprint needs server-side
re-normalization (Seats 1+3).

**v2.1 DRAFTED 2026-05-29** — all 14 must-fix-in-design conditions
closed inline in the spec. Architectural picks locked: D2 coarse
fingerprint threshold = exact + 1 (Option A, no new config key); D3(ii)
cross-project walk = lock-free with 3-retry parse (Option B, matches
best-effort posture). Status header on the spec is now
"DRAFT v2.1 (post-committee-review 2026-05-27, revised 2026-05-29)".
Backend build can proceed once condense is fixed (gate #1 still open).

**Revised build order:**

1. **Fix condense first.** The 2026-05-23 diagnostic noted 58 timeouts +
   48 errors in structured condense. The memory-refinement half of the
   existing learning loop is degrading. Foundation must be working before
   adding new learning layers on top.
2. **Redesign Phase 4 as v2** — DONE 2026-05-27. See
   `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md`.
3. **Committee review of v2** — DONE 2026-05-27. RATIFY-WITH-CONDITIONS.
4. **v2.1 spec revision** — DONE 2026-05-29. All 14 must-fix-in-design
   conditions closed inline. Picks D2 (Option A: exact+1) and D3(ii)
   (Option B: lock-free retry) locked.
5. **Backend build** — DONE 2026-05-29 (commit `d2dc8a6` on
   `local/opus-effort`). `distiller.py` (~1600L) + server.py wiring +
   3 Flask endpoints + 31 tests passing. Self-learning loop LIVE.

**Self-learning system NOW OPERATIONAL.** End-to-end verified:
- Session ends → daemon-thread `_distill_extract_and_aggregate` fires
  parallel to Scribe (best-effort, never blocks completion).
- Closed-vocab fingerprint (80 verbs, 123 nouns, 12 modifiers; D1
  closure proven: subsystem terms `condense/scribe/distiller/hivemind/
  pair/mobile-pair/github-sync/project-sync` are NOUNS, not modifiers).
- Dual-layer recurrence (exact + coarse, threshold = N and N+1).
- Three artifact kinds (SKILL/EXPLORATION/PREFERENCE) generated to
  `data/skills/_proposed/{global,<project_id>}/<...>/`.
- Cross-project inline aggregation, lock-free walk with 3-retry parse.
- Suppression keyed on (fingerprint, kind); record-push endpoint live.
- Cost cap with structured log including cap_value.

**Polish deferred (separate work tracks):** Skills panel UI for the
`_proposed/` queue (frontend), EXPLORATION.md read-floor injection
into `_build_agent_context`, promotion-time UI flows, `auto` mode
(Phase 5).

**No backend learning-system code lands** until condense is fixed AND
v2 has cleared committee. Same discipline as parent design v2 and
Memory System §3.A.MID.

## Exception-swallowing policy (added 2026-06-09)

When touching any function containing `except Exception: pass`, decide: if the
try-body is pure best-effort cosmetics (cleanup of a temp file, optional Pillow
shrink), leave it. If it wraps **subprocess, file I/O on state files, JSON state
load/save, or network**, convert to:

```python
except Exception as e:
    _log(f"[<subsystem>] <operation> failed: {e}", flush=True)
```

(keep swallowing — just make it observable.) Do **not** do a bulk sweep — apply
only when already editing the function. There are ~178 such blocks (104 in
`server.py`, 24 in `agent_runtime.py`); a mass rewrite is out of scope.

**Resumability anchors** (updated 2026-05-27):
- `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` — CURRENT authoritative spec
- `docs/SKILLS_CURATION_PHASE4_SPEC.md` — v1.1, reference-only
- `docs/SKILLS_CURATION_DESIGN.md` — parent design + Conditions 1–11
- `~/.claude/projects/.../memory/decision_learning_definition.md` — locked def
- `docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md` — v1.1 committee assessments
