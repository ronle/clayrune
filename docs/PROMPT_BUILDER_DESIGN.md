# Prompt Builder — design doc

Status: v1 (2026-06-12) — **Phase 1 IMPLEMENTED** same day (CHANGELOG
`[2026-06-12b]`): Claydo workshop modes, briefs, characters CRUD, handoff
cards. Phases 2–3 remain design-only.
Owner decisions locked via AskUserQuestion (2026-06-12):

1. **Artifact scope (v1):** BOTH — a refined *task prompt* and a full *agent
   character* — with an explicit upfront chooser ("simple prompt" vs "full
   persona").
2. **Character storage:** follow common practice, don't reinvent. (Survey
   below → adopt the Claude Code subagent file format verbatim.)
3. **Delivery surface:** Claydo. The mascot FAB becomes the entry point;
   Claydo gets "more context and features".

---

## 1. Problem

Users face a blank dispatch box. Two distinct gaps:

- **Task gap** — "I know what I want but my prompt is two vague lines."
  Output: a sharpened one-shot task prompt, handed back into the agent box.
- **Identity gap** — "I want this project's agent to *be* someone" (a code
  reviewer, a trading analyst, a docs writer). Output: a persistent,
  reusable character definition.

These have different lifetimes (one message vs. durable artifact) and
different storage (none vs. file), so the flow forks at the start — per
decision #1 the user picks, Claydo doesn't guess.

## 2. Rails that already exist (reuse, don't build)

| Rail | Where | Reuse for |
|---|---|---|
| Claydo FAB + chat modal | `static/index.html:577`, `static/js/claydo.js` | The whole builder UI shell |
| No-tools `claude --print` subprocess, sandboxed cwd `data/claydo/`, materialized `CLAUDE.md` | `mc/blueprints/guide_routes.py` (`_claydo_cwd`, `_claydo_prepare_context`, `/api/guide/stream`) | Builder engine — pure conversation, no tools needed |
| `[clayrune:...]` UI-marker rail (goto / open-modal / highlight) | `claydo.js` `_claydoParseMarkers` → `_claydoRunAction` | Handoff: "prompt ready" / "character ready" actions |
| AGENT_RULES.md → system-prompt injection | `_build_agent_context()` (server.py dispatch family); CRUD in `mc/blueprints/project_routes.py` | Phase-2 character *activation* on the main agent |
| Skills surface dual-scope management (`~/.claude/skills/` + `<project>/.claude/skills/`) | `skills.py` + Skills panel | Pattern-clone for the characters library CRUD |
| Sticky settings respawn (`_RESPAWN_TRIGGER_KEYS`) | dispatch family | Phase-2: character switch must respawn the session (see §8) |

Claydo's engine is exactly right for this: the builder needs zero tools,
zero MCP, just a good brief and a conversation. The existing
`_CLAYDO_NO_TOOLS_FLAGS` posture carries over unchanged.

## 3. Storage — what "everyone does" (decision #2 research)

Surveyed conventions:

- **Claude Code subagents** — the ecosystem standard. A markdown file at
  `<project>/.claude/agents/<name>.md` (project scope) or
  `~/.claude/agents/<name>.md` (user/global scope). YAML frontmatter:
  `name` + `description` required; `tools`, `model`, etc. optional. **Body =
  system prompt verbatim.** Scanned recursively; invoked by auto-delegation
  or `@agent-name`. Community libraries (e.g. VoltAgent's
  awesome-claude-code-subagents, 100+ agents) all use this format.
- **ChatGPT Custom GPTs** — conversational builder UX (interview → draft
  instructions → preview → save). Storage is proprietary; the *UX pattern*
  is the takeaway, not the format.
- **AGENTS.md** — cross-tool standard for repo-level agent instructions;
  the analog of our AGENT_RULES.md, not of a character library.
- **Roo/Cline custom modes** — JSON/YAML persona + tool restrictions; same
  shape (name, role definition, constraints), niche format.

**Decision: write characters as standard Claude Code subagent files,
byte-compatible.** Frontmatter `name` + `description`, body = persona.
Project scope by default (`<project_path>/.claude/agents/<name>.md`),
global (`~/.claude/agents/`) as an explicit choice — mirroring the Skills
surface exactly.

Two payoffs, free:
1. Any character Clayrune writes is **natively usable by Claude Code
   today** — the dispatched session can `@mention` it or auto-delegate to
   it with zero new injection machinery. Characters work day one as
   subagents.
