# Clayrune Project Sync — Design

> Status: **DRAFT v1** (2026-05-25). Not implemented. Author: Vector, in
> response to Ron's request to extend the existing GitHub Issues ↔ backlog
> sync (`github_sync.py`) into full project sync — code + backlog +
> review surface — for multi-machine collaboration between Ron and Keegan
> (and, generally, any two Clayrune installs sharing the same project).
>
> This doc exists to align on the **paradigm** before code lands. Two
> shipping decisions are already locked from the conversation:
> 1. **Slicing**: design doc first, then decide build order.
> 2. **Commit cadence**: auto-commit per logical change (not "WIP every 5
>    min", not "only what the user explicitly commits"). Definition of
>    "logical change" is one of the open design points below.
>
> ## Decisions locked (walkthrough 2026-05-25)
>
> Ron walked through the open questions on phone; resolutions:
> - **Worktree strategy (§11.1):** Option A — hidden second checkout under
>   `.clayrune/sync-tree/`. User's primary workspace is never disturbed.
> - **Main auto-pull (Q3):** ON by default (FF-only, clean tree required).
>   Plus a "main was updated" notification surface in the dashboard each
>   time main moves, so nothing slips by silently. Edge case: someone
>   pushing to main outside Clayrune is accepted as a low-likelihood
>   tradeoff.
> - **Commit granularity (Q2 / §6):** per agent turn (§6a) confirmed.
> - **Conflict resolution (Q5 / §10):** flipped — **agent-assisted resolve
>   is the DEFAULT path**, not the optional add-on. Rationale: target
>   audience is non-technical; they'd ask the agent anyway. UI shows the
>   resulting diff before applying so user can sanity-check. Manual
>   resolve still available for advanced users.
> - **First-run UX (§11.5):** Wizard confirmed — shows branch name,
>   hidden checkout path, and what will be pushed, with one Confirm.
> - **Backlog ↔ commit linking (§11.4):** YES. Auto-commit messages
>   include `Refs: backlog/<id>` and the backlog item records the SHA.
> - **Branch protection (§11.6):** Detect on first sync; if `main` is
>   protected, Accept silently routes through `gh pr create` instead of
>   direct push. Invisible UX shift for the user.
> - **Sync-branch privacy (§11.8):** Default branding (`clayrune/sync/*`)
>   accepted; customizable prefix shipped as a setting for users who care.
> - **Sync-branch GC (§11.2):** Automatic — reset sync branch to current
>   main when nothing is pending review. No history loss (commits live on
>   in main).
> - **Install ID stability (§11.3):** Store install UUID in user-data dir
>   (`~/.clayrune/install_id`), not under Clayrune's project data. Wipe +
>   reinstall preserves the same sync branch name.


## 1. Goal

Two people (Ron, Keegan) — or in the general case, two Clayrune installs
— share the same project. Each person's agent can edit code; each person
can also edit code directly. Both sides should converge automatically
when changes are safe, and surface a clear review/merge UI when they
aren't. Issues stay in sync (already shipped). Code stays in sync (new).

The dashboard is the **single pane of glass**: you should never have to
leave Clayrune to see "what's the other side doing right now, and can I
pull it cleanly?"


## 2. Scope

**In scope:**
- Bidirectional code sync between a Clayrune project's workspace and a
  GitHub remote.
- Auto-commit of agent-driven changes on a per-logical-change basis.
- Per-machine branch isolation so two installs never race on `main`.
- A review surface inside the MC dashboard showing pending incoming
  commits, their diff, and one-click accept/reject.
- Conflict surfacing — no silent merges that rewrite a dirty tree.
- Extension of `github_sync.py` (rename module → `project_sync.py`?) so
  Issues sync and code sync share project state, cadence, and UI.

**Out of scope (v1):**
- Real-time co-editing (operational transforms / CRDT). Sync is
  commit-granular, not keystroke-granular.
- Force-push, rebase-on-pull as default, history rewriting.
- LFS / submodule sync (treat as opt-in later).
- Self-hosted git remotes other than GitHub.
- Cross-project sync (each project is its own repo).


## 3. The paradigm question — three candidates

Before designing a custom merge UI from scratch, weigh whether MC should
be a git client at all, or lean on existing GitHub flows.

### Option A — MC becomes a git client (direct mode)

Auto `git fetch` / `git pull --ff-only` / `git push` on a timer.
Conflicts → surface in MC dashboard with a custom diff/merge UI.

- ✅ Single pane of glass; no leaving Clayrune.
- ✅ Works for solo / offline-ish use.
- ❌ Custom merge UI is a deep well — GitHub's review tooling took years
  to mature. Re-implementing diff view + comment + resolve is months.
- ❌ Auto-committing two installs to the same branch (`main`) creates
  merge conflicts immediately on any concurrent edit.
- ❌ No good place for code review *before* a change lands.

### Option B — Pure PR flow (GitHub-native mode)

Each install commits to a per-machine feature branch and opens a PR. The
other side reviews/merges through GitHub's existing UI.

- ✅ Zero custom merge UI; GitHub does it.
- ✅ Review-before-merge is built in.
- ✅ Multiple in-flight changes don't collide.
- ❌ Leaves the dashboard. "What's the other side doing?" → open GitHub.
- ❌ PR overhead per logical change is heavy if "logical change" is small.

### Option C — Hybrid: per-machine branches + dashboard surface (RECOMMENDED)

Each Clayrune install commits its agent's logical changes to a stable
per-machine branch (`clayrune/sync/<install-id>`). MC fetches all
clayrune/* branches periodically. The dashboard shows incoming commits
from the *other* install's sync branch, with:

- A diff viewer (read-only, file-by-file).
- **Accept** → cherry-pick or merge the selected commit(s) into the
  local working branch (typically `main`), fast-forward when possible.
- **Reject** → mark the commit "dismissed" locally; never auto-prompt
  for it again, but it stays on the remote branch (the other side still
  has it).
- **Open as PR** → escalate to a real GitHub PR for big/risky changes
  that warrant comment threads.

The local working branch (`main`) is **only** advanced by:
- Fast-forward pulls from the remote `main`.
- Explicit accept of a commit from a sync branch (cherry-pick / merge).
- Direct user work.

Auto-commits never touch `main` directly — they go to the per-machine
sync branch. This eliminates the auto-collision class of bugs entirely.

**Why C wins:** keeps the single-pane-of-glass benefit of A, dodges the
"two installs writing to the same ref" failure mode of A, dodges the
"every change is a PR" overhead of B, and falls back to B (open as PR)
when a change genuinely needs human review with comments.

Rest of the doc assumes Option C.


## 4. Architecture

```
   Ron's Clayrune install                Keegan's Clayrune install
   ──────────────────────                ─────────────────────────
   workspace/  (git repo)                workspace/  (git repo)
       │                                     │
       │  agent edits                        │  agent edits
       ▼                                     ▼
   auto-commit per logical change       auto-commit per logical change
       │                                     │
       ▼                                     ▼
   clayrune/sync/ron-<host>             clayrune/sync/keegan-<host>
       │                                     │
       │            git push                 │  git push
       └────────────┬────────────────────────┘
                    ▼
                ┌───────────────────┐
                │   GitHub remote   │
                │                   │
                │  main             │ ← merges land here
                │  clayrune/sync/*  │ ← per-install sync branches
                └───────────────────┘
                    │
                    │  git fetch (every 5 min)
                    ├────────────────────────────────┐
                    ▼                                ▼
   Ron sees Keegan's branch in dash    Keegan sees Ron's branch
       │                                     │
       │  click Accept → cherry-pick         │  click Accept
       ▼                                     ▼
   merges into local main               merges into local main
       │                                     │
       └─ push main ─────────► remote main ◄─┘
```

Notes:
- Sync branches are **conceptually long-lived** but **commit-rotating**:
  once a commit is merged to `main` on the remote, its presence on the
  sync branch is moot. A periodic GC rebases each sync branch onto the
  current `main` so it doesn't accumulate forever (open question: how
  aggressive, and does rebase break in-flight diffs the reviewer is
  looking at? See §11.)
- Per-install ID derives from `hostname + first 4 chars of a stable UUID
  written to `data/install_id`` — stable across restarts, unique across
  installs, human-readable.


## 5. Data model additions

### Per-project (in `data/projects/<id>.json`):

```jsonc
{
  // existing
  "github_repo": "user/repo",
  "github_sync_enabled": true,
  "github_last_sync": "...",

  // NEW
  "code_sync_enabled": true,
  "code_sync_branch": "main",              // local working branch
  "code_sync_auto_commit": true,
  "code_sync_auto_pull": "ff-only",        // ff-only | manual | off
  "code_sync_auto_push_sync_branch": true, // auto-push the per-machine sync branch
  "code_sync_last_fetch": "2026-05-25T...",
  "code_sync_status": {                    // computed each sync cycle
    "ahead": 0, "behind": 0,
    "dirty": false,
    "incoming": [ /* commits on other install's sync branch */ ],
    "outgoing_sync_branch": "clayrune/sync/ron-laptop",
    "last_error": null
  },
  "code_sync_dismissed_commits": [ "sha1", "sha2" ]  // rejected via UI
}
```

### Sidecar (lives **outside** `DATA_DIR`, per the
load-bearing-DATA_DIR-pollution rule in CLAUDE.md):

```
data/project_sync/<project_id>/
    incoming_diffs/<sha>.json    # cached diff payload for the review UI
    commit_index.json            # last-seen-shas per sync branch
