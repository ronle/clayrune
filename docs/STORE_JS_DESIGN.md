# Store.js design checkpoint — architecture for the remaining core extraction

**Status:** DRAFT v1 (2026-06-10, Fable orchestrating) — awaiting Ron's sign-off.
**Scope:** the final phase of the frontend modernization track
(`docs/MODERNIZATION_TRACKS.md`, progress `docs/_tracks/frontend_progress.md`).
**Baseline:** `refactor/frontend` @ `d736126` (module 21). index.html = 11,761
lines; main inline script L680–11738 (~11,059L); 20 ES modules shipped.

---

## 1. What this decides

Modules 1–21 exhausted the leaf features. Everything left inline is the
agent/conversation core plus the modal/grid engine, welded together by shared
mutable globals (`agentStatusCache`, `agentOutputBuffers`, `agentEventSources`,
`agentHistory`, `openModals`, `hivemindCache`, …). The track brief deferred this
to a single design decision: **where does shared state live so the remaining
~11kL of feature code can leave index.html without behavior change?**

## 2. The audit (formal, deterministic)

Tooling: `_scratch/store_audit.py` + `_scratch/store_audit2.py` (JS-aware
masker `_scratch/mask_js.py`: comments/strings masked, template-literal `${}`
nesting, regex-literal heuristic, per-char brace depth — validated: final
depth 0; spot-checks `hivemindCache` L8320, `openModals` L1961 match the
module-21 log). Scanned the inline script + all 20 extracted modules +
generated-handler strings.

Headline numbers:

- **134 top-level state declarations**, 390 top-level function declarations.
- **72 top-level parse-time statements** (listener arms, `setInterval`s,
  `fetchProjects()`/`startRefresh()` boot calls, SW registration) — the boot
  skeleton.
- **The feared wholesale-reassignment problem is marginal.** Across the whole
  agent smear: `agentStatusCache` 0 wholesale / 6 prop-writes / 84 reads;
  `agentEventSources` 0/1/48; `agentSSEWatchdogs` 0/1/44; `agentOutputBuffers`
  0/10/20; `agentServerLines` 0/6/12; `activeAgentTab` 0/9/30; `agentHistory`
  **1** wholesale (L5828 `agentHistory = agentHistory.filter(...)`) + 7
  mutations. Outside the smear: `allProjects` 2 wholesale (both
  `= await res.json()`), `_agentProviders` 2 (one FROM provider-auth.js — see
  §3), `projectOrder` 3, `_globalConfig` 2, `cmdPaletteOpen` 3 (two FROM
  walkthrough.js), `_skipAgentOutput` 2 (from Command Palette code).
- Container identities are stable: state is overwhelmingly keyed
  property-writes on long-lived `{}`/`Map`/`Set` objects.

## 3. The load-bearing platform fact (already proven in-repo)

Top-level `let`/`const` in a **classic** script live in the realm's **global
lexical environment**, and every ES module's scope chain terminates in that
same environment. Therefore **modules can read AND assign inline-declared
top-level bindings by bare name** — no `window.` qualification, no bridge,
strict-mode legal (the binding exists; strict only bans creating implicit
globals).

This is not a theory; 20 shipped modules already do it:

- `nextModalZ++` (wholesale mutation of an inline `let`) from 10 modules.
- `cmdPaletteOpen = true/false` from walkthrough.js (L174/193).
- `_agentProviders = null` from provider-auth.js (L216).
- `openModals` (inline `const`) read/mutated from 11 modules.
- `modalActiveTab[pid] = …` from backlog-actions.js + schedule-banner.js.

The asymmetry that forced every bridge so far runs the **other way only**:
state declared at a module's top level is module-scoped — invisible to the
inline script and to generated `on*=` attribute handlers (whose scope chain
also resolves through the global environment). Bridges exist to re-expose
**moved** state, never to let moved **code** see inline state.

## 4. Options considered

**A. State anchors inline; code moves out.** (RECOMMENDED) Keep every
genuinely-shared variable as an inline top-level declaration — consolidated
into one labeled `// ── STORE ──` block — and extract the remaining feature
families as ES modules exactly like modules 1–21. Family-private state moves
*with* its family (module-scoped), per the established pattern.

- Zero bridges for store state, in any direction, by construction.
- Zero timing hazard: the classic script parses before any module evaluates,
  so store bindings exist before the first module top-level runs. (Several
  boot-skeleton statements read state at parse time — L896 `projectLastSeen`,
  L10580 `_isMobileDevice`, L10598 `feedCollapsed` — these keep working
  untouched.)
