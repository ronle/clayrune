# Clayrune — Claude Code project notes

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
