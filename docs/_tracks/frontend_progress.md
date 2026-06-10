# Track B (frontend) — progress log

Single-writer log per `MODERNIZATION_TRACKS.md`. One entry per step: step id,
what moved, commit SHA, gate results.

## Phase 3 — module 1: extract inline `<style>` → `static/css/app.css` (2026-06-09)

- **What moved:** the single inline `<style>` block in `static/index.html`
  (old lines 211–4789; tags at column 0) → new `static/css/app.css`,
  **byte-identical** (proven by binary reassembly assertion: original file ==
  new file with the `<link>` swapped back for `<style>` + css + `</style>`).
  Replaced in place with one `<link rel="stylesheet" href="/static/css/app.css">`
  at line 211 in `<head>`. The only other `<style>` text in the file is a
  single-line *JS string* (spinner keyframes, `'<style>@keyframes mc-spin…'`)
  — not an HTML style block; untouched.
- **Numbers:** `app.css` = 214,041 bytes / 4,577 CSS lines (CRLF, no BOM,
  5,747 non-ASCII UTF-8 bytes — served `text/css; charset=utf-8`, verified).
  `index.html` 1,225,131 → 1,011,123 bytes; 25,165 → 20,587 lines.
- **sw.js:** `SW_VERSION` `mc-push-v1` → `mc-push-v2`. NOTE: this SW has **no
  cache list** by design (online-first; fetch handler passes navigations to
  network, caches nothing) — there is no list to add `app.css` to; the version
  bump alone forces the SW update cycle. Adding a cache would be a behavior
  change, out of scope for a move-only commit.
