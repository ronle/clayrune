# Multi-Provider Full Parity — Separation-Layer Design

**Branch:** `feat/multi-provider-parity` (rollback point: `feat/multi-provider-agents` @ `35e31b4`)
**Date:** 2026-05-22
**Status:** Active — supersedes the "degrade gracefully" stance of `MULTI_PROVIDER_DESIGN.md`
**Companion docs:** `MULTI_PROVIDER_DESIGN.md`, `MULTI_PROVIDER_COUPLING_AUDIT.md`,
`MULTI_PROVIDER_CAPABILITY_MATRIX.md`, `MULTI_PROVIDER_PARITY_MATRIX.md`

---

## 1. The goal and the inversion

**Goal:** Every Mission Control feature works for every agent provider. A project
on Gemini, Codex, OpenCode, Goose, Aider or Kiro behaves the same as a project on
Claude — attachments, Skills, plan-mode, AskUserQuestion, MCP, memory, hivemind.
No feature is silently switched off because of who the provider is.

**The inversion.** The existing `MULTI_PROVIDER_DESIGN.md` treats the *provider CLI*
as the owner of features: "does this CLI support X natively? If not, `DISABLE_WARN`."
That is why Gemini loses image attachments, Skills, and plan-mode — by design.

This document inverts that. **MC's orchestration layer owns the features. The
provider CLI is a model-runner** — it takes a prompt and streams back text and
tool calls. Anything MC needs that the CLI doesn't natively do, MC *emulates* in
the runtime layer rather than disabling.

The test for every feature becomes: not "does the CLI support it?" but "can MC
deliver it given only a prompt channel and an output stream?" For almost
everything, the answer is yes.

## 2. Current state (ground truth, not design intent)

The `AgentRuntime` abstraction in `agent_runtime.py` already exists and is
*partially* wired:

- **Wired through the runtime:** dispatch, follow-up, stop/interrupt, auth/health
  for non-Claude providers. `GeminiRuntime` is genuinely functional for Mode A.
- **Still Claude-coupled:** guardian/revive, hivemind orchestrator, condense,
  project-summary generation, `mcp_installer` AI calls, Scribe session-memory
  extraction (transcript-based).
- **Capability gating is comprehensive:** ~13 gates in `static/index.html`, ~5 in
  `server.py`, all keyed off `ProviderCapabilities` flags. This is *good* — it
  means once a capability flips `true`, the feature's UI re-appears with no
  frontend edit. The gating is the lever; we don't fight it, we satisfy it.

So the separation layer is structurally present. The parity work is: (a) build the
emulation pieces, (b) finish call-site migration, (c) flip the capability flags.

## 3. The parity model — six categories

Every feature reaches parity through exactly one of these mechanisms.

| Cat | Mechanism | Owner | Examples |
|-----|-----------|-------|----------|
| **A — Translate** | Runtime adapter rewrites MC's request into the CLI's idiom | runtime subclass | context injection (`GEMINI.md`/`AGENTS.md`), attachments, MCP config |
| **B — Tool emulation** | MC Tool Protocol: prompt instruction + output-stream parsing (§4) | shared `AgentRuntime` mixin | AskUserQuestion, plan-mode, TodoWrite |
| **C — MC re-implementation** | The subsystem calls `runtime.oneshot()` instead of `claude` | `server.py` | Scribe, condense, project summary, `mcp_installer` |
| **D — Context injection** | Feature content is injected into the prompt | runtime + `server.py` | Skills, MEMORY.md, project rules |
| **E — Synthetic continuity** | Runtime re-spawns with prior context to fake persistence | runtime subclass | Mode B, session resume, followups |
| **F — Estimate** | MC computes an approximation the CLI doesn't report | `server.py` | token counts, cost (**deferred**) |

