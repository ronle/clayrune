---
name: document-commit-deploy
description: Run the end-of-work documentation, commit, and deploy playbook. TRIGGER when the user says "wrap this up", "document and commit", "ship it", "we're done — push it", "deploy this", or any phrasing that signals the current piece of work is complete and needs to be persisted. Also TRIGGER at the natural close of a major feature or fix when the user hasn't said so explicitly but the work is clearly done.
---

# Document, Commit & Deploy

Clayrune's SHARED_RULES requires that every major change be documented, committed, and (if git-synced) pushed. This skill is the concrete playbook so it actually happens.

## Sequence

Run these steps in order. Don't skip. If a step is genuinely N/A for the current change, say so out loud before moving on.

### 1. Identify what changed

```bash
git status
git diff --stat
git diff
```

Read the diff carefully. Group changes by intent (feature / fix / refactor / docs).

### 2. Update documentation artifacts

For each changed area, update the corresponding doc:

- **CHANGELOG.md** — add a dated entry summarizing what changed and why. Follow the existing format in the file (date stamp, bullet list of changes, optional Rollback / Why sections).
- **CLAUDE.md** — update if the change affects how future agents should work in this codebase (new conventions, architectural shifts, new must-do steps).
- **README.md** — update if user-facing behavior, install, or quickstart changed.
- **MEMORY.md** — update if the change introduces a load-bearing fact about the project that future sessions need to know (architecture, gotchas, decisions). Skip if the change is routine.
- **AGENT_RULES.md / SHARED_RULES.md** — update only if a new must-do or must-not-do rule emerged.
- **`docs/USER_GUIDE.md`** (Clayrune only) — update if **anything user-facing** changed: a new UI element, button, menu entry, sidebar item, settings toggle, keyboard shortcut, or any feature an end user can discover. This file is the source of truth for the in-app "Ask Claydo" helper — if the feature isn't documented here, Claydo can't tell users about it. **Gate**: before committing a UI / user-facing change to Clayrune, explicitly answer "did I update USER_GUIDE.md?" and either show the diff or state out loud why it's not needed (e.g. "internal refactor, no user-visible change").

If a doc doesn't need updating, say so explicitly: *"CLAUDE.md doesn't need changes for this — purely a UI tweak."*

### 3. Sanity-check the changes

- Run any relevant tests, linters, or type checks.
- For UI changes: actually open the dev server in a browser and verify the feature works.
- If anything fails, stop and fix it before proceeding.

### 4. Stage and commit

Stage only the files that belong in this commit (not stray edits):

```bash
git add path/to/file1 path/to/file2 ...
```

Avoid `git add -A` or `git add .` — they can pick up sensitive files (`.env`, credentials) or large binaries.

Compose the commit message:
- One-line subject, focused on the **why** not the what.
- Optional body for nuance, decisions, rollback notes.
- Match the repo's existing commit style (run `git log --oneline -10` to check).

Use a HEREDOC to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
Subject line under 70 chars

Optional body explaining why this change was made,
any tradeoffs, and how to roll back if needed.
EOF
)"
```

### 5. Push (only if git-synced)

```bash
git remote -v   # confirm an origin exists
git push
```

If there's no remote, skip this step — committing locally is enough.

### 6. Report

In one or two sentences: what shipped, what's next. No more.

## Important rules

- **Never `git push --force` to a shared branch** unless the user explicitly asks.
- **Never skip hooks** (`--no-verify`, `--no-gpg-sign`) unless explicitly asked. If a hook fails, fix the underlying issue and create a NEW commit (don't amend).
- **Pre-commit hook failure = the commit didn't happen.** Fix, re-stage, commit fresh.
- **If the user hasn't asked for a commit yet**, do steps 1-3 (review + docs + sanity check) and ASK before committing. Only auto-commit when the user signaled "ship it."