- Moved code stays **byte-verbatim** (the track's core verification trick).
- "store.js" the *file* does not exist; the store is the inline declaration
  block + a documented contract. The name survives as the concept.

**B. True `static/js/store.js` module owning state as `window.*` props.**
Inline declarations deleted; a first-in-document module initializes
`window.agentStatusCache = {}` etc.; all code references window-backed
globals. Rejected: the inline script executes at parse time **before any
module**, so every parse-time state read breaks unless those initializers
stay inline — splitting the store in two; plus strict-module bare assignment
to a not-yet-created window prop throws if any ordering slips. All risk, no
functional gain over A.

**C. Namespaced store object (`MC.state.agentStatusCache…`).** Rewrite of
~1,200+ references, destroys byte-verbatim verification, maximal regression
surface for zero behavior gain. Rejected outright.

## 5. The store contract (Option A rules)

1. **One block.** All shared state consolidates into a single
   `// ── STORE: shared mutable state (read/written across families/modules) ──`
   section near the top of the inline script (after `API_BASE`). Each line
   keeps/gains a terse ownership comment: `// owner: <family>; writers: <…>`.
   Pure intra-file move; relative initializer order preserved; gated by boot
   smoke (TDZ class — `tools/smoke/`) like any other change.
2. **Membership = shared.** A variable enters the store iff ≥2 families or
   any extracted module touches it. Single-family state moves out WITH its
   family as module-private (audit col "sections touched" decides; re-verify
   per module at cut time — module-4 lesson).
3. **Modules reference store vars bare-name.** Never re-declare a store name
   at module top level (shadowing = silent split-brain). Per-module gate: a
   declared-names ∩ store-names == ∅ scan.
4. **Container identity discipline (codifies what the audit shows is already
   true):** store containers are never wholesale-reassigned from moved code
   except the audited sites (`agentHistory` filter L5828, `allProjects`
   refetches, `_agentProviders` invalidation, `projectOrder` rebuilds,
   `cmdPaletteOpen`/`_skipAgentOutput` flags). Those keep working under A
   (binding assignment); anything NEW prefers in-place mutation.
5. **Generated `on*=` handlers** may keep referencing store vars (global
   environment resolves them). Handlers referencing **moved family-private**
   state still need the identity-bridge pattern — unchanged from modules 8/21.
6. **The boot skeleton stays inline**: the 72 top-level statements (listener
   arms, intervals, `fetchProjects()`, `startRefresh()`, SW registration,
   `_initNativePush` IIFE) plus `render()` orchestration glue. Function
   *bodies* they call may move; the arming lines do not. (Recurring boot-tail
   trap, module-17/21 lesson.)
7. **All existing per-module gates continue verbatim:** byte-verbatim
   two-sided reassembly, brace-depth region scan, formal writer/refscans,
   window re-exposure tails for cross-module/HTML-attr callers, sw.js version
   bump, route.fulfill in BOTH smoke harnesses, throwaway-port real-server
   check, headless-Chromium feature exercise, never touch live :5199.

## 6. Store membership (from the audit; re-verify at consolidation time)

**Agent core:** `agentStatusCache` `agentEventSources` `agentSSEWatchdogs`
`agentOutputBuffers` `agentServerLines` `agentHistory` `activeAgentTab`
`agentConvNew` `agentLogCache` `conversationsCache` `plansCache`
`hivemindCache` `pendingResumeId` `agentPendingImages` `followupTimeouts`
`_sendInFlight` `sseRetryCount` `agentLastEventAt` `_skipAgentOutput`
`planViewerContent` `planFileTitle` `exitPlanModeCount` `_answeredQuestionIds`
`_recentlyStoppedSessions` `_turnStartAcked` `acExpanded` `acOpenSessions`
`pendingDispatchProvider` `incognitoToggle` `_pinScrollQueue`
`expandedOutputSessions` `_aqCounter`(?single-family — verify)
**Projects/UI core:** `API_BASE` `allProjects` `projectOrder` `_globalConfig`
`_agentProviders` `_isMobileDevice` `openModals` `nextModalZ` `focusedModalId`
`modalActiveTab` `modalSearchQuery` `textareaValues` `textareaHeights`
`activeFilter` `currentView` `feedCollapsed` `domainsList` `advancedFlags`
`tokenCounterMode` `enterKeyMode` `currentTone` `currentAccent` `currentVoice`
`undoStack` `showDoneMap` `openAttPanels` `openNotesPanels` `rulesLoaded`
`_transcriptCache` `modalZoomLevels` `_mc*HistoryActive`×4 `_mcSuppressPop`
`mcUsageCache` `bg*` family (settings-drill coupling) `cmdPaletteOpen`
`refreshCountdown` `refreshInterval` `countdownInterval` `lastDragEnd`
`projectLastSeen` `_orderKey` `FRIENDLY_TO_VOICE` `COLOR_PRESETS`
`_CLAUDE_DEFAULT_CAPS` `ADV_FEATURES` `TOKEN_MODES` `SSE_ZOMBIE_MS`
**Moves with family (module-private; sample):** `dragState` `modalDrag`
`modalPinch` `modalTouchResize` `chatResize` `_fpState` `_micState` `_micGen`
`planSelections` `memoryCache` `cmdSelectedIndex` `_bgCrop` `_emojiPickerProjectId`
`hivemindDashboardWs` `_hmDash*` `hivemindSSE` `hmPopover*` `pendingFiles`
`folderBrowser*` `newProjDomain` `_snapPreviewEl` `SNAP_*` `RESIZE_ZONE`
`_TILE_TEMPLATES` `_convPreview*` `_reconcileBusy` `_resumeBlankoutSince`
`_lastResyncAt` `_modalPref*` `tileLongPressTimer` `_fpState` `_bgDimsLoading`
`_bgResizeTimer` `EMOJI_CHOICES` `VOICE_LABELS` `FRESHNESS_TICK_MS`
`hmPopoverSessionId` `_EMPTY_SET`

(Authoritative regeneration: rerun `_scratch/store_audit.py` against the
then-current file before each cut.)

## 7. Execution queue (modules 22+; sizes from the section map @ d736126)

| # | Module | Sections (≈lines) | Notes |
|---|--------|-------------------|-------|
| 0 | **Store consolidation** | move ~70 shared decls to the STORE block (~120L shuffle) | TDZ-gated; do FIRST so later cuts are clean |
| 22 | rich-text.js | Rich text formatting (1,074) | hot path (SSE); NO CDN imports (mermaid lesson); biggest single win |
| 23 | conversation.js | Conversation model + Agent Panel + worker popover (545+89+80) | the heart: appendAgentLine, connectAgentStream, dispatch, followup, stop |
| 24 | resume-preview.js | Resume-conversation inline preview (834) | contains the `agentHistory` wholesale site — fine under A |
| 25 | agent-log.js | Agent Log + Plans tab + continue + image paste (157+186+49+83) | |
| 26 | hivemind.js | Hivemind tab + Dashboard Modal (202+544) | unblocked: `hivemindCache` is store-resident |
| 27 | agent-console.js | Agent Console + Memory tab + tab switch/search (174+144+87) | |
| 28 | cmd-palette.js | Command Palette (520) | zero bridges under A (`cmdPaletteOpen` store, `cmdSelectedIndex` private) |
| 29 | modal-manager.js | modal prefs + deep link + three-dot (113+256+273) | deep link is boot-coupled — arm-lines stay |
| 30 | render-core.js | Tile HTML + Modal HTML + List View (304+463+45) | template builders; `render()` glue stays inline |
| 31 | interactions.js | DnD + Aero-Snap + multi-modal tiles + separator + touch + zoom (186+193+268+72+78+30) | heavy listener arms stay inline |
| 32+ | mop-up | mobile UI, appearance/bg, attachments, GitHub sync, code sync, folder pickers, project creation, FCM/pollers | per-family judgment; diminishing returns |

Projected end state: inline script ≈ boot skeleton + STORE block + `render()`
glue + arming lines ≈ **1,500–2,000 lines**; index.html total ≈ **2,200–2,700**
(from 25,165 at track start).

## 8. Risks & mitigations

- **TDZ during consolidation** — initializers referencing helpers are safe
  (function decls hoist file-wide) but intra-block order matters
  (`projectOrder` init may read `_orderKey`): preserve relative order, run
  boot smoke + a fresh top-level-read scan. This is the exact bug class
  `tools/smoke/` exists for.
- **Shadowing** — a module re-declaring a store name silently forks state:
  per-module automated check (rule 3).
- **Strict-mode promotion** of moved code — continue the per-module audit
  (zero `this`, every assignment targets a declared binding/window prop).
- **Hot-path regression** (SSE → appendAgentLine → formatter across module
  boundaries) — window-prop/global lookups are nanoseconds; mermaid.js
  already sits on this path. Headless-Chromium synthetic-SSE exercise
  (module-8/9 technique) gates every agent-core cut.
- **Worktree/live-server discipline** — unchanged: cut on `wt-fe-*` branches
  in the fe worktree, throwaway ports only, live :5199 untouched, explicit-
  path staging.

## 9. Acceptance per cut (unchanged from modules 1–21)

`node --check` (module goal) · two-sided byte reassembly · formal scans
(writers, shadowing, generated handlers) · boot-smoke 5/5 · real-server 200 +
exact Content-Length · headless feature exercise incl. synthetic SSE where
applicable · bg-framing-check shows only the pre-existing base error · sw.js
bump · progress-log entry with landmines for the next module.