```

This MUST NOT live under `data/projects/` — `load_projects()` would
choke on it. Follow the same precedent as `_agent_log.json` and
`_scribe_stats.json` — except sidecars that are *per-project files* go
in a sibling directory, not in `DATA_DIR`.


## 6. Auto-commit — definition of "logical change"

This is the load-bearing semantic. Ron picked "auto-commit per logical
change"; we owe a precise definition. Three candidate triggers, in order
of recommendation:

### 6a. **Agent turn boundary (recommended default)**

After every agent turn that touched at least one tracked file, MC runs:

```
git add -A
git commit -m "<scribe-style one-line summary of the turn>"
```

- ✅ Aligns with Scribe's natural rhythm (turn = unit of meaning).
- ✅ Commit messages can reuse the Scribe summary (already cheap-model
     generated; one less roundtrip).
- ✅ User edits between turns aren't captured by the agent's commit;
     they'd be captured on the *next* turn or stay uncommitted.
- ❌ A turn that touches 12 unrelated files becomes one commit. Tradeoff
     accepted — agent turns are usually scoped enough that this is OK.

### 6b. **File-stability window**

Watch the working tree; when no file has been modified for N seconds
(e.g. 30s) AND there are staged changes, commit.

- ✅ Catches direct user edits too, not just agent edits.
- ❌ Commits at arbitrary times that don't align with semantic units.
- ❌ Easy to misfire if the user pauses to think mid-edit.

### 6c. **Explicit checkpoint marker**

Agent (or user) emits a `<!-- clayrune:checkpoint -->` marker and MC
commits at that point.

- ✅ Maximum semantic precision.
- ❌ Requires agent cooperation; older sessions and direct edits won't
     get checkpoints.

**Recommendation:** ship 6a (turn boundary) as the default, with a
project-level toggle to add 6b as a secondary trigger that catches
direct user edits. 6c is over-engineering for v1.

### Commit message

```
[agent] <one-line scribe summary>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