2. Users can **import any community subagent** unmodified (Phase 3).

**UI naming:** call them **Characters** (fits the Claydo/clay identity —
"Claydo sculpts characters"). Never "agents" in UI copy: that word is
taken by MC's dispatched session and would collide. Files on disk are
subagents; the UI label is ours.

## 4. UX flow (v1)

Claydo's greeting gains two chips under the existing welcome bubble:

> Hi — I'm Claydo. Ask me anything about Clayrune…
> `[ ✍️ Help me write a prompt ]` `[ 🎭 Create an agent character ]`

Default mode stays **ask** (help desk, unchanged). Tapping a chip switches
the modal into builder mode (subtitle changes, e.g. "Prompt workshop");
a small "← back to questions" affordance resets to ask mode.

**Builder conversation discipline** (enforced by the brief, §7):
- Interview, then draft: ask at most 2–3 targeted questions per round, max
  ~2 rounds, then produce a draft. Never an interrogation.
- Task mode asks: goal, context/inputs, constraints, desired output shape.
- Character mode asks: role + domain, expertise level, tone, boundaries
  ("what should it never do"), and optionally 1–2 example exchanges.
- Every draft ends with the artifact in a fenced block + a ready marker.

**Handoff** (extends the marker whitelist in `_claydoParseMarkers`):
- `[clayrune:prompt-ready]` → frontend renders an action card on the
  message: **Copy** / **Send to <project>** (inserts into the focused
  project modal's agent input; falls back to copy if none open).
- `[clayrune:character-ready name="code-reviewer"]` → action card with
  **Save character…** → minimal dialog: name (prefilled), scope
  (This project / All projects), → `POST /api/characters`.

Markers stay attribute-light: the artifact text itself is taken from the
**last fenced code block** in the cleaned reply — never stuffed into marker
attrs (the `key="value"` regex parser is not a payload channel).

**"More context" (decision #3):** the frontend sends the focused project's
id with builder requests; the backend appends a compact, per-request
context block — project name, summary, current AGENT_RULES.md head
(~1 KB), installed skills list — so Claydo tailors questions ("you already
have a code-review skill — should the character lean on it?"). Per-request
prompt only, NOT the shared materialized CLAUDE.md (that file is
mode-global and cached).

## 5. Backend changes (v1)

1. **`/api/guide/stream` gains `mode`** (`ask` default | `prompt` |
   `character`) **+ optional `project_id`.** Builder modes use a separate
   sandbox cwd per mode (`data/claydo/builder-prompt/`,
   `data/claydo/builder-character/`) with their own materialized
   `CLAUDE.md` from briefs shipped in the repo (`docs/claydo/` — same
   docs→materialize pattern as USER_GUIDE.md today). Keeps transcripts and
   caches per-mode, keeps ask-mode untouched.
2. **`characters.py` module + endpoints** (clone the skills.py shape):
   - `GET /api/characters?project_id=` — list both scopes, parsed
     frontmatter.
   - `POST /api/characters` — `{name, description, body, scope,
     project_id}` → slugify name, collision-check, write standard subagent
     file. Reject > ~6 KB bodies (see §8 size budget).
   - `GET/PUT/DELETE /api/characters/<scope>/<name>`.
   - Writes go ONLY to `~/.claude/agents/` or `<project_path>/.claude/agents/`
     — never DATA_DIR (load_projects pollution rule does not even come into
     play; keep it that way).

~120 LOC guide_routes delta + ~180 LOC characters module. No dispatch-family
code is touched in v1.

## 6. Frontend changes (v1)

In `claydo.js` (+ a little CSS):
- Mode state on the modal (`_claydoMode`), chips in the greeting, subtitle
  swap, back affordance. History reset on mode switch (different system
  context = different conversation).
- Pass `{mode, project_id}` in the stream POST. `project_id` = topmost
  non-minimized project modal, else null.
- Extend the marker regex whitelist with `prompt-ready` / `character-ready`;
  render action cards; fenced-block extraction; save dialog (follow the
  multi-input modal convention: numbered inputs top-down, single accent
  action button).

~250 LOC. Note the SPA stale-JS gotcha: claydo.js is a module script —
open tabs need a hard reload to see it.

## 7. Builder briefs — borrow from external prior art (the "developed skills")

Author two briefs (`docs/claydo/PROMPT_BUILDER_BRIEF.md`,
`docs/claydo/CHARACTER_BUILDER_BRIEF.md`), distilling:

- **Anthropic prompt-eng guidance** (clarity, context, examples, output
  contracts) — the prompt-coach skill's source material.
- **ckelsoe/prompt-architect** — framework menu (CO-STAR, RISEN, RTF, CoT…)
  + its 5 quality dimensions (clarity, specificity, context, completeness,
  structure) as the draft self-check.
- **Jeffallan/claude-skills `prompt-engineer`** — technique catalog
  (few-shot, CoT, structured output).
- **Claude Code subagent docs** — character brief must produce valid
  frontmatter (keyword-rich `description` matters for auto-delegation) and
  a body that reads as a system prompt.

Brief contract (both modes): interview discipline (§4), draft in ONE fenced
block, end with the ready marker, keep characters ≤ ~4 KB, never offer to
save/write anything itself (the UI owns persistence).

Optionally later: ship the same brief as an `mc-prompt-builder` built-in
skill so in-session agents can run the identical playbook — one source,
two consumers. Not v1.

## 8. Constraints & gotchas (load-bearing)

- **Windows ~32 KB CreateProcess limit.** Characters ride
  `--append-system-prompt` in Phase 2 alongside MEMORY + rules + activity.
  Hence the ≤ ~4 KB brief rule + ≤ 6 KB hard cap at save. (Same warning
  already lives on `_clayrune_agent_rules`; `--append-system-prompt-file`
  is the existing escape hatch.)
- **`claude -r` ignores `--append-system-prompt`** (verified 2026-06-04).
  A system prompt can only be set at session spawn — never changed on a
  resumed session. This is exactly why Phase-2 activation is **per chat,
  chosen at chat creation** (owner decision 2026-06-12): the character is
  injected once at spawn and is immutable for the life of that chat.
  Switching personas = start a new chat. No respawn machinery needed.
- **Claydo stays no-tools.** The builder is pure conversation; never grant
  tools/MCP to builder modes. Project context arrives pre-digested from the
  server, not via agent reads.
- **Marker whitelist is a security boundary.** Extend the regex
  deliberately (two new kinds), keep payloads out of attrs.
- **v1 writes only under `.claude/agents/`** (both scopes). No DATA_DIR
  files, no new sidecars.

## 9. Phasing

- **Phase 1 (v1, this design):** Claydo chips + two builder modes + briefs +
  characters CRUD + handoff cards. A saved project character is *already
  live* as a native Claude Code subagent (@-mention / auto-delegate) — real
  value with zero dispatch-code risk.
- **Phase 2 — activation as the main agent's persona, per CHAT (owner
  decision 2026-06-12):** the character is picked when starting a new
  conversation — selector in the new-chat flow, listing project + global
  characters. **Default = no character = exactly today's behavior** (the
  plain project agent with AGENT_RULES/MEMORY context); a persona is
  strictly opt-in per chat. Stored on the session record (not the
  project). `_build_agent_context()` prepends the character body at spawn
  beside AGENT_RULES. Once set, **immutable for the chat's lifetime** —
  switching = new chat — which makes the `claude -r` limitation (§8) a
  non-issue by construction.
  **Visibility is a requirement, not a nicety** (owner, 2026-06-12): a
  chat running a persona must show it unmistakably — a persistent persona
  pill (name + accent) in the chat header beside provider·model for the
  life of the conversation, plus a marker on the conversation's tab/list
  entry so persona chats are tellable apart at a glance. No-persona chats
  show nothing (no "None" noise).
  Optional later layer, NOT in scope: a project-level *default* character
  that new chats inherit. Touches dispatch family — small, but review
  with the session-lifecycle rules in mind.
- **Phase 3 — library surface:** characters list/edit/delete in the Skills
  panel (retitle "Skills & Characters") or its own sidebar entry; community
  import (paste a GitHub raw URL → preview → save); browse curated
  starter characters.

## 10. Open questions

1. Does character mode also offer "activate now on project X" in v1 by
   appending to AGENT_RULES.md via the existing rules PUT (cheap, no
   dispatch changes) — or do we hold all activation for Phase 2's proper
   `active_character` field? (Leaning: hold; appending to a user-owned
   file blurs ownership.)
2. Claydo model tier for builder modes: ask-mode default vs. force Sonnet
   for better drafting? (Cost vs. quality; builder calls are rare.)
3. Mobile: chips fit the ≤960px modal fine, but the save dialog needs the
   mobile modal-trim pass.