- **Smoke harnesses extended** (`tools/smoke/boot-smoke.mjs`,
  `bg-framing-check.mjs`): both are hermetic and abort all non-fulfilled
  requests, which would have made every future run exercise an *unstyled*
  shell. Added a `route.fulfill` for `/static/css/app.css` serving the real
  file (per the smoke README's own maintenance rule for shell-critical assets).
- **Commit:** tip of `refactor/frontend` (this entry ships in the same commit;
  SHA in the orchestrator report — backfill literal SHA on next entry, same as
  Track A's Phase 0 precedent).
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5378 python server.py` in the
    worktree, then killed): `/static/css/app.css` → 200,
    `text/css; charset=utf-8`, Content-Length 214041, `Cache-Control:
    no-cache` + ETag (Flask default conditional revalidation — **no
    long-lived static caching**, no stale-CSS risk).
  - Headless Chromium against that server: link sheet attached with 1,394
    parsed rules, Inter font + warm-cream theme applied, **0 console errors,
    0 failed requests**; screenshot eyeballed fully styled (header, sidebar,
    walkthrough modal, mascot).
  - `node tools/smoke/bg-framing-check.mjs` — **FAILS AT BASE** (pre-existing,
    NOT this change): harness calls `setBgZoom`/`setBgPosX`/`setBgPosY`, which
    no longer exist anywhere in `index.html` (verified on pristine
    `local/opus-effort` files + pristine harness: identical
    `ReferenceError: setBgZoom is not defined`). The SPA's framing setters were
    renamed/removed after the harness was written. Needs its own fix-up pass;
    out of scope for a move-only commit.

### Landmines for module 2 (first JS extraction)

1. **Anti-FOUC ordering is load-bearing:** the first `<script>` after
   `<body>` (old line 4792, now ~4214) applies the saved tone/accent before
   first paint and relies on the stylesheet being render-blocking in `<head>`.
   When extracting JS, keep that bootstrap inline (or guarantee equivalent
   blocking order) — `type="module"` scripts are deferred and would flash
   dark→cream.
2. **The smoke harnesses abort everything they don't know.** Every new
   `/static/js/*.js` file must get a `route.fulfill` in BOTH
   `tools/smoke/boot-smoke.mjs` and `bg-framing-check.mjs` or the SPA simply
   won't boot under test.
3. **CI path filter gap:** `.github/workflows/frontend-smoke.yml` triggers
   only on `static/index.html` + `tools/smoke/**` — `static/css/**` (and the
   future `static/js/**`) won't trigger CI smoke. Workflow file has no Track
   owner in the tracks doc; flag to orchestrator rather than editing.
4. **`bg-framing-check.mjs` is stale at base** (see gates) — module 2+ can't
   use it as a green/red signal until someone repairs it against the current
   setter API.
5. **`git config core.autocrlf=true` + `* text=auto`:** index.html and app.css
   are CRLF in the working tree, LF in blobs. Do all surgical moves in binary
   mode (Python `rb`/`wb`), never PowerShell text pipes (re-encodes; the repo
   default writes UTF-16). index.html carries a UTF-8 BOM — preserve it.
6. **Packaging:** `build-macos.spec` bundles `('static', 'static')` wholesale,
   so new `static/css|js` subdirs ride along automatically — no spec edit
   needed per split.

## Phase 3 — module 2: extract Ask Claydo → `static/js/claydo.js` (2026-06-09)

- **Module-1 SHA backfill:** module 1 (css/app.css) = commit `e263805`.
- **Feature chosen: Ask Claydo (in-app guide assistant)** — won the
  inbound-reference sizing among the five candidate families (functional refs
  from outside the region / region size):
  - **ask_claydo: 0 refs** / 436 lines (the only textual hit is an HTML
    *comment* at line 579; the FAB has no inline onclick — the region wires
    its own listeners via `addEventListener`)
  - mobile_pairing: 2 refs / 386 lines (called from the settings template)
  - walkthrough: 4 refs / 699 lines (`startWalkthrough` ×4)
  - skills_panel: 4 refs / 1,300 lines
  - terminal: 8 refs / 281 lines — **disqualified on shared mutable globals**
    (`terminalDismissed`/`terminalEventSources` are read by outside code)
  - settings_drill: 16 refs / 662 lines
- **What moved:** old lines 15454–15888 (`// ── Ask Claydo` section header
  through `_claydoFormatText`'s closing brace) + the trailing blank 15889 →
  `static/js/claydo.js`, verbatim (proven by binary reassembly assertion:
  original index.html == new file with the `<script type="module">` tag
  removed and the region re-inserted). Loaded via
  `<script type="module" src="/static/js/claydo.js"></script>` inserted
  immediately after the main inline script's `</script>`, before `</body>`.
  Anti-FOUC bootstrap (line 214) untouched (landmine 1).
- **Numbers:** `claydo.js` = 18,455 bytes / 438 lines (435 moved + 3-line
  interop tail; CRLF, no BOM). `index.html` 1,011,123 → 992,812 bytes;
  20,587 → 20,152 lines.
- **Interop surface (window.* re-exposures): `window.submitClaydo` only** —
  referenced by the region's own generated `onclick`/`onkeydown` attributes,
  which resolve against the global object. Everything else in the region is
  self-contained; nothing in the remaining inline script calls into it.
  Module's outbound deps (`openModals`, `nextModalZ++`, `API_BASE`, `esc`,
  `_clampModalSize`, `centerModalElement`, `focusModal`, `restoreModal`,
  guarded `sidebarNav`/`openProjectModal`) all resolve at call time through
  the shared global scope — classic-script top-level `let`/`const`/function
  bindings are visible to (and reassignable by) module code, so `nextModalZ++`
  mutates the same binding the inline script uses.
- **Timing note:** `type="module"` is deferred — the region's three top-level
  IIFEs (localStorage key migration, FAB pulse, FAB drag wiring) now run after
  document parse instead of mid-parse. All three were already wrapped in
  setTimeout(200–300ms) "wait for DOM" guards and nothing outside the region
  consumes their effects, so order is preserved where it matters.
- **sw.js:** `SW_VERSION` `mc-push-v2` → `mc-push-v3` (still no cache list by
  design; version bump only, same as module 1).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/claydo.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs` (landmine 2).
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched): `/static/js/claydo.js`
    → 200, `text/javascript; charset=utf-8`, Content-Length 18455,
    `Cache-Control: no-cache` (no stale-JS risk).
  - Headless Chromium exercise against that server — **all PASS, 0 console
    errors, 0 uncaught page errors**: `window.submitClaydo` is a function;
    FAB drag moves + persists `claydo_fab_pos`; tap opens the modal with the
    greeting; `claydo_opened` persisted (pulse stop); empty-Send and
    whitespace-Enter guard paths no-op (exercises the onclick/onkeydown
    interop); minimize → restore preserves the modal. Screenshot eyeballed:
    fully styled modal + repositioned FAB.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4); it
    boots the page (grid renders with claydo.js fulfilled) and dies at the
    same later evaluate step as at base. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as module 1).

### Landmines for module 3

1. All six module-1 landmines still apply verbatim (anti-FOUC inline
   bootstrap; route.fulfill in BOTH harnesses per new js file; CI path-filter
   gap for `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary
   surgery; build-macos.spec bundles static/ wholesale).
2. **Deferred-module timing:** extracted regions run after document parse,
   not mid-parse. Safe for Claydo (self-contained IIFEs); for the next
   candidates check that (a) no remaining top-level inline code calls the
   region's functions at parse time, and (b) the region's own top-level
   side effects aren't consumed by earlier boot code. DOMContentLoaded
   handlers are safe — modules execute before that event fires.
3. **Module-scoped `let`/`const` are invisible to the page** — if an
   extracted region's mutable globals are referenced (worse: reassigned) by
   the remaining script, window-bridging them diverges the bindings. That's
   why `terminal` (its Set/maps are read from outside) should NOT move until
   its state moves to js/store.js or the readers move with it.
4. **`tools/smoke/` needs `npm install` in fresh worktrees** (node_modules
   is gitignored; playwright browser binaries come from the shared
   ms-playwright cache).
5. **Next sizing data point:** mobile_pairing (2 refs, called only from the
   settings drill-down template) is the natural module 3; walkthrough
   (4 refs, all late-bound: 2 onclick attrs, 1 palette action, 1 setTimeout)
   is the runner-up.

## Phase 3 — module 3: extract mobile pairing → `static/js/mobile-pairing.js` (2026-06-09)

- **Module-2 SHA backfill:** module 2 (js/claydo.js) = commit `1ff17c1`.
- **Inbound refs re-verified: exactly 2 functional refs** from outside the
  region, both inside `_renderSettings()` (runtime-only — fire when Settings
  opens, never at parse time):
  1. `${mobilePairingSettingsHTML()}` — old line 13511, the Connectivity
     detail-pane template.
  2. `try { refreshMobilePairingSection(); } catch (_) {}` — old line 13585,
     the post-render hydration block.
  (Third textual hit, old line 12944, is the cosmetic category subtitle
  `'Remote access, push & mobile pairing'` — not a code ref.) Whole-repo
  sweep: no other frontend file references any region identifier (server.py /
  control_plane hits are the backend endpoints; distiller/docs hits are vocab).
- **What moved:** old lines 14392–14776 (`// ── Mobile pairing` section header
  through `_mobilePairRenderQR`'s closing brace) + trailing blank 14777 →
  `static/js/mobile-pairing.js`, verbatim (proven by binary reassembly
  assertion: original index.html == new file with the `<script>` tag removed
  and the region re-inserted). Loaded via
  `<script type="module" src="/static/js/mobile-pairing.js"></script>`
  inserted immediately after the claydo.js module tag, before `</body>`.
  Anti-FOUC bootstrap untouched (landmine 1). Diff shape: 1 insertion,
  386 deletions.
- **Numbers:** `mobile-pairing.js` = 20,725 bytes / 399 lines (385 moved +
  14-line interop tail: 1 blank + 3 comment + 10 assignments; CRLF, no BOM).
  `index.html` 992,812 → 973,491 bytes; 20,152 → 19,767 lines.
- **Interop surface (window.* re-exposures), 10 functions:**
  `mobilePairingSettingsHTML`, `refreshMobilePairingSection` (the 2 inbound
  refs; the latter is also a generated-onclick Cancel target) +
  `_mobilePairGenerate`, `_mobilePairRevokeToken`, `_mobilePairCopyFreshUri`,
  `_mobilePairDismissFresh`, `_mobilePairEdit`, `_mobilePairCopyUri`,
  `_mobilePairDelete`, `_mobilePairSave` (region-generated `onclick="..."`
  attributes resolve against the global object at click time). Kept
  module-private (internal-only): `_mobilePairRenderAuto`,
  `_mobilePairRenderManual`, `_mobilePairRenderQRInto`, `_mobilePairFormHTML`,
  `_mobilePairRenderQR`.
- **State stays on window by construction:** the region's only top-level side
  effect is `window._mobilePairState = {...}`; both mutable globals
  (`_mobilePairState`, `_mobilePairFreshUri`) were already explicitly
  window-qualified at every read/write and are touched only by region code —
  landmine 3 (module-scoped bindings diverging) doesn't apply. Outbound deps
  (`API_BASE`, `esc`, `showToast`, `QRCode` CDN global, `confirm`) all resolve
  at call time. Deferred-module timing safe: every `openSettings()` caller is
  user-driven (sidebar nav / palette / provider-refresh) — no parse-time
  inbound calls.
- **sw.js:** `SW_VERSION` `mc-push-v3` → `mc-push-v4` (still no cache list by
  design; version bump only, same as modules 1–2).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/mobile-pairing.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs` (landmine 2).
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched):
    `/static/js/mobile-pairing.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 20725; `GET /api/mobile-pair/config` → `{"configured":false}`
    (the state under test).
  - Headless Chromium exercise against that server — **24/24 PASS, 0 console
    errors, 0 uncaught page errors**: all 10 window.* interop functions
    callable; Settings → Connectivity → Pair Mobile App drill renders the
    section (template + hydration interop); enrolled deployment renders the
    auto flow (`#mobile-pair-create`, paired-token list — read-only GETs;
    "Pair a phone" deliberately NOT clicked, it would mint a real CP token);
    `window._mobilePairState.configured === false` handled; Advanced manual
    form renders the three empty credential inputs with no Cancel button
    (unconfigured branch); empty-form "Verify & save" click exercises the
    generated-onclick interop end-to-end (validation message shown).
    Screenshot eyeballed: fully styled drilled panel.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4); page
    boots with mobile-pairing.js fulfilled, dies at the same later evaluate.
    No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–2).

### Landmines for module 4 (walkthrough is the named candidate)

1. All module-1/2/3 landmines still apply verbatim (anti-FOUC inline
   bootstrap; route.fulfill in BOTH harnesses per new js file; CI path-filter
   gap for `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary
   surgery; build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule).
2. **Walkthrough has a BOOT-PATH inbound call:** the first-run auto-start
   (old line ~18822, now shifted −386) runs
   `setTimeout(() => startWalkthrough(), 600)` from the post-fetchProjects
   init when `realProjectCount === 0 && !localStorage.walkthrough_done`.
   Modules execute before DOMContentLoaded and the call is further delayed
   600 ms, so ordering is safe — but `startWalkthrough` MUST be
   window-exposed or first-run onboarding silently dies (it's also invoked
   via 2 onclick attrs + 1 palette action).
3. **`startWalkthrough()` calls `showDesktop()`** — it minimizes EVERY open
   modal. Discovered live during module-3 gating: on a fresh profile +
   zero-project worktree the auto-tour fired mid-test and display:none'd the
   settings modal under test (`modal-window … minimized`). When gating
   module 4 (or any module) against a throwaway server, either seed
   `localStorage.walkthrough_done=1` via addInitScript or assert the tour
   itself.
4. **Fresh-worktree servers render zero grid tiles** (only the `_incognito`
   pseudo-project exists), so `#projects-col .card` never appears against a
   real throwaway server — wait on the app shell / boot functions instead.
   The grid-renders gate belongs to boot-smoke's fixtures.
5. **Duplicate-id quirk preserved, don't "fix" in a move:** the pairing
   region renders TWO `#mobile-pair-error` divs (auto section + manual
   form); `_mobilePairSave`'s `getElementById` resolves to the auto
   section's. Pre-existing behavior, byte-preserved by this move. Same
   class of trap likely exists in other regions — moves must not deduplicate
   ids or "repair" lookups.

## Phase 3 — module 4: extract walkthrough / tour → `static/js/walkthrough.js` (2026-06-09)

- **Module-3 SHA backfill:** module 3 (js/mobile-pairing.js) = commit `1229ce8`.
- **Inbound refs re-verified: exactly 4 functional refs** to `startWalkthrough`
  from outside the region (matches the module-2 sizing):
  1. Old line 490 — header `?` button, static `onclick="startWalkthrough()"`.
  2. Old line 2847 — command-palette action `'Take Tour'`:
     `action: () => { toggleCommandPalette(); startWalkthrough(); }`.
  3. Old line 13569 — Settings template button, generated
     `onclick="closeModalById('__settings');startWalkthrough()"`.
  4. Old line 18823 — **boot path** (landmine 2): first-run auto-start
     `setTimeout(() => startWalkthrough(), 600)` inside
     `fetchProjects().then(async () => {...})` when
     `realProjectCount === 0 && !localStorage.walkthrough_done`. Network
     callback + 600 ms ⇒ runs long after module evaluation; safe.
  Whole-repo sweep: no other file references any region identifier (only
  CHANGELOG/docs prose).
- **Region boundary — NOT the full 699-line header-to-header span.** The
  module-2 sizing (699 lines) measured section header → next section header
  (old 17962 → 18661), but old lines 18579–18659 are **boot code, not
  walkthrough**: `startRefresh()` (called at parse time from the inline
  script, old line 18826 — moving it into a deferred module would throw
  ReferenceError and kill boot), the filter-dropdown binding,
  `fetchDomains()`, the grid-layout fetch, density/feed/view-mode restores,
  `applyAdvancedFlags()`, and the eager service-worker registration. Those
  82 lines stay inline. What moved is the walkthrough feature proper.
- **What moved:** old lines 17962–18577 (`// ── Walkthrough / Tour` section
  header through the Escape-keydown listener's closing `});`) + trailing
  blank 18578 → `static/js/walkthrough.js`, **byte-verbatim** (proven by
  binary reassembly assertion: original index.html == new file with the
  `<script>` tag removed and the region re-inserted; the interop tail is
  append-only). Loaded via
  `<script type="module" src="/static/js/walkthrough.js"></script>` inserted
  after the mobile-pairing.js module tag, before `</body>`. Anti-FOUC
  bootstrap untouched (landmine 1). Diff shape: 1 insertion, 617 deletions.
- **Numbers:** `walkthrough.js` = 32,207 bytes / 637 lines (617 moved +
  20-line interop tail; CRLF, no BOM). `index.html` 973,491 → 942,867 bytes;
  19,767 → 19,151 lines.
- **Interop surface (window.* re-exposures), 5 functions + 1 accessor:**
  - `startWalkthrough` — the 4 inbound refs above.
  - `wtNext`, `wtBack`, `wtSkip`, `wtEnd` — the wt-card's region-generated
    `onclick` attributes resolve against the global object at click time.
  - **`wtDontShow` is bridged as a window ACCESSOR, not an assignment:**
    `Object.defineProperty(window, 'wtDontShow', { get/set → module binding })`.
    The "Don't show this again" checkbox writes `wtDontShow=this.checked`
    from a generated `onchange` attribute; inline handlers can't see
    module-scoped `let` bindings, and a plain `window.wtDontShow = wtDontShow`
    tail would copy the value once and diverge. The accessor routes
    window-property reads/writes into the module binding — one source of
    truth, moved code stays byte-verbatim. **Pattern note for future
    modules:** this is the move-only answer for a mutable primitive that
    generated inline handlers assign to.
  Kept module-private: `wtActive`, `wtStep`, `WT_STEPS`, `wtDemoTileHTML`,
  `wtDemoModalHTML`, `wtDemoMenuHTML`, `wtShow`, `wtPositionCard` (no
  outside refs).
- **Outbound deps** all resolve at call time through the shared global scope:
  function declarations (`showDesktop`, `esc`, `refreshSilent`,
  `setAdvancedFlag`, `renderCommandResults`, `isIncognitoProject` — global
  object props) and classic-script top-level bindings (`API_BASE`,
  `ADV_FEATURES`, `advancedFlags`, `cmdPaletteOpen` — global declarative
  record, readable AND reassignable from module code per the module-2
  precedent; the cmd-palette step's onEnter/onLeave reassign
  `cmdPaletteOpen` and this works unchanged).
- **Deferred-module timing safe:** the only top-level side effects are the
  three `let` declarations + two listener registrations (`resize`,
  Escape `keydown`). Listener-registration order is not observable here —
  verified zero `stopImmediatePropagation` uses repo-wide; same-target
  listener order has no other observable effect for these events. Strict-mode
  promotion (classic → module) audited: every assignment target is a
  declared binding or a resolvable global; no top-level `this`, no
  block-scoped function declarations.
- **sw.js:** `SW_VERSION` `mc-push-v4` → `mc-push-v5` (still no cache list by
  design; version bump only, same as modules 1–3).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/walkthrough.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs`
  and `bg-framing-check.mjs` (landmine 2).
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios (fixtures
    have 2 real projects ⇒ no auto-tour interference, landmine 3 N/A there).
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched):
    `/static/js/walkthrough.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 32207, `Cache-Control: no-cache`.
  - Headless Chromium, **gate (a) seeded-done state** (landmine 3):
    `localStorage.walkthrough_done=1` via addInitScript → app shell renders,
    **NO `#wt-overlay` appears** (3 s settle > the 600 ms timer), all 5
    window.* functions exposed, accessor descriptor present on `window`,
    **0 console errors, 0 uncaught page errors**.
  - Headless Chromium, **gate (b) fresh state — the critical behavior**:
    fresh storage + zero-project worktree server → **first-run auto-tour
    fires from the extracted module**: `#wt-overlay` renders step 1
    ("Welcome to Clayrune", progress "1 / 16"); clicked the generated
    "Start Tour" button (window.wtNext onclick interop) → advanced to step 2
    ("Choose your level", "2 / 16", ADV_FEATURES checkbox list rendered —
    proves outbound classic-script deps resolve from module code); checked +
    unchecked "Don't show this again" → `window.wtDontShow` flipped
    true/false through the accessor into the module binding (the onchange
    interop end-to-end); `walkthrough_done` stayed unset mid-tour.
    **0 console errors, 0 uncaught page errors.** Screenshots eyeballed:
    styled wt-card over dimmed shell, both steps.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4);
    page boots with walkthrough.js fulfilled, dies at the same later
    evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–3).

### Landmines for module 5

1. All module-1/2/3 landmines still apply verbatim (anti-FOUC inline
   bootstrap; route.fulfill in BOTH harnesses per new js file; CI path-filter
   gap for `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary
   surgery; build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; don't
   "repair" pre-existing quirks in a move).
2. **Don't trust the sizing table's line counts as region boundaries.** The
   walkthrough's "699 lines" included 82 lines of unrelated boot code
   (incl. `startRefresh`, **called at parse time** — moving it would have
   killed boot). Re-derive the real feature boundary from the code, not the
   header-to-header span; check every function in the span for parse-time
   callers before moving it.
3. **Generated inline handlers that ASSIGN a region variable** (e.g.
   `onchange="wtDontShow=this.checked"`) need the
   `Object.defineProperty(window, …, { get/set })` accessor bridge — a plain
   tail assignment copies the value once and silently diverges. Grep the
   region's generated HTML for `="<ident>=` patterns when auditing.
4. **Remaining candidates (sizes from the module-2 table, see #2 caveat):**
   - `settings_drill` — 16 refs / 662 lines. Many refs but they're
     runtime-only (`_renderSettings` etc.); mobile-pairing already proved
     the settings-template interop pattern.
   - `skills_panel` — 4 refs / ~1,300 lines, the biggest single win.
   - `terminal` — **still blocked on shared mutable globals** per module 2
     (`terminalDismissed`/`terminalEventSources` are read by outside code);
     needs a js/store.js or the readers to move with it. Note the accessor
     pattern (#3) now gives a viable bridge for *primitives*, but
     `terminalEventSources` is a Map mutated from both sides — object
     identity survives a plain window bridge, so terminal may be unblockable
     with one `window.x = x` per shared object IF all writers reassign
     through window; audit before attempting.
5. **Throwaway-server gating recipe that worked here** (reuse it): start
   `MC_PORT=5379 python server.py` from the worktree; gate (a) seed
   `walkthrough_done=1` + wait on `.header` + a boot function (NOT grid
   cards, landmine 4); gate (b) fresh profile asserts the feature itself.
   ESM scratch runners can't use NODE_PATH — import playwright via relative
   path into `tools/smoke/node_modules/playwright/index.mjs`.

## Phase 3 — module 5: extract skills panel → `static/js/skills-panel.js` (2026-06-09)

- **Module-4 SHA backfill:** module 4 (js/walkthrough.js) = commit `75c597d`.
- **Inbound refs re-verified: exactly 2 functional refs** from outside the
  region (the module-2 sizing said 4 — the other 2 textual hits are the
  sidebar/mobile-drawer items at old lines 408/648, whose static onclicks call
  `sidebarNav('skills')`/`mobileDrawerNav('skills')`, NOT region identifiers;
  `mobileDrawerNav` delegates to `sidebarNav`, so all nav funnels into ref 1):
  1. Old line 2652 — `openAllSkills()` inside `sidebarNav(target)`
     (runtime-only, user-driven).
  2. Old line 1848 — project-modal three-dot menu, generated
     `onclick="openAllSkillsForProject('${esc(p.id)}')"` (template literal
     inside the menu builder — runtime, resolves at click time).
  Whole-repo sweep (`*.py`, `*.js`, `*.html`, `*.md`, smoke fixtures): no
  other file references any of the region's 55 identifiers (CHANGELOG hits
  are prose).
- **Region boundary re-derived (landmine 2):** old lines 15616–16914
  (`// ── Skills (global + per-project …)` header through `_sibInstallHere`'s
  closing brace) + trailing blank 16915 = **exactly the 1,300-line
  header-to-header span this time** — unlike module 4 there is NO foreign code
  inside it. The span contains seven sub-sections that are all one feature
  (skills manager, Learning queue/Distiller `_proposed` UI, import shell,
  paste/folder/Git/browse import flows), bidirectionally coupled
  (`openAllSkills` → `loadDistillerQueue`; `promoteProposed` →
  `loadAllSkills`) with zero outside refs into the sub-sections.
  **Parse-time audit:** brace-depth scan + manual read prove the region's top
  level is pure declarations — 8 single-line `let` + 47 `function`, no IIFEs,
  no top-level calls (the module-4 `startRefresh` trap has no analogue here).
- **What moved:** old lines 15616–16915 → `static/js/skills-panel.js`,
  **byte-verbatim** (binary reassembly assertion: original index.html ==
  new file with the `<script>` tag removed and the region re-inserted; the
  interop tail is append-only). Loaded via
  `<script type="module" src="/static/js/skills-panel.js"></script>` after
  the walkthrough.js module tag, before `</body>`. Anti-FOUC bootstrap
  untouched (landmine 1). Diff shape: 1 insertion, 1,300 deletions.
- **Numbers:** `skills-panel.js` = 75,399 bytes / 1,358 lines (1,300 moved +
  58-line interop tail; CRLF, no BOM). `index.html` 942,867 → 872,756 bytes;
  19,151 → 17,852 lines. Biggest single extraction so far.
- **Interop surface, 30 window function re-exposures + 3 accessor bridges:**
  - Inbound (2): `openAllSkills` (sidebarNav), `openAllSkillsForProject`
    (project-menu generated onclick).
  - Region-generated `on*=` handler targets (28): `loadAllSkills`,
    `renderAllSkills`, `loadDistillerQueue`, `promoteProposedByIdx`,
    `rejectProposedByIdx`, `toggleProposedReadByIdx`, `openSkillEditor`,
    `_seToggleProjectPicker`, `_seLintDescription`, `saveSkillFromEditor`,
    `archiveSkillAction`, `deleteSkillAction`, `restoreSkillAction`,
    `_toggleSkillsImportMenu`, `openSkillImportPaste`, `openSkillImportFolder`,
    `openSkillImportGit`, `openSkillImportBrowse`, `_doSkillImportPaste`,
    `_doSkillImportFolder`, `_doSkillImportGit`, `_doSkillImportGitInstallOne`,
    `_doSkillImportGitCancel`, `_doSkillImportFullPlugin`, `_siToggleProject`,
    `_doSkillImportBrowseSearch`, `_sibReadBody`, `_sibInstallHere`.
  - **Accessor bridges (landmine 3), 3:** `_distillerQueueOpen` and
    `_distillerDiagOpen` are whole-var ASSIGNED from generated onclicks
    (`_distillerQueueOpen=!_distillerQueueOpen;…`) — get/set required;
    `_allSkillsFilter` is resolved + property-written from four generated
    oninput/onchange handlers (`_allSkillsFilter.search=this.value;…`) —
    getter routes the handler's property writes into the live module object
    (it is never wholesale-reassigned, verified; set included for symmetry).
  - Kept module-private (17 functions + 5 vars, no outside/handler refs):
    `loadSkillUsage`, `_distillerKindBadge`, `renderDistillerQueue`,
    `toggleProposedRead`, `_distillerPost`, `promoteProposed`,
    `rejectProposed`, `_renderSkillRow`, `lintSkillDescription`,
    `_renderPluginBanner`, `_renderFullPluginButton`, `_hideSkillsImportMenu`,
    `_defaultImportContext`, `_importContextHTML`, `_siReadContext`,
    `_siModalShell`, `_siStatus`; `_allSkillsCache`, `_skillUsageCache`,
    `_distillerQueueItems`, `win_gitStaging`, `win_importPluginSource`.
    (`window._sibTimer` in the browse debounce is already explicitly
    window-qualified — needs no bridge.)
- **Outbound deps** all resolve at call time through the shared global scope
  (`openModals`, `nextModalZ++` reassignment, `API_BASE`, `esc`, `allProjects`,
  `isIncognitoProject`, `showToast`, `closeModalById`, `restoreModal`,
  `focusModal`, `_clampModalSize`, `centerModalElement`, `confirm`) — same
  classic-script global-declarative-record mechanics as modules 2–4.
  **Strict-mode promotion audited:** zero `this` in module code (all hits are
  HTML handler strings/comments), no `with`/`arguments.callee`/octals, every
  assignment targets a declared binding or explicit `window.` property;
  `node --check` parses the file in module goal.
- **Deferred-module timing safe:** region top level = declarations only (no
  side effects at all — even less surface than claydo's IIFEs).
- **sw.js:** `SW_VERSION` `mc-push-v5` → `mc-push-v6` (still no cache list by
  design; version bump only, same as modules 1–4).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/skills-panel.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs`
  and `bg-framing-check.mjs` (landmine 2 of module 1).
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched):
    `/static/js/skills-panel.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 75399, `Cache-Control: no-cache` + ETag (no stale-JS
    risk); `GET /api/skills` → the machine's real 9 global skills.
  - Headless Chromium against that server (seeded `walkthrough_done=1`,
    landmine 3 of module 4; **read-only** — this machine's skills are live,
    no create/edit/archive/delete fired) — **13/13 PASS, 0 console errors,
    0 uncaught page errors**: all 30 window.* interop functions callable;
    all 3 accessor descriptors present (get+set) and `_distillerQueueOpen`
    round-trips window↔module; Skills modal opens via the real
    `sidebarNav('skills')` path; list renders 9 rows against the real
    `GET /api/skills`; search-box generated oninput writes
    `_allSkillsFilter.search` through the bridge end-to-end (list narrows
    9 → 1 on "distill", then clears); detail/read view (`openSkillEditor`
    on `mc-distill`) fetches + populates the 12,712-char body via
    `include_body=true` and closes WITHOUT saving; Import ▾ menu opens via
    the generated-onclick interop. Screenshots eyeballed: fully styled list
    (badges, actions, paths), populated detail editor, import dropdown.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of
    module 1); page boots with skills-panel.js fulfilled, dies at the same
    later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–4).

### Landmines for module 6 (settings_drill is the named candidate)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; re-derive
   boundaries, audit parse-time callers; accessor-bridge any
   handler-assigned variables; don't "repair" pre-existing quirks).
2. **settings_drill (16 refs / 662 lines per the module-2 table — RE-VERIFY
   both):** the refs are runtime-only per the module-2 audit, and
   mobile-pairing (module 3) already proved the settings-template interop
   pattern (`mobilePairingSettingsHTML` + hydration call from
   `_renderSettings`). Watch specifically for: (a) `openSettings()` callers
   in the palette/sidebar/provider-refresh paths; (b) the drill-down's
   depth-based hardware-back integration (History API sentinel, see
   arch_settings_modal memory) — if back-stack state lives as region `let`s
   read by the popstate handler OUTSIDE the region, that's the
   terminal-class shared-mutable-global trap; audit before moving.
3. **Object-valued region state read from handlers is bridgeable with a
   getter-only accessor** when the binding is never wholesale-reassigned
   (proven here with `_allSkillsFilter` — four generated handlers
   property-write through it). Verify "never reassigned" with a
   whole-region `(?<![\w$.])name\s*=(?![=.])` scan, not by eyeball.
4. **The sizing table CAN be right:** skills_panel's 1,300-line
   header-to-header span had no foreign code (unlike walkthrough's 699).
   The lesson stays "re-derive, then trust the re-derivation" — the
   brace-depth top-level scan (declarations only?) is a cheap, reusable
   parse-time-safety proof.
5. **Terminal note (for whoever attempts it):** nothing new learned this
   module — `terminalDismissed`/`terminalEventSources` writer audit still
   pending; the Map-identity bridge idea from module-4's landmine 4 remains
   the candidate approach.

## Phase 3 — module 6: extract settings drill-down → `static/js/settings-drill.js` (2026-06-09)

- **Module-5 SHA backfill:** module 5 (js/skills-panel.js) = commit `daceab0`.
- **Popstate/back-stack hazard audit (the module-specific terminal-class
  risk) — CLEAR, move is safe:** the entire hardware-back machinery lives
  OUTSIDE the region and stays inline *together*: sentinel `let`s
  (`_mcSettingsHistoryActive` L1023, `_mcSettingsNavDepth` L1024), the push
  helpers (`mcPushSettingsHistory`/`mcPushSettingsNav` L1026–1033),
  `_mcUnwindHistory`, the popstate handler (L1084–1117), and
  `closeModalById`'s sentinel unwind (L2319–2327). The trap direction
  (region-declared `let`s read by the outside popstate handler) **does not
  exist**: the region's only `let`s (`_settingsActiveCat`/`_settingsActiveSub`/
  `_settingsView`) have ZERO outside references (formal
  `(?<![\w$.])name\s*=(?![=.])` scan + full-identifier scan, not eyeball).
  The two real couplings are both safe: (a) region code REASSIGNS the
  outside `let` `_mcSettingsNavDepth` (×3: `settingsBack`, `filterSettings`,
  `openSettings`) — module→classic-global-declarative-record writes, the
  proven module-2/4 direction (`nextModalZ++`, `cmdPaletteOpen`); (b) the
  popstate handler calls the region's `_settingsUpUI()` — bare-identifier
  lookup reaches the window re-exposure at event time (runtime-only), same
  mechanics as every generated-onclick interop since module 2.
- **Inbound refs re-verified: 16 textual = 15 functional + 1 comment**
  (module-2 table said 16 — exact): `_renderSettings` ×9 (setTone/setAccent/
  setDensity/setVoice ×4 + bg setters ×4 at L4584–4926, `setBriefRepliesMode`
  L14859 — all guarded by `getElementById('settings-body')`), `openSettings`
  ×3 (`sidebarNav('settings')` L2647, palette action closure L2843,
  `settingsProviderRefresh` setTimeout fallback L11833),
  `_applySettingsSectionVisibility` ×2 (refreshLocalAccessSection L13614 +
  refreshRemoteAccessSection L13753 re-asserts after outerHTML hydration),
  `_settingsUpUI` ×1 (popstate L1097), + 1 comment (L12829). All late-bound
  runtime; zero parse-time callers. Whole-repo sweep: `demo-export/
  demo-app.js` has its own self-contained COPIES (not refs);
  `bg-framing-check.mjs` calls openSettings/drillSettings/filterSettings via
  page.evaluate (typeof-guarded; all three window-exposed).
- **Region boundary re-derived:** old lines 12930–13590 (`// ── Settings
  categories + drill-down` header through `_renderSettings`'s closing brace)
  + trailing blank 13591 = **exactly the 662-line header-to-header span**
  (like module 5, no foreign code). Brace-depth top-level scan: 3 `let` +
  3 `const` + 12 `function`, no IIFEs/top-level calls; the only top-level
  evaluation is `SETTINGS_CATS`'s initializer calling the region's own pure
  `_settingsIcon` string builder. (Scanner ends confused by triple-nested
  templates inside `_renderSettings`; everything above L13185 tracked in
  clean state, and `node --input-type=module --check` parses the final file.)
- **What moved:** old lines 12930–13591 → `static/js/settings-drill.js`,
  **byte-verbatim** (binary reassembly assertion: original index.html == new
  file with the `<script>` tag removed and the region re-inserted; interop
  tail append-only). Loaded via
  `<script type="module" src="/static/js/settings-drill.js"></script>` after
  the skills-panel.js tag, before `</body>`. Anti-FOUC bootstrap untouched.
  Diff shape: 1 insertion, 662 deletions.
- **Numbers:** `settings-drill.js` = 37,615 bytes / 674 lines (662 moved +
  12-line interop tail; CRLF, no BOM). `index.html` 872,756 → 836,560 bytes;
  17,852 → 17,191 lines (BOM preserved).
- **Interop surface: 8 window function re-exposures, 0 accessor bridges**
  (the `="<ident>=` generated-handler-assignment scan is EMPTY — first
  module since the accessor pattern landed that needs none):
  - Outside callers (4): `openSettings`, `_renderSettings`,
    `_applySettingsSectionVisibility`, `_settingsUpUI` (see refs above).
  - Region-generated `on*=` targets (4): `drillSettings` (master rows +
    profile card), `drillSettingsSub` (L2 rows), `settingsBack` (header
    arrow), `filterSettings` (search oninput).
  - Module-private (3 `let` + 3 `const` + 4 functions): `_settingsActiveCat`,
    `_settingsActiveSub`, `_settingsView`; `_settingsIcon`, `SETTINGS_CATS`,
    `_settingsRow`; `_settingsCatLabel`, `_settingsSectionEls`,
    `_renderSettingsSubList`, `_applySettingsView`.
- **Outbound deps** resolve at call time through the shared global scope:
  inline declarations (`esc`, `openModals`, `nextModalZ++`, `restoreModal`,
  `focusModal`, `_clampModalSize`, `_positionSettingsModal` (L2042),
  `mcPushSettingsHistory`, `mcPushSettingsNav`, `_mcUnwindHistory`,
  `_mcSettingsNavDepth` reassignment, `_ensureAgentProviders`,
  `fetchRemoteStatus`, `refreshRemoteAccessSection`,
  `refreshLocalAccessSection`, `refreshUpdateStatus`, `refreshPushSection`,
  `_renderProviderSettings`, `localAccessSettingsHTML`,
  `remoteAccessSettingsHTML`, `pushNotificationsSettingsHTML`, `API_BASE`,
  appearance globals, `ADV_FEATURES`, `advancedFlags`) + **cross-module
  window props from mobile-pairing.js** (`mobilePairingSettingsHTML`,
  `refreshMobilePairingSection` — module-3 interop, document-order
  evaluation + runtime calls make ordering moot). Strict-mode promotion
  audited: zero module-code `this` (all hits are handler strings/comments);
  every assignment targets a declared binding.
- **sw.js:** `SW_VERSION` `mc-push-v6` → `mc-push-v7` (no cache list by
  design; version bump only, same as modules 1–5).
- **Smoke harnesses:** added `route.fulfill` for
  `/static/js/settings-drill.js` in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched):
    `/static/js/settings-drill.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 37615, `Cache-Control: no-cache`.
  - Headless Chromium, **desktop pass (1280×800), 28/28 assertions, 0
    console / 0 page errors**: 8/8 interop functions; open via the REAL
    `sidebarNav('settings')` path; L1 list (title/back-arrow state); 6 panes
    stay in DOM while hidden (hydration rule); Agent → 'subs' (4 sections) →
    Model 'detail' (only section idx 1 visible, other 5 panes hidden);
    header back arrow walks L3→L2→L1; **adaptive skip** (Providers
    single-section → straight to detail, back → straight to list); live
    search ('condense' → memory pane only; no-match shows placeholder;
    clear → list); **Connectivity → Pair Mobile App renders + hydrates the
    module-3 mobile-pairing section** (enrolled auto-flow: pairing host,
    "Pair a phone", token list — read-only); Escape closes the modal with
    `_mcSettingsNavDepth === 0`.
  - Headless Chromium, **mobile pass (412×915) — the depth-based
    hardware-back regression risk, asserted not just rendered — 16/16
    assertions, 0 console / 0 page errors**: open pushes the `settings`
    sentinel (history.state verified); drill L2/L3 increments depth 0→1→2;
    `history.back()` ×3 walks detail→subs→list→**close** with
    `_mcSettingsNavDepth` asserted at every step and
    `_mcSettingsHistoryActive` cleared at close (popstate → window._settingsUpUI
    bridge end-to-end); search pushes one nav level and hardware back exits
    it; **UI back arrow's synthetic unwind keeps the stack in sync** (after
    arrow-back from L2, `history.state.mc === 'settings'` and a SINGLE
    hardware back closes the modal — the desync trap).
  - Screenshots eyeballed (desktop L1/L2/L3/search/pair + mobile L3): fully
    styled. NOTE: `.settings-detail-pane` has a 200 ms `settingsPaneIn`
    opacity animation — screenshots taken immediately after a drill look
    blank/faded; settle ≥400 ms before shooting (cost one false alarm).
  - `node tools/smoke/bg-framing-check.mjs` — **identical pre-existing base
    error** (`setBgZoom is not defined`); boots with settings-drill.js
    fulfilled, dies at the same later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–5).

### Landmines for module 7 (agent-panel / projects-grid / terminal remain)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; re-derive
   boundaries + brace-depth scan; accessor-bridge handler-assigned vars
   (verify with the formal regex scan); don't "repair" pre-existing quirks).
2. **Screenshot gates vs. CSS entry animations:** `settingsPaneIn` (and any
   sibling animation) makes immediately-shot panes look empty. Assert
   class/DOM state for behavior; settle past the animation for "look"
   evidence.
3. **Cross-module window-prop deps are now normal:** settings-drill calls
   mobile-pairing's window exports. Document-order module evaluation +
   runtime-only call sites make this safe; keep new module tags AFTER the
   modules they depend on as a courtesy, but don't rely on eval order for
   anything parse-time.
4. **Remaining core candidates:** `terminal` (8 refs / ~281 lines) — STILL
   blocked on shared mutable globals (`terminalDismissed` Set,
   `terminalEventSources` Map read by outside code); writer audit pending;
   object-identity window bridge or js/store.js are the candidate designs —
   this is the orchestrator's store.js design checkpoint. Agent-panel and
   projects-grid have no sizing rows in the module-2 table — derive fresh
   (both are deeply entangled with the conversation model / grid renderer;
   expect heavy shared state — audit before promising a move).
5. **The settings family is now fully split across modules 3+6** (pairing
   templates in mobile-pairing.js, drill machinery in settings-drill.js,
   section templates `localAccessSettingsHTML`/`remoteAccessSettingsHTML`/
   `pushNotificationsSettingsHTML` + their refresh/hydration functions still
   INLINE at L12933+ post-move). A future "settings-sections" module could
   take those three template+hydration families; their only couplings are
   `_renderSettings` (window prop now) and `_applySettingsSectionVisibility`
   (window prop now) — both already bridged.

## Phase 3 — module 7: extract settings sections → `static/js/settings-sections.js` (2026-06-09)

- **Module-6 SHA backfill:** module 6 (js/settings-drill.js) = commit `eb19921`.
- **Region re-derived — ONE contiguous segment, not three:** old lines
  12930–14159 (`// ── Local network access (LAN passcode)` header through
  `disconnectRemoteAccess`'s closing brace + trailing blank 14159; next line
  14160 is `async function saveSetting`, which stays inline — it's the
  generic helper used by templates across the whole app, not part of these
  families). The span contains exactly the three settings-section families
  and nothing else: Local network access (12930–13033), Remote Access
  templates (13035–13279), Web push + PWA install (13281–13728), Devices
  list (13730–13791), CF sign-in sessions (13793–14051), and the remote
  action handlers (14053–14158) — the last three are the remote family's
  closure (called only from its generated HTML / refresh chain). 35 function
  declarations total.
- **Inbound refs re-verified (formal whole-repo scan of all 35 function
  names + 5 state names): only 8 cross-file hits, ALL in
  `static/js/settings-drill.js`, all runtime-only** — `${localAccessSettingsHTML()}`
  /`${remoteAccessSettingsHTML()}`/`${pushNotificationsSettingsHTML()}`
  (L576/578/580, the Connectivity pane template), `fetchRemoteStatus` +
  `refreshRemoteAccessSection` (L264), `refreshLocalAccessSection` (L266),
  `refreshPushSection` (L655) + 1 comment (L269). Plus ONE remaining-inline
  ref: `refreshPushSection()` at L16066 inside the eager SW registration's
  statechange listener (runtime callback, guarded by `#sw-status-line`
  existence + try/catch). Zero parse-time callers anywhere. demo-export /
  smoke fixtures / server.py: zero hits.
- **Parse-time audit (brace-depth scan):** region top level = 33 function
  declarations + 4 `window.*` state inits (`_localAuthState`, `_remoteState`,
  `_pushState`, `_deferredInstallPrompt`) + 2 `window.addEventListener`
  registrations (`beforeinstallprompt`/`appinstalled`) — no IIFEs, no bare
  top-level calls. (Scanner derails at L14104's `replace(/'/g, "\\'")` regex
  literal — same known limitation as module 6; the three functions below it
  verified plain declarations by read. `node --input-type=module --check`
  parses the final file.)
- **`window._pushState` ordering INVERTS — analyzed equivalent:** the inline
  boot block's `window._pushState = window._pushState || {}` (L16051, parse
  time) now runs BEFORE the module's defaults-object init (which `||`-no-ops
  against the boot `{}`), so the full defaults (`supported:null, subs:[],
  publicKey:'', …`) are never applied. Verified every read site is
  falsy-tolerant or guarded: `thisDeviceEndpoint`/`publicKey` only in
  boolean/`!x` contexts or set-before-read, `swState`/`swError` read with
  `|| 'unknown'`/`|| ''` fallbacks, `subs` written before any read,
  `supported` never read at all. Undefined ≡ default at every site. Live
  gate confirms (`swState='activated'`, hydration intact).
- **Deferred-listener timing:** `beforeinstallprompt`/`appinstalled` now
  register at module eval (pre-DOMContentLoaded) instead of mid-parse.
  Chrome fires bip only after manifest+SW+engagement heuristics (seconds
  after load) — no realistic miss window; a miss would only delay the
  Install row's "available" state until the next fire.
- **What moved:** old lines 12930–14159 → `static/js/settings-sections.js`,
  **byte-verbatim** (binary reassembly assertion: original index.html ==
  new file with the `<script>` tag removed and the region re-inserted;
  interop tail append-only). Loaded via
  `<script type="module" src="/static/js/settings-sections.js"></script>`
  after the settings-drill.js tag, before `</body>`. Anti-FOUC bootstrap
  untouched. Diff shape: 1 insertion, 1,230 deletions.
- **Numbers:** `settings-sections.js` = 61,585 bytes / 1,262 lines (1,230
  moved + 32-line interop tail; CRLF, no BOM). `index.html` 836,560 →
  776,962 bytes; 17,191 → 15,962 lines (BOM preserved).
- **Interop surface: 24 window function re-exposures, 0 accessor bridges**
  (the `="<ident>=` generated-handler-assignment scan is EMPTY; the formal
  assignment-target scan found zero writes to non-region bindings — no
  `nextModalZ++`-class couplings at all in this region):
  - Cross-module/inline callers (7): `localAccessSettingsHTML`,
    `remoteAccessSettingsHTML`, `pushNotificationsSettingsHTML`,
    `refreshLocalAccessSection`, `refreshRemoteAccessSection`,
    `refreshPushSection`, `fetchRemoteStatus`.
  - Region-generated `on*=` targets (17): `showLocalAuthForm`,
    `submitLocalAuth`, `enableRemoteAccess`, `copyToClipboardSafe`,
    `disableRemoteAccess`, `resumeRemoteAccess`, `disconnectRemoteAccess`,
    `testPushNotification`, `unsubscribeThisDevice`, `enablePushOnThisDevice`,
    `installPwaApp`, `removePushSubscription`, `updatePushSubscription`,
    `renameRemoteSession`, `signOutSession`, `enforceSessionCleanup`,
    `signOutAllSessions`. (NOTE: `copyToClipboardSafe` is a generic-looking
    utility but its ONLY callers are this region's generated onclicks —
    verified; if future inline code wants it, it's on window.)
  - Module-private (11): `fetchLocalAuthStatus`, `_ra_pill`,
    `_ra_section_open`, `_isPwaInstalled`, `_pushSupported`, `_b64urlToUint8`,
    `refreshPushDeviceList`, `refreshRemoteDevices`, `refreshRemoteSessions`,
    `refreshEnforcerState`, `_showEnrollmentFallback`.
  - State needs no bridges: all 5 mutable globals (`_localAuthState`,
    `_remoteState`, `_remoteAutoPoll`, `_pushState`, `_deferredInstallPrompt`)
    are explicitly `window.`-qualified at every read/write on BOTH sides
    (region + inline boot/FCM code) — object identity shared via the window
    property, same as mobile-pairing's `_mobilePairState`.
- **Outbound deps** (all resolve at call time through the shared global
  scope): inline globals `API_BASE`, `esc`, `showToast`, `timeAgoShort`
  (declared twice inline at L9773/L10946 — second wins; pre-existing quirk,
  untouched) + cross-module window prop `_applySettingsSectionVisibility`
  (settings-drill.js, module 6) + browser globals (`confirm`,
  `window.prompt`, `Notification`, `navigator.serviceWorker`, `atob`).
  Strict-mode promotion audited: all 23 `this` hits are template-string
  HTML/prose (incl. `onchange="...this.checked"` handler strings — sloppy
  inline-handler scope, not module code); no `with`/`arguments.callee`/octals.
- **sw.js:** `SW_VERSION` `mc-push-v7` → `mc-push-v8` (no cache list by
  design; version bump only, same as modules 1–6).
- **Smoke harnesses:** added `route.fulfill` for
  `/static/js/settings-sections.js` in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` in the
    worktree, then killed; live :5199 never touched):
    `/static/js/settings-sections.js` → 200, `text/javascript;
    charset=utf-8`, Content-Length 61585, `Cache-Control: no-cache`.
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **strictly read-only** — the deployment is LIVE-enrolled
    (ronl.clayrune.io, online): no enable/disable/pause/resume/disconnect/
    sign-out/revoke/subscribe clicked) — **24/24 PASS, 0 console errors,
    0 uncaught page errors**: all 24 window.* interop functions callable;
    `window._pushState.swState='activated'` (boot-block ordering intact);
    Settings via real `sidebarNav('settings')`; Connectivity sub-list shows
    the 4 sections; **local access** renders + hydrates the gate status from
    `/api/local-auth/status` (configured=false → "Set a passcode" pill+CTA);
    **remote access** renders the enrolled state-3 template read-only
    (hostname, Online pill, Copy link/Pause buttons present, bandwidth bar,
    devices list hydrated async to "2 of 2 on free plan" incl. This-device
    badge, sessions list "0 active") ; **push** renders + hydrates sub state
    (SW diag row ACTIVATED, headless permission-denied banner branch,
    "No devices subscribed yet.", `_pushState.subs === []`);
    **mobile-pairing (module 3) end-to-end intact** (enrolled auto flow:
    pairing host pill, label input, paired-phones list); **drill (module 6)
    intact** (back walks detail→subs→master, live search 'passcode'
    surfaces the moved local-access section, Escape closes with
    `_mcSettingsNavDepth === 0`). Screenshots eyeballed (≥400 ms settle past
    `settingsPaneIn`): all four panes fully styled.
  - `node tools/smoke/bg-framing-check.mjs` — **identical pre-existing base
    error** (`setBgZoom is not defined`); boots with settings-sections.js
    fulfilled, dies at the same later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–6).

### Landmines for module 8 (remaining-core map)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; re-derive
   boundaries + brace-depth scan; accessor-bridge handler-assigned vars via
   the formal scan; don't "repair" quirks; settle CSS entry animations
   before screenshots; cross-module window props are normal).
2. **Parse-time-ordering trap GENERALIZES (this module's find):** when a
   region's `window.x = window.x || {defaults}` init moves to a deferred
   module, any inline parse-time code that ALSO lazy-inits the same prop
   (`window.x = window.x || {}`) now wins, and the defaults never apply.
   Audit every default field's read sites for falsy-tolerance before
   accepting — here it was provably equivalent; elsewhere it may not be.
3. **Settings family is now FULLY extracted** (modules 3+6+7: pairing,
   drill machinery, section templates+hydration+actions). What's left of
   the old "Global Settings" neighborhood inline: `saveSetting`/
   `toggleSetting`/`setBriefRepliesMode`/`saveModelChoice` (generic helpers
   used by drill-pane templates — they stay until/unless a settings-helpers
   module is worth it), provider settings (`_renderProviderSettings` at
   ~L12828), update/power/restart-detection sections (~L12409–12827).
4. **The heredoc backslash trap (tooling):** the Bash tool mangles `\\` in
   heredocs on this Windows setup — write analysis/surgery scripts to
   `_scratch/*.py` files instead of piping them through stdin.
5. **Remaining core (orchestrator store.js checkpoint data):** `terminal`
   (8 refs / ~281 lines) — writer audit STILL pending; `terminalDismissed`
   Set + `terminalEventSources` Map are read by outside code; the
   object-identity window bridge remains viable only if all writers
   reassign through window (this module's 5 state globals prove the
   pattern works when BOTH sides are window-qualified by construction —
   terminal's are NOT, they're bare `let`s). Agent-panel / projects-grid:
   no sizing rows exist; both interleave with the conversation model,
   `computeLiveStatus`/grid renderer, and SSE slot management — expect a
   js/store.js prerequisite rather than a clean lift; derive fresh before
   promising.

## Phase 3 — module 8: extract terminal pop-out → `static/js/terminal.js` (2026-06-09)

- **Module-7 SHA backfill:** module 7 (js/settings-sections.js) = commit
  `32d571b`.
- **First module with shared mutable globals — unblocked by the
  object-identity window bridge.** Modules 2–7 deferred terminal because its
  state is read by inline code that stays behind. The writer audit (module 7
  data, re-verified formally here) proves the bridge is sound; this is also
  the **first not-fully-byte-verbatim move**: the 5-line state block is
  TRANSFORMED (`let` → guarded `window.` inits), everything else stays
  byte-verbatim with the reassembly assertion adapted accordingly.
- **Region re-derived:** old lines 10508–10756 (`// ── Terminal Pop-Out`
  header through `cleanupTerminalModal`'s closing brace + trailing blank;
  next header `// ── Scheduler` at 10757) = 249 lines, exactly 8 function
  declarations (`openTerminalPopout`, `initTerminalXterm`, `termZoom`,
  `connectTerminalStream`, `sendTerminalInput`, `stopTerminal`,
  `fetchTerminalStatus`, `cleanupTerminalModal`). Brace-depth top-level
  scan: declarations only, depth ends 0, no IIFEs/top-level calls. The
  state block is old lines 5064–5069 ONLY (header + 5 `let`s) — the
  dispatch hint's "~L5064–5095" over-spanned into hivemind/session-tab
  helpers (L5071+), which are NOT terminal state and stay inline.
- **Inbound refs re-verified: exactly 8 textual = the module-2 sizing** —
  `fetchTerminalStatus` L2262 (`openProjectModal`'s
  `Promise.all([fetchAgentStatus, fetchTerminalStatus])`),
  `cleanupTerminalModal` L2293 (`closeModalById`, unconditional — the
  region function self-filters via the `__terminal_` regex),
  `openTerminalPopout` L6394 + `terminalDismissed` L6388/6395/6396 (the
  `[terminal:sid:cmd]` marker scan inside `connectAgentStream`'s
  `es.onmessage`), `terminalEventSources` L15306/15307 (`beforeunload` SSE
  cleanup). All runtime-only (≥1 network round-trip + user/SSE action past
  module eval). Whole-repo sweep: only index.html + this doc (prose).
- **Equivalence analysis per shared global (the unblocking design — all 5
  PASS, no STOP):** for each of `terminalInstances`, `terminalEventSources`,
  `terminalOutputBuffers`, `terminalOutputCount`, `terminalDismissed`:
  - **Writer scan (formal, not eyeball):** direct `(?<![\w$.])name\s*=(?!=)`
    + compound `[+\-*/%&|^…]=` + `++/--` scans → exactly ONE hit each: the
    declaration itself. Every other reference (46 total) is a property-level
    op (`[sid]` get/set/delete, `.has/.add`, `.push`, `.close`, `for…in`) —
    object identity is the only thing that matters, and property ops behave
    identically through any resolution path.
  - **Binding-kind change (`let` global-declarative-record → window prop)
    observability:** zero `window.<name>` refs, zero `typeof` probes, zero
    `delete <name>`, zero re-declarations, no DOM elements with these ids
    (id-based window globals would poison `||` adoption), no
    window-enumeration code. TDZ: baseline boots clean ⇒ no parse-time read
    precedes L5065 (a pre-existing one would have TDZ-thrown at base).
  - **Init-ordering / first-read guarding (the module-7 analysis,
    replicated):** the inline bridge executes at the SAME parse-time line
    the `let`s did ⇒ the props exist before any reader can possibly fire —
    first-read-before-init is impossible *by construction* (stronger than
    module 7, which had a genuine ordering inversion to argue). The
    module-side re-inits `||`-short-circuit against the inline-created
    objects (no second allocation, no divergence); defaults are identical
    empty containers on BOTH sides, so even a hypothetical inversion is
    vacuously equivalent — module 7's "rich defaults never apply" hazard
    has no analogue here.
- **Bridge design (dual-side guarded inits):** index.html's state block
  (L5064–5069) is REPLACED in place by `window.x = window.x || {…}` lines
  (+ bridge comment); terminal.js carries the same guarded inits as its
  prologue. Inline runs first (parse time vs deferred), creates the
  objects; the module adopts them by identity. Rationale for keeping the
  inline side: with module-only inits, a `fetchProjects()` response task
  can theoretically run between inline-script end and module eval (module
  scripts fetch over network and the event loop runs while waiting), and a
  bare inline read of a never-created global is a ReferenceError, not
  undefined. The 5 inline lines eliminate that whole class.
- **What moved:** old lines 10508–10756 → `static/js/terminal.js`,
  **byte-verbatim** (split reassembly assertion: (1) the region segment in
  terminal.js == original region bytes; (2) new index.html with the script
  tag removed + region re-inserted at the deletion point + bridge block
  swapped back to the original `let` block == original file byte-for-byte).
  Loaded via `<script type="module" src="/static/js/terminal.js"></script>`
  after the settings-sections.js tag, before `</body>`. Anti-FOUC bootstrap
  untouched. Diff shape: state block 6 → 12 lines, region −249, tag +1.
- **Numbers:** `terminal.js` = 12,304 bytes / 279 lines (249 moved +
  13-line prologue incl. the byte-verbatim original state-header comment +
  17-line interop tail; CRLF, no BOM). `index.html` 776,962 → 767,452
  bytes; 15,962 → 15,720 lines (BOM preserved).
- **Interop surface: 6 window function re-exposures + 5 identity-bridged
  state globals, 0 accessor bridges** (generated-handler assignment scan
  empty — handlers only CALL, never assign):
  - Inline callers (3): `openTerminalPopout`, `fetchTerminalStatus`,
    `cleanupTerminalModal`.
  - Region-generated `on*=` targets (3): `stopTerminal`, `termZoom`,
    `sendTerminalInput`.
  - Module-private (2): `initTerminalXterm`, `connectTerminalStream` (only
    called from `openTerminalPopout`; NOT to be confused with the INLINE
    `connectAgentStream`, which stays).
  - State (5): identity-bridged via window props as above — NOT re-exposed
    functions, the objects themselves are the interface.
- **Outbound deps** (resolve at call time through the shared global scope):
  `API_BASE`, `esc`, `openModals` (Map method ops), `nextModalZ++`
  (module→inline-let write, proven direction), `_clampModalSize`,
  `centerModalElement`, `focusModal`, `restoreModal`, `closeModalById`,
  `minimizeModal` (handler string), `_isMobileDevice` (inline const), CDN
  classics `Terminal`/`FitAddon` (head `<script src>`, L106–107), browser
  builtins (`EventSource`, `fetch`, `ResizeObserver`). Strict-mode
  promotion audited: zero `this`/`with`/`callee`/octals in region code.
- **sw.js:** `SW_VERSION` `mc-push-v8` → `mc-push-v9` (no cache list by
  design; version bump only, same as modules 1–7).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/terminal.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - `node --input-type=module --check` parses terminal.js in module goal.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` from the
    worktree, then killed; live :5199 never touched):
    `/static/js/terminal.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 12304, `Cache-Control: no-cache` + ETag.
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    throwaway `termtest` project created via `POST /api/project/termtest`
    since fresh worktrees have zero real projects) — **24/24 PASS,
    0 console errors, 0 uncaught page errors**:
    - 6/6 interop functions callable; module-private fns NOT leaked; state
      props correct shapes (4 objects + Set).
    - **Prescribed exercise:** `POST /api/terminal/launch` with
      `python -c "print('hello from term')"` → `openTerminalPopout` →
      xterm attached → **"hello from term" arrived via the SSE stream**
      into the shared buffer → status event flowed (dot → `completed`,
      EventSource removed from the shared object).
    - **Real inline discovery path:** second launch (print + sleep) →
      inline `openProjectModal('termtest')` → L2262 `Promise.all` →
      module `fetchTerminalStatus` → pop-out auto-opened, live SSE output.
    - **THE identity gate, asserted both sides:** page-captured probes
      (`window.__pES = window.terminalEventSources` etc. at boot) remain
      `===` to the live globals at three points (boot / mid-exercise /
      end); the module-created `EventSource` is `instanceof EventSource`
      INSIDE the boot-captured object — same object, no divergence, all 5
      globals.
    - **Dismiss:** real `.modal-close` click → inline `closeModalById` →
      module `cleanupTerminalModal` → `terminalDismissed.has(sid)` true
      through BOTH references (shared Set), SSE + xterm instance cleaned
      from the shared objects, server session deleted.
    - **Marker path (outside reader) exercised for real:** stubbed
      `EventSource`, called inline `connectAgentStream`, fired synthetic
      agent-output messages through the REAL inline `es.onmessage`:
      (a) marker for the dismissed sid → NOT reopened (inline reader
      honored the module-written Set); (b) marker for a fresh running sid
      → real status fetch → module `openTerminalPopout` → modal appeared +
      stream registered in the shared object (inline scan → module call,
      end-to-end). EventSource restored after.
    - Screenshots eyeballed (600 ms settle): styled pop-out with output +
      completed status; live pop-out (running dot, Stop visible) over the
      project modal; marker-opened pop-out with the dismissed modal
      correctly absent.
  - `node tools/smoke/bg-framing-check.mjs` — **identical pre-existing
    base error** (`setBgZoom is not defined`, landmine 4 of module 1);
    boots with terminal.js fulfilled, dies at the same later evaluate. No
    new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–7).

### Landmines for module 9 (remaining-core map, post-terminal)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary
   surgery; build-macos.spec bundles static/ wholesale; npm install in
   fresh worktrees; deferred-module timing; module-scoped-globals rule;
   re-derive boundaries + brace-depth scan; accessor-bridge
   handler-assigned vars via the formal scan; don't "repair" quirks;
   settle CSS animations before screenshots; cross-module window props
   are normal; surgery scripts in `_scratch/*.py`, not heredocs).
2. **The object-identity window bridge is now a proven 4th interop tool**
   (after plain re-exposure, `Object.defineProperty` accessors, and
   already-window-qualified state): for bare-`let` state shared across the
   module boundary, (a) formally prove zero wholesale reassignments,
   (b) replace the `let`s in place with guarded `window.x = window.x || …`
   inits (parse-time creation preserved ⇒ no first-read analysis debt),
   (c) duplicate the guarded inits at module top (self-documenting,
   order-independent), (d) check for DOM id collisions + `typeof`/
   `window.`-qualified probes before trusting `||` adoption.
3. **Gate scripts must not race region `setTimeout`s:** `openTerminalPopout`
   defers xterm/SSE wiring by 50 ms — `waitForFunction` on the observable
   state, never assert immediately after the triggering call (cost one
   false FAIL here).
4. **Faking SSE for inline-handler exercise works:** stub
   `window.EventSource` AFTER real streams are established, call the inline
   connecter, drive `stub.onmessage({data: JSON.stringify(...)})` — runs
   the real handler in its real scope. Restore after. (Watchdog intervals
   keyed on fake sids no-op against an empty `agentStatusCache`.)
5. **Remaining-core sizing (re-derived post-move, 15,720 lines total; main
   inline script L679–15711 ≈ 15,033L).** Cleanly-liftable-LOOKING feature
   families ≥150L (refs UNVERIFIED — re-derive before promising, module-4
   lesson): Mermaid interception 1,127L; MCP family 1,128L (476 manager +
   652 From-URL state machine); Hivemind family ~1,294L (202 tab + 544
   dashboard + 366 cross-project + 182 run-history shared w/ Scheduler);
   Search past chats 849L; Command Palette 520L; Excalidraw bridge 492L;
   System status 456L; Scheduler 377L; Update section 360L; provider
   auth/settings ~630L (312+165+154). Sum ≈ 7,200L of candidate families.
   The genuinely-entangled core (agent-panel/projects-grid smear +
   boot/modal engine): Conversation model 545L, Modal HTML 463L, Tile HTML
   304L, Rich text formatting 254L, Deep link 256L, Three-dot menu 273L,
   DnD grid 186L, Aero-Snap 193L, Multi-modal tiles 268L, Agent Log/Plans/
   Console/Drawer ~670L, plus ~59 sub-150L sections (SSE slot management,
   dispatch, status resolver, boot sequence) ≈ **~6,500–7,500L that should
   wait for the orchestrator's js/store.js design checkpoint** — the
   conversation model's state (`agentOutputBuffers`, `agentEventSources`,
   `agentStatusCache`, …) is the bridge pattern's stress test: dozens of
   globals, some possibly wholesale-reassigned; run the formal writer scan
   FIRST and expect accessor bridges where it fails.

## Phase 3 — module 9: extract Mermaid render pipeline → `static/js/mermaid.js` (2026-06-10)

- **Module-8 SHA backfill:** module 8 (js/terminal.js) = commit `28e27bb`.
- **Sizing correction (module-4 lesson, third occurrence):** the "Mermaid
  interception 1,127L" row measured header-to-header (`// ── Mermaid diagram
  interception` 6939 → `// ── Agent Log Panel` 8066). The real cleanly-
  separable family is **306 lines (6939–7244) + trailing blank 7245**; the
  other ~820 lines of the span are the conversation model (`appendAgentLine`
  7246, `approvePlan`, the AskUserQuestion form machinery, `sendFollowup`,
  `stopAgent`, `fetchAgentStatus`, …) — NOT mermaid; stays inline.
- **Boundary finding — the lazy-load/theming bootstrap is NOT monolith code
  and STAYS in `<head>`:** the mermaid library loader lives in its own,
  already-isolated `<script type="module">` at L112–210 (static CDN import of
  `mermaid@11` ESM, `mermaid.initialize` with the Clayrune theme,
  `window.mermaid`, a startup orphan-error sweep that is an inline COPY of
  the region's sweep logic, `mermaid-ready` dispatch, and the Excalidraw
  bridge background `import()` → `window._excalidrawAPI` +
  `excalidraw-ready`). Merging it verbatim into a body-end mermaid.js was
  analyzed and DISQUALIFIED as a behavior change: a top-level static CDN
  import gates the whole module's evaluation, so the family's window
  re-exposures would wait on a multi-hundred-KB jsdelivr fetch — but the
  inline callers are hot paths (`appendAgentLine` → `_handleMermaidLine`
  fires on every SSE output line; a reload-while-an-agent-streams would race
  the CDN and throw ReferenceError per line = dropped agent output; CDN-down
  would permanently break the formatter, vs. baseline's graceful "Building
  diagram…" pend). The static→dynamic import rewrite that would avoid the
  gating is not a move. Partial-scope delivery, documented here: render
  pipeline + buffers + error handling + both viewers moved; loader/theming
  stays as the head module it already was.
- **What moved:** old lines 6939–7245 (`// ── Mermaid diagram interception`
  header through `_openImageViewer`'s closing brace + trailing blank) →
  `static/js/mermaid.js`, **byte-verbatim** (binary reassembly assertion:
  original index.html == new file with the `<script>` tag line removed and
  the region re-inserted at the cut point; interop tail append-only).
  `_openImageViewer` (the image lightbox that reuses the mermaid-viewer
  chrome) is part of the contiguous family and moved with it. Loaded via
  `<script type="module" src="/static/js/mermaid.js"></script>` after the
  terminal.js tag, before `</body>`. Anti-FOUC bootstrap untouched. Diff
  shape: 1 insertion, 307 deletions.
- **Numbers:** `mermaid.js` = 15,836 bytes / 326 lines (307 moved incl. the
  trailing blank + 19-line interop tail; CRLF, no BOM). `index.html`
  767,452 → 753,117 bytes; 15,720 → 15,414 lines (BOM preserved).
- **Interop surface: 4 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Inline callers (3 fns): `_renderAllMermaidPlaceholders` (refreshModal
    L3260 + openPlanViewer L10427, pre-move numbering),
    `_mermaidPlaceholderHTML` (outputLines builder L5368 + openPlanViewer
    body builder L10377), `_handleMermaidLine` (appendAgentLine L7271).
  - Generated-onclick target (1): `_openImageViewer` — the rich-text
    formatter emits `onclick="_openImageViewer(this.src)"` (L6675/L6738,
    builders stay inline; resolve against the global object at click time).
  - Module-private (6): `_mermaidBuffers` (region-internal `const` container,
    zero outside refs — formal scan), `_resizeSvgForFit`,
    `_sweepOrphanMermaidNodes`, `_renderViaExcalidraw`, `_renderViaMermaid`,
    `_openMermaidViewer` (wired only via region-internal addEventListener).
  - Formal scans: per-identifier whole-file writer scan (wholesale +
    compound + inc/dec) → zero writes beyond the one `const` decl; zero
    `typeof`/`window.`-qualified probes; generated-handler assignment scan
    EMPTY; region outbound-assignment scan → zero writes to non-region
    bindings (the viewers use their own overlay — the region never touches
    `nextModalZ` or the modal manager at all); zero `this` in region code.
- **Parse-time audit (brace-depth scan):** region top level = 1 `const` +
  9 `function` declarations, depth ends 0, no IIFEs, no top-level calls.
  Deferred-module timing: all inbound calls are ≥1 network round-trip +
  user/SSE action past module eval (same accepted class as module 8).
- **Outbound deps** (resolve at call time): `esc` (inline fn),
  `window.mermaid` / `window._excalidrawAPI` (head-module products,
  window-qualified reads), the `mermaid-ready` window event (listener armed
  by `_renderAllMermaidPlaceholders` when the lib isn't loaded yet — the
  lazy-load handshake is unchanged), DOM/browser builtins. No cross-module
  deps. `node --input-type=module --check` parses.
- **sw.js:** `SW_VERSION` `mc-push-v9` → `mc-push-v10` (no cache list by
  design; version bump only, same as modules 1–8).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/mermaid.js` in
  BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios.
  - Real-server check (throwaway `MC_PORT=5379 python server.py` from the
    worktree, then killed; live :5199 never touched):
    `/static/js/mermaid.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 15836, `Cache-Control: no-cache`.
  - Headless Chromium against that server (seeded `walkthrough_done=1`) —
    **24/24 PASS, 0 console errors, 0 uncaught page errors**:
    - Interop: 4/4 window fns callable; 6/6 module-privates NOT leaked.
    - `window.mermaid` loaded LIVE from the CDN (head module evaluated; the
      lazy-load path therefore exercised in its loaded branch).
    - **Prescribed exercise through the REAL inline formatter path:** stubbed
      `EventSource` post-boot, called inline `connectAgentStream`, fired
      synthetic `{type:'output'}` SSE messages through the REAL
      `es.onmessage` → `appendAgentLine` → module `_handleMermaidLine`:
      placeholder created with captured source, then **the diagram SVG
      RENDERED through the real pipeline** (13,423-char SVG;
      `renderer=mermaid` — the Excalidraw bridge hadn't finished its
      background esm.sh load at render time, so the designed
      excalidraw→mermaid fallback chain fired, identical to baseline's
      race), click-to-enlarge wired.
    - Viewer: block click → overlay; zoom 100%→125%; source toggle shows
      the original fence; Escape closes + keydown listener cleaned.
    - **Error path:** invalid mermaid streamed the same way →
      `.mermaid-error` ("Diagram error: No diagram type detected…") +
      `.mermaid-source` raw-source block, no page errors — baseline
      degradation shape.
    - Rebuild-path interop: `_mermaidPlaceholderHTML` string into a fresh
      host + `_renderAllMermaidPlaceholders(host)` → rendered SVG (the
      L5368/L3260 shape end-to-end).
    - Image viewer: `window._openImageViewer(dataURI)` → overlay with img,
      zoom 100%→125%, Escape close.
    - Screenshots eyeballed: Clayrune-themed diagram (cream nodes, orange
      borders, clay-brown edges — head-module theming flowing through the
      extracted pipeline), styled viewer + source pane.
  - `node tools/smoke/bg-framing-check.mjs` — **identical pre-existing base
    error** (`setBgZoom is not defined`, landmine 4 of module 1); boots with
    mermaid.js fulfilled (grid renders), dies at the same later evaluate.
    No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–8).

### Landmines for module 10 (remaining-core map, post-mermaid)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; re-derive
   boundaries + brace-depth scan; accessor-bridge handler-assigned vars via
   the formal scan; object-identity bridge for bare-`let` shared state;
   don't "repair" quirks; settle CSS animations before screenshots;
   cross-module window props are normal; surgery scripts in `_scratch/*.py`;
   don't race region setTimeouts — waitForFunction on observable state).
2. **NEW CLASS — head-module residency:** not every sizing-table family is
   monolith code. Check FIRST whether part of the family lives in a separate
   head `<script type="module">`. A top-level static CDN import gates a
   module's entire evaluation — hot-path helpers (formatter callees, SSE
   line handlers) must NEVER ride in a module that imports from a CDN.
   Leave CDN loaders where they are, or the move becomes a behavior change.
3. **Queue correction — the "Excalidraw bridge 492L" row is DEAD:** after
   module 9, `excalidraw` appears ONLY in the head module (loader, stays
   per landmine 2); the inline consumer `_renderViaExcalidraw` moved into
   js/mermaid.js as module-private. There is no extractable inline
   excalidraw code left. Strike it from the candidate list.
4. **Remaining candidate queue (sizes from the module-8 list, all refs
   UNVERIFIED — re-derive; post-module-9 inline section starts shifted
   −307 from the quoted pre-move line numbers):** MCP family 1,128L
   (manager 476 + From-URL state machine 652; headers were 13481/13957,
   now ~13174/13650); Hivemind family ~1,294L (tab 202 + dashboard 544 +
   cross-project 366 + run-history 182 shared with Scheduler — the sharing
   is a boundary risk, derive carefully); Search past chats 849L
   (5836–6684, unshifted — BEFORE the cut point); Command Palette 520L
   (2807–3326, unshifted; palette actions reference many feature
   open-functions — expect a wide outbound-dep list, all late-bound);
   System status 456L (10910→~10603); Scheduler 377L (10514→~10207);
   Update section 360L (12225→~11918); provider auth/settings ~630L.
   The synthetic-SSE + stub-EventSource technique (modules 8–9) now covers
   any feature whose entry point is an SSE line or marker.
5. **The gate-host trick from this module:** for formatter-fed features,
   a hand-made `agent-output-<sid>` container + stubbed EventSource +
   inline `connectAgentStream` runs the ENTIRE real pipeline (the formatter
   only needs the element to exist) — no real agent dispatch, no model
   cost, fully read-only against the server.
