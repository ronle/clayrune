# Clayrune Conversation Redesign — Master Implementation Action Plan

**What this is.** An executable, per-task implementation plan synthesized from a
7-agent code investigation of the "Calmer dashboard" brief
(`C:\Users\levir\Documents\_claude\CLAYRUNE_CONVERSATION_REDESIGN.md`). Every
claim below is grounded in a verified `file:line + symbol` anchor from that
investigation. A coding agent should be able to implement each section without
re-deriving anything. Where the original brief was wrong, the correction is
called out inline.

**Branch.** Do this work on `refactor/conversation-redesign`, cut off
`refactor/backend`. Land each § as its own commit in the order in §"Commit
order" below.

## Implementation status (2026-07-05)

| § | Task | Status | Commit |
|---|------|--------|--------|
| §3 | Chat bubbles on desktop (+ mermaid bugfix, 720px cap) | ✅ shipped | `ed3bd15` |
| §4 | Typing-dots indicator | ✅ shipped | `8114775` |
| §7 | Numbered plan-approval card | ✅ shipped | `8a123ea` |
| §6 | Quick-reply chips (AskUserQuestion) | ✅ shipped | `ca682ca` |
| §5 | Starter suggestion chips | ✅ shipped | `f06a3ff` |
| §8 | ＋ options bottom sheet (mobile) | ✅ shipped | `fa88463` |
| §1a | "Needs you" feed → deep-link to waiting chat | ✅ shipped | `c1e0fd4` |
| §1b | Mobile inbox surface | ⏸ deferred | — |
| §1c | Context-adaptive bottom nav bar | ⏸ deferred | — |
| §1d | Desktop multi-pane (recents rail + thread) | ⏸ deferred | — |
| §1e | Desktop docked nav cluster | ⏸ deferred | — |

**Verified:** all edits pass `node --check`; CSS brace balance unchanged from
baseline; desktop composer output is byte-identical (§8 is mobile-gated); DOM-
structure assumptions confirmed by reading the JS (direct `.agent-output`
children for stretch rules; both render paths for plan/typing). **NOT verified:**
live pixels — needs a hard-reloaded MC tab (Ctrl+Shift+R; CSS/JS don't reach a
live SPA tab otherwise). QA checklist: bubbles both themes + both breakpoints;
typing dots appear/clear on every run-end path incl. manual Stop; plan card
Approve/Review; a yes/no AskUserQuestion → chips; empty +New → starter chips;
mobile ＋ sheet opens/holds selections; "Needs you" tap lands on the plan/
question state.