The only genuine *hard limit* is category F — exact provider-side token/cost/
rate-limit numbers. Ron has explicitly deferred F ("estimating tokens is fine for
now"). Everything else reaches *real* parity.

## 4. The MC Tool Protocol (category B — the key new piece)

Claude Code ships `AskUserQuestion`, `ExitPlanMode`, and `TodoWrite` as native
tools. Other CLIs don't. Rather than disable those features, MC defines a
**provider-agnostic, text-based tool-call convention** that any instruction-
following model can use.

### 4.1 How it works

1. **Injection.** For any non-Claude session, the runtime appends a protocol
   section to the system prompt:

   > When you need to ask the user a multiple-choice question, output a fenced
   > block and then stop:
   > ` ```mc:question ` + JSON `{questions:[{question,header,options:[...],multiSelect}]}`
   >
   > When you want to present a plan for approval before doing work, output:
   > ` ```mc:plan ` + the plan markdown, then stop.
   >
   > When your task list changes, output:
   > ` ```mc:todo ` + JSON `{todos:[{content,status}]}`.

2. **Parsing.** The runtime's reader thread scans the output stream for these
   fenced sentinels. On a match it synthesizes the **same normalized
   `AgentEvent`** the Claude path produces — `EventType.QUESTION`,
   `EventType.PLAN_REQUEST`, `EventType.TOOL_USE(name='TodoWrite')`.

3. **Downstream is unchanged.** Because the events are identical to Claude's,
   every existing consumer — the SSE `question` event, `renderAgentQuestion()`,
   plan-approval UI, `TodoWrite`→backlog sync — works with zero changes.

4. **The answer round-trips as a normal follow-up message.** This already works
   for every provider (it is just stdin text). Confirmed in this session's
   AskUserQuestion fix.

### 4.2 Why this is sound

- It is the *same* mechanism Claude uses internally — a tool is just a
  structured request the harness intercepts. MC becomes the harness.
- It degrades safely: if a weaker model emits malformed JSON, the runtime falls
  back to surfacing the block as plain text (no crash, no lost turn).
- It is one shared implementation on the `AgentRuntime` base class. Every
  non-Claude provider inherits it; no per-provider duplication.
- Claude is untouched — it keeps its native tools. The protocol is only injected
  when `capabilities().native_tools is False`.

### 4.3 Scope of the protocol

`mc:question`, `mc:plan`, `mc:todo` for v1. The protocol is extensible — any
future MC-side "tool" (e.g. `mc:notify` for push) follows the same pattern.

## 5. Per-feature parity plan

| Feature | Today (non-Claude) | Parity mechanism | Stage |
|---------|--------------------|--------------------|-------|
| Image / file attachments | disabled (`image_input:false`) | **A** — runtime injects attachment paths into the prompt with a read-instruction; multimodal where the CLI's read tool supports it | 1 |
| AskUserQuestion | disabled | **B** — `mc:question` | 2 |
| Plan mode | disabled | **B** — `mc:plan` | 2 |
| TodoWrite → backlog | disabled | **B** — `mc:todo` | 2 |
| Skills | disabled | **D** — catalog injected into system prompt; full `SKILL.md` injected on invocation | 3 |
| MEMORY.md / rules / context | partial | **A/D** — `GEMINI.md`/`AGENTS.md` or prompt prepend | 3 |
| Scribe (session memory) | Claude-only | **C** — `runtime.oneshot()`; extract from MC `log_lines` when no transcript | 4 |
| Condense | Claude-only | **C** — `runtime.oneshot()` | 4 |
| Project summary | Claude-only | **C** — `runtime.oneshot()` | 4 |
| `mcp_installer` AI calls | Claude-only | **C** — `runtime.oneshot()`, housekeeping provider | 4 |
| MCP servers | partial | **A** — translate config per provider; Aider via MCP-to-prompt bridge | 4 |
| Session resume / Mode B | Mode A only | **E** — synthetic Mode B (re-spawn + context) | 5 |
| Hivemind (worker + orchestrator) | worker wired, orchestrator Claude-only | **C** — orchestrator through runtime | 7 |
| Guardian / revive | Claude-only | **C** — provider-aware respawn | 7 |
| Token / cost / rate-limit | disabled | **F** — estimate | 8 (deferred) |

## 6. Staged rollout

Tracked as tasks #1–#9. Each stage is one or more commits on
`feat/multi-provider-parity`; each is independently revertable.

- **Stage 0** — branch + this doc. *(done)*
- **Stage 1** — attachment / image parity. Smallest real vertical slice; proves
  category A end-to-end (runtime change → capability flip → UI re-appears).
- **Stage 2** — the MC Tool Protocol. The core new architecture; unlocks
  AskUserQuestion, plan-mode, TodoWrite for every provider at once.
- **Stage 3** — Skills + context injection parity.
- **Stage 4** — memory subsystems (Scribe / condense / summary / `mcp_installer`)
  + MCP config translation, all via `runtime.oneshot()`.
- **Stage 5** — synthetic Mode B + session resume.
- **Stage 6** — flip `ProviderCapabilities` flags; the gates pass naturally.
- **Stage 7** — migrate the remaining Claude-coupled call sites (guardian,
  revive, hivemind orchestrator).
- **Stage 8** — *(deferred)* token/cost estimation.

Stages 1–5 are largely independent and can be built in parallel (separate
sub-agents / worktrees) since they touch different regions; Stage 6 depends on
1–5; Stage 7 is independent.

## 7. The capability model after parity

`ProviderCapabilities` stops being a feature *kill-switch* and becomes an honest
*mechanism descriptor*. After this work, for every Tier-1/2 provider:

- `supports_skills`, `supports_plan_mode`, `supports_ask_user_question`,
  `image_input`, `supports_mcp`, `supports_session_resume`, `supports_mode_b`
  → **all `true`** (delivered by emulation).
- `mode_b_kind` → `'synthetic'` for providers without a native persistent stream
  (honest: it describes *how*, not *whether*).
- `emits_usage` / `emits_cost` / `emits_rate_limit` → stay **`false`** until
  Stage 8; these gate only the telemetry badges, nothing functional.

A new flag `native_tools: bool` (default `false`, `true` only for Claude) decides
whether the runtime injects the MC Tool Protocol. This is the one capability the
emulation layer reads.

## 8. Rollback & safety

- **All work is on `feat/multi-provider-parity`.** A full rollback is
  `git checkout feat/multi-provider-agents` — that branch already carries this
  session's verified UI fixes and the prototype, so rollback loses only the
  parity work.
- **Claude is never touched.** The `native_tools` gate means the entire emulation
  layer is dead code on a Claude session. `ClaudeRuntime` and the legacy dispatch
  path stay byte-identical — the `MULTI_PROVIDER_TEST_PLAN.md` hard-stop (B2:
  "Claude unchanged") still holds and is re-verified each stage.
- **Each stage is its own commit** with its own test, so a single stage can be
  reverted without unwinding the rest.
- **Emulation fails soft.** A malformed `mc:` block, an unreadable attachment, a
  failed `oneshot()` — each degrades to plain text / a logged warning, never a
  crashed session. Same posture as Scribe's thin-guard.

## 9. Deferred / out of scope

- **Stage 8 — exact telemetry.** Token/cost/rate-limit numbers. Deferred by Ron
  2026-05-22. Interim: estimate from character counts so the badges show
  *something*; the real fix (per-provider usage parsing where the CLI exposes it)
  comes later.
- **Cross-provider session migration** — a session stays on the provider it
  started on. Unchanged from `MULTI_PROVIDER_DESIGN.md`.
- **Provider API integration** — direct CLI only, no cloud APIs. Unchanged.