Direct user commits (6b path, if enabled) get `[user] <stability-window
auto-commit>` so the two are distinguishable in history.

### Safety rails

- Skip auto-commit if `.gitignore` / `git status` shows nothing staged.
- Skip if HEAD is detached.
- Skip if the local branch is the working branch (`main`) — auto-commits
  ALWAYS land on the sync branch. If currently on `main`, MC checks out
  the sync branch first (using `git worktree`? Or a parallel checkout
  under `.clayrune/sync-tree/`? — open question §11.)


## 7. Pull posture

Every 5 min, for each enabled project:

1. `git fetch origin` (all branches).
2. Read remote `main` head; if local `main` is behind and clean
   (`git status --porcelain` empty), `git pull --ff-only`. If not clean
   or not FF, set `code_sync_status.last_error = "dirty"|"diverged"`
   and **stop** — no auto-merge, no auto-stash.
3. Read remote `clayrune/sync/*` branches; for each that isn't ours,
   diff against `main` and surface new commits in
   `code_sync_status.incoming`.
4. Auto-push our own sync branch (`code_sync_auto_push_sync_branch`).

Manual "Sync Code Now" button bypasses the 5-min cadence (still
rate-limited like the Issues sync — 60s/project).


## 8. Push posture

The local sync branch is the only thing MC auto-pushes. Pushing `main`
is a deliberate user action triggered by the merge UI accept flow:

