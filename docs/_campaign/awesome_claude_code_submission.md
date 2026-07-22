# awesome-claude-code — submission package (ready)

## ⚠️ UPDATE — primary list is temporarily closed; use the alternative

`hesreallyhim/awesome-claude-code` has **locked issue creation to collaborators**
(anti-spam), and it refuses PRs — so its documented path is blocked right now.
Retry it in a week or two (locks are usually temporary).

**Submit to `jqueryscript/awesome-claude-code` instead** — active (405 PRs),
PR-based, and has a "Clients & GUIs" section that fits Clayrune exactly.

### Steps (Ron — ~2 min, all in the GitHub web UI, no local git)
1. Open the README: https://github.com/jqueryscript/awesome-claude-code/blob/main/README.md
2. Click the **pencil (Edit)** icon — GitHub auto-forks to your account.
3. Find the **"Clients & GUIs"** section; add this line (alphabetical-ish is fine):

```
*   [**Clayrune**](https://github.com/ronle/clayrune) - Mission control dashboard for running and monitoring multiple Claude Code agents across projects: parallel sessions on one grid, a scheduler, cross-session memory, and browser/phone access. Runs locally against your own Claude subscription.
```

4. **Commit changes** → **Propose changes** → **Create pull request**. Done.

(Star badge like `(1 ⭐)` is optional — maintainers often auto-add it; skip it at 1 star.)

---

## (Reference — the ORIGINAL hesreallyhim path, for when it reopens)

**Why this play:** durable, no downvote mechanic, permanent discovery surface where
people actively browse for Claude Code tools. Dodges everything that sank the two
Reddit posts. One-time effort.

**How to submit (their hard rule):** it's a **GitHub Issue form**, NOT a PR — the
maintainer temporarily bans people who submit via PR/script. Plain descriptive text,
**no emojis, no marketing language, no "you"** (a bot validates format).

## Steps (Ron — ~2 min)
1. Go to: https://github.com/hesreallyhim/awesome-claude-code/issues/new/choose
2. Pick the **"Submit a resource"** issue template.
3. Fill the fields with the values below. Submit.

## Field values (pre-written to their style)
- **Resource name:** Clayrune
- **Resource URL:** https://github.com/ronle/clayrune
- **Category:** Multi-Agent Orchestration  *(if that's not offered, use Tooling)*
- **Author / handle:** ronle
- **License:** MIT  *(their bot auto-detects; leave a comment if it fails)*
- **One-line description** (descriptive, no marketing, no emojis):

> Local-first dashboard for running and monitoring multiple Claude Code agents across separate projects from one console, with parallel sessions, a scheduler, cross-session memory, and browser or phone access.

## Notes
- No minimum stars required — they judge on "genuinely useful," not metrics. Good for us at 1 star.
- If the form asks "how does it use Claude Code": it wraps and orchestrates the Claude Code CLI (one agent process per project), does not replace it.
