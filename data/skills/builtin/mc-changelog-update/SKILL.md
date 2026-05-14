---
name: mc-changelog-update
description: Add a properly-formatted entry to a project's CHANGELOG.md following the project's existing conventions. TRIGGER when the user says "update the changelog", "add to changelog", "log this change", or as part of a document-and-commit flow when CHANGELOG.md is one of the artifacts that needs to change.
---

# CHANGELOG.md update — guided

Most Clayrune-managed projects keep a CHANGELOG.md in the project root. This skill walks through adding an entry that matches the project's existing format.

## Steps

### 1. Read the current CHANGELOG.md

```bash
head -120 CHANGELOG.md
```

Pattern-match the existing style:

- **Date stamp format** — `[2026-05-10]`, `## 2026-05-10`, `[v1.4.2 — 2026-05-10]`, etc.
- **Section style** — flat bullets, or grouped under `### New`, `### Changed`, `### Fixed`?
- **Voice** — past tense ("Added X") or imperative ("Add X")?
- **Detail level** — one-line bullets, or paragraphs with Why/Rollback subsections?

### 2. Compose the entry

Match the style exactly. Don't introduce a new format. Common structures:

**Style A — flat bullets:**
```markdown
## [2026-05-10] — Short headline

- Added foo
- Fixed bar in baz
- Removed deprecated quux
```

**Style B — grouped:**
```markdown
## [2026-05-10] — Short headline

### Added
- New foo widget for X

### Fixed
- bar no longer crashes when Y

### Changed
- baz default flipped to true
```

**Style C — narrative with Why/Rollback:**
```markdown
## [2026-05-10] — Short headline

- **Change:** One-sentence description of what changed.
- **Why:** The motivation — incident, requirement, user request.
- **Rollback:** How to revert if needed.
```

### 3. Insert at the top (most projects) — or at the bottom (rarely)

Most CHANGELOG.md files have newest entries at the top. Confirm by checking the existing first few entries: do dates descend or ascend?

Insert above the most recent entry, NOT at the very top of the file (which usually has a header / title / "Unreleased" section).

### 4. Show the diff before saving

After editing, show the user the diff so they can sanity-check:

```bash
git diff CHANGELOG.md
```

### 5. Companion gate — `docs/USER_GUIDE.md` (Clayrune only)

If the CHANGELOG entry describes a **user-facing feature** — a new UI
element, button, menu entry, sidebar item, settings toggle, keyboard
shortcut, or any feature an end user can discover — then `USER_GUIDE.md`
needs a matching update **in the same change set**. That file is the
source of truth for the in-app "Ask Claydo" helper; if the feature isn't
in it, Claydo can't tell users about it.

After step 4, ask yourself: *"Is the thing I just added to CHANGELOG.md
a user-facing feature?"*

- **Yes** → grep `docs/USER_GUIDE.md` for the feature name. If it's not
  there (or only barely mentioned), draft the matching guide section
  before this skill considers itself done. Surface the diff to the user
  alongside the CHANGELOG diff.
- **No** (refactor, internal-only change, bug fix with no UX delta) →
  state explicitly: *"USER_GUIDE.md doesn't need an update — this is
  internal-only."*

This gate exists because Clayrune has historically shipped UI
features that Claydo wasn't taught about, leaving the in-app helper
unable to answer "how do I use X?" questions.

### 6. Do not commit yet

This skill only edits CHANGELOG.md (and, when warranted, `USER_GUIDE.md`).
Commit happens separately — usually as part of the `document-commit-deploy`
flow.

## Important

- Don't fabricate detail. If you're unsure why a change was made, ask the user — don't invent a justification.
- Don't reformat existing entries. New entry only.
- Preserve trailing newlines, separator lines, and any header comments at the top of the file.