```
User clicks Accept on commit X from Keegan's sync branch
  → git cherry-pick X onto local main (or merge --no-ff if multi-commit)
  → if clean: git push origin main
  → if conflict: show conflict UI, no push
```

No force-push, ever. No `--rebase` on `git pull`. No history rewriting.


## 9. Review / merge UI

A new project-level tab or panel — "Code Sync" — next to the Issues
view. Sections:

### Outgoing
- Local sync branch name + ahead count.
- Last push time.
- "Push now" button.

### Incoming
For each commit on another install's sync branch not yet merged:

```
┌──────────────────────────────────────────────────────────────┐
│ keegan-mbp · 2 min ago                                       │
│ [agent] Add penalty-kick scoring to match summary            │
│                                                              │
│ 3 files · +47 / -12                                          │
│  ▸ src/match.ts                                              │
│  ▸ src/types.ts                                              │
│  ▸ tests/match.test.ts                                       │
│                                                              │
│ [ View diff ]  [ Accept ]  [ Reject ]  [ Open as PR ]        │
└──────────────────────────────────────────────────────────────┘
```

- **View diff** — opens a modal with the unified diff per file.
- **Accept** — cherry-pick onto local `main`; if clean, push `main`; if
  conflict, drop into a conflict resolution view (see §10).
- **Reject** — add SHA to `code_sync_dismissed_commits`; commit stays on
  the other side's sync branch but doesn't re-prompt locally.
- **Open as PR** — `gh pr create` with the sync branch as head and the
  current commit selection in the body. Useful when a change needs
  comment threads, not just yes/no.

### Status
- "✓ in sync" / "↓ N incoming" / "↑ N to push" / "✗ N conflicts" /
  "⚠ dirty tree — auto-pull skipped".


## 10. Conflict handling

If `cherry-pick` fails with a conflict on Accept:

1. Leave the working tree in the conflict state.
2. Surface a "Conflicts to resolve" view showing each conflicted file.
3. Two affordances:
   - **Resolve manually** — opens the file in the user's editor; MC
     polls for `git status` to detect resolution.
   - **Abort** — `git cherry-pick --abort`; commit returns to pending.
4. Once resolved → user clicks "Continue" → MC runs `git cherry-pick
   --continue` and pushes.

Optional Claude-assisted resolve: a button that hands the conflict to
the project's configured agent with a focused prompt. Off by default
(human in the loop for merges is safer for v1).


## 11. Open design questions

These are the unresolved points; design needs another pass before code.