**§1b–§1e deferred — NOT a scope cut, a gate.** These four are genuinely new
surfaces (not restyle/relocate) and each needs one of the five open product
questions answered before it can be built correctly (see §1 "Open questions"):
what "Search"/"You"/"Backlog" mean at Layer 1, recents-rail scope, and whether
the mobile inbox replaces or sits beside the existing "Needs you" filter pill.
Building them blind would mean guessing product decisions and risking core
navigation. The 720px cap (§1's only CSS-only part) already shipped with §3.

**The golden rule (from the brief's Ground Rules §0, confirmed by every agent).**
This is a **restyle / relocate** effort, not a new-capability effort. Specifically:

1. **Read theme tokens, never hex.** Use `--bg`, `--surface2/3`, `--border2`,
   `--accent`, `--green`, `--text`, `--text-dim`, etc. Do **not** paste the
   mockup's warm-cream hex (`#4888de`, `#f6f0e4`, …).
2. **Three themes, not two.** The brief says "dark + cream"; the app actually
   ships **three**: default dark (`:root`, `app.css:1`), `body.tone-warm`
   (cream/rounded, `app.css:79`), and `body.tone-editorial` (a second, distinct
   cream/serif tone, `app.css:117`). All three share the same custom-property
   names, so token-driven CSS covers all three automatically — but eyeball-check
   all three.
3. **Both breakpoints.** The only breakpoint in the agent-panel CSS is
   `@media (max-width: 960px)`. "Mobile" = ≤960px, "desktop" = >960px. The
   Turn-5 chat treatment (bubbles, typing dots, chips, plan card, ＋ sheet)
   applies to **both**.
4. **ES-module `window.*` export gotcha.** `static/js/*.js` load as
   `<script type="module">`. A function invoked from an inline `onclick=""`
   string, or called by bare name from another module, MUST be explicitly
   attached to `window.` (see the existing re-export blocks at
   `conversation.js:1655-1675` and `rich-text.js:255-264`). Forgetting this
   throws `ReferenceError` at click/call time — it was the root cause of a prior
   dispatch outage.

---

## Decisions — RESOLVED by Ron (2026-07-05)

All gating decisions answered. Build to these; the detailed rationale for each is
retained below.

| # | Decision | Ron's pick |
|---|----------|-----------|
| 1 | Unified desktop bubbles | **YES** — desktop = bubbles + typing dots, same as mobile |
| 2 | Tool/status chip font | **Sans narration + mono override on `[tool:]` traces** |
| 3 | §1 nav/IA scope | **FULL IA build now** (~450 LOC): desktop recents rail + thread, context-adaptive bottom bar. Not deferred. |
| 4 | Where chat-search lands | **Inside the ＋ sheet's Resume section** now (revisit at §1) |
| 5 | ＋ sheet scope | **Pre-dispatch / +New screen only** — in-chat composer keeps attach+mic inline |
| 6 | Desktop ＋ treatment | **Header pills on desktop** (`◐ model · resume ↻ · ⋮`); the slide-up sheet is mobile-canonical |
| 7 | Primary/accent chip | **All chips equal** — no data field justifies a primary |
| 8 | Multi-question fallback | **>1 sub-question → full radio/checkbox form always** |

**Visual reference (confirms all of the above):** `C:\Users\levir\Documents\_claude\Simplified Dashboard.pdf`
(single tall board, Turns 1–7). Readable crops rendered to `_scratch/redesign_pdf/`
(gitignored). Turn-5 states `b_turn5_phones.png` (5a Start / 5b Working / 5c Needs
you / 5d Resume / 5e ＋sheet), desktop `c_turn5_desktop.png` (recents rail + thread),
project bottom bar `d_turn4_projectbar.png`. Concrete UI copy the mockup pins down:
5a empty-state = "What should Claude work on?" + 3 chips ("Fix a failing test or
bug" / "Add a feature to this project" / "Explain how the codebase works") + status
line "◐ Claude Sonnet · Incognito off · Change"; 5c plan card header = "📋 Plan ready
to approve" with Approve Plan / Review buttons; bottom bar = 🏠 Home · 💬 Chats ·
⊕ New Chat · ☑ Backlog · ⋮ More (More = today's project 3-dot menu: Agent Log ·
Plans · Memory & Rules · Skills/MCP · Appearance · Settings).

---

### Rationale (original decision analysis)

These were genuine product/UX judgment calls surfaced across the investigation.

1. **Unified desktop bubbles — the core product bet (§3, §2).**
   Decision: Promote the mobile chat-bubble treatment to desktop as the default
   for `.agent-output` at all widths?
   Options: (a) yes — desktop becomes bubbles+typing-dots like mobile (the
   brief's stated correction to the mockup); (b) keep desktop as the flat mono
   `agent-line` stream.
   Recommendation: (a). Everything in §3–§7 assumes it. This is the single
   decision that gates the whole conversation-view redesign.

2. **Mono vs sans on the quiet tool/status chips (§3).**
   Decision: What font do the `-tool / -status / -error / -queued / -followup`
   chips use?
   Options: (a) sans-serif Inter — what actually ships today (`app.css:2768-2783`);
   (b) monospace — what the brief's prose ("quiet mono lines") asks for.
   Recommendation: This is a real design call, not a lift-unchanged side effect.
   Pick deliberately. Suggest keeping Inter for narration bubbles but allowing a
   mono override on tool-trace chips so `[tool: …]` lines stay legible.

3. **Where chat-search lands (§8, §1).**
   Decision: Is "chat search" a composer control or a Layer-2 surface?
   Context: it is **not** a general search — it's "search past resumable
   sessions to pick one to resume," gated identically to the Resume picker
   (`conversation.js:370-371`), funneling into `selectResumeSession`. The brief's
   own IA (§1, Layer 2 = "conversation list, searchable") points to the recents
   list as its natural home.
   Options: (a) bundle it inside the ＋ sheet's Resume section now (fastest,
   reuses today's gating); (b) promote it to the Layer-2 project conversation
   list.
   Recommendation: Ship inside the sheet now (a), revisit when §1 lands.

4. **Is §1 nav-bar a CSS job or real routing work?**
   Decision: How much of §1 do we commit to?
   Context: the investigation found §1 is **largely NOT CSS** — see the §1
   section. There is no context-adaptive bottom bar today; on mobile there is
   **zero** entry point to the attention list (it's a desktop-only `display:none`
   panel); session-level deep-linking from the Feed does not exist; desktop has
   no bottom bar at all to relocate into. Only the 720px thread cap is genuinely
   CSS-only.
   Options: (a) ship only the low-risk CSS caps (720px thread cap) now, defer the
   nav/inbox/multi-pane work to a scoped follow-up; (b) commit to the full IA
   build (~450 LOC, "large").
   Recommendation: (a). Split §1 into "720px cap now" + "IA build as its own
   spike." Do not let §1 block §3–§8.

5. **Primary-chip convention for quick-reply chips (§6).**
   Decision: Should one AskUserQuestion chip be visually promoted (accent-filled)
   as the "primary" option?
   Context: there is **no** `recommended`/`primary` field in the AskUserQuestion
   data (`options[].label/.description`, `multiSelect` only). Any promotion is a
   UI convention, not data-driven.
   Options: (a) no accent-fill, all chips equal (safe default); (b) always
   accent-fill the first option by convention.
   Recommendation: (a). If Ron wants a primary chip, spell out the convention.

6. **Multi-question AskUserQuestion fallback (§6).**
   Decision: When one AskUserQuestion call carries >1 sub-question, always fall
   back to the full radio/checkbox form even if each sub-question is individually
   chip-eligible?
   Recommendation: Yes — gating chip mode on `questions.length === 1` avoids an
   ambiguous part-chips/part-form card under one Submit button. Confirm this
   degradation is acceptable.

7. **Does the ＋ sheet exist for the active/in-chat composer at all (§8)?**
   Decision: The in-chat composer today has only **attach + mic** (provider /
   persona / incognito / resume / search are pre-dispatch-only, and are frozen
   read-only header badges once a session is live). Should ＋ span both screens?
   Recommendation: Restrict the ＋ sheet to the **pre-dispatch / +New screen**;
   leave the active-chat composer's attach+mic inline. This deviates from the
   literal brief text — flag in the PR.

8. **Desktop ＋ sheet treatment (§8).**
   Decision: On desktop does ＋ open the same slide-up sheet (centered), or skip
   the sheet for header pills (per brief 5f)?
   Recommendation: Decide before finalizing CSS — it determines whether the
   desktop branch of the sheet CSS is throwaway or permanent.

9. **Starter-chips visibility gate (§5).**
   Decision: Show starter chips on every `+New` screen (whenever
   `noActiveTab && !resumeId`), or only on a project's very first-ever chat?
   Context: the brief's Target ("a conversation is empty") supports the former;
   its Acceptance ("first-run empty chat") supports the latter.
   Recommendation: The former (chips reappear on every new chat) — matches
   mainstream chat-app UX. Confirm.

10. **Historical (non-latest) plan cards (§7).**
    Decision: In a multi-plan session, how do already-resolved earlier
    ExitPlanMode plans render?
    Recommendation: Keep them as today's inert view-only `plan-show-btn`; only
    the **latest, still-pending** plan (`p.live_agent.reason === 'plan'`) gets an
    actionable Approve/Review card. A blanket substitution would let users
    re-approve stale plans — see §7 corrections.

11. **Is the demo-export sandbox in scope (§7)?**
    Decision: The brief's claim that the walkthrough depends on a
    `btn-approve-plan` id is **false for production** — that id/step exists only
    in `demo-export/` (a self-contained marketing sandbox). Should the demo be
    updated to match the new plan-card design?
    Recommendation: Out of scope unless Ron says otherwise. Production
    `walkthrough.js` needs no accommodation.

---

## Commit order

Recommended (conversation view first; §1 is a separate/parallel workstream):

1. **§3** — Chat bubbles on desktop (CSS promotion). Biggest visible win, lowest
   risk. Also fixes the `.mermaid-placeholder` dead-selector bug.
2. **§4** — Typing-dots indicator. Depends on §3's unconditional bubble parent.
3. **§7** — Numbered plan-approval card.
4. **§6** — Quick-reply chips.
5. **§5** — Starter chips.
6. **§8** — ＋ options bottom sheet (largest restructure — do last).
7. **§1** — IA / nav-bar + scaling caps. Parallel/separate; do NOT block the
   above. Ship the 720px cap early (it belongs with §3), defer the nav/inbox/
   multi-pane build to its own spike.

---

## §3 — Chat bubbles on desktop (CSS promotion)

**Complexity: medium · ~70 LOC · Risk: medium** (large sensitive CSS region;
the "which element gets the 720px cap" call is easy to get wrong).

### Verified anchors
- `static/css/app.css:2718` — comment `Mobile chat-bubble mode for agent output`
  (the brief's "~2724").
- `static/css/app.css:2723` — `@media (max-width: 960px) {` — opening of the
  block to unwrap.
- `static/css/app.css:2831` — `}` closing that media query.
- `static/css/app.css:2621` — `.agent-output` desktop base rule (`flex:1`,
  `overflow-y:auto`, `white-space:pre-wrap`, mono font) — must be reconciled with
  the promoted flex-column rule, not left as a duplicate.
- `static/css/app.css:2698` — `.agent-output .agent-line { padding:1px 0; }` (the
  brief's exact "~2698").
- `static/css/app.css:2737` — `.agent-output .agent-line` mobile left-bubble
  (`align-self:flex-start`, `--surface3` bg, `border-radius:14px 14px 14px 4px`,
  `max-width:84%`).
- `static/css/app.css:2752` — `.agent-output .agent-line-prompt` mobile right
  accent bubble.
- `static/css/app.css:2768` — `.agent-output .agent-line-tool/-status/-error/
  -queued/-followup` chip rule. **Currently `font-family:'Inter',…sans-serif`,
  NOT mono** as the brief's prose implies.
- `static/css/app.css:2794` — the `align-self:stretch; flex-shrink:0` opt-out
  list: `.hl-table, .hl-table-pre, .plan-show-btn, .plan-hidden-block,
  .agent-question, .mermaid-placeholder`. **`.mermaid-placeholder` is a dead
  selector — bug (see below).**
- `static/css/app.css:2803` — `.agent-output .agent-line .hl-codeblock` (code
  block nests inside a bubble, gets `overflow-x:auto`).
- `static/css/app.css:2824` — `.agent-output .agent-img` mobile thumbnail
  override.
- `static/js/mermaid.js:20` and `:160` — both emit `class="mermaid-block"`, never
  `mermaid-placeholder` (proves `app.css:2799` is dead).
- `static/js/conversation.js:647` — `.agent-output` is a direct flex child of
  `.agent-chat`; the composer (`.agent-chat-input`) is a **sibling** of
  `.agent-output`, not nested inside it. **This determines where the 720px cap
  goes** (see step 6).
- `static/js/conversation.js:828` — existing comment confirming the flex-column
  requirement for `align-self:flex-end` on prompts.
- `static/js/conversation.js:932` — `.plan-approve-row` (Approve/Collapse row),
  lives directly under `.agent-output`, omitted from the brief's stretch list.
- `static/js/conversation.js:951` — unclassed "stuck ExitPlanMode" warning div,
  also omitted; relies on flex default-stretch.
- `static/js/conversation.js:449` (Path A, buffer rebuild) vs
  `static/js/conversation.js:908` (Path B, live stream) — the two divergent
  ExitPlanMode render paths (see §7; relevant to visual QA here).
- `static/js/rich-text.js:159` — `agentLineCls()` — line classifier, unchanged
  by this task.
- `static/css/app.css:952` — `.modal-content` (conversation is NOT full-viewport;
  it's a floating resizable modal, `width:700px` default, `resize:both`,
  `max-width:95vw`).
- `static/css/app.css:836` — `.modal-window.is-snapped .modal-content{max-width:
  none}` — the Aero-snap/maximize path where the 720px cap actually becomes
  visible.
- `static/css/app.css:1190` — `.modal-tab-content.active[data-tab="agent"]`
  (`display:flex;flex-direction:column;flex:1;min-height:0`) — the flex context
  the cap centering needs.
- `static/css/app.css:3710` — `.plan-history-card / .plan-card-body /
  .plan-card-delete` — **unrelated** Plan-History tab CSS; naming-collision
  signpost for §7.
- `static/css/app.css:3635` — the only other `@media (max-width:720px)`
  (unrelated schedule banner); confirms the new 720px cap is a fresh value, not
  an existing breakpoint.

### Steps
1. Delete the `@media (max-width: 960px)` wrapper (`app.css:2723`–`2831`) so its
   contents become unconditional. Keep the `2718` comment but update it to say
   the block is now the default at all widths.
2. Reconcile the duplicate declarations: replace the old desktop-only
   `.agent-output` (`2621`) and `.agent-output .agent-line/-tool/-status/-error/
   -followup/-queued/-prompt/.agent-echo-queued` (`2698-2716`) with the promoted
   (now-unwrapped) mobile versions, so there is **exactly one ruleset per
   selector** — do not rely on source-order to win. Net container change:
   `.agent-output` → `display:flex; flex-direction:column; gap:6px;
   white-space:normal;` at all widths. Drop the container-level
   `white-space:pre-wrap` + base `JetBrains Mono` (every leaf class sets its own
   font).
3. **Fix the dead-selector bug:** change `.agent-output .mermaid-placeholder`
   (`app.css:2799`) → `.agent-output .mermaid-block`. Mermaid emits
   `mermaid-block`; `.mermaid-block` has `overflow:auto` (`app.css:2910`) and is
   exactly the kind of child the adjacent comment warns gets crushed to a "black
   line" without `flex-shrink:0`. This is a real latent mobile bug fixed as part
   of the promotion.
4. Add the two omitted elements to the stretch/`flex-shrink:0` list (`2794-2801`):
   add `.agent-output .plan-approve-row`; and give the unclassed stuck-warning
   div at `conversation.js:951` an explicit class (e.g. `agent-plan-stuck-
   warning`) and add `.agent-output .agent-plan-stuck-warning`. Gives both an
   explicit `align-self:stretch` instead of relying on implicit flex default.
5. **Resolve Decision #2 (mono vs sans chips)** at `app.css:2768-2783` — do not
   let it be an unexamined side effect of the promotion.
6. Add the 720px thread cap as a **NEW, separately-gated** rule — **not** bolted
   onto the unconditional bubble block:
   ```css
   @media (min-width: 961px) {
     .agent-chat { max-width: 720px; margin: 0 auto; width: 100%; }
   }
   ```
   Cap **`.agent-chat`** (`app.css:2606-2610`), NOT `.agent-output` alone —
   because the composer is a *sibling* of `.agent-output`; capping only
   `.agent-output` centers the bubbles but leaves the full-width composer
   unaligned beneath. `max-width`/`margin:auto` are cross-axis only, safe
   alongside `.agent-chat`'s existing `flex:1; min-height:0`. Gating behind
   `min-width:961px` is load-bearing: an unconditional cap would silently change
   today's shipped 720–960px tablet behavior (edge-to-edge) into centered-with-
   gutters. See cross-cutting notes.
7. `agentLineCls()` (`rich-text.js:159-167`) — **no change**. Confirm no line-
   classification logic was added; this is a pure CSS re-skin.
8. `appendAgentLine()` and the `outputLines` builder — **no change**. Manually
   verify both render paths agree once CSS is unconditional (step 4 shows they
   emit different ExitPlanMode markup).
9. Theme blocks (`:root` ~1, `body.tone-warm` ~79, `body.tone-editorial` ~117) —
   **no token changes**; promoted rules only reference existing tokens. Spot-
   check all three tones.

### Corrections to the brief
- The brief's chip-font claim ("keep the mono font on these") is **wrong** — the
  shipped chips are already Inter/sans (`2768`), and `.agent-line-prompt` (`2760`)
  is also sans. A literal "lift unchanged" ships sans chips, contradicting the
  brief's own prose. Needs Decision #2.
- The brief's stretch-list enumeration omits `.plan-approve-row` and the stuck-
  warning div (step 4).
- The brief's "collapse into Show Plan button" describes the **rebuild path only**
  (`conversation.js:449-461`); the live-stream path (`908-957`) leaves the plan
  inline + `.plan-approve-row` and only force-collapses on a *second*
  ExitPlanMode. Both need the stretch treatment but they aren't the same markup.
- "max-width:720px on the line container" is ambiguous — apply to `.agent-chat`,
  not `.agent-output` (step 6).
- "Never grows with the monitor" — the conversation is NOT normally full-viewport;
  it's a 700px default resizable modal. The cap is **inert** on a default modal
  and only bites once dragged wider / snapped / maximized.
- `.mermaid-placeholder` dead-selector bug (step 3) is pre-existing, not
  introduced here.

### Risks
- Picking the wrong cap target (`.agent-output` vs `.agent-chat`) misaligns the
  composer on a widened/snapped modal — easy to miss if the tester never resizes.
- An ungated 720px cap silently changes shipped 720–960px tablet behavior.
- Editing the large `2621-2831` CSS region risks accidentally dropping/
  duplicating an adjacent unrelated rule (`hl-code`, `hl-table`, mermaid,
  `agent-img`). Do a careful reviewed diff, not a blind cut/paste.
- Two divergent ExitPlanMode paths mean single-path QA can miss a regression.

### Acceptance
- Desktop-width modal: user prompts = right accent bubbles, agent text = left
  bubbles, tool/status = centered/quiet chips.
- All three themes read clearly.
- Thread caps ~720px only above 961px; ≤960px unchanged. Verify: (a) default
  700px modal (cap inert); (b) modal dragged ~1400px (bubbles capped, gutters);
  (c) snapped/maximized (cap holds); (d) mobile unchanged; (e) both plan render
  paths; (f) all three tones.

### Revert
Re-wrap the promoted rules in `@media (max-width:960px)` and remove the
`min-width:961px` cap block. (The `.mermaid-block` fix should stay — it's an
independent bugfix.)

---

## §4 — Typing-dots indicator

**Complexity: small · ~45 LOC · Risk: medium** (two render paths must agree; the
manual-Stop removal path is non-obvious). **Depends on §3** landing first so
`.agent-output` is flex at all widths (else the desktop indicator won't
left-align).

### Verified anchors
- `static/js/conversation.js:266` — `isRunning = st === 'running'` (`st` from
  `activeSession.status`).
- `static/js/conversation.js:499` — `stopBtn` guard is
  `isRunning || st==='idle' || st==='error'`.
- `static/js/conversation.js:823` / `:901` — `appendAgentLine`; `el.appendChild
  (div)` is the single real-line insertion point for every non-special line.
- `static/js/conversation.js:1013` — `.agent-echo` div: existing precedent for a
  DOM-only ephemeral node NOT persisted to `agentOutputBuffers`.
- `static/js/conversation.js:1420` — `stopAgent` closes the EventSource
  **synchronously** before the server round-trip; a terminal `status:'stopped'`
  SSE never arrives on this stream.
- `static/js/conversation.js:1476` — `fetchAgentStatus` (poll reconciliation).
- `static/js/conversation.js:1060` — `renderAgentQuestion` (called from 3 sites:
  SSE `question`, `fetchAgentStatus`, `_rerenderPendingQuestions`) — best single
  place to guarantee removal on "agent now waiting."
- `static/js/resume-preview.js:539` — `es.onmessage` type switch.
- `static/js/resume-preview.js:583` — `appendAgentLine(sessionId, msg.text)`
  stream call site.
- `static/js/resume-preview.js:627` — `turn_start` handler; sets `status=
  'running'`, **explicitly skips `refreshModal()`** (comment 643-650) — the
  run-start point to show the indicator.
- `static/js/resume-preview.js:652` — `turn_complete`; sets `status='idle'`,
  skips `refreshModal()`, **early-returns (667-670) when waitingForQuestion/
  waitingForPlanApproval** so status never flips to idle in that window.
- `static/js/resume-preview.js:602` — `question` handler; sets
  `waitingForQuestion=true`, does NOT change status (so `isRunning` stays true).
- `static/js/resume-preview.js:693` / `:736` — `status` / `error` handlers (set
  terminal status, close SSE, `refreshModal()`).
- `static/js/conversation.js:904` — client-side text-pattern ExitPlanMode
  detection inside `appendAgentLine`.
- `static/css/app.css:4546` / `:4556` — `.claydo-thinking` / `claydo-dots`
  animates a single `::after` `content` string, **not** three independent spans —
  do NOT try to reuse it.
- `static/css/app.css:2718` — mobile bubble block (§3 target); indicator CSS
  lives near the promoted bubble rules.
- `static/js/rich-text.js:227` — `_isAgentOutputPinned` / `_scheduleAgentPinScroll`
  (rAF-batched scroll-pin; call after insert/remove).

### Steps
1. In `appendAgentLine` (top, right after `if (!el) return;`, **above** the
   mermaid/table early-returns): remove any `#typing-<sessionId>` node. This is
   the single guarantee the indicator disappears the instant the next real line
   (tool/status/error/text/mermaid/table) streams in.
2. Add `showTypingIndicator(sessionId)` / `hideTypingIndicator(sessionId)`,
   **exported on `window`**. `show`: no-op if output missing or `#typing-<sid>`
   exists; else build `<div class="agent-line typing-indicator" id="typing-
   ${sessionId}"><span></span><span></span><span></span></div>`, append, then
   `_scheduleAgentPinScroll` using the wasPinned-before-append pattern. `hide`:
   the same removal one-liner as step 1, factored so both call sites share it. Do
   **not** persist into `agentOutputBuffers`.
3. `turn_start` handler (`resume-preview.js:627`, after `updateAgentStatusUI
   (sessionId,'running')`): call `showTypingIndicator(sessionId)`. This handler
   skips `refreshModal()`, so it's the only reliable hot-path place to show dots.
4. `turn_complete` handler (after the 667-670 early-return guard): call
   `hideTypingIndicator(sessionId)` — after the guard so a turn_complete masking
   a pending question doesn't wrongly hide (that path hides via
   `renderAgentQuestion`, step 6).
5. `status` and `error` handlers (`693` / `736`, where status is set): call
   `hideTypingIndicator(sessionId)` in both — backstop for remote-stop / natural
   completion / guardian crash, avoids a flash before the `refreshModal()`
   rebuild.
6. `renderAgentQuestion` (`1060`, top, before the dedup checks): call
   `hideTypingIndicator(sessionId)` unconditionally — one spot covers all 3 call
   sites. The agent is by definition no longer generating once a question
   renders, regardless of `agentStatusCache.status`.
7. `stopAgent` (`1420`, right after the SSE-close block, before the `fetch()`):
   call `hideTypingIndicator(sessionId)`. **REQUIRED, not optional** — stopAgent
   closes its own EventSource synchronously so no terminal `status` SSE arrives;
   without this the dots animate forever after a manual Stop.
8. `agentPanelHTML` cold-render fallback (tabContent branch ~`631-650`, where
   `outputLines` is built): declaratively decide indicator presence from cache:
   `const showTyping = isRunning && !(activeSession.waitingForPlanApproval ||
   activeSession.waitingForQuestion);` and append the same `.typing-indicator`
   markup as the last child when true. Needed because turn_start/turn_complete
   skip `refreshModal()` but other triggers (switchAgentTab, modal reopen) DO
   rebuild — without this the dots vanish after tab-away/back, or (if gated on
   `isRunning` alone) wrongly re-show during a pending question/plan.
9. CSS near the promoted §3 bubble block:
   ```css
   .typing-indicator { display:flex; align-self:flex-start; gap:4px;
     background:var(--surface3); border:1px solid var(--border2);
     border-radius:14px 14px 14px 4px; padding:8px 12px; max-width:60px; }
   .typing-indicator span { width:6px; height:6px; border-radius:50%;
     background:var(--text-dim); animation:typing-pulse 1.2s infinite ease-in-out; }
   .typing-indicator span:nth-child(2){animation-delay:.2s}
   .typing-indicator span:nth-child(3){animation-delay:.4s}
   @keyframes typing-pulse { 0%,60%,100%{opacity:.3;transform:scale(.8)}
     30%{opacity:1;transform:scale(1)} }
   ```
   Do NOT reuse `claydo-dots` (it animates `content` text, can't stagger 3 dots).
   Use tokens for both/all themes. If §4 lands before §3, ALSO add a matching
   copy inside the `@media (max-width:960px)` block, since desktop `.agent-output`
   isn't flex until §3 lands.

### Corrections / caveats
- `turn_complete`'s early-return guard means status never flips to idle under a
  pending question/plan — hiding gated purely on "status → idle" leaves dots
  running forever; hide explicitly in `renderAgentQuestion` (step 6).
- Manual Stop has no terminal SSE (step 7 is the only removal path for it).
- Cold path (step 8) and hot path (steps 3/4/6/7) are independent and **both**
  needed, not either/or.

### Risks
- Guardian states (`recovering`/`needs_attention`, `resume-preview.js:762-775`)
  are a third run-state family the brief ignores — a session may resume
  generating without a fresh `turn_start`; reconsider indicator logic against
  `guardianState` or dots may stay hidden through a recovery burst.
- Rapid show/hide/show cycling shares the single rAF pin-scroll queue keyed by
  sessionId — burst-test multiple quick tool calls.

### Open questions (for Ron)
- Show optimistically on Send (like the existing `.agent-echo` bubble) or wait
  for server `turn_start`? "The instant a run starts" is ambiguous.
- Does `recovering` count as running for the indicator?
- Any accessibility affordance (`aria-live` "agent is typing"), or purely
  decorative?

### Acceptance
Dots appear under the last agent bubble the instant a run starts, disappear when
output resumes or the run stops; never written to the transcript; present on
both breakpoints; Send↔◼Stop toggle intact.

### Revert
Delete the `.typing-indicator` node render (steps 1-8) + CSS (step 9).

---

## §7 — Numbered plan-approval card

**Complexity: medium · ~180 LOC · Risk: medium.** Three emission sites, not one;
the load-bearing DOM id must be preserved.

### Verified anchors
- `static/js/conversation.js:449` — **Path A** (full-buffer rebuild): emits
  `plan-show-btn` + `.plan-hidden-block` for EVERY `[tool: ExitPlanMode]` in the
  buffer, using local `planRawLines`; sets `planViewerContent[activeSessionId] =
  planRawLines`.
- `static/js/conversation.js:908` — **Path B** (live SSE append): increments
  `exitPlanModeCount`, walks backward through DOM children collecting text into
  `planViewerContent[sessionId]` (a *separately-sourced* array, NOT
  `planRawLines`), calls `refreshModal()`.
- `static/js/conversation.js:927-938` — first-occurrence `approveRow`: builds
  `#plan-approve-${sessionId}` div (class `plan-approve-row`) with two inline-
  styled buttons calling `approvePlan(pid,sessionId)` / `collapseIntoPlanButton`.
  **This is the actionable UI the brief's single-site framing misses.**
- `static/js/conversation.js:1001` — `approvePlan(projectId, sessionId)`: removes
  `#plan-approve-${sessionId}`, clears `waitingForPlanApproval`, appends local
  echo, POSTs `/api/project/${projectId}/agent/followup`. **The real button
  carries NO id — `btn-approve-plan` does not exist in production.**
- `static/js/rich-text.js:169` — `collapseIntoPlanButton(sessionId, container)`:
  used only for the stuck-loop path + manual "Collapse Plan"; produces
  `plan-show-btn` + `.plan-hidden-block`. **Untouched by §7.**
- `static/js/rich-text.js:262` — `window.collapseIntoPlanButton = …`: the cross-
  module `window` re-export pattern any new shared helper must follow.
- `static/js/project-forms.js:307` — `openPlanViewer(sessionId)` reads
  `planViewerContent[sessionId] || agentOutputBuffers[sessionId] || []`; exported
  at `:844`.
- `static/css/app.css:2794` — the `flex-shrink:0/align-self:stretch` list
  (unconditional after §3); `.plan-card` must be added here.
- `static/css/app.css:2834` — `.agent-question`: best precedent for a token-
  driven in-thread card (surface3 bg, accent border, 8px radius, 14px 16px pad).
- `static/css/app.css:3047` — `.plan-show-btn`/`:hover` **hardcode non-token
  purple hex** (`rgba(139,92,246,…)`, `#c4b5fd`, `#8b5cf6`, `#e9e5ff`) — violates
  the theme-token rule; **do not copy into new `.plan-card` CSS**.
- `static/css/app.css:3717` / `:3722` — `.plan-card-body` / `.plan-card-delete`:
  pre-existing global classes for the unrelated Plan-History tab — **naming
  collision; use `.plan-card-steps` / `.plan-card-actions` instead.**
- `static/js/render-core.js:39` — `p.live_agent.reason === 'plan'`: the server-
  authoritative "still awaiting plan approval" signal; the recommended gate for
  which ExitPlanMode occurrence gets the actionable card. `p` is already the
  outer param of `agentPanelHTML(p)`.
- `demo-export/demo-app.js:559` — `id="btn-approve-plan"` / `approvePlan(p)`: the
  ONLY place this id and same-named fn exist (separate marketing sandbox, verified
  by `demo-export/_verify.mjs:88,303`).
- `static/js/walkthrough.js:75` — the REAL in-app tour; only descriptive prose,
  **zero DOM dependency on any plan element.**
- `static/js/conversation.js:550-552, 959-997` — `activeSession.planFile` /
  `#plan-file-btn-${sessionId}` (`openPlanFileViewer`): a THIRD, separate plan
  affordance (disk-written plan file) — do NOT merge/repurpose.

### Steps
1. Add a shared `_parsePlanSteps(lines)` in `rich-text.js` (near `agentLineCls`
   / `collapseIntoPlanButton`): join raw lines, detect leading marker per line
   via `/^\s*(?:\d+[\.\)]|[-*•])\s+/`, group each marker + following continuation
   lines into one step, return `{steps:[...]}` when ≥2 markers found (mirrors the
   existing `>= 2` threshold at `conversation.js:451` / `rich-text.js:188`) else
   `null` so callers fall back to raw text. **Add `window._parsePlanSteps =
   _parsePlanSteps;`** next to the `rich-text.js:255-264` re-exports.
2. Add `.plan-card` CSS near `.agent-question` (`app.css:2868`), reusing that
   token pattern: `.plan-card` (surface3 bg, `1px solid var(--accent)`, 8px
   radius, 14px 16px pad, `margin:8px 4px`); `.plan-card-header`/`.plan-card-
   title` (small uppercase, `color:var(--accent)`); `.plan-card-steps` (an
   `<ol>`, left-padded, gap); `.plan-card-raw` (fallback, `white-space:pre-wrap`,
   `color:var(--text-dim)`); `.plan-card-actions` (flex row, gap:8px);
   `.btn-plan-approve` (`background:var(--green); color:#fff`);
   `.btn-plan-review` (`background:var(--surface2); border:1px solid var
   (--border2); color:var(--text)` — **replace the broken `var(--bg2)`** used at
   `conversation.js:938`; `--bg2` is undefined in all three themes). **Do NOT
   reuse `.plan-card-body`/`.plan-card-delete`.**
3. Add `.plan-card` to the `flex-shrink:0/align-self:stretch` list at
   `app.css:2794-2801` (unconditional by the time §7 lands, per commit order).
4. **Path A** (`conversation.js:448-462`): replace the unconditional
   `plan-show-btn` emission with: call `_parsePlanSteps(planRawLines)`; compute
   `isLatestPending = (this is the last '[tool: ExitPlanMode]' line in buf) &&
   p.live_agent && p.live_agent.reason === 'plan'`. If latest+pending, emit the
   full `.plan-card` (header + `<ol class="plan-card-steps">` or `.plan-card-raw`
   fallback + Approve/Review buttons calling the **existing**
   `approvePlan('${esc(p.id)}','${esc(activeSessionId)}')` and
   `openPlanViewer('${esc(activeSessionId)}')`). Otherwise keep today's inert
   `plan-show-btn` + `.plan-hidden-block`. Still set `planViewerContent
   [activeSessionId] = planRawLines` (Review depends on it).
5. **Path B** (`conversation.js:908-942`): where `approveRow` is built
   (`932-938`), build the SAME `.plan-card` markup as step 4, calling
   `_parsePlanSteps` on the just-collected `planViewerContent[sessionId]` array.
   **CRITICAL: keep the wrapper `id="plan-approve-${sessionId}"` unchanged** —
   `approvePlan()` (`:1003`) and the stuck-loop handler (`:947`) look it up by
   that literal id. Buttons keep calling existing `approvePlan(pid,sessionId)` /
   `openPlanViewer(sessionId)` (`pid` already resolved via `agentStatusCache
   [sessionId].projectId` at `:930`).
6. `approvePlan` (`1001-1025`): **no functional change** — it already removes
   `#plan-approve-${sessionId}` (the whole `.plan-card` root, given step 5
   preserves the id). Grep confirms nothing depends on the old `.plan-approve-
   row` class in CSS, so it's safe to drop.
7. `collapseIntoPlanButton` (`rich-text.js:169-207`): **leave as-is** — it still
   produces `plan-show-btn`/`plan-hidden-block`, the correct "no longer live"
   treatment for the stuck-loop / manual-collapse states §7 doesn't touch.

### Corrections to the brief
- "One emission site" is **three**: Path A (`449`), Path B (`908`, which does NOT
  emit `plan-show-btn` today — it appends `.plan-approve-row`), and
  `collapseIntoPlanButton` (`rich-text.js:169`). Missing Path B means only the
  reload path is reskinned while the more common live path keeps the old row.
- "Keep `btn-approve-plan` id so the walkthrough finds it" is **false for
  production** — that id/step exist only in `demo-export/`. Real `walkthrough.js`
  has zero DOM dependency. See Decision #11.
- A blanket substitution of every historical `[tool: ExitPlanMode]` with a live
  Approve button is a **regression** — re-opening an old multi-plan session would
  let the user re-approve a stale plan. Gate on `p.live_agent.reason === 'plan'`
  + latest occurrence (Decision #10).
- The `.plan-show-btn` purple hex and the undefined `var(--bg2)` are pre-existing
  bugs; the new card must not copy either.
- Naming collision with `.plan-card-body`/`.plan-card-delete` (use different
  names).
- Don't confuse with the separate `planFile`/`#plan-file-btn` affordance.

### Risks
- Gating on `p.live_agent.reason === 'plan'` assumes one project ↔ one live plan-
  pending session; a project with two concurrent sessions where the non-primary
  is plan-pending may not line up with `activeSessionId`. Mirrors an existing
  render-core simplification (not new), but spot-check multi-tab panels.
- Preserving `id="plan-approve-${sessionId}"` is load-bearing; an implementer who
  "cleans up" the id to `plan-card-${sessionId}` silently breaks plan removal.
- Path A and Path B must produce visually identical markup or a mid-approval
  reload shows a different card than the live stream did.
- Leaving `.plan-show-btn`'s hardcoded purple means two plan visual languages
  side by side unless migrated to tokens in the same pass (opportunistic).

### Open questions (for Ron)
- Restyle the stuck-loop warning's Approve button to `.btn-plan-approve`, or keep
  it a distinct "something's wrong" treatment?
- Historical non-latest plans: plain `plan-show-btn`, or a "resolved" card
  variant with an "Approved" badge?
- Is `demo-export/` in scope (Decision #11)?

### Acceptance
Approving from the card behaves identically to today; Review opens the plan
viewer; card renders in all themes and both breakpoints; both render paths
produce the same card; walkthrough unaffected.

### Revert
Restore the `plan-show-btn` emission in Path A and the `.plan-approve-row` build
in Path B; remove `.plan-card` CSS and `_parsePlanSteps`.

---

## §6 — Quick-reply chips (AskUserQuestion → tap chips)

**Complexity: small · ~90 LOC · Risk: low** (FE-only; data contract stable).

### Verified anchors
- `static/js/conversation.js:1060` — `renderAgentQuestion(sessionId, projectId,
  questions, questionId)`; per-sub-question loop `1079-1098` emits radio/checkbox
  `.agent-question-option` rows + a fixed "Other" row (`1092-1096`) + a single
  Submit (`1099-1101`) wired to `submitQuestionAnswer` via inline `onclick`.
- `static/js/conversation.js:1126` — `submitQuestionAnswer(projectId, sessionId,
  formId, questionCount)`: reads checked inputs (`1130-1148`) into `answers[]`,
  builds directive text (`1152-1164`), marks DOM answered + disables inputs
  (`1166-1180`), dispatches via `sendFollowup` (`1187-1190`) or raw fetch POST
  fallback (`1192-1197`).
- `static/js/conversation.js:1152` — the directive message template ("I answered
  your AskUserQuestion through the Clayrune UI… Do NOT re-ask") is answer-shape-
  agnostic, reusable verbatim by any factored dispatch fn.
- `static/js/conversation.js:1664` — the `window.*` export block (`1655-1675`);
  any new fn used from an inline `onclick` MUST get `window.fn = fn;` here
  (mirroring `window.submitQuestionAnswer` at `1674`).
- `mc/blueprints/agent_routes.py:2010` and `:2190` — the two structurally
  identical AskUserQuestion interception sites (different provider/mode paths);
  confirm the data contract (`questions[].header/question/options[].label/
  .description`, `multiSelect`) is stable. **No backend change needed.**
- `mc/blueprints/agent_routes.py:3884` — SSE emission: `{'type':'question',
  'question_id':qid,'questions':pq.get('questions',[])}` — confirms the wire
  shape.
- `static/css/app.css:2833` — `.agent-question` full-form CSS block (`2833-2868`);
  new chip CSS lives adjacent, sharing the `.agent-question`/`.agent-question.
  answered` container.
- `static/css/app.css:4586` — `.claydo-chips`/`.claydo-chip` (`4586-4597`): the
  actual existing pill precedent (`background:var(--surface2); border:1px solid
  var(--border); border-radius:16px; padding:7px 14px;` + `border-color:var
  (--accent)` + `translateY(-1px)` on hover). **Reuse this**, not the brief's
  (nonexistent) mobile-pairing `.suggest`.

### Steps
1. Factor `submitQuestionAnswer` lines `1150-1198` (message build → DOM answered-
   state → dispatch) into `_dispatchQuestionAnswer(projectId, sessionId,
   container, answers)` taking a built `answers` array (`[{question, answer}]`)
   and the container element. Keep the directive template (`1158-1164`) verbatim.
   Keep **both** dispatch branches (`sendFollowup` AND raw-fetch fallback).
2. Shrink `submitQuestionAnswer` to: look up `container = getElementById
   (formId)`; run the existing checked-input collection loop (`1130-1148`)
   unchanged; if `answers.length===0` return; call `_dispatchQuestionAnswer(...)`.
   Preserves exact current behavior for full-form/multi-select/long-option paths.
3. Add `submitQuestionChip(projectId, sessionId, formId, qIndex, label)`:
   `container = getElementById(formId)`; read `qText` via `container.
   querySelectorAll('.agent-question-text')[qIndex].textContent`; call
   `_dispatchQuestionAnswer(projectId, sessionId, container, [{question:qText,
   answer:label}])`. **Export `window.submitQuestionChip = submitQuestionChip;`**
   next to `window.submitQuestionAnswer` at `1674`.
4. In `renderAgentQuestion` compute ONCE for the whole call (not per sub-question):
   ```js
   const useChips = questions.length === 1 && !questions[0].multiSelect
     && (questions[0].options||[]).length <= 4
     && (questions[0].options||[]).every(o => (o.label||'').length <= 24 && !o.description);
   ```
   When true, render `.agent-question-chips` (a `.agent-question-chip` button per
   option, `onclick="submitQuestionChip('${esc(projectId)}','${esc(sessionId)}',
   '${formId}',0,'${esc(opt.label)}')"`) + a trailing "Other" chip. When false,
   render the existing radio/checkbox block (`1080-1098`) unchanged. (Excluding
   options with a `.description` — a pill can't show the `.aq-desc` explanatory
   line.)
5. "Other" in chip mode: give the Other chip an onclick toggling the SAME
   existing `#${groupName}-other-text` input's `.visible` class (already emitted
   at `1096`), swapping its row for a compact confirm control (Enter-to-submit or
   a small Send) that calls `_dispatchQuestionAnswer(..., [{question:qText,
   answer:otherTextEl.value.trim()}])` when non-empty.
6. In `_dispatchQuestionAnswer` teardown (formerly `1166-1180`), also disable chip
   buttons: `container.querySelectorAll('button.agent-question-chip').forEach
   (b=>b.disabled=true);` (harmless no-op in form mode) — today's teardown only
   disables `<input>`s, which chip mode has none of.
7. CSS after `.agent-question` (`~2868`), modeled on `.claydo-chip`:
   ```css
   .agent-question-chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
   .agent-question-chip { background:var(--surface2); border:1px solid var(--border);
     color:var(--text); padding:7px 16px; border-radius:16px; cursor:pointer;
     font-size:12.5px; font-family:'Inter',sans-serif;
     transition:border-color .15s, transform .15s; }
   .agent-question-chip:hover { border-color:var(--accent); transform:translateY(-1px); }
   .agent-question-chip:disabled { opacity:.5; cursor:default; transform:none; }
   ```
   No accent-fill on any chip by default (Decision #5).
8. Reuse `.agent-question-answer` (`2868`) unchanged for the post-answer summary
   in chip mode.

### Corrections to the brief
- Precedent is `.claydo-chip` (`app.css:4586`), not the brief's mobile-pairing
  `.suggest` (which doesn't exist).
- Factor the network call into `_dispatchQuestionAnswer` — don't duplicate it.

### Risks
- `questions` length can be >1; gating chips on `length===1` means any multi-
  question call falls back to the full form even if every sub-question is
  individually eligible (Decision #6).
- Excluding options with a `.description` isn't in the brief; verify against
  recent `pending_questions` payloads that this doesn't suppress chips too often.
- `container.dataset.qid` + `_answeredQuestionIds[sessionId]` dedup
  (`1063-1069, 1169-1171`) must keep working for chip cards; since
  `renderAgentQuestion` still makes one `.agent-question` container with
  `data-qid`, this carries over — verify a reconnect/rebuild doesn't resurrect an
  answered chip card.
- Two backend interception sites (`2010`, `2190`) must stay in sync if a field is
  ever added (e.g. a real `recommended` flag).

### Open questions (for Ron)
- Chip-eligibility thresholds (≤4 options, ≤24-char labels, no description) as
  tunable constants at the top of `conversation.js`, or inlined?
- A distinct "primary" chip at all (Decision #5)?
- "Other" inline field: own Send button or Enter-only?

### Acceptance
A yes/no or short-choice single-select renders as 2–4 tap chips that post on tap;
multi-select and long/described/free-form still use the current form; answers
still route through the existing `submitQuestionAnswer`/`sendFollowup` path.

### Revert
Restore the original `submitQuestionAnswer` body; remove `submitQuestionChip`,
`_dispatchQuestionAnswer`, the `useChips` branch, and the chip CSS.

---

## §5 — Starter suggestion chips on the empty composer

**Complexity: small · ~45 LOC · Risk: low.**

### Verified anchors
- `static/js/conversation.js:221` — `agentPanelHTML(p)` (builds tabBar,
  convListHTML, dispatchRow, tabContent).
- `static/js/conversation.js:263` — `activeSessionId` (null on the +New screen).
- `static/js/conversation.js:326` — `noActiveTab` (true == empty/dispatch screen).
- `static/js/conversation.js:347` — `picker` = `sessionPickerHTML(p.id)` when
  `noActiveTab && _pcaps.supports_session_resume` — a resume LIST renders on the
  same screen whenever the project has prior sessions.
- `static/js/resume-preview.js:112` — `sessionPickerHTML` returns `''` only when
  no prior conversations at all.
- `static/js/conversation.js:351` — `resumeIndicator` (shown when
  `noActiveTab && resumeId`).
- `static/js/conversation.js:388` — `dispatchRow` template (the only place the
  `#agent-task-${p.id}` textarea exists).
- `static/js/conversation.js:393` — `#agent-task-${esc(p.id)}` — the dispatch
  textarea the chips fill (distinct from the followup textarea `#agent-followup-
  ${activeSessionId}` at `539`).
- `static/js/conversation.js:400` — `btn-dispatch onclick=dispatchAgent` — chips
  must NOT call this, only set `.value`.
- `static/js/conversation.js:781` — `newAgentTab`: focus pattern
  `refreshModal(); setTimeout(()=>getElementById('agent-task-'+pid)?.focus(),50)`.
- `static/js/conversation.js:1189` — `input.value = message;` — precedent for
  direct `.value` assignment (no `input` event needed).
- `static/js/claydo.js:243` — `.claydo-chips/.claydo-chip` markup (the real chip
  precedent; brief cites the wrong file).
- `static/css/app.css:4586` — `.claydo-chip` token-driven CSS.
- `static/css/app.css:2488` — `.resume-indicator` block ends `2496`; blank line
  `2497` before the `.conv-preview` comment is the insertion anchor for the new
  `.composer-empty-state` CSS.
- `static/css/app.css:2195` — `.agent-panel` (`display:flex; flex-direction:
  column`) — the empty-state block stacks vertically here, no extra wrapper.

### Steps
1. Near line 17 (module state): add
   `const STARTER_CHIPS = ['Fix a failing test or bug', 'Add a feature to this
   project', 'Explain how the codebase works'];` (static per brief; project-aware
   is v2).
2. New fn near `newAgentTab`/`selectResumeSession` (~`821`):
   `function fillStarterChip(projectId, text) { const ta = getElementById
   ('agent-task-'+projectId); if (!ta) return; ta.value = text; ta.focus();
   ta.setSelectionRange(text.length, text.length); }` — **no `setTimeout`/
   `refreshModal` needed** (the textarea is already on screen; see brief-
   correction below). Export `window.fillStarterChip = fillStarterChip;`.
3. Inside `agentPanelHTML(p)`, before the `dispatchRow` const, using in-scope
   `noActiveTab`, `resumeId`, `sessions`:
   ```js
   const showEmptyState = noActiveTab && !resumeId;
   const emptyStateHTML = showEmptyState ? `<div class="composer-empty-state">
     <div class="ces-icon">💬</div>
     <div class="ces-heading">What should Claude work on?</div>
     <div class="ces-chips">${STARTER_CHIPS.map(t =>
       `<button type="button" class="ces-chip" data-chip-text="${esc(t)}"
        onclick="fillStarterChip('${esc(p.id)}', this.dataset.chipText)">${esc(t)}</button>`
     ).join('')}</div></div>` : '';
   ```
   Use the **data-attribute** approach (read `this.dataset.chipText`), NOT inlined
   JSON, to avoid quote-escaping bugs. Gate on `!resumeId` so chips never show
   alongside the "Resuming: <label>" indicator.
4. Insert `${emptyStateHTML}` in the `dispatchRow` template immediately after
   `${resumeIndicator}` and before the `.agent-input-row` opening tag. Ordering:
   chatSearch → picker → resumeIndicator → emptyStateHTML → input row.
5. CSS after `app.css:2496` (before the `.conv-preview` comment), unconditional
   (works at both breakpoints; `.agent-panel` is already flex-column), modeled on
   `.claydo-chip` token-for-token:
   ```css
   .composer-empty-state { text-align:center; padding:18px 8px 14px; }
   .composer-empty-state .ces-icon { font-size:28px; opacity:.7; margin-bottom:6px; }
   .composer-empty-state .ces-heading { font-size:14px; font-weight:600;
     color:var(--text-dim); margin-bottom:12px; }
   .composer-empty-state .ces-chips { display:flex; flex-wrap:wrap; gap:8px;
     justify-content:center; }
   .composer-empty-state .ces-chip { background:var(--surface2); border:1px solid
     var(--border); color:var(--text); padding:7px 14px; border-radius:16px;
     cursor:pointer; font-size:12px; font-family:inherit;
     transition:border-color .15s, transform .15s; }
   .composer-empty-state .ces-chip:hover { border-color:var(--accent);
     transform:translateY(-1px); }
   ```
6. Disappearance is automatic (verification only, no code): once `dispatchAgent()`
   spawns a session, `activeSessionId` becomes non-null and the mutually-exclusive
   `if (activeSession && activeSessionId)` branch (`407`) renders tabContent
   instead of dispatchRow — chips stop rendering on the next `refreshModal()`.

### Corrections to the brief
- Wrong precedent file: the brief's `mobile-pairing.js` `.suggest` has **zero**
  occurrences. Use `.claydo-chips`/`.claydo-chip` (`claydo.js:243`,
  `app.css:4586-4597`).
- The "empty composer" screen is NOT blank — it already renders `sessionPicker
  HTML` (a resume list) and a `resumeIndicator`. The empty-state block needs an
  explicit gate (`!resumeId`) and a defined position, neither in the brief.
- Three themes, not two (spot-check `tone-editorial` too).
- The focus pattern is simpler than the brief implies: `newAgentTab` uses
  `setTimeout+refreshModal` because it rebuilds the DOM; a chip tap doesn't
  rebuild, so a synchronous `ta.focus()` is the correct (load-bearing) mirror.

### Risks
- Inlining chip text into onclick is fragile with quotes; the data-attribute
  approach avoids it but the visible label still needs `esc()`.
- The `!resumeId` gate is inferred, not in the brief (Decision #9).
- On mobile, `noActiveTab` for a multi-session project is true only when
  `sessions.length===0` or `wantNew`; the drill-down list wins otherwise, so
  existing multi-conversation projects show chips only right after "+New" — the
  intended path.
- No auto-resize `oninput` listener exists on `.agent-task-input`, so setting
  `.value` won't trigger height adjustment (low risk; note in code).

### Open questions (for Ron)
- Chips on every `+New` screen or only a project's first-ever chat (Decision #9)?
- Should chips hide once the user types manually (needs an `oninput` handler), or
  stay until dispatch? Not requested by the brief.

### Acceptance
First-run empty chat shows the prompt + 3 chips; tapping fills (not sends) the
input and focuses it; chips disappear once the thread has content.

### Revert
Remove `STARTER_CHIPS`, `fillStarterChip`, the `emptyStateHTML` insertion, and
the `.composer-empty-state` CSS.

---

## §8 — The ＋ options bottom sheet

**Complexity: medium · ~220 LOC · Risk: medium.** The brief materially
mischaracterizes current state — read the corrections first.

### Verified anchors
- `static/js/conversation.js:402` — `.composer-controls-row` (provider + persona
  + incognito); renders ONLY when `noActiveTab` (pre-dispatch), calling
  `_composerProviderPicker(p)` + `_composerCharacterPicker(p, resumeId)` +
  incognito chip.
- `static/js/conversation.js:370` — `chatSearchHTML(p.id)` / `searchPaneInner
  (p.id)` gated on `noActiveTab && _pcaps.supports_session_resume` (same gate as
  Resume) — chat search is a **resume sub-feature**, not a standalone control.
- `static/js/conversation.js:347` — `picker = sessionPickerHTML(p.id)` (Resume),
  same gate.
- `static/js/conversation.js:388` — `dispatchRow = noActiveTab ? …` — the whole
  cluster is one conditional block gated on `noActiveTab`.
- `static/js/conversation.js:525` — `_fuAttachBtn` / `_fuMicBtn` — **the ONLY two
  controls in an active/in-chat composer** (attach gated `_pcaps.image_input`,
  mic gated `micAvailable()`).
- `static/js/conversation.js:605` / `:610` — `_provBadge` / `_charBadge` — read-
  only header badges; provider/persona cannot change mid-chat.
- `static/js/conversation.js:99` — `_composerProviderPicker(p)` returns `''` when
  only 1 provider (sheet must preserve this collapse-to-nothing).
- `static/js/conversation.js:38` — `_composerCharacterPicker(p, resumeId)` returns
  `''` on resume or zero characters (persona is spawn-time only).
- `static/js/conversation.js:119` — `getIncognitoFor(projectId)` /
  `toggleIncognito(projectId)` (forced-on for the `_incognito` pseudo-project at
  `121`).
- `static/js/conversation.js:803` — `getDefaultResumeId(projectId)`.
- `static/js/conversation.js:817` — `selectResumeSession(projectId,
  claudeSessionId)`.
- `static/js/composer-extras.js:12` — `micBtnHTML(textareaId)` returns `''`
  unless `micAvailable()` (Capacitor native only).
- `static/js/agent-log.js:463` — `triggerAgentAttach(key)` (attach click handler).
- `static/js/search-chats.js:11` — `chatSearchHTML`/`searchPaneInner`; result
  click → `pickChatResult` → `selectResumeSession`.
- `static/css/app.css:2404` — `.composer-controls-row` comment: *"New-chat
  composer controls…"* — CSS author's own label confirming pre-dispatch-only.
- `static/css/app.css:4105` — `.mc-dialog-overlay` — the only reusable overlay
  convention (fixed-inset scrim, `.visible` toggle, centered) — **NOT a bottom
  sheet.** No `translateY(100%)`/`slide-up`/`bottom-sheet` exists anywhere.
- `static/index.html:750` — `let nextModalZ = 201;` — modal windows start far
  below the 9900-9999 band used by mc-dialog/emoji-picker/cmd overlays, so a new
  sheet in that band stacks safely above the agent-panel modal.
- `static/js/mobile.js:55` — `isMobileChatList()` = `innerWidth <= 960`.

### Steps
1. Near the top state maps (`14-17`), add sheet-open state. **Use a single global
   keyed by projectId** (mirror `openProjectProfileDialog` / a
   `composerSheetProjectId` var, `modal-manager.js:583`), **not a set-based map** —
   see the multi-modal risk below.
2. New `openComposerSheet(projectId)` / `closeComposerSheet(projectId)` (after
   `~129`) that set the state and call `refreshModalById(projectId)` (mirrors
   `toggleIncognito`'s refresh at `128`). Export both on `window`.
3. New `_composerPlusStatusLineHTML(p, resumeId)` next to
   `_composerProviderPicker`: build the compact status line from existing
   accessors only (`_composerProvider(p)` + `_agentProviders` display_name,
   `getPendingCharacter(p.id)`/`resolveCharacterMeta`, `getIncognitoFor(p.id)`).
   Render **ONLY when `noActiveTab`** (an active chat already shows
   `_provBadge`/`_charBadge` in the header — don't duplicate).
4. New `_composerSheetHTML(p, resumeId)` calling the SAME existing functions
   unchanged: `_composerProviderPicker(p)`, `_composerCharacterPicker(p,
   resumeId)`, the incognito toggle (extract the inline block at `375-380` into
   `_incognitoChipHTML(p, noActiveTab)` so both the old call site and the sheet
   share it — **preserve the `isIncognitoProject(p)` forced-on branch**),
   `chatSearchHTML(p.id)`, `sessionPickerHTML(p.id)`. Wrap each in a labeled row
   (Agent / Persona / Incognito / Resume). Do NOT touch the picker/toggle
   internals.
5. In the `dispatchRow` template (`388-403`), replace the always-inline
   `${chatSearch}${picker}${resumeIndicator}` + `.composer-controls-row` with: a
   ＋ button (`<button class="btn-composer-plus" onclick="openComposerSheet
   ('${esc(p.id)}')">+</button>`) left of the textarea in `.agent-input-row`,
   `_composerPlusStatusLineHTML(p, resumeId)` under the input row, and a
   conditionally-rendered `_composerSheetHTML(p, resumeId)` (visibility CSS-driven
   off sheet-open state via a `.visible` class, per `app.css:4114/4141`).
   `resumeIndicator` and `dispatchPreviews` stay where they are (status/preview,
   not controls).
6. Followup composer (`525-546`): **leave `_fuAttachBtn` and `_fuMicBtn`
   inline** — the active chat has only these 2 controls; hiding them behind ＋
   adds friction to the one already-minimal screen. Flag this deviation in the PR
   (Decision #7).
7. CSS after the cmd-palette rules (`~4194`) or alongside `.composer-controls-row`
   (`~2404`): `.composer-sheet-overlay` (reuse the `.mc-dialog-overlay` recipe
   verbatim, 9900-tier z-index) + `.composer-sheet` panel. Mobile
   (`@media max-width:960px`): overlay `align-items:flex-end`, panel
   `transform:translateY(100%)` → `.visible{transform:translateY(0)}` slide-up
   with rounded top corners (iOS look). Desktop: a right-anchored popover off the
   ＋ button, or fall back to the centered `.mc-dialog` treatment (Decision #8
   determines whether this branch is throwaway).
8. `.btn-composer-plus` CSS near `.btn-attach` (`~2650`): round icon button
   matching `.btn-attach`/`.btn-mic` sizing.
9. Repurpose the existing `.composer-controls-row` rule (`2405`) for the new
   `.composer-plus-status` line (reuse its flex/gap) rather than leaving dead CSS.
10. Add `window.openComposerSheet` / `window.closeComposerSheet` at `~1665`.

### Corrections to the brief
- The brief conflates two DIFFERENT render sites into "one always-visible
  cluster." In reality `.composer-controls-row` (provider+persona+incognito,
  `conversation.js:402`) and the session picker / chat search (`371-372`) render
  ONLY inside `dispatchRow`, gated on `noActiveTab` (`388`). Once a session is
  active, **none of these five controls exist anywhere.**
- The active/in-chat composer (`525-546`) has only attach + mic + Send/Stop.
  There is no in-chat provider/persona/incognito/resume/search to move — those
  are frozen at spawn and already shown read-only as header badges. Re-injecting
  them as editable pickers in an in-chat sheet would be inert/misleading.
- Chat search is a Resume sub-feature (searches past transcripts to pick a session
  to resume), not a general search; its natural long-term home is the Layer-2
  recents list (Decision #3).
- `modal-manager.js` has NO existing bottom-sheet to reuse — only centered dialog
  overlays. "Reuse modal-manager conventions" = reuse the scrim/toggle/z-index
  mechanics; the slide-up panel shell is new markup+CSS.
- The compact "…Change" status line only makes sense pre-dispatch — during an
  active chat the header badges already show this state.

### Risks
- `refreshModal()`/`refreshModalById()` re-render the whole panel on every poll/
  SSE tick; the open sheet must survive that (reading sheet-open state every
  render handles it, but verify no path resets the state map the way
  `agentConvNew`/`activeAgentTab` get cleared).
- If the sheet is `display:none` when closed rather than always-rendered + class
  toggle, the `<select>` onchange handlers and lazy `_ensureCharacters()` fetch
  (`line 40`) still fire every render regardless of visibility — no behavior
  change, but confirm the sheet doesn't defer `_ensureCharacters()` to open-time
  (would change existing lazy-load timing).
- A fixed scrim inside a modal that is itself a draggable/z-managed
  `.modal-window` needs per-project or single-global-keyed identity — **use the
  `openProjectProfileDialog` single-global-keyed pattern (`modal-manager.js:583`),
  not a set-based map**, to avoid multi-modal state bugs.
- Extracting `_incognitoChipHTML` touches the inline template at `375-380` with
  the `isIncognitoProject(p)` forced-on special case — preserve that branch
  exactly.
- No `walkthrough.js` selectors reference `composer-controls-row`/`btn-attach`/
  `btn-mic`/session-picker/chat-search (verified) — not a regression risk, but
  re-verify after landing.

### Open questions (for Ron)
- Where does chat search land long-term (Decision #3)?
- Does the ＋ sheet exist for the active-chat composer at all (Decision #7)?
- Desktop treatment: same slide-up sheet centered, or header pills / mobile-only
  sheet (Decision #8)?

### Acceptance
Every control the old pre-dispatch composer showed is reachable from the ＋
sheet; provider/persona/incognito/attach/mic/resume all still function via their
existing handlers; the default pre-dispatch composer is input + ＋ + send.

### Revert
Restore the inline `.composer-controls-row` render + `chatSearch`/`picker` in
`dispatchRow`; remove the sheet, status line, and ＋ button.

---

## §1 — Information architecture / context-adaptive bottom nav + scaling caps
### (parallel/separate workstream — do NOT block §3–§8)

**Complexity: large · ~450 LOC · Risk: high.** The investigation found §1 is
**mostly NOT "just CSS"** — it is real routing/state work. Only the 720px thread
cap is CSS-only (and that belongs with §3). Strongly recommend splitting: ship the
cap now, treat the nav/inbox/multi-pane build as its own spike after a product
sign-off on Decision #4 and the open questions below.

### Verified anchors
- `static/js/feed.js:26` — `_buildAttentionList` (builds "Needs you" items from
  live status; **no sessionId attached today**).
- `static/js/feed.js:179` — `renderFeed` click handler: only
  `openProjectModal(projectId)` — **no session-level deep link.**
- `static/js/render-core.js:36` — `computeLiveStatus`/`runningSess`
  (`runningSess.sessionId` exists client-side but is never surfaced to the Feed).
- `static/js/modal-manager.js:124` — `_handleDeepLinkFromUrl`: the real session-
  level deep-link mechanism (URL `?project=&session=`), wired to push
  notifications only, NOT the Feed.
- `static/js/render-core.js:441` — `modal-menu-btn`/`modal-menu-dropdown`: the
  real 3-dot menu, **same on both breakpoints**, holds a 6-tab switcher + a long
  settings tail.
- `static/js/render-core.js:445` — `.mc-tabs-in-menu` tabs: actual current tabs
  are Agent/Backlog/Agent Log/Plans/Activity/Workflows — **not** "Chats/Backlog/
  More."
- `static/css/app.css:1766` — `.mc-tabs-in-menu` `display:none` at `min-width:961px`
  (the tab-switcher section of the ⋮ menu is mobile-only; desktop uses the
  horizontal `.modal-tab-bar`).
- `static/index.html:630` — `#bottom-tab-bar`: static global 5-item mobile-only
  nav (Home/Backlog/+New/Scheduler/Hivemind) — **no project-context awareness, no
  Inbox/Search/You slot.**
- `static/css/app.css:1838` — `.bottom-tab-bar` base `display:none`, only enabled
  ≤960px (`1722`) — **no desktop equivalent exists.**
- `static/index.html:399` — `.sidebar`: always-visible desktop vertical nav, 9
  global items, no per-project context.
- `static/css/app.css:952` — `.modal-content`: desktop project surface is a
  floating, draggable, resizable modal (700px default, up to 95vw) — not a full-
  bleed single-pane view.
- `static/js/conversation.js:268` — mobileMode conv-list vs back-bar branch
  (Layer2⇄Layer3 already works via `activeAgentTab`).
- `static/js/conversation.js:740` — `backToConvList`/`mcBackFromConv` (reusable
  "go back to list," incl. hardware-back sentinel).
- `static/js/mobile.js:146` — `mcPushModalHistory`/`mcPushConvHistory`/
  `mcPushDrawerHistory` (existing history-sentinel pattern; the closest thing to
  "routing," but it does NOT drive nav-bar content).
- `static/index.html:1978` — `consoleStatusLabel` (confirms the pending-plan/
  pending-question vocabulary the brief references exists).
- Multi-pane: `static/css/app.css:1188-1189` — exactly one `.modal-tab-content`
  visible at a time (`display:none` except `.active`); no vertical "recents rail"
  exists; no wide-viewport breakpoint above 961px anywhere in `app.css`.

### Steps (if the full build is greenlit)
1. `feed.js:_buildAttentionList` (`26-54`): attach a `sessionId` per item, pulled
   from `computeLiveStatus(p.id).runningSess.sessionId` (`render-core.js:36-37`).
2. `feed.js:renderFeed` click (`179-181`): replace bare `openProjectModal(pid)`
   with a shared `openProjectAtSession(pid, sid)` mirroring `modal-manager.js:
   139-176` (`openProjectModal → await fetchAgentStatus → switchAgentTab`, with
   the reconstruct-from-transcript fallback at `156-173` for a non-live session).
   Export it so both the desktop Feed and the new mobile inbox share it — do NOT
   duplicate the race-prone logic.
3. New mobile "Inbox" surface (`mobile.js` + a new `#bottom-tab-bar` slot): since
   `.feed-col` is desktop-only (`app.css:1721`), build a mobile full-screen list
   reusing `_buildAttentionList` data + `classifyFeedEvent` styling, modeled on
   `renderMobileChatList`/`projectRowHTML` (`mobile.js:101-144`), each row tapping
   through the step-2 helper.
4. `modal-manager.js`: add a `renderBottomTabBar()`/`_syncNavContext()` call at
   each lifecycle point — `openProjectModal` (`186`), `closeModalById` (`306`),
   `focusModal` (`104`), minimize/restore — to swap `#bottom-tab-bar` between the
   global 5-item markup and a project-context 5-item markup (Home / Chats
   (`backToConvList`) / +New (`newAgentTab`) / Backlog (`_mcMenuSwitchTab(pid,
   'backlog')`) / ⋮ More (`toggleModalMenu`)).
5. `app.css` near `.bottom-tab-bar` (`1838-1844, 2047-2069`): add a
   `.bottom-tab-bar.project-context` variant reusing the existing grid-5/FAB-lift
   pattern (keep it a fixed docked cluster, `justify-content:space-around`/grid-5,
   NOT `space-between`).
6. **720px thread cap (the only CSS-only, low-risk part — ship with §3):** wrap
   `.agent-output`'s inner content in `max-width:720px; margin:0 auto`. Safe
   against the resizable modal. (See §3 step 6 — apply to `.agent-chat`, gated
   `@media (min-width:961px)`.)
7. Multi-pane desktop reveal (genuinely new): a new `@media (min-width:~1400px)`
   grid (vertical recents rail | capped thread | plan/log pane), requiring
   relaxing the single-active-tab rule (`app.css:1188-1189`) to show two tab-
   contents at once. **Its own spike, not CSS-only.**
8. Desktop nav cluster (net-new, no analog): decide explicitly how a docked
   centered cluster coexists with the always-on vertical `.sidebar` before
   building — a UX decision, not a relocation.

### Corrections to the brief (this section has the most)
- The Layer-1 nav table (`Home · Inbox · ＋ · Search · You`) does not match the
  real bar (Home/Backlog/+New/Scheduler/Hivemind, `index.html:630-646`). No
  Inbox/Search/You slot exists.
- "Waiting on you" lives ONLY in a desktop-only side panel (`feed.js`,
  `#feed-entries`, `display:none` on mobile at `app.css:1721`). On mobile there is
  **zero** entry point today — this is building a mobile surface from scratch, not
  a relocation.
- "Tapping deep-links straight to that chat's Needs-you state" is **false today** —
  the Feed attaches no sessionId and lands on the Layer-2 list. A real session-
  level deep-link exists (`_handleDeepLinkFromUrl`) but is wired to push/URL only.
  Reaching the brief's target needs real new glue.
- The "mobile 3-dot menu" is mischaracterized — the dropdown is the same on both
  breakpoints and holds a 6-tab switcher + settings tail, not "Chats/Backlog/
  More."
- There is no context-adaptive bottom bar on either breakpoint; making it swap
  needs new JS state wired into `openProjectModal`/`closeModalById`/`focusModal`/
  minimize-restore — real routing/state work.
- Desktop has no bottom nav bar at all to relocate into; the desktop nav is the
  vertical `.sidebar` + floating modals. A docked centered cluster is brand-new
  UI with no precedent, and the brief doesn't address coexistence with the
  sidebar.
- The 720px cap is genuinely CSS-only and correctly scoped.
- Multi-pane reveal is *more* new than implied (only one tab-content visible
  today; no recents rail; no >961px breakpoint).
- Layer-2 "conversation list (searchable)" overstates current capability — the
  recents list exists but is NOT searchable; the only "chat search" is the resume
  sub-feature on the +New screen.
- Three themes, not two.

### Risks
- Deep-link reuse must not skip the `await fetchAgentStatus` step (the reason for
  the reconstruct-from-transcript fallback) — skipping it reproduces a known race
  where the chat renders before the pending question/plan loads.
- The context swap must fire on every close path (incl. `sidebarNav('dashboard')`'s
  close-all branch and minimize) or the project-context bar goes stale/orphaned.
- `sidebarNav()`'s active-state toggling assumes fixed `[data-nav]` values on
  `#bottom-tab-bar` — a swapped bar must preserve the `data-nav` convention.
- `walkthrough.js:134` targets `#bottom-tab-bar` by id; swapping contents by
  context could break that step if it fires while a modal is focused.
- New bottom chrome changes available vertical space; anything not reserving space
  the way `.agent-console` reserves `bottom:52px` (`app.css:1723`) gets covered.
- A desktop bottom cluster + always-on vertical sidebar has no existing z-index/
  space contract — overlap risk.

### Open questions (for Ron)
- "Backlog" at Layer 1: the in-modal per-project backlog tab, or the existing
  global cross-project backlog surface?
- What does Layer-1 "🔍 Search" mean — is there an existing global project search,
  or is it net-new? (None found.)
- What does "You" mean — Settings/profile (the existing `mc-avatar-btn` already
  opens Settings)? If it replaces Hivemind's slot, where does Hivemind go?
- Is the multi-pane "recents rail" per-project or cross-project?
- Should the mobile "Waiting on you" inbox replace or sit alongside the existing
  "Needs you" filter pill (`mobile.js:35`)? Both risks a redundant surface.

### Acceptance / revert
Scope-dependent on Decision #4. If shipping only the 720px cap: acceptance/revert
per §3. If building the full IA: define acceptance per surface (mobile inbox deep-
links to the waiting chat; context bar swaps correctly on every lifecycle path;
multi-pane reveals side-by-side above the wide breakpoint) — and land it behind
its own commit series, not bundled with the conversation-view work.

---

## Cross-cutting notes

1. **Pre-existing bug: `.mermaid-placeholder` dead selector** (`app.css:2799`).
   Mermaid emits `class="mermaid-block"` (`mermaid.js:20, :160`), so the
   `flex-shrink:0` opt-out never matches, and `.mermaid-block` (which has
   `overflow:auto`) gets crushed to a "black line" on mobile today. **Fix
   `.mermaid-placeholder` → `.mermaid-block` during §3** (it rides along with the
   bubble promotion).

2. **Pre-existing bug: hardcoded purple hex on `.plan-show-btn`** (`app.css:3047`)
   and **undefined `var(--bg2)`** on the inline "Collapse Plan" button
   (`conversation.js:938`; `--bg2` is defined in none of the three themes, so it
   renders with no effective background). The new §7 `.plan-card` must NOT copy
   either; migrating `.plan-show-btn` to tokens in the same pass is opportunistic
   good hygiene.

3. **ES-module `window.*` export gotcha.** Any new function invoked from an
   inline `onclick=""` or by bare name from another module MUST be attached to
   `window.` (see `conversation.js:1655-1675`, `rich-text.js:255-264`). Affects
   §4 (`showTypingIndicator`/`hideTypingIndicator`), §5 (`fillStarterChip`), §6
   (`submitQuestionChip`), §7 (`_parsePlanSteps`), §8 (`openComposerSheet`/
   `closeComposerSheet`). Forgetting it throws `ReferenceError` at click time.

4. **Namespace collision: avoid bare `.plan-card` naming for children.**
   `app.css:3710-3726` already defines global `.plan-card-body`/`.plan-card-delete`
   for the unrelated Plan-History tab. §7 must use `.plan-card-steps`/
   `.plan-card-actions` (the root `.plan-card` itself is fine — verify it doesn't
   clash). §3's `.plan-card` stretch entry is the same intended class.

5. **The 720px cap must be gated `@media (min-width:961px)`, not global.** An
   unconditional cap silently changes today's shipped 720–960px tablet behavior
   (edge-to-edge → centered-with-gutters). Apply it to `.agent-chat` (so the
   composer stays aligned with the bubble column), not `.agent-output` alone.

6. **§3 ↔ §7 interaction (bubble cap vs plan card width).** The `.plan-card` must
   be added to the `flex-shrink:0/align-self:stretch` opt-out list
   (`app.css:2794`) so it isn't squeezed by the 72% bubble cap — the plan card
   should stretch to the thread width, not sit as a narrow right/left bubble. §3
   and §7 both touch that same selector list; land §3 first (it makes the block
   unconditional) then §7 just adds the selector.

7. **§3 ↔ §4 interaction (bubble parent vs typing dots).** §4's
   `.typing-indicator` uses `align-self:flex-start`, which only works when
   `.agent-output` is flex — which is exactly what §3 makes unconditional. If §4
   ever lands before §3, add a duplicate indicator rule inside the
   `@media (max-width:960px)` block so mobile still works, and expect the desktop
   indicator to be misaligned until §3 lands. Keep the recommended order (§3 → §4).

8. **§1 may be real routing work, not "just CSS."** The brief frames the nav-bar
   relocation and multi-pane reveal as mostly presentation; the investigation
   shows there is no context-adaptive bottom bar, no mobile attention-list surface,
   no Feed→session deep-link, and no >961px multi-pane precedent today. Treat §1
   beyond the 720px cap as a scoped follow-up spike, gated on Decision #4.

9. **Two divergent plan render paths.** Path A (buffer rebuild,
   `conversation.js:449`) and Path B (live stream, `:908`) emit *different*
   ExitPlanMode markup and must be kept visually consistent in both §3 (bubble
   layout) and §7 (plan card). QA both a live run and a mid-plan tab-switch/reload.

10. **Backend is out of scope.** §6's AskUserQuestion data contract is stable
    across both interception sites (`agent_routes.py:2010, :2190`) and the SSE
    emission (`:3884`) — no `mc/blueprints/agent_routes.py` change is needed for
    any task. This entire plan is frontend (`static/js/*`, `static/css/app.css`,
    `static/index.html`).

---

## Honest scoping summary

| § | Task | Complexity | LOC | Risk |
|---|------|-----------|-----|------|
| §3 | Chat bubbles on desktop (CSS promotion + mermaid bugfix + 720px cap) | medium | ~70 | medium |
| §4 | Typing-dots indicator | small | ~45 | medium |
| §7 | Numbered plan-approval card | medium | ~180 | medium |
| §6 | Quick-reply chips (AskUserQuestion) | small | ~90 | low |
| §5 | Starter suggestion chips | small | ~45 | low |
| §8 | ＋ options bottom sheet | medium | ~220 | medium |
| §1 | IA / context-adaptive nav + scaling (full build) | large | ~450 | high |

**Bottom line.** The conversation-view redesign (§3–§8) totals roughly **650 LOC
of front-end work across ~5 files** (`static/js/conversation.js`,
`static/js/resume-preview.js`, `static/js/rich-text.js`, `static/css/app.css`,
plus small touches to `mobile.js`/`composer-extras.js`). None of it requires a
backend change — every element maps to a shipped feature, exactly as the brief
promised. The single biggest landmine is **§1**: the brief sells it as
relocation/CSS, but the investigation shows it is ~450 LOC of genuine routing/
state work (mobile attention-list built from scratch, Feed→session deep-linking,
a context-adaptive bottom bar wired into four modal-lifecycle points, a net-new
desktop nav cluster with no precedent, and a multi-pane layout that requires
relaxing the one-tab-visible rule). Split it: ship the 720px thread cap with §3
now, and gate the rest of §1 behind Ron's answers to Decisions #3, #4 and the §1
open questions. Secondary landmines: §3's large sensitive CSS region (wrong cap
target misaligns the composer; ungated cap regresses tablet width) and §7's three
emission sites plus the load-bearing `plan-approve-${sessionId}` DOM id. The two
free bugfixes riding along — `.mermaid-placeholder`→`.mermaid-block` (§3) and the
`var(--bg2)`/hardcoded-purple plan CSS (§7) — should not be skipped.