1. **Worktree strategy.** Should the sync branch live in a separate
   `git worktree` under `.clayrune/sync-tree/` so the user's main
   checkout is never disturbed by auto-commits? Cleaner but introduces a
   second copy of the tree. Alternative: branch-switching, which is
   disruptive to an active editor.

2. **Sync-branch GC.** When do we rebase or reset a sync branch onto
   `main` to keep history bounded? If we rebase mid-review, SHAs in the
   incoming list change. Probably: GC only when nothing pending review.

3. **Install ID stability across reinstall.** If Ron wipes & reinstalls
   Clayrune, the sync branch name should ideally survive. UUID file in
   user-data dir, backed up to the repo?

4. **Backlog ↔ commit linking.** When an auto-commit closes a backlog
   item, link them (`Refs: backlog/<id>` in the commit message + a
   field on the backlog item). Cheap; high value for traceability.

5. **First-run UX.** Existing project with no `clayrune/sync/*` branch:
   how does MC bootstrap without surprising the user? Probably:
   pre-flight check, explicit "Enable code sync" wizard that shows what
   will happen ("MC will create a branch `clayrune/sync/<install>` and
   commit agent changes to it. Continue?").

6. **Branch protection rules.** Some repos protect `main` against
   direct push. Accept-flow needs to detect this and switch to "Open as
   PR" automatically.

7. **Submodules / LFS.** Out of scope v1, but design shouldn't
   foreclose. The sync-branch model still works; auto-commit just needs
   to know to skip submodule pointer bumps.

8. **Privacy of sync branches.** A public repo means everyone sees
   `clayrune/sync/*`. Is that desirable? Could be polish — branch
   prefix is opinionated; let users override.


## 12. Build order (proposed)

1. **Spike: install ID + sync-branch creation.** Read-only fetch loop;
   surface incoming-commit list in a stub UI. No accept/reject yet, no
   auto-commit. (~1 day; proves the plumbing.)
2. **Auto-commit on turn boundary.** Behind a project toggle. Pushes to
   sync branch on each turn. (~1 day.)
3. **Accept / Reject / View diff.** The merge UI in §9. (~2-3 days.)
4. **Conflict UI.** §10. (~1 day.)
5. **Open as PR escalation.** §9. (~half day.)
6. **GC + edge cases.** §11.1, §11.2, §11.6. (~1-2 days.)

Total v1 envelope: ~1-2 weeks of focused work, gated on this doc's
review.


## 13. Migration & rollback

- `github_sync.py` rename → `project_sync.py` with the Issues sync
  module living alongside the code sync module. Both register through
  the same `register()` injection pattern.
- Rollback tag before any of this lands: `pre-project-sync`.
- Per-project toggle (`code_sync_enabled`) means existing projects are
  untouched; opt-in only.
- DATA_DIR pollution rule: all per-project sidecars go in
  `data/project_sync/<id>/`, NOT `data/projects/`. Test added to the
  load_projects regression suite.


## 14. Committee questions (for review before build)

Before any code lands past the spike, the design needs feedback on:

- **Q1.** Option C vs. just doing Option B (always-PR) — is the
  dashboard-surface gain worth the per-machine-branch complexity?
- **Q2.** Turn-boundary auto-commit (§6a) — is one-commit-per-turn the
  right granularity, or do we need finer (per file group) / coarser
  (per task completion)?
- **Q3.** Should `main` auto-pull (FF-only) be the default, or should
  every incoming change require explicit Accept? Default-on FF feels
  safe (no merge risk), but means `main` advances without user say-so.
- **Q4.** Worktree vs. branch-switch (§11.1) — strong opinions?
- **Q5.** Claude-assisted conflict resolution — useful, dangerous, or
  defer?

---

End of v1 draft. Next step per Ron's slicing decision: read this,
decide which slices to ship (probably starting with the §12.1 spike to
prove the plumbing) and how to handle the open §11 / committee §14
questions before building further.
