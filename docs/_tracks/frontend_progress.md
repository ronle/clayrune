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

## Phase 3 — module 10: extract search past chats → `static/js/search-chats.js` (2026-06-10)

- **Module-9 SHA backfill:** module 9 (js/mermaid.js) = commit `537298c`.
- **Sizing correction (module-4 lesson, FOURTH occurrence) — the "849L"
  row was header-to-header and 86% foreign code.** The module-8 list quoted
  "Search past chats 849L (5836–6684)" = the span from the
  `// ── Search past chats by transcript content` header (5836) to the next
  header `// ── Rich text formatting for agent output` (6685). The real
  cleanly-separable family is **124 lines (5836–5959)** — the 5 state decls +
  8 functions through `pickChatResult`'s closing brace. Lines **5961–6684 are
  the conversation-model / agent-panel core** (`sessionPickerHTML`,
  `closeAgentTab`, `dispatchAgent`, the `agentSSEWatchdogs`/`_sendInFlight`/
  `_turnStartAcked`/`_reconcileBusy` state, `_reconcileAgentBuffer`, and an
  image-token helper that keeps in sync with `formatAgentText`) — exactly the
  heavily-entangled smear the module-8/9 landmines said must WAIT for the
  store.js checkpoint. They stay inline. Trailing blank 5960 stays as the
  separator before `sessionPickerHTML`.
- **Inbound refs re-verified (formal whole-repo scan of all 14 region names):
  exactly 2 cross-module/inline functional refs**, both inside the inline
  `agentPanelHTML` modal builder (runtime-only — fire when the dispatch
  screen renders, never at parse time):
  1. `chatSearchHTML(p.id)` — L5300, `const chatSearch = showResume ? …`.
  2. `searchPaneInner(p.id)` — L5301, `const searchPane = showResume ? …`.
  (`showResume = noActiveTab && _pcaps.supports_session_resume`.) Whole-repo
  sweep across `*.py/*.js/*.mjs/*.html/*.md/*.css/*.json`: **zero** references
  to ANY of the 14 region identifiers in any non-index file (no server.py,
  no demo-export, no smoke fixtures, no docs prose).
- **Parse-time audit (brace-depth scan):** region top level = 3 `let` +
  2 `const` + 9 `function` declarations, depth ends 0, **0 top-level
  non-decl/comment/blank lines** — no IIFEs, no top-level calls (the module-4
  `startRefresh` trap has no analogue). `node --input-type=module --check`
  parses the final file.
- **State needs NO bridges (zero outside refs):** the 3 mutable `let`s
  (`chatSearchQuery`, `chatSearchResults`, `chatSearchLoading`) + 2 `const`s
  (`_chatSearchTimers`, `_chatSearchSeq`) are read AND written ONLY by region
  code (formal outside-ref scan = 0 hits each). Writer scan (wholesale
  `(?<![\w$.])name\s*=(?![=>])` + compound + `++/--`) → exactly ONE hit each:
  the declaration itself; every mutation is property-level (`[projectId]`
  get/set/delete). So they stay **module-private `let`/`const`** — no
  identity bridge, no accessor bridge. First post-terminal module whose
  shared-looking state turned out to be fully private.
- **What moved:** old lines 5836–5959 → `static/js/search-chats.js`,
  **byte-verbatim** (binary reassembly assertion, two-sided: (1)
  `before + region_bytes + after == original` proving the region is a clean
  contiguous byte span; (2) the new index.html, with the inserted `<script>`
  tag line removed AND the region bytes re-inserted at the cut point,
  `== original` byte-for-byte; interop tail append-only on the js side).
  Loaded via `<script type="module" src="/static/js/search-chats.js"></script>`
  inserted immediately after the mermaid.js module tag, before `</body>`.
  Anti-FOUC bootstrap untouched. Diff shape: 1 insertion, 124 deletions.
- **Numbers:** `search-chats.js` = 6,542 bytes / 135 lines (124 moved +
  11-line interop tail: 1 blank + 5 comment + 5 assignment; CRLF, no BOM,
  149 non-ASCII UTF-8 bytes). `index.html` 753,117 → 747,322 bytes;
  15,414 → 15,291 lines (BOM preserved).
- **Interop surface: 5 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Cross-module/inline callers (2): `chatSearchHTML`, `searchPaneInner`
    (the agent-panel modal builder, refs above).
  - Region-generated `on*=` handler targets (3): `onChatSearchInput`
    (search-box `oninput`), `clearChatSearch` (clear-× `onclick` + the
    Escape branch of the input `onkeydown`), `pickChatResult` (each result
    row's `onclick`) — resolve against the global object at event time.
  - Module-private (9, zero outside/handler refs): `renderAgentSearchPane`,
    `_highlightTerm`, `chatResultsHTML`, `runChatSearch` + the 5 state
    globals. (`selectResumeSession` in `pickChatResult`'s body is an
    OUTBOUND dep, defined inline at L5721, not re-exposed here.)
  - Formal scans: generated-handler ASSIGNMENT scan (`="<ident>=`) EMPTY (no
    accessor bridge); per-identifier writer scan → zero wholesale writes
    beyond declarations; zero `typeof`/`window.`-qualified probes; zero
    `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `esc`, `API_BASE` (L681 `const`), `convPreviewHTML` (L5771, the adjacent
  Resume-preview section that stays inline), `selectResumeSession` (L5721,
  same section) + browser builtins (`fetch`, `setTimeout`, `clearTimeout`,
  `RegExp`, `encodeURIComponent`, `document.getElementById`). Same
  classic-script global-declarative-record mechanics as modules 2–9.
  Strict-mode promotion audited: clean.
- **sw.js:** `SW_VERSION` `mc-push-v10` → `mc-push-v11` (no cache list by
  design; version bump only, same as modules 1–9).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/search-chats.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs`
  and `bg-framing-check.mjs`.
- **Gates:**
  - `node --input-type=module --check` parses search-chats.js in module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5390 python server.py` from the
    worktree, then killed; live :5199 never touched):
    `/static/js/search-chats.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 6542, `Cache-Control: no-cache` + ETag.
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **strictly read-only** — no agent dispatch, no model cost) —
    **16/16 PASS, 0 console errors, 0 uncaught page errors**: 5/5 window.*
    interop callable; 9/9 module-privates (fns + state) NOT leaked to window;
    throwaway `sctest` project created (`POST /api/project/sctest`) →
    real `openProjectModal('sctest')` → dispatch screen renders the
    `#chat-search-sctest` box (chatSearchHTML via the agent panel);
    **real GET `/search-chats` ran** for a no-match query → empty-state
    "No chats mention …" rendered through `searchPaneInner`→`chatResultsHTML`;
    **results path** via a stubbed fetch returning 2 synthetic rows (still
    driven through the REAL `onChatSearchInput`→`runChatSearch` handler) →
    `chatResultsHTML` header "2 chats mention …", `_highlightTerm` wrapped
    the query in `<mark class="cp-hit">`, each row wired
    `onclick="pickChatResult('sctest','CSID-AAA')"`; `clearChatSearch` reset
    the input + flipped the pane off results. Throwaway project deleted
    after. Screenshot eyeballed: fully styled search box (magnifier + clear
    ×, rounded border) above the composer in the AGENT dispatch screen; modal
    + sidebar + mascot all styled, no FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined` at the same L78
    evaluate, landmine 4 of module 1); page boots with search-chats.js
    fulfilled (grid renders, gets past the card wait), dies at the same later
    evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–9).

### Landmines for module 11 (remaining-core map, post-search-chats)

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
2. **The header-to-header span is now a PROVEN unreliable boundary 4×**
   (modules 4, 9, and 10 all had foreign code in the quoted span; only
   modules 5/6/7 matched exactly). For search-chats the quoted 849L was 86%
   foreign — the family was 124L. **Always brace-depth-scan from the header
   and read where the family actually ends; the "remaining candidate" line
   counts in this log are header-to-header upper bounds, not boundaries.**
3. **Some "shared-looking" state is fully private** (search-chats' 5 state
   globals had ZERO outside refs despite living next to the conversation
   model). Run the outside-ref scan BEFORE assuming an identity/accessor
   bridge is needed — module-private `let`/`const` is the cheapest outcome
   and needs no bridge at all.
4. **Remaining candidate queue (sizes from the module-8 list, all
   header-to-header upper bounds, refs UNVERIFIED — re-derive; the inline
   section is now shifted −124 more from any quoted pre-module-10 line ≥5960):**
   MCP family 1,128L (manager + From-URL state machine; headers ~13050/13526
   now); Hivemind family ~1,294L (tab + dashboard + cross-project +
   run-history shared with Scheduler — the sharing is a boundary risk);
   Command Palette 520L (2807–3326, unshifted — BEFORE all cut points;
   palette actions reference many feature open-functions, expect a wide
   late-bound outbound-dep list); System status 456L; Scheduler 377L (shares
   run-history with Hivemind); Update section 360L; provider auth/settings
   ~630L. The genuinely-entangled agent-panel/projects-grid core
   (conversation model, modal/tile HTML, SSE slot management, dispatch,
   status resolver — incl. the 5961–6684 block this module deliberately left
   behind) still WAITS for the orchestrator's js/store.js design checkpoint.
5. **The throwaway-project + real-modal recipe (reuse it for any
   modal-gated feature):** `POST /api/project/<id>` → `fetchProjects()` →
   real `openProjectModal(<id>)` renders the dispatch screen; assert the
   feature's DOM (`#chat-search-<id>` here) then drive its real handlers.
   For a feature whose only live data is server transcripts the worktree
   doesn't have, exercise the empty path against the REAL endpoint AND the
   populated path through a stubbed `fetch` (restored after) — both run the
   real in-region handlers, zero model cost, fully read-only. Delete the
   throwaway project at the end.

## Phase 3 — module 11: extract backlog actions → `static/js/backlog-actions.js` (2026-06-10)

- **Module-10 SHA backfill:** module 10 (js/search-chats.js) = commit `dd0d637`.
- **Candidate selection this run (3 clean modules; cleaner end first):** of
  the module-8 queue, the named candidates were RE-DERIVED and several
  disqualified before picking. **System status (456L) is BLOCKED** — its boot
  call `fetchSystemStatus()` runs at PARSE TIME at the inline script top level
  (L14349, right after the inline `startRefresh()`), exactly the module-4
  `startRefresh` trap; moving `fetchSystemStatus` to a deferred module would
  ReferenceError-abort the rest of the inline boot. **Command Palette's "520L"
  span is 80% foreign** (fifth header-to-header miss): 2807–2903 is the real
  family (cmd state + toggle + render + input listener); L2905+ is core modal
  engine — `restoreModal` (13 callers), `sizeAgentChat`, `updateAgentStatusUI`,
  `guardianReset`, `refreshModalById` (9), `refreshModal` (50) — store.js
  territory; AND the real family needs 2 accessor bridges (`cmdPaletteOpen` is
  written by walkthrough.js's strict-module bare assignment; `cmdSelectedIndex`
  is `++`/`=`-mutated by the shared inline keydown handler that also touches
  `focusedModalId`/`closeModalById` and can't be split). Deferred both; picked
  the two clean Backlog families + Scheduler instead.
- **Inbound refs re-verified (formal whole-repo scan of all 7 region names):
  every ref is a generated `on*=` handler in tile/modal HTML or one
  cross-module caller — all runtime-only, zero parse-time callers:**
  `addBacklogItem` (tile add button L1932 + `handleInputEnter` keydown L1920),
  `toggleDone` (tile check L1556 + **cross-project backlog list L12472**, the
  module-12 region), `cyclePriority` (L1569), `saveBacklogText` (onblur L1561),
  `deleteBacklogItem` (L1580), `dispatchBacklogItem` (L1573),
  `patchItem` (the Code-sync retry path L4010). Whole-repo sweep: no other file
  references any of the 7 names.
- **Region boundary re-derived:** old lines 5836… no — Backlog actions header
  `// ── Backlog actions` at 3579 through `patchItem`'s closing brace (3698) +
  trailing blank 3699; next header `// ── GitHub sync actions` at 3700 (its
  `showToast` at 3702 stays inline). **Exactly the 121-line header-to-header
  span — no foreign code** (like modules 5/6/7). Brace-depth top-level scan:
  7 `async function`/`function` decls, depth ends 0, **0 top-level non-decl
  lines** (no IIFEs, no parse-time calls, no listeners). `node --check
  --input-type=module` (via stdin) parses.
- **State: NONE.** The region declares zero module-level variables — purely 7
  functions. No identity bridge, no accessor bridge. Cleanest possible move.
- **What moved:** old lines 3579–3699 → `static/js/backlog-actions.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before +
  region + after == original` proving the region is a clean contiguous byte
  span; (2) the new index.html, with the inserted `<script>` tag line removed
  AND the region bytes re-inserted at the cut point, `== original`
  byte-for-byte; interop tail append-only on the js side). Loaded via
  `<script type="module" src="/static/js/backlog-actions.js"></script>`
  inserted immediately after the search-chats.js module tag, before `</body>`.
  Anti-FOUC bootstrap untouched. Diff shape: 1 insertion, 121 deletions.
- **Numbers:** `backlog-actions.js` = 5,726 bytes / 133 lines (121 moved +
  12-line interop tail: 1 blank + 4 comment + 7 assignment; CRLF, no BOM,
  189 non-ASCII UTF-8 bytes). `index.html` 747,322 → 742,287 bytes;
  15,291 → 15,171 lines (BOM preserved).
- **Interop surface: 7 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Generated `on*=` handler targets (6): `addBacklogItem`, `toggleDone`,
    `cyclePriority`, `saveBacklogText`, `deleteBacklogItem`,
    `dispatchBacklogItem` (tile + modal HTML, resolve against window at event
    time).
  - Cross-module callers (2, overlap): `toggleDone` (also the cross-project
    backlog list, module 12) + `patchItem` (the Code-sync retry path L4010).
  - Module-private: none (all 7 functions are externally referenced).
  - Formal scans: generated-handler ASSIGNMENT scan (`="<ident>=`) EMPTY;
    per-identifier writer scan → zero writes (no state); zero `typeof`/
    `window.`-qualified probes; zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `API_BASE`, `refreshSilent`, `esc`, `showToast`, `confirm`, `allProjects`,
  `_pushUndo`/undo machinery, `dispatchAgent`-adjacent helpers + browser
  builtins (`fetch`, `document.getElementById`). Same classic-script
  global-declarative-record mechanics as modules 2–10. Strict-mode promotion
  audited: clean (no `this`, every assignment is a `window.` property).
- **sw.js:** `SW_VERSION` `mc-push-v11` → `mc-push-v12` (no cache list by
  design; version bump only, same as modules 1–10).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/backlog-actions.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses backlog-actions.js in
    module goal. (NOTE for future runs: this Node — v24 — rejects
    `--input-type=module --check <file>`; pipe the file via stdin instead. The
    progress log's earlier `--input-type=module --check <file>` form errors
    here.)
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5391 python server.py` from the
    worktree, then killed; live :5199 never touched):
    `/static/js/backlog-actions.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 5726, `Cache-Control: no-cache` + ETag.
  - Headless Chromium against that server (seeded `walkthrough_done=1`) —
    **16/16 PASS, 0 console errors, 0 page errors**: all 7 window.* interop
    callable; throwaway `bltest` project created (`POST /api/project/bltest`)
    → real `openProjectModal('bltest')` rendered `#backlog-input-bltest`;
    **real CRUD end-to-end** drove the in-region handlers against real
    endpoints — `addBacklogItem` (verified item in `/api/projects`),
    `cyclePriority` (normal → high, the `patchItem` path), `toggleDone`
    (status → done), `patchItem` (text updated), `deleteBacklogItem` (backlog
    emptied). Throwaway project deleted after.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of
    module 1; line shifted to 81 by the added const but the same evaluate
    step); page boots with backlog-actions.js fulfilled, dies at the same
    later evaluate. No new breakage.
- **Commit:** module 11 (js/backlog-actions.js) = commit `92b0fbc`.

## Phase 3 — module 12: extract cross-project backlog → `static/js/cross-backlog.js` (2026-06-10)

- **Inbound refs re-verified (formal whole-repo scan of all 4 region names):
  exactly 2 cross-module/inline functional refs, both runtime-only:**
  `openAllBacklog` (L2650, `sidebarNav('backlog')`) and `renderAllBacklog`
  (L835, the central `render()` — but **guarded** by
  `if (openModals.has('__all_backlog')) renderAllBacklog()`, so it fires only
  once the modal is open, which is necessarily after this deferred module has
  evaluated; never at the parse-time `startRefresh()` boot, where no modal is
  open). `_jumpToBacklogItem` is a region-generated row `onclick`;
  `_allBacklogFilter` has zero non-handler refs. Whole-repo sweep: no other
  file references any of the 4 names.
- **Region boundary re-derived (post-module-11 numbering, shifted −121):** old
  lines 12246–12380 (`// ── Cross-project Backlog View` header through
  `_jumpToBacklogItem`'s closing brace) + trailing blank 12380; next header
  `// ── Run history (shared between Scheduler + Hivemind surfaces)` at 12381
  stays inline — **the shared run-history is NOT touched** (the Scheduler/
  Hivemind HARD-STOP boundary). **Exactly the 135-line header-to-header span,
  no foreign code.** Brace-depth top-level scan: 1 `let` + 3 function decls,
  depth ends 0, **0 top-level non-decl lines** (no IIFEs/parse-time calls/
  listeners). `node --check --input-type=module` (stdin) parses.
- **State: ONE object, OBJECT-IDENTITY bridge.** `_allBacklogFilter` (a
  `{status, search, priority}` object) is **property-written** by 3 generated
  handlers (`oninput="_allBacklogFilter.search=this.value;renderAllBacklog()"`,
  two `onchange` for status/priority) — bare-identifier *property* writes that
  resolve against window at event time. Formal wholesale-write scan
  (`(?<![\w$.])_allBacklogFilter\s*=`) → exactly ONE hit: the declaration; the
  handler writes are all `.`-prefixed (never reassigned). So a plain
  `window._allBacklogFilter = _allBacklogFilter` identity bridge routes every
  handler property-write into the module's live object — one source of truth,
  same as mobile-pairing's `_mobilePairState` (NOT an accessor — the object is
  never wholesale-reassigned, so a getter is unnecessary).
- **What moved:** old lines 12246–12380 → `static/js/cross-backlog.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before +
  region + after == original`; (2) new index.html with the inserted `<script>`
  tag line removed AND the region re-inserted at the cut point `== original`;
  interop tail append-only). Loaded via
  `<script type="module" src="/static/js/cross-backlog.js"></script>` inserted
  immediately after the backlog-actions.js module tag (keeps it AFTER the
  module that exposes `toggleDone`, which this region's row checkbox onclick
  calls), before `</body>`. Anti-FOUC bootstrap untouched. Diff shape:
  1 insertion, 135 deletions.
- **Numbers:** `cross-backlog.js` = 8,458 bytes / 155 lines (135 moved +
  20-line interop tail: 1 blank + 15 comment + 4 assignment; CRLF, no BOM,
  186 non-ASCII UTF-8 bytes). `index.html` 742,287 → 735,155 bytes;
  15,171 → 15,037 lines (BOM preserved).
- **Interop surface: 3 window function re-exposures + 1 object-identity bridge,
  0 accessor bridges:**
  - Cross-module/inline callers (2): `openAllBacklog` (sidebarNav),
    `renderAllBacklog` (guarded `render()` + `openAllBacklog`).
  - Region-generated `on*=` target (1): `_jumpToBacklogItem` (row onclick).
  - Object-identity bridge (1): `_allBacklogFilter` (handler property-writes).
  - Module-private: none (all 4 region names are externally referenced).
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY (the `.search=`/`.status=`/`.priority=` are property writes, not
    whole-var → identity bridge, not accessor); per-identifier writer scan →
    zero wholesale writes beyond the decl; zero `typeof`/`window.`-qualified
    probes; zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `esc`, `allProjects`, `openModals`, `restoreModal`, `focusModal`,
  `_clampModalSize`, `nextModalZ++` (module→inline-let write, proven
  direction), `centerModalElement`, `minimizeModal` (handler),
  `closeModalById` (handler), **`toggleDone` (handler — cross-module window
  prop from module 11 backlog-actions.js; document-order eval + runtime call
  makes ordering moot, and the tag order keeps 11 before 12 as a courtesy)**,
  `openProjectModal` + browser builtins. Strict-mode promotion audited: clean.
- **sw.js:** `SW_VERSION` `mc-push-v12` → `mc-push-v13` (no cache list by
  design; version bump only, same as modules 1–11).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/cross-backlog.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses cross-backlog.js in
    module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5391 python server.py` from the
    worktree, then killed; live :5199 never touched):
    `/static/js/cross-backlog.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 8458.
  - Headless Chromium against that server (seeded `walkthrough_done=1`) —
    **11/11 PASS, 0 console errors, 0 page errors**: 3/3 window.* interop
    callable; `window._allBacklogFilter` exposed with the default shape;
    throwaway `cbtest` project + one seeded backlog item
    (`POST /api/project/cbtest/backlog`) → real `sidebarNav('backlog')`
    rendered the `__all_backlog` modal with the seed row; **the
    object-identity bridge asserted end-to-end** — a bare property-write
    `window._allBacklogFilter.search='zzz…'` + `renderAllBacklog()` emptied
    the list ("No matching backlog items"), then `='zeta'` re-surfaced the
    seed (proves the module's `renderAllBacklog` reads the filter through the
    shared object); `_jumpToBacklogItem` invoked without throwing. Throwaway
    project deleted after.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of
    module 1; line shifted to 84 by the added const, same evaluate step);
    page boots with cross-backlog.js fulfilled, dies at the same later
    evaluate. No new breakage.
- **Commit:** module 12 (js/cross-backlog.js) = commit `3c42a6e`.

## Phase 3 — module 13: extract scheduler → `static/js/scheduler.js` (2026-06-10)

- **Region re-derived (post-module-11 numbering, shifted −121; modules 11/12
  removed lines BEFORE/AFTER but only 11 was before this region):** the
  Scheduler family is old lines 9962–10338 (`// ── Scheduler` header through
  `deleteSchedule`'s closing brace + trailing blank 10338); next header
  `// ── Schedule Banner` at 10339 stays inline. Brace-depth top-level scan:
  1 `let` (`schedulerEditId`) + 16 function decls, depth ends 0, **0 top-level
  non-decl lines** (no IIFEs/parse-time calls/listeners). 377-line span.
- **2-SEGMENT MOVE — the duplicate-`timeAgoShort` entanglement forces a hole
  (module-4-class, behavior-preservation).** The span DEFINES
  `function timeAgoShort(isoStr)` (L10151, the `'… ago'` / `'never'` variant),
  which is the **LATER of two top-level `function timeAgoShort` declarations**
  (the other is L9227, the agent-console `'…'` / `''` variant). In a classic
  script the later top-level declaration wins for ALL bare-identifier callers,
  so L10151 is the **de-facto live impl** for the inline callers at L9164
  (agent-console session time) and L11092/11100 (schedule banner). Moving
  L10151 into a deferred module would make the inline global revert to the
  L9227 variant → those inline callers would render `'5m'`/`''` instead of
  `'5m ago'`/`'never'` = **BEHAVIOR CHANGE**. So `timeAgoShort` + its flanking
  blanks (L10150–10162) **STAY INLINE** (left stranded where Scheduler was);
  the rest moves as two segments (segA 9962–10149 = header…formatScheduleTime
  close; segB 10163–10338 = showScheduleForm…deleteSchedule close + blank).
  The moved module's own `timeAgoShort(s.last_run)` calls (in
  `refreshScheduleList`) resolve through the global object to the inline
  L10151 def → the module behaves identically too. (Same class as module 4's
  `startRefresh` carve-out; same split-reassembly proof as module 8.)
- **Inbound refs re-verified (formal whole-repo scan of all 17 region names):
  cross-region callers are runtime-only** — `openScheduler` ×3 (sidebarNav
  L2646, command-palette action L2842, schedule-banner row onclick L11097),
  `formatScheduleTime` ×2 (schedule-banner Next-run line L11092/11100). Plus
  `loadScheduleRunsPage` is invoked from the **pagination onclick string**
  that the INLINE shared `renderRunsPagination` emits (so it must be
  window-exposed). `timeAgoShort`'s 2 "outside refs" are the L9164 caller +
  the L9227 duplicate decl (handled above). Whole-repo sweep: no other file
  references any region name.
- **Shared run-history is an OUTBOUND dep, NOT moved (the Scheduler/Hivemind
  hard-stop respected):** `loadScheduleRunsPage` calls `renderRunRows` (L12288)
  + `renderRunsPagination` (L12265), both in the `// ── Run history (shared
  between Scheduler + Hivemind surfaces)` section that **stays inline** —
  resolved at call time through the global scope. The Hivemind run-history
  consumer (L8833) is untouched. `refreshScheduleBanner` (L11058, Schedule
  Banner section) is likewise an inline outbound dep.
- **What moved:** old lines 9962–10149 (segA) + 10163–10338 (segB) →
  `static/js/scheduler.js`, **byte-verbatim** (split two-sided binary
  reassembly assertion: (1) `before + segA + hole + segB + after == original`;
  (2) the new index.html — with the inserted `<script>` tag removed, segA
  re-inserted before the inline hole, and segB re-inserted after it —
  `== original` byte-for-byte; interop tail append-only). Module body =
  segA + segB + tail (no blank separator where `timeAgoShort` was — cosmetic,
  the only non-byte-verbatim aspect is the *join point*, both segments are
  individually verbatim). Loaded via
  `<script type="module" src="/static/js/scheduler.js"></script>` inserted
  after the cross-backlog.js module tag, before `</body>`. Anti-FOUC bootstrap
  untouched. Diff shape: 1 insertion, 364 deletions across two hunks (the
  13-line `timeAgoShort` block remains in place).
- **Numbers:** `scheduler.js` = 19,027 bytes / 385 lines (364 moved + 21-line
  interop tail: 1 blank + 7 comment + 13 assignment; CRLF, no BOM, 213
  non-ASCII UTF-8 bytes). `index.html` 735,155 → 717,420 bytes;
  15,037 → 14,674 lines (BOM preserved).
- **Interop surface: 12 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Cross-region/inline callers (3): `openScheduler`, `formatScheduleTime`,
    `loadScheduleRunsPage` (the last via the inline pagination onclick).
  - Region-generated `on*=` targets (9): `showScheduleForm`, `hideScheduleForm`,
    `setSchedType`, `saveSchedule`, `toggleScheduleEnabled`, `toggleScheduleRuns`,
    `editSchedule`, `deleteSchedule`, `runScheduleNow`.
  - Module-private (4): `schedulerEditId` (state — zero outside refs),
    `refreshScheduleList`, `scheduleDescription`, `renderSchedTypeFields`.
  - **`timeAgoShort` is NOT exposed by this module — it stays inline** (see
    the 2-segment rationale). The tail comment documents this explicitly.
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY (handlers only CALL); `schedulerEditId` writer scan → in-region
    writes only (decl + showScheduleForm + hideScheduleForm); zero `typeof`/
    `window.`-qualified probes; zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `esc`, `API_BASE`, `openModals`, `restoreModal`, `focusModal`,
  `_clampModalSize`, `nextModalZ++` (module→inline-let write, proven
  direction), `centerModalElement`, `minimizeModal`/`closeModalById`
  (handlers), `allProjects`, `showToast`, `confirm`, `alert`, **the inline
  `timeAgoShort` (L10151, stays)**, **the inline shared `renderRunRows`/
  `renderRunsPagination` (run-history, stay) + `refreshScheduleBanner`
  (schedule-banner, stays)** + browser builtins. Strict-mode promotion
  audited: clean (zero `this`, every assignment is a declared binding or a
  `window.` property).
- **sw.js:** `SW_VERSION` `mc-push-v13` → `mc-push-v14` (no cache list by
  design; version bump only, same as modules 1–12).
- **Smoke harnesses:** added `route.fulfill` for `/static/js/scheduler.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses scheduler.js in module
    goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5391 python server.py` from the
    worktree, then SHUT DOWN; live :5199 never touched):
    `/static/js/scheduler.js` → 200, `text/javascript; charset=utf-8`,
    Content-Length 19027.
  - Headless Chromium against that server (seeded `walkthrough_done=1`) —
    **22/22 PASS, 0 console errors, 0 page errors**: all 12 window.* interop
    callable; 4/4 module-privates NOT leaked; **the duplicate-`timeAgoShort`
    behavior asserted** — bare `timeAgoShort(...)` still returns the inline
    `'5m ago'` variant (empty→`'never'`), proving the 2-segment cut preserved
    the live impl for inline + module callers alike; throwaway `schedtest`
    project (with `project_path`) → real `sidebarNav('scheduler')` opened the
    modal; form drill (`showScheduleForm` → `setSchedType('interval')` →
    `renderSchedTypeFields` drew `#sched-interval`); **`saveSchedule` created
    a REAL schedule** (interval=120, verified in `/api/schedules`) → the card
    rendered in `#schedule-list`; **Runs panel via the shared run-history**
    (`toggleScheduleRuns` → `loadScheduleRunsPage` → inline `renderRunRows`/
    `renderRunsPagination` → "No runs yet."); `deleteSchedule` removed it.
    Throwaway schedule + project cleaned up. (The schedule was created but
    never fired — zero model cost.)
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of
    module 1; line shifted to 87 by the added const, same evaluate step);
    page boots with scheduler.js fulfilled, dies at the same later evaluate.
    No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–12).

### Landmines for module 14 (remaining-core map, post-scheduler)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh
   worktrees; deferred-module timing; module-scoped-globals rule; re-derive
   boundaries + brace-depth scan; accessor-bridge handler-assigned vars via
   the formal scan; object-identity bridge for handler-property-written
   objects; 2-segment carve-out for parse-time/duplicate-def entanglements;
   don't "repair" quirks; settle CSS animations before screenshots;
   cross-module window props are normal; surgery scripts in `_scratch/*.py`;
   don't race region setTimeouts — waitForFunction on observable state;
   `node --input-type=module --check <file>` ERRORS on Node 24 — pipe via
   stdin instead).
2. **PARSE-TIME-CALLER BLOCK is now a confirmed disqualifier — check the
   inline boot tail (the lines AFTER the `fetchProjects().then(…)` `});`).**
   **System status (456L) is BLOCKED:** `fetchSystemStatus()` +
   `setInterval(fetchSystemStatus, 60000)` run at the inline-script top level
   (right after the inline `startRefresh()`), so moving `fetchSystemStatus`
   to a deferred module ReferenceError-aborts the rest of the inline boot.
   Same trap would block any feature whose function is *called* (not just
   referenced) in that top-level boot tail. Grep the boot tail for bare
   `feature()` calls before promising a move; a `setTimeout(()=>fn(),N)` or a
   call INSIDE the `.then` async callback is safe (runtime), a bare top-level
   `fn()` is not.
3. **Command Palette (520L header-to-header) is 80% FOREIGN + needs 2 accessor
   bridges — deferred.** Real family = the cmd state + `toggleCommandPalette`
   + `renderCommandResults` + the `cmd-input` listener (~98L, 2807–2903 in the
   pre-module-11 numbering, now shifted). L2905+ (`restoreModal` 13 callers,
   `sizeAgentChat`, `updateAgentStatusUI`, `guardianReset`, `refreshModalById`
   9, `refreshModal` 50) is the conversation/modal-engine CORE → store.js
   checkpoint. AND `cmdPaletteOpen` is bare-assigned by walkthrough.js (a
   STRICT module → needs the `Object.defineProperty` accessor or it throws),
   and `cmdSelectedIndex` is `++`/`=`-mutated by the SHARED inline keydown
   handler (L3306+) that ALSO does `focusedModalId`/`closeModalById` and can't
   be split out. Both primitives need accessor bridges. Doable but it is the
   most entangled of the "small" candidates; recommend pairing it with the
   store.js design pass.
4. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED
   — re-derive; the inline section is now shifted −364 more from any quoted
   pre-module-13 line ≥10339, and −121 from pre-module-11 lines ≥3700):**
   MCP family ~1,128L (manager + From-URL state machine — STOP per the brief
   if the From-URL machine is entangled with the manager state); Hivemind
   family ~1,294L (tab + dashboard + cross-project + the shared run-history —
   the run-history is now a multi-consumer inline dep; STOP per the brief);
   Update section ~360L (**ENTANGLED**: the "Update Clayrune" header's span
   ALSO contains the Power/restart/shutdown dialog `openPowerDialog`/
   `performRestart`/`showRestartingOverlay`/`performShutdown`/
   `showPoweredOffOverlay` AND the server-restart-detection block with a
   parse-time `setTimeout(()=>_checkServerRestart(),1500)` — a 2-segment or
   3-segment carve is needed, and `_handleServerRestart`→`showRestartingOverlay`
   couples the detection block to the power block; derive carefully);
   provider auth/settings ~630L; the Provider Settings section
   (`_renderProviderSettings`) is a settings-family leaf that could join a
   settings-helpers module. Process Manager is a 48-line single function (too
   small alone). The genuinely-entangled agent-panel/projects-grid core still
   WAITS for the orchestrator's js/store.js design checkpoint.
5. **The throwaway-project + REAL-write exercise recipe (extends the module-10
   read-only recipe to write-features):** for a feature that mutates project/
   schedule state, `POST /api/project/<id>` (with `project_path` if the
   feature filters on it) → drive the REAL handlers against REAL endpoints
   (create → assert in the GET → delete), overriding `window.confirm = () =>
   true` for delete paths. Zero model cost as long as no agent is dispatched
   (a created-but-never-fired schedule is free). Always clean up
   (schedule + project) in the `finally`.

## Phase 3 — module 14: extract MCP family → `static/js/mcp.js` (2026-06-10)

- **Module-13 SHA backfill:** module 13 (js/scheduler.js) = commit `7bed9d6`.
- **Candidate selection this run (cleaner-first):** of the post-scheduler queue
  the MCP family was the cleanest single win. The sizing table said
  "~1,128L (manager ~476L + From-URL state machine ~652L); may split into 2
  modules" — RE-DERIVED below: the family is **one coupled unit**, the
  header-to-header upper bound was wrong (foreign boot code was inside it), and
  it does NOT split cleanly (so it ships as ONE module).
- **Region re-derived — ONE contiguous family, NOT two modules, and the
  header-to-header span was wrong (6th miss):** the MCP family is old lines
  **12430–13474** (`// ── MCP servers` header through `deleteMCPAction`'s
  closing brace at 13473 + one trailing blank 13474). The "From URL" header at
  12906 is a SUB-section of the same family, not a separate module — the
  manager's `openMCPEditor` renders the Manual/From-URL toggle and calls
  `_mcpEditorSetMode`, and the From-URL flow stores ALL its state on the modal
  `entry._mcpUrlState` object (NOT a module-level bare `let`), so there is
  **zero shared mutable module global** between the two halves to bridge —
  splitting would only manufacture cross-module window deps for no benefit.
  **The header-to-header "1,128L" span (12430→13558 Native FCM push) is WRONG:**
  old lines **13476–13556 are FOREIGN BOOT CODE** — the same inline boot tail
  module 4 left behind: `startRefresh()` (**called at parse time**, the module-4
  trap), the filter-dropdown binding, `fetchDomains()`, the grid-layout fetch,
  density/feed/view-mode restores, `applyAdvancedFlags()`, and the eager
  service-worker registration. Those 81 lines STAY INLINE (blank 13475 +
  13476+). The MCP family proper ends at `deleteMCPAction` (13473).
- **Parse-time audit (brace-depth scan):** the manager half (12430–12905) scans
  clean — depth ends 0, 13 function decls + 2 `let` state vars (`_allMCPCache`,
  `_allMCPFilter`), **0 IIFEs / 0 top-level calls / 0 listeners**. (The combined
  scan derails to depth 5 inside the From-URL `_mcpUrlRenderPreview`'s deeply
  nested `${\`…\`}` templates — the same known scanner limitation as modules 6/7;
  the 25 region function declarations are all plain `function`/`async function`
  at col 0, verified by a separate col-0 declaration listing, and
  `node --check --input-type=module` parses the final file in module goal.)
  **No parse-time caller of any region function exists in the inline boot tail.**
- **Inbound refs re-verified (formal whole-file scan of all 25 region function
  names + 2 state vars): exactly 2 cross-module functional refs, both
  runtime-only:** `openAllMCP` (L2653, `sidebarNav('mcp')`) and
  `openAllMCPForProject` (L1851, project-modal three-dot menu generated
  `onclick`). Every OTHER region function with an external touch is a
  region-generated `on*=` handler target (resolves against window at event
  time). Whole-repo sweep (`*.py`, `*.js`, `*.html`, `*.md`, smoke fixtures):
  only `CHANGELOG.md` (prose) — no other file references any of the 27 names.
- **What moved:** old lines 12430–13474 → `static/js/mcp.js`, **byte-verbatim**
  (two-sided binary reassembly assertion: (1) `before + region + after ==
  original` proving the region is a clean contiguous byte span; (2) the new
  index.html, with the inserted `<script>` tag line removed AND the region bytes
  re-inserted at the cut point, `== original` byte-for-byte; interop tail
  append-only on the js side). Loaded via
  `<script type="module" src="/static/js/mcp.js"></script>` inserted immediately
  after the scheduler.js module tag, before `</body>`. Anti-FOUC bootstrap
  untouched (landmine 1). Diff shape: 1 insertion, 1,045 deletions.
- **Numbers:** `mcp.js` = 55,559 bytes / 1,074 lines (1,045 moved + 29-line
  interop tail: 1 blank + 11 comment + 17 assignment; CRLF, no BOM, 159
  non-ASCII UTF-8 bytes). `index.html` 717,420 → 663,402 bytes;
  14,674 → 13,630 lines (BOM preserved). Largest extraction since module 5.
- **Interop surface: 16 window function re-exposures + 1 object-identity bridge,
  0 accessor bridges:**
  - Cross-module/inline callers (2): `openAllMCP` (sidebarNav),
    `openAllMCPForProject` (project three-dot menu onclick).
  - Region-generated `on*=` handler targets (14): `loadAllMCP`, `renderAllMCP`,
    `openMCPEditor`, `_mcpToggleActive`, `_mcpResetLoadout`,
    `_mcpEditorRenderTransport`, `_mcpEditorSetMode`, `_mcpUrlPreview`,
    `_mcpUrlBack`, `_mcpUrlFieldChange`, `_mcpUrlSecretChange`, `_mcpUrlInstall`,
    `saveMCPServer`, `deleteMCPAction` (enumerated from the region's generated
    HTML `on*="fn(...)"` strings — resolve against window at event time).
  - **Object-identity bridge (1):** `_allMCPFilter` (a `{scope,project,search}`
    object). Generated handlers property-write it (`oninput="_allMCPFilter.
    search=this.value;…"`, two `onchange` for scope/project — L12480/12482/12488);
    formal wholesale-write scan (`(?<![\w$.])_allMCPFilter\s*=`) → exactly ONE
    hit (the declaration; the two in-region property-writes at 12441/12442 are
    `.`-prefixed). So a plain `window._allMCPFilter = _allMCPFilter` identity
    bridge routes every handler property-write into the module's live object —
    one source of truth, same pattern as cross-backlog's `_allBacklogFilter`
    and mobile-pairing's `_mobilePairState` (NOT an accessor — never wholesale-
    reassigned, so no getter needed).
  - **From-URL state needs NO bridge:** the entire From-URL sub-state-machine
    (`input → preview → installing → done`) lives on `entry._mcpUrlState`
    (the modal-entry object in the shared `openModals` map), not a module
    global — verified end-to-end in gating. The cleanest possible shared-state
    design; survives the move untouched.
  - Module-private (10, no outside/handler refs): `_allMCPCache` (state —
    wholesale-reassigned only INSIDE the module, zero outside reads),
    `_renderMCPRow`, `_mcpEditorStatus`, `_parseKVLines` (generic name, but the
    ref scan proved zero outside collision), `_mcpUrlRender`, `_mcpUrlRenderInput`,
    `_mcpUrlRenderPreview`, `_mcpUrlRenderInstalling`, `_mcpUrlRenderDone`,
    `_mcpUrlAppendLog`.
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY (handlers only CALL functions / property-write `_allMCPFilter` — no
    accessor bridge); per-identifier writer scan outside region → zero
    wholesale writes; zero `typeof`/`window.`-qualified probes; zero `this` in
    region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `openModals` (classic-script top-level `const` — reached via bare identifier,
  NOT a window prop), `nextModalZ++` (module→inline-let write, the proven
  module-2/4 direction), `restoreModal`, `focusModal`, `centerModalElement`,
  `_clampModalSize`, `minimizeModal`/`closeModalById` (handlers), `API_BASE`,
  `esc`, `allProjects`, `isIncognitoProject`, `showToast`, `confirm`, `alert` +
  browser builtins (`fetch`, `TextDecoder`, `URLSearchParams`, streaming
  `res.body.getReader()`). Strict-mode promotion audited: zero module-code
  `this` (all hits are HTML handler strings/comments), every assignment targets
  a declared binding or an explicit `window.` property.
- **Deferred-module timing safe:** region top level = declarations + 2 `let`
  state inits only; no IIFEs, no parse-time calls, no listener registrations.
  Both inbound refs are user-driven (sidebar nav / project menu) — never fire
  at the parse-time boot.
- **sw.js:** `SW_VERSION` `mc-push-v14` → `mc-push-v15` (no cache list by
  design; version bump only, same as modules 1–13). Added a v15 changelog line.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/mcp.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses mcp.js in module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5392 python server.py` from the
    worktree, then KILLED; live :5199 never touched): `/static/js/mcp.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 55559 (exact match),
    `Cache-Control: no-cache` + ETag; `GET /api/mcp` → this machine's real
    global MCP servers (sequential-thinking, tradingview, …).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **strictly read-only** — no MCP server created/saved/installed) —
    **43/43 PASS, 0 console errors, 0 page errors**: all 16 window.* interop
    functions callable; `window._allMCPFilter` object-identity bridge present
    with the default `{project,scope,search}` shape; all 10 module-privates NOT
    leaked to window; MCP modal opens via the REAL `sidebarNav('mcp')` path
    (`#am-list`/`#am-search`/`#am-scope` rendered); list populated from the real
    `GET /api/mcp`; **identity bridge asserted end-to-end** — a bare
    `window._allMCPFilter.search='zzzz_nomatch…'` + `renderAllMCP()` produced
    the "No MCP servers match" empty state (proves the module's `renderAllMCP`
    reads the filter through the SHARED object), then clearing it restored the
    list; New MCP editor opens (`#me-name`/`#me-transport`/`#me-mode-url`);
    **From-URL state machine** toggled via `_mcpEditorSetMode(modalId,'url')`
    rendered the `#me-url-input` (input stage) with `entry._mcpUrlState.stage
    === 'input'` stored on the modal entry; toggle back to manual restored
    `#me-manual-mode`. Screenshot eyeballed: fully styled MCP list modal (real
    servers + Delete buttons) behind the styled New-MCP-server editor (Manual/
    From URL toggle, all fields), no FOUC, mascot + sidebar styled.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined` at the same L90
    evaluate, landmine 4 of module 1); page boots with mcp.js fulfilled (gets
    past the grid/card stage), dies at the same later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–13).

### Landmines for module 15 (remaining-core map, post-MCP)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh worktrees;
   deferred-module timing; module-scoped-globals rule; re-derive boundaries +
   brace-depth scan; accessor-bridge handler-assigned vars; object-identity
   bridge for handler-property-written objects; 2-/3-segment carve for
   parse-time/duplicate-def entanglements; don't "repair" quirks; settle CSS
   animations before screenshots; cross-module window props are normal; surgery
   scripts in `_scratch/*.py`; don't race region setTimeouts;
   `node --input-type=module --check <file>` ERRORS on Node 24 — pipe via stdin).
2. **State-on-the-modal-entry is a bridge-free win — look for it.** MCP's
   From-URL machine stored ALL sub-state on `entry._mcpUrlState` (the
   `openModals` map's value object), so the manager↔From-URL coupling needed
   ZERO bridges despite being a 1,045-line two-feature region. When auditing a
   modal-centric family, check whether its mutable state lives on the entry
   object vs. a module `let` — the former survives the move untouched.
3. **`openModals` is a classic-script `const` (NOT a window prop).** Module
   code reaches it via bare identifier (lexical global). In a `page.evaluate`
   gate you must read it via `(0,eval)('openModals')`, not `window.openModals`
   (which is `undefined`). Same for any inline top-level `const`/`let` you want
   to inspect from a Playwright probe.
4. **The header-to-header span has now been WRONG 6× (modules 4, 9, 10, 13×2,
   14).** Modules 5/6/7/11/12 matched exactly. The MCP span hid 81 lines of the
   module-4 inline boot tail (`startRefresh` + SW registration). **Always read
   where the family actually ends; check the lines after the last family
   function for the `startRefresh()`/`document.getElementById(...).addEventListener`
   boot block before trusting any header-to-header count.**
5. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED
   — re-derive; the inline section is now shifted −1,044 more from any quoted
   pre-module-14 line ≥13475):**
   - **provider auth/settings (~630L) — ENTANGLED / mis-headered.** The
     "Provider Auth helpers" header (was 10604) covers ONLY ~88L of real
     provider-auth code (`PROVIDER_AUTH_KEYS`, `settingsProviderSetEnv`,
     `settingsProviderTerminalLogin`, `settingsProviderRefresh`,
     `_renderClaudeAuthStatusLine`); the lines BELOW it (was 10694–10913) are
     the **Schedule Banner** family (`refreshScheduleBanner`/`_sbRender`/
     `toggleSchedulePanel`/`_sbLoadRecent`/`_sbOpenTranscript`/etc. + two
     document-level click/keydown listeners) placed under the wrong header.
     The "Auth banner — multi-provider" header (was 10450) is a THIRD provider
     chunk. A provider module needs a careful multi-segment carve that
     SEPARATES the Schedule Banner family (which is referenced by scheduler.js's
     `refreshScheduleBanner` outbound dep + the schedule-banner inline section)
     — derive all three headers before promising a single module. The Schedule
     Banner could alternatively be its own module (it has 2 top-level
     `document.addEventListener` listeners — safe, listener-order not
     observable, same as walkthrough).
   - **Update section (~360L) — ENTANGLED (3-segment).** The "Update Clayrune"
     header (was 11309) span also holds the Power/restart/shutdown dialog
     (`openPowerDialog`/`performRestart`/`showRestartingOverlay`/
     `performShutdown`/`showPoweredOffOverlay`, header was 11302) AND the
     server-restart-detection block (header was 11252) with a **parse-time
     `setTimeout(()=>_checkServerRestart(),1500)`** boot registration, and
     `_handleServerRestart`→`showRestartingOverlay` couples detection to the
     power block. Needs a careful 2-/3-segment carve + a boot-trap decision for
     the setTimeout (move the registration into the module body, OR a thin
     `addEventListener('load', …)` shim — argue equivalence per case).
   - **System status (~456L) — BLOCKED on a parse-time setInterval boot-trap.**
     `fetchSystemStatus()` + `setInterval(fetchSystemStatus, 60000)` run at the
     inline-script top level (the boot tail). The brief's boot-trap technique
     (move the `setInterval` registration into the module body alongside
     `fetchSystemStatus`, OR a `load`-event shim) makes this MOVABLE and
     behavior-equivalent (a 60s poll starting a few hundred ms later is
     immaterial; there's no missed first-tick that matters — the inline boot
     ALSO calls `fetchSystemStatus()` once at parse time for the immediate
     paint, so that one-shot must be preserved too). Re-derive the exact span
     and the boot-tail call sites before attempting.
   - **Hivemind family (~1,294L)** — tab (was 8318) + dashboard modal (was 8520)
     + cross-project (was 12064); shares the inline run-history renderers
     (`renderRunRows`/`renderRunsPagination`, now a MULTI-consumer inline dep —
     scheduler.js also calls them via window) which **STAY INLINE** per the
     brief; carve hivemind AROUND them.
   - Provider Settings section (`_renderProviderSettings`, was 11669) is a
     settings-family leaf that could join a settings-helpers module. Process
     Manager (was 11834) is a small single-function region.
   - The genuinely-entangled agent-panel/projects-grid CORE (conversation
     model, modal/tile HTML, SSE slot management, dispatch, status resolver)
     and Command Palette's real ~98L (needs 2 accessor bridges:
     `cmdPaletteOpen` bare-assigned by walkthrough.js + `cmdSelectedIndex`
     `++`/`=`-mutated by the shared inline keydown handler) still WAIT for the
     orchestrator's js/store.js design checkpoint.

## Phase 3 — module 15: extract System status → `static/js/system-status.js` (2026-06-10)

- **Module-14 SHA backfill:** module 14 (js/mcp.js) = commit `aece9aa`.
- **Candidate selection this run:** System status was BLOCKED in the module-14
  landmines on a parse-time `setInterval(fetchSystemStatus, 60000)` boot-trap.
  The orchestrator brief explicitly green-lights the boot-trap technique
  (move the registration into the deferred module body, OR a thin `load`-event
  shim — pick + justify equivalence per case). It's now MOVABLE; chosen as the
  cleanest next candidate (self-contained feature, all 6 state vars private,
  only 4 window exposures, the only entanglement was the boot-trap).
- **Region re-derived:** old lines **9994–10448** (`// ── System status (CC
  /status equivalent)` header through the document-level outside-click
  listener's closing `});` at 10447 + one trailing blank 10448). Next header
  `// ── Auth banner — multi-provider` at 10450 stays inline. Brace-depth
  top-level scan: depth ends 0, 6 `let` state vars + 20 function decls + exactly
  **2 top-level `addEventListener` registrations** (`window` resize L10435 +
  `document` click L10440) — no IIFEs, **no parse-time function calls inside the
  region**. The two listeners are safe top-level side effects (listener-order
  not observable — same precedent as walkthrough/schedule-banner). (NOTE: the
  Schedule Banner family is pre-existingly split — its `_sbState` decl sits at
  L9984 just ABOVE this header, its functions live ~700 lines below under a
  mis-placed "Provider Auth helpers" header; NOT this module's concern, left
  exactly as-is.)
- **BOOT-TRAP relocation (fix (a), behavior-equivalent) — the one entanglement:**
  the inline boot tail had, at old lines 12681–12685, a 3-line comment + a
  parse-time one-shot `fetchSystemStatus();` + `setInterval(fetchSystemStatus,
  60000);`. `fetchSystemStatus` is module-private now, so leaving those inline
  would ReferenceError-abort the boot. **Both lines were RELOCATED byte-verbatim
  into the module body** (after the feature region, before the interop tail).
  **Equivalence argument:** a `type="module"` script is deferred, so instead of
  firing mid-parse the one-shot fetch + the 60s poll now start a few hundred ms
  later, right after document parse when the module evaluates. This is
  immaterial for a status pill: `fetchSystemStatus` is itself `async` and the
  pill renders idle ("—") until the fetch resolves — which already happened
  after parse in the original. There is **no missed first-tick that matters**;
  the one-shot is preserved (the pill still gets its initial paint), just
  slightly later. Live-validated: the relocated boot one-shot DID run from the
  module and the pill rendered content (gate below). The other inline boot lines
  (`startRefresh()` L12678, `setInterval(refreshScheduleBanner, …)` L12680) are
  untouched and unaffected — the removal is a clean 5-line excision.
- **Inbound refs re-verified (formal whole-file scan of all 20 region functions
  + 6 state vars):** outside references are exactly — `fetchSystemStatus` ×2
  (the boot-trap, RELOCATED into the module, not window-exposed),
  `toggleSysStatusPopover` ×1 (pill static `onclick` L475 → window),
  `_rerenderSysStatusSurfaces` ×1 (the token-usage refresher's
  `if (typeof _rerenderSysStatusSurfaces === 'function')` runtime guard L4343 →
  window), `_positionSysStatusPopover` ×1 (an HTML COMMENT at L571, not a code
  ref → no exposure). All 6 state vars: **ZERO outside refs → fully
  module-private**, no bridge. Region-generated `on*=` handler targets:
  `_sysStatusSwitchTab` + `refreshSystemStatus` (→ window). Whole-repo sweep
  (`*.py`/`*.js`/`*.html`/smoke fixtures): only CHANGELOG.md + this progress log
  (prose) — no other file references any region identifier.
- **What moved:** old lines 9994–10448 (feature region) + 12681–12685
  (boot-trap) → `static/js/system-status.js`, **byte-verbatim** (DUAL two-sided
  binary reassembly assertion for the two disjoint removals: (1)
  `seg_pre + region + seg_mid + boot + seg_post == original` proving both spans
  are clean disjoint byte ranges; (2) the new index.html, with the inserted
  `<script>` tag line removed AND both the region and the boot block re-inserted
  at their original cut points, `== original` byte-for-byte; the relocation
  note + interop tail are append-only on the js side, and the moved boot lines
  are byte-verbatim — the only non-verbatim aspect is the *join points*). Loaded
  via `<script type="module" src="/static/js/system-status.js"></script>`
  inserted immediately after the mcp.js module tag, before `</body>`. Anti-FOUC
  bootstrap untouched. Diff shape: 1 insertion, 460 deletions across two hunks
  (455 region + 5 boot).
- **Numbers:** `system-status.js` = 23,889 bytes / 479 lines (455 region + 5
  boot moved + 19-line notes/interop tail; CRLF, no BOM, 281 non-ASCII UTF-8
  bytes). `index.html` 663,402 → 640,717 bytes; 13,630 → 13,171 lines (BOM
  preserved).
- **Interop surface: 4 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Inbound/static-handler callers (2): `toggleSysStatusPopover` (pill onclick),
    `_rerenderSysStatusSurfaces` (typeof-guarded token-usage caller — the bare
    `typeof` resolves the window prop via the global scope chain; asserted).
  - Region-generated `on*=` handler targets (2): `_sysStatusSwitchTab` (tab
    buttons), `refreshSystemStatus` (Refresh button).
  - Module-private (16 functions + 6 state vars): `fetchSystemStatus`,
    `fetchSystemUsage`, `renderSysStatusPill`, `renderSysStatusPopover`,
    `renderSysStatusPanel`, `_positionSysStatusPopover`, `_ssRateLimitHealth`,
    `_ssRelTime`, `_ssFormatTokens`, `_ssShortenModel`, `_renderProviderHealthRows`,
    `_renderStatusTab`, `_renderConfigTab`, `_renderMcpTab`,
    `_renderMcActivitySection`, `_renderUsageTab`; `systemStatusCache`,
    `systemUsageCache`, `_sysStatusPopoverOpen`, `_sysStatusRefreshing`,
    `_sysStatusActiveTab`, `_sysUsageFetching`.
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY for all 6 state vars (handlers only CALL functions — no accessor
    bridge); per-identifier writer scan outside region → zero; zero `this` in
    region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `API_BASE`, `esc`, `setTokenMode` (token-mode helper, handler), `mcUsageCache`
  / token-usage globals, `_isMobileDevice` + appearance/format helpers + browser
  builtins (`fetch`, `Date`, `Number`, `Math`, `document.*`). The two top-level
  listeners (`window`-resize + `document`-click) moved WITH the module and fire
  on the same events. Strict-mode promotion audited: zero module-code `this`
  (all hits are HTML handler strings/comments), every assignment targets a
  declared binding or an explicit `window.` property.
- **sw.js:** `SW_VERSION` `mc-push-v15` → `mc-push-v16` (no cache list by
  design; version bump only, same as modules 1–14). Added a v16 changelog line
  documenting the boot-trap relocation.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/system-status.js`
  (contentType `text/javascript; charset=utf-8`) in BOTH `boot-smoke.mjs` and
  `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses system-status.js in
    module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
    **This is the boot-trap regression gate** — the inline boot tail no longer
    references `fetchSystemStatus`, and the SPA boots clean (a `fetchSystemStatus
    is not defined` ReferenceError would have aborted boot and failed all 5).
  - Real-server check (throwaway `MC_PORT=5392 python server.py` from the
    worktree, then KILLED; live :5199 never touched): `/static/js/system-status.js`
    → 200, `text/javascript; charset=utf-8`, Content-Length 23889 (exact match),
    `Cache-Control: no-cache` + ETag; `GET /api/system/status` →
    `{"cache_age_seconds":null}` (no agent has run on the throwaway → empty
    cache, the realistic state).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — Refresh deliberately NOT clicked, it POSTs
    `/api/system/status/refresh` which spawns a one-shot `claude` for `/status`;
    zero model cost) — **40/40 PASS, 0 console errors, 0 page errors**: all 4
    window.* interop callable; all 16 privates + 6 state vars NOT leaked to
    window; **boot-trap relocation asserted** — `#sys-status-pill` rendered
    content after the relocated `fetchSystemStatus()` ran post-parse from the
    module; popover opens via the REAL `toggleSysStatusPopover(event)` pill
    path; all 4 tabs (`status`/`config`/`mcp`/`usage`) switch via
    `_sysStatusSwitchTab` without throwing; `refreshSystemStatus` wired (not
    invoked); the bare `typeof _rerenderSysStatusSurfaces === 'function'` guard
    resolves true (global-scope reaches the window prop) and the function repaints
    without throwing; the document outside-click listener (moved with the module)
    closes the popover. Screenshot eyeballed: fully styled dashboard, status pill
    in the header, no FOUC, mascot + sidebar styled.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module
    1; line shifted to L93 by the added const, same evaluate step); page boots
    with system-status.js fulfilled, dies at the same later evaluate. No new
    breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–14).

### Landmines for module 16 (remaining-core map, post-System-status)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh worktrees;
   deferred-module timing; module-scoped-globals rule; re-derive boundaries +
   brace-depth scan; accessor-bridge handler-assigned vars; object-identity
   bridge for handler-property-written objects; multi-segment / boot-trap carve
   for parse-time entanglements; don't "repair" quirks; settle CSS animations
   before screenshots; cross-module window props are normal; surgery scripts in
   `_scratch/*.py`; don't race region setTimeouts; state-on-modal-entry is a
   bridge-free win; `openModals` is a `const` NOT a window prop — read it via
   `(0,eval)('openModals')` in a Playwright probe;
   `node --input-type=module --check <file>` ERRORS on Node 24 — pipe via stdin).
2. **The boot-trap relocation (fix (a)) is PROVEN behavior-equivalent for a
   periodic poll + idle-until-async one-shot** (System status). The recipe:
   excise the inline `fn();`/`setInterval(fn,…)` boot lines, relocate them
   byte-verbatim into the deferred module body after the region, and argue the
   few-hundred-ms-later start is immaterial (no observable first-tick). Use the
   DUAL two-sided assertion (`seg_pre + region + seg_mid + boot + seg_post ==
   original`) when a module move also relocates a disjoint boot block. The
   boot-smoke 5/5 IS the regression gate (a leftover ReferenceError aborts boot).
   **Caution:** this is safe ONLY when the relocated call has no parse-time-order
   dependency with surviving inline boot code. System status's two lines were
   isolated (between `setInterval(refreshScheduleBanner,…)` and the Fallback
   poll); audit that the boot lines you relocate are self-contained.
3. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED —
   re-derive; the inline section is now shifted −459 more from any quoted
   pre-module-15 line ≥10449, and −1,044 from pre-module-14 lines ≥13475):**
   - **Update section (~360L) — ENTANGLED (3-segment + boot-trap).** The
     "Update Clayrune" header span also holds the Power/restart/shutdown dialog
     (`openPowerDialog`/`performRestart`/`showRestartingOverlay`/
     `performShutdown`/`showPoweredOffOverlay`) AND the server-restart-detection
     block with a **parse-time `setTimeout(()=>_checkServerRestart(),1500)`**
     boot registration; `_handleServerRestart`→`showRestartingOverlay` couples
     detection↔power. ALSO note `_checkServerRestart()` is called from the
     15s-fallback `setInterval` (old L12692, now shifted) — so it's a
     MULTI-consumer inline dep, not just the parse-time setTimeout. A careful
     2-/3-segment carve + a boot-trap decision for the setTimeout (the proven
     fix-(a) relocation applies) — but check the `_checkServerRestart` fallback
     consumer stays satisfied (window-expose it). The biggest-care candidate.
   - **provider auth/settings (~630L) — ENTANGLED / mis-headered** (unchanged
     from module-14 landmines): the "Provider Auth helpers" header covers only
     ~88L; the lines below are the Schedule Banner family under the wrong header;
     "Auth banner — multi-provider" is a third chunk. The Schedule Banner could
     be its OWN clean module (2 top-level `document.addEventListener` listeners,
     safe — same posture as System status's listeners) — but its
     `refreshScheduleBanner` is an outbound dep of scheduler.js + the inline
     schedule-banner section, so window-expose it. Derive all three headers.
   - **Hivemind family (~1,294L)** — tab + dashboard modal + cross-project;
     shares the inline run-history renderers (`renderRunRows`/
     `renderRunsPagination`, multi-consumer with scheduler.js — STAY INLINE per
     the brief); carve hivemind AROUND them.
   - Provider Settings section (`_renderProviderSettings`) is a settings-family
     leaf; Process Manager is a small single-function region.
   - The genuinely-entangled agent-panel/projects-grid CORE + Command Palette's
     real ~98L (2 accessor bridges) still WAIT for js/store.js.

## Phase 3 — module 16: extract Update/Power/restart-detection → `static/js/update-power.js` (2026-06-10)

- **Module-15 SHA backfill:** module 15 (js/system-status.js) = commit `8215547`.
- **Candidate selection this run:** the Update section was flagged ENTANGLED
  (3 headers, boot-trap) in the module-15 landmines. RE-DERIVED below: the
  three headers (Server-restart detection / Power / Update Clayrune) are ONE
  entangled family that moves cleanly as a SINGLE contiguous region, and the
  parse-time boot-trap is IN-region (moves with it — fix (a) for free). Chosen
  as the cleanest tractable cluster (Hivemind is larger + shares run-history;
  provider/Schedule-Banner is mis-headered and split).
- **Region re-derived — ONE family across 3 headers, single contiguous span:**
  old lines **10797–11213** (`// ── Server-restart detection` header through
  `showPoweredOffOverlay`'s closing brace at 11212 + one trailing blank 11213).
  The "Global Settings" banner header (L10795) + its blank (L10796) stay inline
  ABOVE the region; next section `// ── Provider Settings section` (L11214)
  stays inline below. The span holds: restart-detection (`_serverStartedAt`/
  `_restartHandlingInFlight` state + `_checkServerRestart`/`_handleServerRestart`
  + the boot-trap setTimeout), Update (`refreshUpdateStatus`/
  `performClayruneUpdate`), and Power (`openPowerDialog`/`performRestart`/
  `showRestartingOverlay`/`performShutdown`/`showPoweredOffOverlay`) — bound
  together by `_handleServerRestart`→`showRestartingOverlay` and
  `performRestart`→`showRestartingOverlay`. Brace-depth top-level scan: depth
  ends 0, 2 `let` state vars + 9 function decls + exactly **1 OTHER** (the
  parse-time `setTimeout(()=>{ _checkServerRestart(); }, 1500)` at L10845).
- **BOOT-TRAP — IN-region, moves with the region (fix (a), behavior-equivalent),
  no separate relocation needed:** unlike module 15 (where the boot-trap was in
  the distant inline boot tail and had to be excised + relocated), this cluster's
  parse-time `setTimeout(()=>_checkServerRestart(),1500)` lives INSIDE the moved
  span (L10845). When the whole region becomes a deferred `type="module"`, that
  registration simply runs at module-eval time (post-parse) instead of mid-parse
  — a ~hundreds-of-ms-later one-shot seed of `_serverStartedAt` from
  `/api/system/heartbeat`. Equivalent: the seed still lands well before any
  server restart could be detected (the detection only matters on a LATER
  heartbeat whose `started_at` differs). Live-validated: after the relocated
  1.5s timer, `_checkServerRestart()` returns false on the unchanged server,
  proving the seed took and the detection runs from the module (gate below).
- **Inbound refs re-verified (formal whole-FILE scan of all 11 names + a
  WHOLE-REPO sweep — the latter caught 3 cross-module callers the file-only scan
  missed):**
  - In index.html: `_checkServerRestart` ×2 real calls (SSE-drop error handler
    L6372 `_checkServerRestart().then(…)` + the 15s fallback `setInterval` body
    L12232 `await _checkServerRestart()` — both runtime), `openPowerDialog` ×1
    (sidebar Power item static `onclick` L445). (`_handleServerRestart`'s only
    outside hit is a comment.)
  - **In `static/js/settings-drill.js` (module 6) — 3 cross-module callers:**
    `onclick="openPowerDialog()"` (L625, Settings→Server), `onclick=
    "performClayruneUpdate()"` (L632, the Update button), `refreshUpdateStatus()`
    (L650, the Server-pane render/hydration). These resolve against window at
    call time — the established cross-module pattern (settings-drill already
    consumes mobile-pairing's window exports). **The file-only refscan does NOT
    see other modules — the whole-repo `Grep` sweep is mandatory; it's why
    these 3 are exposed.**
  - Whole-repo sweep otherwise: only CHANGELOG.md + this progress log (prose).
- **What moved:** old lines 10797–11213 → `static/js/update-power.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before +
  region + after == original`; (2) the new index.html, with the inserted
  `<script>` tag line removed AND the region re-inserted at the cut point,
  `== original` byte-for-byte; interop tail append-only). Loaded via
  `<script type="module" src="/static/js/update-power.js"></script>` inserted
  immediately after the system-status.js module tag, before `</body>`. Anti-FOUC
  bootstrap untouched. Diff shape: 1 insertion, 417 deletions.
- **Numbers:** `update-power.js` = 22,326 bytes / 433 lines (417 moved + 16-line
  interop tail; CRLF, no BOM, 232 non-ASCII UTF-8 bytes). `index.html` 640,717
  → 619,515 bytes; 13,171 → 12,755 lines (BOM preserved).
- **Interop surface: 6 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Inbound/cross-module callers (4): `_checkServerRestart` (SSE-drop handler +
    15s fallback poll — both inline, runtime), `openPowerDialog` (sidebar Power
    + settings-drill.js), `performClayruneUpdate` (settings-drill.js Update
    button), `refreshUpdateStatus` (settings-drill.js render/hydration).
  - Region-generated `on*=` handler targets (2): `performRestart`,
    `performShutdown` (the power dialog's Restart / Shut-down buttons).
  - Module-private (3 functions + 2 state vars): `_handleServerRestart` (called
    only by `_checkServerRestart`), `showRestartingOverlay` (called only by
    `_handleServerRestart` + `performRestart` — in-region), `showPoweredOffOverlay`
    (called only by `performShutdown` — in-region); `_serverStartedAt`,
    `_restartHandlingInFlight` (zero outside refs, zero handler-assignment).
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY for both state vars (handlers only CALL — no accessor bridge); zero
    `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `_saveOpenModalsSnapshot` (L1997) + `_flushModalPrefs` (L1983) (modal-prefs
  helpers, store.js territory, stay inline), `openModals` (`const`, bare
  identifier), `nextModalZ++`, `_clampModalSize`, `centerModalElement`,
  `focusModal`, `closeModalById` (handler), `esc`, `API_BASE`, `showToast` +
  browser builtins (`fetch`, `setTimeout`, `document.*`, `window.location`).
  Strict-mode promotion audited: zero module-code `this` (all hits are HTML
  handler strings/comments), every assignment targets a declared binding or an
  explicit `window.` property.
- **sw.js:** `SW_VERSION` `mc-push-v16` → `mc-push-v17` (no cache list by
  design; version bump only, same as modules 1–15). Added a v17 changelog line.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/update-power.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses update-power.js in module
    goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
    (Boot-trap regression: the inline boot no longer has the parse-time
    `_checkServerRestart` seed; the SPA boots clean. The 15s fallback's bare
    `_checkServerRestart` call resolves the window prop at its +15s fire, long
    after module eval.)
  - Real-server check (throwaway `MC_PORT=5392 python server.py` from the
    worktree, then KILLED; live :5199 never touched): `/static/js/update-power.js`
    → 200, `text/javascript; charset=utf-8`, Content-Length 22326 (exact match),
    `Cache-Control: no-cache`; `GET /api/system/update/status` → real git status
    (branch wt-fe-mech2, has_local_changes true); `GET /api/system/heartbeat` →
    `started_at` (the seed value).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **STRICTLY read-only** — the restart/shutdown/update buttons are DESTRUCTIVE,
    so a hard route-guard ABORTED + asserted-zero on `/api/system/restart`,
    `/shutdown`, `/update/run`, `/update/apply`; we NEVER clicked them and never
    called `performRestart`/`performShutdown`/`performClayruneUpdate`) —
    **19/19 PASS, 0 console errors, 0 page errors, NO destructive endpoint hit**:
    all 6 window.* interop callable; all 5 privates (incl. 2 state vars +
    `showRestartingOverlay`/`showPoweredOffOverlay`) NOT leaked; **boot-trap
    relocation asserted** — after the relocated 1.5s timer, `_checkServerRestart()`
    returns false on the unchanged server (proves the in-region setTimeout seeded
    `_serverStartedAt` post-parse and the detection runs from the module), and a
    second call is idempotent-false; `refreshUpdateStatus()` populated the
    Update hint + button from the real `/api/system/update/status` (the
    has_local_changes → "Blocked" state); `openPowerDialog()` rendered the power
    modal (read-only GET `/api/system/restart/status` for the active-flow list)
    with the `performRestart(…)`/`performShutdown(…)` buttons, then closed
    without confirming. Screenshot eyeballed: the Update status line + Blocked
    button rendered from the real endpoint; dashboard fully styled, no FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module
    1; line shifted to L96 by the added const, same evaluate step); page boots
    with update-power.js fulfilled, dies at the same later evaluate. No new
    breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–15).

### Landmines for module 17 (remaining-core map, post-Update/Power)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap for
   `static/js/**`; bg-framing-check broken at base; CRLF+BOM binary surgery;
   build-macos.spec bundles static/ wholesale; npm install in fresh worktrees;
   deferred-module timing; module-scoped-globals rule; re-derive boundaries +
   brace-depth scan; accessor-bridge handler-assigned vars; object-identity
   bridge for handler-property-written objects; multi-segment / boot-trap carve
   for parse-time entanglements; in-region parse-time setTimeout/setInterval
   moves WITH the region and becomes fix-(a) for free; don't "repair" quirks;
   settle CSS animations before screenshots; cross-module window props are
   normal; surgery scripts in `_scratch/*.py`; state-on-modal-entry is a
   bridge-free win; `openModals` is a `const` — `(0,eval)('openModals')` in a
   Playwright probe; `node --input-type=module --check <file>` ERRORS on Node 24
   — pipe via stdin).
2. **WHOLE-REPO `Grep` sweep is MANDATORY, not optional — the file-only refscan
   misses cross-module callers.** Module 16's `openPowerDialog`/
   `performClayruneUpdate`/`refreshUpdateStatus` are called from
   `settings-drill.js` (module 6), which the index.html-only `refscan.py` does
   NOT see. Always run the repo-wide `Grep` over `!**/index.html` before
   finalizing the window-exposure list — an already-extracted sibling module is
   a real consumer.
3. **DESTRUCTIVE-feature gating recipe (reuse for any restart/delete/install
   feature):** add a Playwright `page.route('**/<destructive-endpoint>', r =>
   { hit.push(...); r.abort(); })` HARD GUARD up front and assert `hit.length
   === 0` at the end; drive ONLY the read-only paths (GET-backed renders, modal
   OPEN, the detection/seed probe) and NEVER click the action button or call its
   handler. For the modal-OPEN assertion, detect the action buttons by their
   generated `onclick` substring (`performRestart(`/`performShutdown(`) rather
   than clicking.
4. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED —
   re-derive; the inline section is now shifted −416 more from any quoted
   pre-module-16 line ≥11214):**
   - **Hivemind family (~1,294L)** — tab (was 8318) + dashboard modal (was 8520)
     + cross-project (was 11609 post-m15). Shares the inline run-history
     renderers (`renderRunRows`/`renderRunsPagination` — multi-consumer with
     scheduler.js, STAY INLINE per the brief); carve hivemind AROUND them. Also
     check the "Hivemind worker popover" (was 4981) — it may be part of the
     agent-panel core (SSE/conversation-adjacent), audit before including.
     Largest remaining non-core family; expect a multi-segment carve.
   - **provider auth/settings (~630L) — ENTANGLED / mis-headered** (unchanged):
     the "Provider Auth helpers" header (was 10149 post-m15) covers ~88L; the
     lines below are the Schedule Banner family under the wrong header; the
     "Auth banner — multi-provider" (was 9995) is a third chunk. The Schedule
     Banner could be its OWN clean module (2 top-level `document.addEventListener`
     — safe, same as System status), but its `refreshScheduleBanner` is an
     outbound dep of scheduler.js + the inline schedule-banner section, AND its
     own `setInterval(refreshScheduleBanner, 60000)` is a parse-time boot-trap
     in the inline tail (fix (a) relocation, same as System status). Derive all
     three headers; the cleanest sub-extraction is probably the multi-provider
     Auth banner OR the Schedule Banner as standalone modules.
   - Provider Settings section (`_renderProviderSettings`, was 11214) is a
     settings-family leaf (one render function); Process Manager is a small
     single-function region.
   - The genuinely-entangled agent-panel/projects-grid CORE (conversation
     model, modal/tile HTML, SSE slot management, dispatch, status resolver,
     the Hivemind-worker-popover if it's SSE-coupled) + Command Palette's real
     ~98L (2 accessor bridges: `cmdPaletteOpen` + `cmdSelectedIndex`) still
     WAIT for the orchestrator's js/store.js design checkpoint.

## Phase 3 — module 17: extract provider-auth → `static/js/provider-auth.js` (2026-06-10)

- **Module-16 SHA backfill:** module 16 (js/update-power.js) = commit `4b56b9d`.
- **Candidate selection this run:** the "provider auth/settings (~630L)" named
  candidate was flagged ENTANGLED / mis-headered. RE-DERIVED below: the clean
  carveable piece is the provider-auth FAMILY (multi-provider Auth banner +
  Provider Auth helpers) = ONE contiguous region; the Schedule Banner family
  (mis-placed under the "Provider Auth helpers" header) stays inline. Chosen
  over Hivemind (larger, multi-segment, shares run-history) and the Schedule
  Banner (its own parse-time boot-trap + outbound-dep coupling to scheduler.js).
- **Region re-derived — Auth-banner + Provider-Auth-helpers are ONE family,
  single contiguous span:** old lines **9995–10237** (the `// ── Auth banner —
  multi-provider` header through `_renderClaudeAuthStatusLine`'s close at 10236
  + one trailing blank 10237). The "Provider Auth helpers" header (L10149) is a
  SUB-section of the same family — `settingsClaudeAuthCheck` (auth-banner half)
  calls both `_renderAuthBanner` AND `_renderClaudeAuthStatusLine`
  (provider-helpers half), binding the two. Brace-depth top-level scan: depth
  ends 0, 3 state decls (`_authBannerDismissed`/`_authBannerLastReason` `let` +
  `PROVIDER_AUTH_KEYS` `const`) + 13 function decls, **0 OTHER** (declarations
  only — no IIFEs, no parse-time calls, no listeners; the cleanest region body
  since modules 5/6/7). The Schedule Banner `_sbState` (L9984, ABOVE this
  header) and its functions (`refreshScheduleBanner` at L10239, BELOW) STAY
  inline — this region lifts the provider-auth middle out from between the two
  Schedule-Banner halves.
- **PARSE-TIME TRAP + the one inline shim (boot-trap fix (b), the first non-move
  edit in this track):** `refreshAuthStatus` is referenced at PARSE TIME by the
  inline `startRefresh()` (which runs at parse time, L11806-ish) via
  `setInterval(refreshAuthStatus, 90000)` inside its body. Moving
  `refreshAuthStatus` to a deferred module would make that bare identifier
  resolve to `undefined` at `startRefresh()`'s parse-time execution →
  `setInterval(undefined, 90000)` = the 90s auth poll silently dies = BEHAVIOR
  CHANGE. **Fix (b):** a 1-line inline edit to `startRefresh` (NOT part of the
  moved region) — `setInterval(refreshAuthStatus, 90000)` →
  `setInterval(() => window.refreshAuthStatus && window.refreshAuthStatus(),
  90000)`. The arrow defers the lookup to each 90s tick (runtime, long after the
  module evaluates), so the poll still fires at +90s. Behavior-equivalent (the
  first tick is at +90s either way; the function is loaded by then) — this is
  the brief's boot-trap fix (b) applied to a registration BURIED inside a
  surviving inline function rather than a standalone top-level one. The 4
  remaining `refreshAuthStatus` references (SSE-error handlers L6305/6343,
  fetchProjects `.then` callback L11790) are all RUNTIME → satisfied by the
  window export alone. **This is the ONLY inline line changed outside a moved
  region across modules 1–17; it is documented, minimal, and provably
  behavior-equivalent.**
- **Inbound refs re-verified (formal whole-FILE refscan + WHOLE-REPO sweep):**
  - In index.html: `refreshAuthStatus` ×4 (the parse-time `startRefresh`
    `setInterval` → shimmed; 2 SSE-error handlers + 1 fetchProjects callback →
    runtime), `dismissAuthBanner`/`claudeAuthenticate`/`claudeAuthRecheck` ×1
    each (the STATIC auth-banner HTML onclicks at L498–500),
    `settingsClaudeLogin`/`settingsClaudeAuthCheck`/`settingsProviderSetEnv`/
    `settingsProviderTerminalLogin`/`settingsProviderRefresh` (generated onclicks
    in the inline **Provider Settings section** `_renderProviderSettings`,
    L10839–10860 — runtime), and **`PROVIDER_AUTH_KEYS` read cross-region** at
    L10847 (`const envKey = PROVIDER_AUTH_KEYS[p.name]` inside the inline
    `_renderProviderSettings`).
  - **Whole-repo sweep:** `settings-drill.js` hit is a COMMENT only (mentions
    `settingsProviderRefresh` in an `openSettings` interop note) — no code
    caller. Otherwise CHANGELOG + this log (prose). No live cross-module caller.
- **What moved:** old lines 9995–10237 → `static/js/provider-auth.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before +
  region + after == original`; (2) the new index.html, with the inserted
  `<script>` tag line removed AND the region re-inserted at the cut point,
  `== original` byte-for-byte — asserted BEFORE the separate inline shim edit,
  so the region move itself is provably verbatim; interop tail append-only).
  Loaded via `<script type="module" src="/static/js/provider-auth.js"></script>`
  inserted immediately after the update-power.js module tag, before `</body>`.
  Anti-FOUC bootstrap untouched. Diff shape: 1 insertion (tag) + 243 deletions
  (region) + the 1-line shim edit (4 comment lines + 1 changed line in
  `startRefresh`).
- **Numbers:** `provider-auth.js` = 11,503 bytes / 266 lines (243 moved +
  23-line interop tail; CRLF, no BOM, 258 non-ASCII UTF-8 bytes). `index.html`
  619,515 → 609,859 bytes by the region move; then the shim Edit added 4 comment
  lines → final 12,517 lines (BOM preserved, all CRLF).
- **Interop surface: 9 window function re-exposures + 1 window const, 0 accessor
  bridges, 0 identity bridges:**
  - Inbound/static-HTML/cross-region callers (6 fns + 1 const):
    `refreshAuthStatus` (startRefresh shim + SSE + fetchProjects),
    `dismissAuthBanner`/`claudeAuthenticate`/`claudeAuthRecheck` (static
    auth-banner HTML), `settingsClaudeLogin`/`settingsClaudeAuthCheck` (inline
    Provider Settings section), **`PROVIDER_AUTH_KEYS`** (read-only const read by
    the inline `_renderProviderSettings` at render time — window-exposed so the
    bare read resolves; it's never written, so a plain `window.X = X` suffices).
  - Inline Provider-Settings-section generated onclicks (3): `settingsProviderSetEnv`,
    `settingsProviderTerminalLogin`, `settingsProviderRefresh`.
  - Module-private (4 fns + 2 state vars): `_renderAuthBanner`, `_authBannerMessage`,
    `_renderClaudeAuthStatusLine`, `refreshProviderAuthStatus`;
    `_authBannerDismissed`, `_authBannerLastReason` (zero outside refs, zero
    handler-assignment).
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`)
    EMPTY for both state vars; the region itself generates NO `on*=` handlers
    (the auth-banner buttons' onclicks live in the static HTML, outside the
    region); zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `API_BASE`, `esc`, `showToast`, `alert`/`confirm`, `openModals` (`const`,
  bare), `closeModalById`, `openSettings`/`refreshSettings` (the
  `settingsProviderRefresh` fallback), `_agentProviders` (the provider list
  global — read for display names), `setTimeout` + browser builtins. Strict-mode
  promotion audited: zero module-code `this`; every assignment targets a declared
  binding or an explicit `window.` property.
- **sw.js:** `SW_VERSION` `mc-push-v17` → `mc-push-v18` (no cache list by
  design; version bump only). Added a v18 changelog line documenting the shim.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/provider-auth.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses provider-auth.js in module
    goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
    **This is the shim regression gate** — `startRefresh()` runs at boot with the
    deferred `() => window.refreshAuthStatus && …` form; the SPA boots clean (a
    leftover bare `refreshAuthStatus` would have captured undefined, but boot
    would still pass — so the shim's *behavior* is asserted in the Chromium gate
    below, not here; here we only confirm no boot throw).
  - Real-server check (throwaway `MC_PORT=5392 python server.py`, then KILLED;
    live :5199 never touched): `/static/js/provider-auth.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 11503 (exact match),
    `Cache-Control: no-cache`; `GET /api/claude/auth-status` → `{ok:true}` (this
    machine's claude is signed in → banner hidden).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — a hard route-guard ABORTED + asserted-zero on
    `/api/agent/provider/*/env`, `/login-launch`, `/api/claude/login-launch`;
    Save/Launch/Sign-in NEVER clicked) — **28/28 PASS, 0 console errors, 0 page
    errors, NO side-effecting endpoint hit**: 9 window fns + `PROVIDER_AUTH_KEYS`
    const exposed (`.gemini === 'GEMINI_API_KEY'`); all 6 privates (incl. 2 state
    vars) NOT leaked; **the shim FORM asserted** — `() => window.refreshAuthStatus
    && window.refreshAuthStatus()` is callable and drove the real
    `/api/claude/auth-status` (ok:true → banner hidden); the **not-ok render
    path** (stubbed `{ok:false, reason:'not_logged_in'}`) showed the banner via
    `_renderAuthBanner` with the correct `_authBannerMessage` text
    ("Claude isn't signed in…"); `dismissAuthBanner()` hid it; **dismiss-sticky-
    per-reason** verified (same reason stays hidden after re-fetch);
    `settingsClaudeAuthCheck`/`settingsProviderRefresh` wired. Screenshot
    eyeballed: dashboard fully styled, banner correctly hidden, no FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module
    1; line shifted to L99, same evaluate step); page boots with provider-auth.js
    fulfilled, dies at the same later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–16).

### Landmines for module 18 (remaining-core map, post-provider-auth)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap; bg-
   framing-check broken at base; CRLF+BOM binary surgery; build-macos.spec
   bundles static/ wholesale; npm install in fresh worktrees; deferred-module
   timing; module-scoped-globals rule; re-derive boundaries + brace-depth scan;
   accessor-bridge handler-assigned vars; object-identity bridge for
   handler-property-written objects; multi-segment / boot-trap carve for
   parse-time entanglements; in-region setTimeout/setInterval moves WITH the
   region; **buried parse-time registrations inside a surviving inline function
   need a 1-line inline deferral shim — fix (b) — NOT a region change**;
   WHOLE-REPO grep is mandatory; don't "repair" quirks; cross-module window
   props normal; `(0,eval)('openModals')` in Playwright probes; destructive-
   feature route-guard recipe; `node --input-type=module --check <file>` ERRORS
   on Node 24 — pipe via stdin).
2. **A cross-region read of a `const` needs window exposure too.**
   `PROVIDER_AUTH_KEYS` is read (not just functions called) by the inline
   `_renderProviderSettings` — a top-level `const` is visible to inline code via
   the global scope, but once it moves to a module the inline reader needs
   `window.PROVIDER_AUTH_KEYS`. The refscan flags these as plain outside hits
   (no WHOLESALE-WRITE/prop-write marker since they're reads) — don't overlook a
   read-only cross-region binding; window-expose it (no bridge needed, it's
   never written).
3. **`startRefresh` is the recurring parse-time hazard.** It is called at parse
   time (inline boot) and its body references feature functions
   (`refreshAuthStatus` here; `refreshAuthStatus` was the only one this round).
   ANY future module whose function is referenced inside `startRefresh`'s body
   (or any other parse-time-executed inline function) needs the same 1-line
   deferral shim. Grep `startRefresh` (and the inline boot tail) for the
   region's function names before moving.
4. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED —
   re-derive; the inline section is now shifted −242 more from any quoted
   pre-module-17 line ≥10238 (then +4 from the shim comment); the Schedule
   Banner functions are now at ~9997+ after this removal):**
   - **Schedule Banner family** — `_sbState` (was 9984) + functions
     (`refreshScheduleBanner`/`_sbRender`/`toggleSchedulePanel`/`_sbLoadRecent`/
     `_sbOpenTranscript`/`_sbOpenAgentLog`/etc.) now CONTIGUOUS after the
     provider-auth removal (state + functions were split AROUND provider-auth;
     they're adjacent again). 2 top-level `document.addEventListener` (click +
     Escape — safe) + a parse-time `setInterval(refreshScheduleBanner, 60000)`
     in the inline boot tail (fix (a) relocation OR fix (b) shim) AND a parse-
     time/boot reference — AND `refreshScheduleBanner` is an OUTBOUND dep of
     scheduler.js (window-exposed already) + the inline schedule-banner render.
     Also `formatScheduleTime` (scheduler.js, inline) is called by `_sbRender`.
     A tractable next module with one boot-trap; derive carefully.
   - **Provider Settings section** (`_renderProviderSettings`, the inline reader
     of `PROVIDER_AUTH_KEYS`/`settingsProvider*`) — a settings-family leaf (one
     render function + maybe helpers). Now that provider-auth's helpers are a
     module, this section's only deps are window props (already bridged) — could
     be a small clean module or join a settings-helpers grouping.
   - **Hivemind family (~1,294L)** — tab + dashboard modal + cross-project;
     shares the inline run-history renderers (STAY INLINE); the "Hivemind worker
     popover" (was 4981) sits in the agent-panel/conversation zone — audit
     whether it's SSE/store.js-coupled before including. Largest remaining
     non-core family.
   - Process Manager is a small single-function region.
   - The genuinely-entangled agent-panel/projects-grid CORE + Command Palette's
     real ~98L (2 accessor bridges) still WAIT for js/store.js.

## Phase 3 — module 18: extract Schedule Banner → `static/js/schedule-banner.js` (2026-06-10)

- **Module-17 SHA backfill:** module 17 (js/provider-auth.js) = commit `4be9b19`.
- **Candidate selection this run:** the Schedule Banner family became CONTIGUOUS
  after module 17 lifted the provider-auth middle out from between its two halves
  (`_sbState` const above, functions below). Chosen as the clean next module (one
  standalone-line boot-trap — fix (a), like System status — vs. Hivemind's larger
  multi-segment carve).
- **Region re-derived:** old lines **9975–10216** (the `// ── Schedule Banner`
  header through `_sbOpenAgentLog`'s close at 10198 + the two top-level
  `document.addEventListener` (click L10201 + Escape-keydown L10210) + 1 trailing
  blank 10216). Next section `// ── New Project Creation` (L10218) stays inline.
  Brace-depth top-level scan: depth ends 0, `_sbState` const + 9 function decls +
  exactly **2 OTHER** (the click + keydown listeners — safe, listener order not
  observable; same posture as System status / walkthrough).
- **BOOT-TRAP relocation (fix (a), standalone line — like module 15):** the
  inline boot tail had, at old lines 11568–11569, a `// Refresh schedule banner
  every 60s` comment + `setInterval(refreshScheduleBanner, 60000)`. Since
  `refreshScheduleBanner` is now in a deferred module, this RELOCATES byte-verbatim
  into the module body (a clean standalone excision, NOT buried in a function like
  module 17's `startRefresh` case). Equivalent: the 60s poll starts a few hundred
  ms post-parse; the banner is non-critical and renders from
  `refreshScheduleBanner`'s async `/api/schedules` fetch. The initial paint is
  preserved by the INLINE `fetchProjects().then(…)` callback's
  `refreshScheduleBanner()` (L11550, runtime — now resolves `window.refreshScheduleBanner`).
- **Inbound refs re-verified (formal whole-FILE refscan + WHOLE-REPO sweep):**
  - In index.html: `refreshScheduleBanner` ×2 — the fetchProjects `.then`
    initial-paint callback (L11550, runtime) + the boot `setInterval` (L11569,
    relocated). All other region names (`_sbRender`/`_sbLoadRecent`/`_sbFormatAbs`/
    `_sbState` + the handler targets) have ZERO non-region, non-handler refs.
  - **WHOLE-REPO sweep:** `scheduler.js` (module 13) calls `refreshScheduleBanner()`
    **×3** (in `saveSchedule`/`toggleScheduleEnabled`/`deleteSchedule`, runtime,
    after schedule mutations) — cross-module callers that resolve
    `window.refreshScheduleBanner` at click time. scheduler.js loads BEFORE this
    module (tag order), but the calls are runtime (button clicks), so ordering is
    moot. Otherwise CHANGELOG + this log (prose).
- **What moved:** old lines 9975–10216 (region) + 11568–11569 (boot-trap) →
  `static/js/schedule-banner.js`, **byte-verbatim** (DUAL two-sided binary
  reassembly assertion: (1) `seg_pre + region + seg_mid + boot + seg_post ==
  original`; (2) new index.html with the inserted `<script>` tag removed AND both
  the region and the boot block re-inserted at their original cut points,
  `== original` byte-for-byte; relocation note + interop tail append-only). Loaded
  via `<script type="module" src="/static/js/schedule-banner.js"></script>`
  inserted after the provider-auth.js module tag, before `</body>`. Diff shape:
  1 insertion, 244 deletions across two hunks (242 region + 2 boot).
- **Numbers:** `schedule-banner.js` = 10,235 bytes / 266 lines (242 region + 2
  boot moved + 22-line notes/interop tail; CRLF, no BOM, 122 non-ASCII UTF-8
  bytes). `index.html` 610,233 → 601,313 bytes; 12,517 → 12,274 lines (BOM
  preserved).
- **Interop surface: 6 window function re-exposures, 0 accessor bridges,
  0 identity bridges:**
  - Inbound/cross-module callers (1): `refreshScheduleBanner` (inline
    fetchProjects initial paint + scheduler.js ×3 mutations — all runtime).
  - Region-generated `on*=` handler targets (5): `toggleSchedulePanel`,
    `_sbSetTab`, `_sbSetWindow`, `_sbOpenTranscript`, `_sbOpenAgentLog` (the last
    two via the `${handler}` template-variable indirection in `_sbRender`'s recent
    rows — caught by scanning template-literal `'_sb…('`/`` `_sb…( `` forms, not
    just literal `on*=`).
  - Module-private (3 fns + 1 const): `_sbRender`, `_sbLoadRecent`, `_sbFormatAbs`,
    `_sbState` (`const`, property-mutated only by region code; zero outside refs,
    zero generated-handler assignment → no bridge).
  - Formal scans: generated-handler whole-var ASSIGNMENT scan for `_sbState`
    EMPTY; zero `this` in region code.
- **Pre-existing quirk discovered + BYTE-PRESERVED (NOT repaired):** with 0
  upcoming schedules, `_sbRender` early-returns (`if (!_sbState.upcoming.length &&
  !_sbState.open) { banner.classList.add('hidden'); return; }`) and does NOT
  regenerate `banner.innerHTML` — so on close the stale inner `#sb-panel` keeps
  its `.open` class while the PARENT `#schedule-banner` goes `hidden`
  (display:none). The real "closed" signal with 0 schedules is
  `#schedule-banner.hidden`, not `#sb-panel.open`. This is unchanged behavior;
  the gate asserts the parent re-hide (see below). (A move must not "fix" this.)
- **Outbound deps** (resolve at call time through the shared global scope):
  `API_BASE`, `esc`, `formatScheduleTime` (scheduler.js window export — called by
  `_sbRender`), `openScheduler` (scheduler.js window export — banner-row onclick),
  `openTranscriptViewer`, `openProjectModal`, `modalActiveTab`,
  `_providerBadge`/`_sbFormatAbs` + browser builtins. Strict-mode promotion
  audited: zero module-code `this`; every assignment targets a declared binding or
  an explicit `window.` property.
- **sw.js:** `SW_VERSION` `mc-push-v18` → `mc-push-v19` (no cache list by design;
  version bump only). Added a v19 changelog line documenting the boot relocation.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/schedule-banner.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses schedule-banner.js in
    module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered
    (boot-trap regression: the standalone `setInterval(refreshScheduleBanner,…)`
    is gone from inline; the SPA boots clean).
  - Real-server check (throwaway `MC_PORT=5392 python server.py`, then KILLED;
    live :5199 never touched): `/static/js/schedule-banner.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 10235 (exact match),
    `Cache-Control: no-cache`; `GET /api/schedules` → `[]`, `GET /api/recent-runs`
    → `{runs:[]}` (the empty state).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — no agent dispatch, zero model cost) — **22/22 PASS, 0 console
    errors, 0 page errors**: 6 window.* interop callable; 4 module-privates
    (incl. `_sbState`) NOT leaked; `refreshScheduleBanner()` ran against the real
    `/api/schedules` (banner correctly STAYS hidden with 0 schedules — the
    byte-preserved quirk); `toggleSchedulePanel` opened the dropdown (un-hides +
    renders `#sb-trigger` + the Recent/Upcoming tabs, kicks `_sbLoadRecent` →
    `/api/recent-runs`); `_sbSetTab(upcoming)`/`_sbSetTab(recent)` switch without
    throwing; `_sbSetWindow(24)` re-fetches the 24h window without throwing; the
    **outside-click document listener (moved with the module) re-hides
    `#schedule-banner`** (the correct close signal with 0 schedules). Screenshot
    eyeballed: dashboard fully styled, banner correctly hidden, no FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module
    1; line shifted to L102, same evaluate step); page boots with
    schedule-banner.js fulfilled, dies at the same later evaluate. No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–17).

### Landmines for module 19 (remaining-core map, post-Schedule-Banner)

1. All prior landmines still apply verbatim (anti-FOUC inline bootstrap;
   route.fulfill in BOTH harnesses per new js file; CI path-filter gap; bg-
   framing-check broken at base; CRLF+BOM binary surgery; build-macos.spec
   bundles static/ wholesale; npm install in fresh worktrees; deferred-module
   timing; module-scoped-globals rule; re-derive boundaries + brace-depth scan;
   accessor-bridge handler-assigned vars; object-identity bridge for handler-
   property-written objects; multi-segment / boot-trap carve (in-region setTimeout/
   setInterval moves WITH the region = fix (a); a standalone inline-tail boot line
   relocates into the module = fix (a); a buried-in-an-inline-function registration
   needs a 1-line deferral shim = fix (b)); WHOLE-REPO grep mandatory; a
   cross-region `const` READ needs window exposure; **scan template-variable
   `${handler}` onclicks (`'_fn('`/`` `_fn( ``), not just literal `on*=`, for the
   full handler set**; don't "repair" pre-existing quirks (e.g. `_sbRender`'s
   0-schedule early-return that leaves a stale inner `#sb-panel.open`); cross-
   module window props normal; `(0,eval)('openModals')` in Playwright probes;
   destructive-feature route-guard recipe; `node --input-type=module --check
   <file>` ERRORS on Node 24 — pipe via stdin).
2. **Remaining candidate queue (header-to-header upper bounds, refs UNVERIFIED —
   re-derive; the inline section is now shifted −243 more from any quoted
   pre-module-18 line ≥10217):**
   - **Hivemind family (~1,294L)** — the LARGEST remaining non-core family: tab
     (was 8318) + dashboard modal (was 8520) + cross-project (was 11192, now
     shifted). Shares the inline run-history renderers (`renderRunRows`/
     `renderRunsPagination` — multi-consumer with scheduler.js, STAY INLINE per
     the brief); carve hivemind AROUND them. The "Hivemind worker popover" (was
     4981) sits in the agent-panel/conversation zone (between Agent Panel L4854
     and Conversation model L5061) — likely SSE/store.js-coupled; AUDIT whether
     it's separable before including (it may belong to the store.js core, not the
     Hivemind feature module). Expect a multi-segment carve + a careful
     run-history-stays-inline boundary. This is the last big non-core win.
   - **Provider Settings section** (`_renderProviderSettings`) — a settings-family
     leaf; its deps on `PROVIDER_AUTH_KEYS`/`settingsProvider*` are now all window
     props (bridged by module 17), so it could move cleanly as a small module or
     join a settings-helpers grouping.
   - Process Manager (small single-function region).
   - The agent-panel/projects-grid CORE (conversation model, modal/tile HTML, SSE
     slot management, dispatch, status resolver, the Hivemind-worker-popover if
     SSE-coupled) + Command Palette's real ~98L (2 accessor bridges:
     `cmdPaletteOpen` + `cmdSelectedIndex`) still WAIT for the orchestrator's
     js/store.js design checkpoint. Once the non-core queue (Hivemind + Provider
     Settings + Process Manager) is exhausted, the remainder IS the store.js pass.

## Phase 3 — module 19: extract Provider Settings section → `static/js/provider-settings.js` (2026-06-10)

- **Module-18 SHA backfill:** module 18 (js/schedule-banner.js) = commit `f62c833`.
- **Candidate selection this run:** a small clean leaf (the `_renderProviderSettings`
  Settings block), now fully bridgeable since module 17 made all its provider-action
  deps window props. Chosen over the larger Hivemind carve (whose worker popover is
  store.js-coupled — see landmines).
- **Region re-derived — ONE function (header-to-header mismatch, 7th time):** old
  lines **10312–10413** (the `// ── Provider Settings section` header through
  `_renderProviderSettings`'s close at 10412 + one trailing blank 10413). The
  header-to-header span (to Process Manager at 10477) ALSO contains the GENERIC
  settings helpers `saveSetting`/`toggleSetting`/`setBriefRepliesMode`/
  `saveModelChoice` (10414+) — shared settings infrastructure used by virtually
  every settings control app-wide (store.js/settings-core territory) — those STAY
  inline. Brace-depth top-level scan: depth ends 0, exactly 1 function decl
  (`_renderProviderSettings`), 0 OTHER. (The "Global Settings" banner header at
  L10310 stays inline above.)
- **Inbound refs re-verified (formal whole-FILE refscan + WHOLE-REPO sweep):**
  the index.html-internal refscan found ZERO outside refs — but the **WHOLE-REPO
  sweep** caught the real caller: `settings-drill.js` (module 6) interpolates
  `${_renderProviderSettings(cfg)}` at L371 inside `_renderSettings`'s template
  (runtime, when Settings renders) → cross-module window caller. (Mandatory-grep
  lesson again: the file-only scan misses sibling-module callers.) Otherwise only
  prose hits.
- **What moved:** old lines 10312–10413 → `static/js/provider-settings.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before + region
  + after == original`; (2) the new index.html, with the inserted `<script>` tag
  line removed AND the region re-inserted at the cut point, `== original`
  byte-for-byte; interop tail append-only). Loaded via
  `<script type="module" src="/static/js/provider-settings.js"></script>` inserted
  after the schedule-banner.js module tag, before `</body>`. Diff shape: 1
  insertion, 102 deletions.
- **Numbers:** `provider-settings.js` = 5,871 bytes / 111 lines (102 moved +
  9-line interop tail; CRLF, no BOM, 179 non-ASCII UTF-8 bytes). `index.html`
  601,313 → 596,063 bytes; 12,274 → 12,173 lines (BOM preserved).
- **Interop surface: 1 window function re-exposure, 0 bridges:**
  - Cross-module caller (1): `_renderProviderSettings` (settings-drill.js's
    `_renderSettings` template — runtime).
  - Module-private: none (the single function IS the export).
  - Formal scans: zero generated-handler assignment; zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `_agentProviders` (the provider list global, inline), `esc`, and the **module-17
  window props** `PROVIDER_AUTH_KEYS` / `settingsClaudeLogin` / `settingsClaudeAuthCheck`
  / `settingsProviderSetEnv` / `settingsProviderTerminalLogin` /
  `settingsProviderRefresh` (referenced inside the generated `on*=` handler strings
  this function builds — resolved at click time against window). `saveSetting`
  (the default-provider dropdown onchange — stays inline as shared settings infra).
  Strict-mode promotion audited: zero module-code `this`; the single assignment is
  the `window.` export.
- **sw.js:** `SW_VERSION` `mc-push-v19` → `mc-push-v20` (no cache list by design;
  version bump only). Added a v20 changelog line.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/provider-settings.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses provider-settings.js in
    module goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5392 python server.py`, then KILLED;
    live :5199 never touched): `/static/js/provider-settings.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 5871 (exact match).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — Sign-in/Save/Launch never clicked) — **9/9 PASS, 0 console
    errors, 0 page errors**: `window._renderProviderSettings` exposed + callable
    (returns the `#settings-providers-section` markup with the Claude sign-in
    control); the **REAL caller path** verified — `openSettings()` →
    `drillSettings('agent')` rendered `#settings-providers-section` via
    settings-drill.js calling `window._renderProviderSettings`, with the
    cross-module `settingsClaudeLogin` onclick (module-17 interop) intact.
    Screenshot caught the `settingsPaneIn` fade mid-animation (the documented
    landmine — DOM assertions are the source of truth, all passed).
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module
    1; line shifted to L105, same evaluate step). No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–18).

### Landmines for module 20 (remaining-core map, post-Provider-Settings)

1. All prior landmines still apply verbatim (see the module-18 list — anti-FOUC,
   route.fulfill ×2, CI path-filter gap, bg-framing-check broken at base,
   CRLF+BOM binary surgery, build-macos.spec, npm install, deferred-module
   timing, module-scoped-globals, re-derive + brace-scan, accessor/identity
   bridges, multi-segment/boot-trap carves + the three boot-trap fixes,
   WHOLE-REPO grep mandatory, cross-region const reads, template-`${handler}`
   scanning, don't repair quirks, `(0,eval)('openModals')`, destructive-feature
   route-guard, settle `settingsPaneIn` before screenshots, `node
   --input-type=module --check <file>` ERRORS on Node 24 — pipe via stdin).
2. **The non-core queue is nearly exhausted. What remains:**
   - **Hivemind family** — the LAST big non-core win, but a careful carve:
     * The **Hivemind worker popover** (was 4981, in the agent-panel zone L4854–5061)
       reads `agentStatusCache` + `agentHistory` + `hivemindCache` — STORE.JS-
       COUPLED (the smear the brief reserves for last). It is immediately followed
       by `let _skipAgentOutput` + `agentPanelHTML` (the agent-panel core). **Leave
       the worker popover for the store.js pass.**
     * The **Hivemind tab (was 8318) + Dashboard Modal (was 8520)** span (8318–9063)
       reads `hivemindCache` (8×) but does NOT read the store.js smear
       (`agentStatusCache`/`agentHistory`/`agentOutputBuffers`/`agentEventSources`
       = 0 refs each — verified). Plus **Cross-project Hivemind view** (was 11192).
       These THREE are likely carveable as a Hivemind module IF `hivemindCache` is a
       Hivemind-family global (verify it isn't written by the agent-panel core) AND
       around the shared inline run-history renderers (`renderRunRows`/
       `renderRunsPagination` — STAY inline, multi-consumer with scheduler.js). The
       Hivemind run-history consumer the module-13 entry noted is here. Expect a
       2-/3-segment carve. **Re-derive the exact dashboard-modal end (the Agent
       Console at 9064 is NOT hivemind) and audit `hivemindCache`'s writers before
       committing to a move.**
   - **Process Manager** (`openProcessManager` + `refreshProcessList`, ~48L, was
     10477) — a small single-feature region; check it doesn't read the store.js
     smear, then it's a clean small module. Likely the easiest remaining win.
   - Everything else (conversation model, modal/tile HTML, SSE slot management,
     dispatch, status resolver, the worker popover, Command Palette's real ~98L
     with its 2 accessor bridges) is the **store.js design pass** — HARD STOP per
     the brief.

## Phase 3 — module 20: extract Process Manager → `static/js/process-manager.js` (2026-06-10)

- **Module-19 SHA backfill:** module 19 (js/provider-settings.js) = commit `d5a46e2`.
- **Candidate selection this run:** the smallest clean remaining win — the Process
  Manager modal — and the last easy non-core family before the Hivemind carve /
  store.js pass.
- **Region re-derived — 2-SEGMENT (the family is SPLIT, mis-filed):**
  `openProcessManager` sits under the `// ── Process Manager` header (old 10375),
  but its helpers `_formatDuration` + `refreshProcessList` + `killTrackedProcess` +
  `cleanupOrphanedProcesses` are mis-filed ~480 lines down UNDER the `// ──
  Cross-project Hivemind view` header (old 10889–10969, right before the inline
  boot tail). So the move is two byte-verbatim segments:
  * **segA** old 10375–10422 (header + `openProcessManager()` + 1 blank).
  * **segB** old 10889–10970 (`_formatDuration` + `refreshProcessList` +
    `killTrackedProcess` + `cleanupOrphanedProcesses` + 1 blank).
  The gap (10423–10888 = Run history + Cross-project Hivemind) STAYS inline.
  Brace-depth top-level scan of each segment: both end depth 0; segA = 1 fn, segB
  = 4 fns; 0 OTHER in either (no IIFEs/parse-time calls/listeners). `_formatDuration`
  is Process-Manager-PRIVATE (used only by `refreshProcessList` at one call site —
  verified).
- **Inbound refs re-verified (precise scan EXCLUDING BOTH segments + WHOLE-REPO
  sweep):** only `openProcessManager` has outside refs — L2649 (`sidebarNav('processes')`)
  + L2845 (command-palette `Processes` action) — both runtime. The other 4 names
  have ZERO refs outside the two segments (a custom scan that excludes BOTH spans,
  not the naive single-range refscan, was used since the segments are far apart).
  Whole-repo sweep: only prose. No cross-module callers.
- **What moved:** old lines 10375–10422 (segA) + 10889–10970 (segB) →
  `static/js/process-manager.js`, **byte-verbatim** (SPLIT two-sided binary
  reassembly assertion: (1) `seg_pre + segA + seg_mid + segB + seg_post ==
  original` proving both spans are clean disjoint byte ranges; (2) the new
  index.html — with the inserted `<script>` tag removed AND both segA and segB
  re-inserted at their original cut points — `== original` byte-for-byte; interop
  tail append-only; module body = segA + segB + tail, the only non-verbatim aspect
  being the cosmetic join point — both segments individually verbatim, same class
  as scheduler.js's 2-segment `timeAgoShort` carve). Loaded via
  `<script type="module" src="/static/js/process-manager.js"></script>` after the
  provider-settings.js module tag, before `</body>`. Diff shape: 1 insertion, 130
  deletions across two hunks (48 segA + 82 segB).
- **Numbers:** `process-manager.js` = 6,363 bytes / 142 lines (48 segA + 82 segB
  moved + 12-line interop tail; CRLF, no BOM, 201 non-ASCII UTF-8 bytes).
  `index.html` 596,063 → 590,543 bytes; 12,173 → 12,044 lines (BOM preserved).
- **Interop surface: 4 window function re-exposures, 0 bridges:**
  - Inbound caller (1): `openProcessManager` (sidebarNav + palette).
  - Region-generated `on*=` handler targets (3): `refreshProcessList`,
    `killTrackedProcess`, `cleanupOrphanedProcesses` (the modal's Refresh / Kill /
    Cleanup buttons).
  - Module-private (1): `_formatDuration` (used only by `refreshProcessList`).
  - Formal scans: zero generated-handler assignment; zero `this` in region code.
- **Outbound deps** (resolve at call time through the shared global scope):
  `openModals` (`const`, bare), `nextModalZ++`, `restoreModal`, `focusModal`,
  `_clampModalSize`, `centerModalElement`, `minimizeModal`/`closeModalById`
  (handlers), `API_BASE`, `esc`, `showToast`, `confirm` + browser builtins. No
  store.js smear (`agentStatusCache`/`agentHistory`/etc. — 0 refs; the segments
  read only DOM + `/api/processes`). Strict-mode promotion audited: zero
  module-code `this`; every assignment targets a declared binding or a `window.`
  property.
- **sw.js:** `SW_VERSION` `mc-push-v20` → `mc-push-v21` (no cache list by design;
  version bump only). Added a v21 changelog line.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/process-manager.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses process-manager.js in module
    goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5392 python server.py`, then KILLED;
    live :5199 never touched): `/static/js/process-manager.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 6363 (exact match);
    `GET /api/processes` → `[]` (no tracked processes — the empty state).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — Kill + Cleanup are DESTRUCTIVE POSTs, so a hard route-guard
    ABORTED + asserted-zero on `/api/processes/*/kill` and `/api/processes/cleanup`;
    never clicked) — **16/16 PASS, 0 console errors, 0 page errors, NO destructive
    endpoint hit**: 4 window.* interop callable; `_formatDuration` NOT leaked;
    Process Manager modal opens via the REAL `sidebarNav('processes')` path
    (`#process-list` + `#process-count` + the wired Cleanup button); list populated
    from the real `GET /api/processes` (empty-state "No tracked processes.");
    `refreshProcessList()` re-ran without throwing; kill/cleanup wired but never
    invoked. Screenshot eyeballed: fully styled Process Manager modal (title,
    counter, Refresh/Cleanup buttons, empty state), sidebar nav item active, no
    FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module 1;
    line shifted to L108, same evaluate step). No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–19).

### Landmines for module 21 (the non-core queue is now down to Hivemind)

1. All prior landmines still apply verbatim (see the module-18/19 lists).
2. **The non-core queue is essentially EXHAUSTED. Only the Hivemind family
   remains as a non-store.js candidate, and it is a careful carve:**
   - **Hivemind worker popover** (was 4981, in the agent-panel zone L4854–5061) —
     STORE.JS-COUPLED (reads `agentStatusCache` + `agentHistory` + `hivemindCache`;
     immediately followed by `agentPanelHTML`). **Leave for the store.js pass.**
   - **Hivemind tab + Dashboard Modal** (was 8318–9063) — reads `hivemindCache`
     (8×) but NOT the store.js smear (verified 0 refs to `agentStatusCache`/
     `agentHistory`/`agentOutputBuffers`/`agentEventSources`). + **Cross-project
     Hivemind view** (was 11192, now shifted; note Process Manager's helpers were
     removed FROM the top of that region this module, so re-derive its current
     bounds). These THREE could form a Hivemind module IF: (a) `hivemindCache`'s
     WRITERS are all within the Hivemind family (audit — if the agent-panel core
     writes it, it's shared state needing an identity bridge or store.js); (b) the
     carve goes AROUND the shared inline run-history renderers (`renderRunRows`/
     `renderRunsPagination` — STAY inline, multi-consumer with scheduler.js); (c)
     the exact Dashboard-Modal end is re-derived (Agent Console at old 9064 is NOT
     hivemind). Expect a 2-/3-segment carve. **This is the last non-core module;
     if `hivemindCache` is shared with the agent-panel core, DEFER it to store.js
     and STOP.**
   - Everything else is the **store.js design pass** — HARD STOP per the brief
     (conversation model, modal/tile HTML, SSE slot management, dispatch, status
     resolver, the worker popover, Command Palette's real ~98L + its 2 accessor
     bridges `cmdPaletteOpen`/`cmdSelectedIndex`).

## Phase 3 — module 21: extract Cross-project Hivemind view → `static/js/cross-hivemind.js` (2026-06-10)

- **Module-20 SHA backfill:** module 20 (js/process-manager.js) = commit `ccb221b`.
- **Candidate selection this run — the Hivemind family SPLITS cleanly:** the
  module-20 landmines flagged the Hivemind family as the last non-core candidate
  but gated on whether `hivemindCache` is shared with the agent-panel core. AUDIT
  RESULT: `hivemindCache` (declared L8320 in the Hivemind TAB region) is READ by
  the Tile HTML core (L1200/1396) AND the store.js-coupled worker popover (L5007)
  → the **tab + Dashboard Modal stay for the store.js pass** (extracting them would
  push a `hivemindCache` identity bridge into the projects-grid core renderer).
  **BUT the Cross-project Hivemind view is a SEPARATE, self-contained family** that
  does NOT touch `hivemindCache` — it has its own `_allHivemindFilter`/
  `_allHivemindsCache` state (the cross-backlog/all-MCP pattern). That's this module.
- **Region re-derived — self-contained, single contiguous span (header-to-header
  mismatch, 8th time):** old lines **10557–10840** (the `// ── Cross-project
  Hivemind view` header through `newHivemindFromGlobal`'s close at 10839 + 1
  trailing blank 10840). The header-to-header span (to Native FCM push at 10928)
  ALSO held the inline boot tail — `startRefresh` (L10842, with the module-17 shim)
  + the filter-dropdown binding + grid-layout fetch + density/feed/viewmode restores
  + `applyAdvancedFlags()` + the SW registration (10842–10927) — the SAME recurring
  boot-tail trap; those STAY inline. Brace-depth top-level scan of the REAL region:
  depth ends 0, 3 state decls (`_allHivemindFilter`/`_allHivemindsCache` `let` +
  `HM_STALE_HOURS` `const`) + 10 function decls, 0 OTHER (no IIFEs/parse-time
  calls/listeners in the family proper).
- **Inbound refs re-verified (formal whole-FILE refscan EXCLUDING the boot tail +
  WHOLE-REPO sweep):** outside refs are exactly — `openAllHivemindsForProject` ×1
  (project three-dot menu onclick L1649), `openAllHiveminds` ×1
  (`sidebarNav('hivemind')` L2651), `renderAllHiveminds` ×1 (the central `render()`
  L835 — but **GUARDED** by `if (openModals.has('__all_hivemind'))`, so it fires
  only once the modal is open = necessarily after this deferred module evaluates;
  same safe pattern as cross-backlog's `renderAllBacklog`). All other 9 names
  (`_hmEffectiveStatus`/`_hmShortId`/`loadAllHiveminds`/`_hmTreeMiniViz`/
  `_hmRelativeTime`/`newHivemindFromGlobal`/`_allHivemindFilter`/`_allHivemindsCache`/
  `HM_STALE_HOURS`) are region-only. **WHOLE-REPO sweep: ZERO non-index hits** (no
  cross-module callers in other JS modules — unlike modules 16–19).
- **What moved:** old lines 10557–10840 → `static/js/cross-hivemind.js`,
  **byte-verbatim** (two-sided binary reassembly assertion: (1) `before + region +
  after == original`; (2) the new index.html, with the inserted `<script>` tag line
  removed AND the region re-inserted at the cut point, `== original` byte-for-byte;
  interop tail append-only). Loaded via
  `<script type="module" src="/static/js/cross-hivemind.js"></script>` after the
  process-manager.js module tag, before `</body>`. Diff shape: 1 insertion, 283
  deletions.
- **Numbers:** `cross-hivemind.js` = 14,805 bytes / 300 lines (284 moved + 16-line
  interop tail; CRLF, no BOM, 168 non-ASCII UTF-8 bytes). `index.html` 590,543 →
  576,941 bytes; 12,044 → 11,761 lines (BOM preserved).
- **Interop surface: 4 window function re-exposures + 1 object-identity bridge,
  0 accessor bridges:**
  - Inbound/cross-region callers (3): `openAllHiveminds` (sidebarNav),
    `openAllHivemindsForProject` (project menu), `renderAllHiveminds` (guarded
    central render()).
  - Region-generated `on*=` handler target (1): `newHivemindFromGlobal` (the
    "+ New Hivemind on this project" card onclick).
  - **Object-identity bridge (1):** `_allHivemindFilter` (a `{status,search,project}`
    object). Generated handlers property-write it (`oninput="_allHivemindFilter.
    search=this.value;…"`, two `onchange` for status/project — L10613/10615/10623);
    formal wholesale-write scan → exactly ONE hit (the declaration; the in-region
    writes at 10579/10580 are `.`-prefixed). Plain `window._allHivemindFilter =
    _allHivemindFilter` identity bridge routes handler property-writes into the
    module's live object — same as cross-backlog's `_allBacklogFilter` and all-MCP's
    `_allMCPFilter` (NOT an accessor — never wholesale-reassigned).
  - Module-private (6 fns + `_allHivemindsCache` + `HM_STALE_HOURS`):
    `_hmEffectiveStatus`, `_hmShortId`, `loadAllHiveminds`, `_hmTreeMiniViz`,
    `_hmRelativeTime`; `_allHivemindsCache` (wholesale-reassigned only inside the
    module, zero outside refs), `HM_STALE_HOURS`.
  - Formal scans: generated-handler whole-var ASSIGNMENT scan (`="<ident>=`) EMPTY
    (handlers CALL functions / property-write `_allHivemindFilter`); zero `this`.
- **Outbound deps** (resolve at call time through the shared global scope): the
  inline Hivemind tab/dashboard functions `openHivemindDashboard` (L8522) +
  `startHivemindChat` (L8473, called by `newHivemindFromGlobal`), `openModals`
  (`const`, bare), `nextModalZ++`, `restoreModal`, `focusModal`, `centerModalElement`,
  `_clampModalSize`, `minimizeModal`/`closeModalById` (handlers), `openProjectModal`,
  `allProjects`, `isIncognitoProject`, `esc`, `API_BASE`, `showToast` + browser
  builtins. Strict-mode promotion audited: zero module-code `this`; every assignment
  targets a declared binding or a `window.` property.
- **sw.js:** `SW_VERSION` `mc-push-v21` → `mc-push-v22` (no cache list by design;
  version bump only). Added a v22 changelog line.
- **Smoke harnesses:** added `route.fulfill` for `/static/js/cross-hivemind.js`
  in BOTH `boot-smoke.mjs` and `bg-framing-check.mjs`.
- **Gates:**
  - `node --check --input-type=module` (stdin) parses cross-hivemind.js in module
    goal.
  - `node tools/smoke/boot-smoke.mjs` — **PASS**, 5/5 scenarios, grid rendered.
  - Real-server check (throwaway `MC_PORT=5392 python server.py`, then KILLED;
    live :5199 never touched): `/static/js/cross-hivemind.js` → 200,
    `text/javascript; charset=utf-8`, Content-Length 14805 (exact match);
    `GET /api/hivemind/list` → `[]` (the empty state).
  - Headless Chromium against that server (seeded `walkthrough_done=1`;
    **read-only** — `newHivemindFromGlobal` (opens a project + dispatches a hivemind
    chat = model cost) deliberately NEVER invoked) — **20/20 PASS, 0 console
    errors, 0 page errors**: 4 window.* interop callable; `_allHivemindFilter`
    object-identity bridge present with the default `{project,search,status}` shape;
    all 7 module-privates NOT leaked; cross-project Hivemind modal opens via the
    REAL `sidebarNav('hivemind')` path (`__all_hivemind` in `openModals`) + renders
    from the real `/api/hivemind/list` (empty-state "No matching hiveminds…");
    **identity bridge asserted end-to-end** — a property-write via
    `window._allHivemindFilter.search/status` + `renderAllHiveminds()` reads it (no
    throw); the **guarded `render()` path** re-renders the open modal without
    throwing; `newHivemindFromGlobal` wired but never invoked. Screenshot eyeballed:
    fully styled cross-project Hivemind modal (title, search, Active/All-projects
    filters, "0 hiveminds", + New Hivemind, empty state), sidebar nav item active,
    no FOUC.
  - `node tools/smoke/bg-framing-check.mjs` — fails with the **identical
    pre-existing base error** (`setBgZoom is not defined`, landmine 4 of module 1;
    line shifted to L111, same evaluate step). No new breakage.
- **Commit:** SHA in the orchestrator report; backfill on next entry (same
  convention as modules 1–20).

### Queue status after module 21 — the move-only non-core families are EXHAUSTED

The non-store.js feature queue is now empty. What remains in index.html is the
agent-panel / projects-grid / conversation-model CORE — the **store.js design
pass** Ron deferred to last. Specifically:

- **Hivemind TAB + Dashboard Modal** (`hivemindCache` at L8320 + the tab/dashboard
  functions, ~8318–9063) — BLOCKED on `hivemindCache` being read by the Tile HTML
  core (L1200/1396) and the worker popover (L5007). Extractable only WITH the
  store.js core (or with a `hivemindCache` object-identity bridge that the core
  renderer reads — a store.js-design decision, not a clean leaf move). DEFERRED.
- **Hivemind worker popover** (L~4900, in the agent-panel zone) — reads
  `agentStatusCache` + `agentHistory` + `hivemindCache`; pure store.js core.
- **Command Palette's real ~98L** — needs 2 accessor bridges (`cmdPaletteOpen`
  bare-assigned by walkthrough.js; `cmdSelectedIndex` `++`/`=`-mutated by the
  shared inline keydown handler). Brief says extract ONLY if it cleanly separates
  with ≤2 accessor bridges; it's the most entangled "small" candidate — recommend
  pairing with the store.js pass.
- The conversation model / modal+tile HTML / SSE slot management / dispatch /
  status resolver / Agent Console / Memory tab / agent-log / plans / rules / the
  remaining settings-section templates — all the `agentOutputBuffers`/
  `agentEventSources`/`agentStatusCache` smear — is the store.js pass.

index.html is now **11,761 lines** (down from 14,674 at module 13 / ~25,165 at
the start of Phase 3). 8 modules extracted this session (14–21): mcp, system-status,
update-power, provider-auth, schedule-banner, provider-settings, process-manager,
cross-hivemind. Recommend the orchestrator now begin the **store.js design
checkpoint** for the remaining core.

## Phase 4 — step 0: STORE consolidation (2026-06-10, Fable driving)

- **Design ratified:** `docs/STORE_JS_DESIGN.md` (commit `13c5834`). Option A —
  state anchors INLINE (global lexical environment is module-visible in both
  directions; bridges were only ever needed for state that moved INTO modules).
  Ron approved: Option A + full hollow-out + Fable drives.
- **Membership derived, not curated** (`_scratch/store_membership.py`): a var is
  STORE iff its non-decl refs span ≥2 families (families = target modules
  M22–M31, M32 sub-families, each shipped js module, INLINE). Result: **74
  STORE / 60 family-private / 0 dead** of 134 top-level state decls (133
  statements; one 2-name decl). Notable derivation wins over the hand list:
  `agentEventSources`/`agentSSEWatchdogs` span INLINE+M22+M24 (not M23);
  `cmdSelectedIndex` confirmed palette-private (no bridge ever needed);
  6 orphaned decls flagged `[DECL OUTSIDE FAMILY]` for pull-in at their
  family's cut (`newProjDomain`, `TOKEN_MODES`, `sseRetryCount`, `acExpanded`,
  `exitPlanModeCount` + one more in membership.json).
- **What moved:** 73 decl statements (API_BASE already in place = member #1)
  → one `// ── STORE ──` block at L683–822 directly after API_BASE. Decl
  statements moved byte-verbatim INCLUDING their var-describing leading
  comment blocks (8 reviewed); 2 comment blocks stayed put as
  section-mechanism docs (`_mcModalHistoryActive` Android-back explainer,
  `bgMode` background-posture note — NO_LEAD override in the script).
- **Order:** original file order, topo-adjusted for eager init deps
  (Kahn, stable). Only real edge: `_orderKey` → `projectOrder` (already
  satisfied). Eager-init identifier scan: every identifier resolves to
  builtin / hoisted function / earlier store member (object-literal keys and
  regex flags excluded); zero refs to family-private state; one eager
  `window.innerWidth` read (`_isMobileDevice`) — pure window metric,
  position-independent.
- **Assertions (all in `_scratch/store_surgery.py`):** cut-lines multiset ==
  inserted-lines multiset (byte-exact); unmoved-line sequence preserved
  exactly; +10 lines total = block header(8)+footer(2); post-surgery re-scan:
  133 top-level decl statements, brace depth 0; `node --check` PASS (classic
  goal).
- **Gates:** boot-smoke **5/5 PASS**; bg-framing-check **identical pre-existing
  base error** (`setBgZoom`, L111 evaluate — landmine 4 of module 1), no new
  breakage; throwaway real server `MC_PORT=5391` (killed; :5199 untouched):
  heartbeat OK, `GET /` → 200 (577,678 bytes), STORE block present in served
  HTML. No new js file ⇒ no sw.js bump, no harness route.fulfill changes.
- **index.html:** 11,761 → 11,771 lines.

### Landmines for M22 (rich-text.js)

1. All module-1–21 landmines apply verbatim.
2. **Never re-declare a store name at a module's top level** — silent state
   fork; add a declared-names ∩ STORE check to every module's formal scans
   (the STORE list lives in `_scratch/membership.json`; regenerate after each
   cut since line numbers shift).
3. Empty/near-empty section stubs left behind by decl moves (e.g. "Enter Key
   Behavior" is now header+comment-less) are EXPECTED; sweep at M32, don't
   "fix" mid-queue.
4. M22's family-private captures: `_pinScrollQueue`, `_aqCounter`,
   `_EMPTY_SET`, `_recentlyStoppedSessions`, `exitPlanModeCount` (decl
   currently in the Agent Panel zone — orphaned, pull into the module).
5. Line numbers in membership.json are PRE-step-0; re-derive everything
   against the committed file before cutting.

## Phase 4 — M22: extract rich-text formatter → `static/js/rich-text.js` (2026-06-10)

- **First post-STORE cut; cutter is now parameterized** (`_scratch/cut_module.py`:
  scans + cut + assertions + sw bump + harness patching in one deterministic
  script; per-module config at top).
- **Sizing correction (module-4 lesson, 4th occurrence):** the "Rich text
  formatting 1,074L" row was header-to-header. The REAL formatter family is
  **253L (6474–6726)**; the other ~815L of the old section (appendAgentLine
  6727 → fetchAgentStatus end ~7540) is conversation model — stays for M23.
  M22 = formatAgentText + isTableLine/formatTableLine/isPipeTable/
  isSeparatorLine/buildPipeTable + agentLineCls + collapseIntoPlanButton +
  expandAgentOutput + _isAgentOutputPinned/_scheduleAgentPinScroll +
  `_pinScrollQueue` (module-private const).
- **What moved:** L6474–6726 byte-verbatim (two-sided reassembly asserted);
  12-line interop tail. `rich-text.js` = 265 lines / 12,004 bytes (CRLF, no
  BOM). index.html 11,771 → 11,519 lines.
- **Interop surface: 10 window re-exposures, 2 module-privates, 0 bridges:**
  - The table pipeline is NOT private — `isTableLine`/`formatTableLine`/
    `isPipeTable`/`buildPipeTable` are called by the transcript renderer
    (conversation model ~5284–5342), agent-log panel (~7838–7852), console/
    hivemind zone (~8983–9231), plan viewer (~9857). They expose with the
    rest; only `isSeparatorLine` + `_pinScrollQueue` are private.
  - `_scheduleAgentPinScroll`/`_isAgentOutputPinned` callers include inline
    appendAgentLine (M23-pending) + cmd-palette zone (3032) + boot-zone
    repin (11050).
  - Generated handlers emitted by region: only `_openImageViewer` (outbound
    to mermaid.js's export — unchanged pattern).
- **Scans:** zero top-level parse-time code in region; zero `this`; zero
  STORE-name shadowing (new per-cut gate per step-0 landmine #2).
- **Store proof under fire:** module code reads/writes `expandedOutputSessions`,
  `agentStatusCache` etc. bare-name via global lexical env — exercised live.
- **sw.js:** v22 → v23 + changelog line.
- **Gates:** module+inline `node --check` PASS; boot-smoke **5/5**;
  bg-framing-check **identical base error** (`setBgZoom`, evaluate step
  unchanged, now L113 after harness insert); throwaway real server
  `MC_PORT=5393`: `/static/js/rich-text.js` → 200, **12,004B exact**;
  headless exercise **13/13 PASS, 0 console / 0 page errors** — 10/10
  exposures callable, privates not leaked, direct formatter checks (hl-h,
  linkify, agent-img + viewer onclick, inline code), **synthetic-SSE through
  the REAL inline `connectAgentStream` → `es.onmessage` → `appendAgentLine`
  → module formatters** (header + collapsed `<table>` + linkified URL, 3
  blocks), STORE read/write from page scope. Screenshot eyeballed (rendered
  header/table/link on booted dashboard).
- **Gotcha for the next synthetic-SSE user:** the stream payload field is
  `msg.text`, NOT `msg.line` ({type:'output', text}).

### Landmines for M23 (conversation.js)

1. M23's true region is THREE pieces: Agent Panel (~4904–4993 current),
   worker popover (~5031–5111), Conversation model section (5116–5660),
   PLUS the orphaned conversation half of old rich-text (appendAgentLine
   ~6475 → fetchAgentStatus end ~7290 post-M22 shift) — re-derive exact
   bounds + the AskUserQuestion machinery ownership (it sits between
   approvePlan and sendFollowup).
2. `connectAgentStream` (~6127 pre-M22, now shifted) holds top-level-armed
   watchdog `setInterval`s INSIDE the function (fine) — but the REGION
   around it includes `_repaintAgentOutput`/`_reconcileAgentBuffer` used by
   boot-zone pollers; expect a wide EXPOSE list.
3. `updateConsoleOutput` is called from the onmessage path but lives in the
   Agent Console family (M27) — leave it inline until M27; cross-module
   call via global scope works either way.
4. exitPlanModeCount decl is in the agent-panel zone but its refs live in
   the appendAgentLine block — decl rides with M23 (membership says
   single-family M22 by SECTION attribution; reality is M23 by function).

## Phase 4 — M23: extract conversation model → `static/js/conversation.js` (2026-06-10)

- **The heart, 5-segment carve:** [4923–4924]+[4930–4939]+[4952–4998]+
  [5012–5660]+[6474–7288] — Agent Panel header/composer family, the popover
  helpers mis-filed under the terminal-state section (isHivemindWorker/
  Orchestrator/getProjectSessions/getProjectTabSessions), worker popover,
  agentPanelHTML→selectResumeSession (the indented "Conversation model"
  header is INSIDE agentPanelHTML), and the orphan appendAgentLine→
  fetchAgentStatus block from old rich-text. **Carved AROUND:** sseRetryCount
  decl (M24's), the session-metrics `setInterval` (boot skeleton), acExpanded
  decl (M27's), the terminal window-bridge block.
- `conversation.js` = 1,540 lines / 85,338 bytes. index.html 11,519 → 9,997.
- **Interop: 28 window re-exposures, 17 privates, 0 bridges.** Includes 7
  handler-target promotions — **NEW CUTTER GATE:** region fns named inside
  `on*="…"` attribute values (incl. conditional/nested-template emission like
  `onclick="${incForced ? '' : \`toggleIncognito(...)\`}"`) are auto-promoted
  to EXPOSE even with zero outside source refs; attribute handlers resolve via
  window at event time. `toggleIncognito` was the catch — literal-name scan
  missed it.
- **Gates:** parse ×2 PASS; boot-smoke 5/5; bg-framing baseline-only;
  real server 200 **85,338B exact**; exercise **12/12, 0 errors,
  0 write-endpoint hits** (dispatch/followup/stop route-guard tripwire) —
  28/28 callable, panel renders, synthetic-SSE through MODULE
  connectAgentStream→appendAgentLine→rich-text module (header+table),
  AskUserQuestion form render + DOM-dedup re-render, fetchAgentStatus
  graceful, store agentHistory↔getProjectSessions roundtrip.
- **Cutter fix:** subprocess node-stdin parse gate needs encoding='utf-8'
  (cp1252 chokes on ── box chars).

### Landmines for M24 (resume-preview.js)

1. Region ≈ 5661→? (current numbering POST-M23: re-derive; the old
   6440-zone escPromptWithImages sits at the region tail — it's called by
   conversation.js (appendAgentLine/agentPanelHTML) → will need EXPOSE).
2. sseRetryCount decl now sits orphaned at ~4925 with agentConvNew's
   leftover comment fragment — pull both decl+comment into M24's region or
   leave; membership says M24-private.
3. connectAgentStream/_reconcileAgentBuffer/_repaintAgentOutput live in the
   resume zone (5945+) — heavily called by conversation.js + boot pollers;
   expect a wide EXPOSE list.

## Phase 4 — M24: extract resume-preview + dispatch/SSE → `static/js/resume-preview.js` (2026-06-10)

- 2-segment carve [(4923,4923)+(4953,5765)]: the orphaned sseRetryCount decl
  + the whole resume zone (conv preview family, closeAgentTab, dispatchAgent,
  _reconcileAgentBuffer, _repaintAgentOutput, connectAgentStream,
  escPromptWithImages). 814 lines / 42,793 bytes. index.html 9,997 → 9,185.
- Interop: 10 exposures (incl. previewOpenFull handler promotion), 7 privates,
  0 bridges. agentConvNew's orphan comment fragment left at ~4923 (M32 sweep).
- Gates: parse ×2 PASS; boot-smoke 5/5; bg-framing baseline; real server 200
  42,793B exact; exercise **10/10, 0 errors, 0 write hits** — highlight:
  **3-module chain** (module connectAgentStream → module appendAgentLine →
  module formatters) with store watchdog/buffer assertions.

### Landmines for M25 (agent-log.js)

1. Region = Agent Log Panel + Plans tab + continue + image paste headers
   (re-derive; now ~4953+). agentLogPanelHTML/toggleAgentLog/loadAgentLog/
   loadConversations/upsertConversationCache/_lastUserFromBuffer +
   loadProjectPlans/renderPlansTab + continue/paste families.
2. Top-level dragover/drop preventDefault listeners in the paste zone STAY
   (boot skeleton).
3. continueInputOpen/planSelections decls in-region (private per membership).

## Phase 4 — M25: extract agent-log family → `static/js/agent-log.js` (2026-06-10)

- 2-segment carve [(4952,5367)+(5372,5422)] AROUND the boot dragover/drop
  preventDefault listeners (stay inline at ~5369-5370 with their comment).
  Agent Log Panel + Plans tab + continue + image paste. 467 lines / 21,056
  bytes. index.html 9,185 → 8,719.
- Interop: 20 exposures (12 ref-derived + 8 handler-target promotions —
  the whole plans-toolbar/row onclick family), 3 privates
  (toggleAgentLog, continueInputOpen, planSelections).
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; real server 200
  21,056B exact; exercise **9/9, 0 errors, 0 write hits** (plans render
  seeded via store plansCache; loadAgentLog real GET). Seed gotcha: plan
  rows key on `plan_file`, not `file`.

### Landmines for M26 (hivemind.js)

1. Hivemind tab + Dashboard Modal headers (re-derive lines). hivemindCache
   is STORE (step 0) — the tile/popover readers are module-side now; no
   bridge needed.
2. renderRunRows/renderRunsPagination (Run history) are shared with
   scheduler.js and STAY INLINE (module-21 landmine, still true).
3. hivemindSSE/_hmDashDebounce/_hmDashInFlight are STORE (deep-link
   coupling); hivemindDashboardWs/_hmTabDebounce private per membership.

## Phase 4 — M26: extract hivemind tab+dashboard → `static/js/hivemind.js` (2026-06-10)

- Single span [(5284,6025)]: tab builders + dashboard modal + runs modal +
  SSE + directives. 742 lines / 37,644 bytes. index.html 8,719 → 7,978.
- Unblocked by step 0 (hivemindCache in STORE — tile/popover readers now
  module-side conversation.js, zero bridges).
- Interop: 11 exposures (3 ref-derived + 8 handler promotions), 12 privates.
- **Finding: `hivemindTabHTML` + `loadHiveminds` have ZERO callers anywhere**
  — the per-project Hivemind tab appears superseded by the cross-project
  view (module 21). Dead code moves with its family (no-behavior-change
  discipline); flagged for a deliberate deletion pass post-track.
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; real server 200
  37,644B exact; exercise **6/6** — incl. unknown-id failure shape asserted
  IDENTICAL to d736126 baseline (unguarded 404 → TypeError reading 'goal';
  pre-existing, not a regression).

### Landmines for M27 (agent-console.js)

1. Region: Agent Console header (~5284 now) through Memory tab / Tab switch /
   Tab search families — re-derive; updateHistoryStatus/updateAgentStatusUI/
   renderAgentConsole/updateConsoleOutput are called from conversation.js +
   resume-preview.js → wide EXPOSE.
2. acExpanded decl (M27's) still orphaned near ~4920 — pull in (segment).
3. memoryCache (private), acOpenSessions (STORE).

## Phase 4 — M27: extract agent console + memory tab + tab switch/search → `static/js/agent-console.js` (2026-06-10)

- 4-segment carve [(4938,4938)+(5284,5446)+(5468,5563)+(5612,5696)]:
  orphaned acExpanded decl, console family, memory tab (openRulesModal
  EXCLUDED — rules family, stays), tab switch + search. 345 lines /
  14,998 bytes. index.html 7,978 → 7,634.
- **Duplicate-function shadow trap (NEW lesson):** the file has TWO inline
  `function timeAgoShort` decls (console zone 5447: returns "5m"; run-history
  zone 6183: returns "5m ago" + 'never' fallback). The SECOND wins at parse
  time — the console-zone copy has been DEAD since it was written. Moving it
  would have RESURRECTED it module-locally and changed console labels
  ("5m" vs "5m ago"). Excluded from the region; stays inline + shadowed
  (M32 sweep candidate). **Add to every future cut: check region fn names
  against a whole-file duplicate-decl scan.**
- Note: classic-script top-level `function` decls are window props by
  spec — `window.timeAgoShort` === the inline winner; only MODULE fns need
  exposure tails.
- Interop: 12 exposures (10 + saveMemory/toggleConsoleSession promotions),
  7 privates.
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; real server
  14,998B exact; exercise 6/6 meaningful (renderAgentConsole with seeded
  store history; inline timeAgoShort winner verified "5m ago").

## Phase 4 — M29 (absorbs M28): window manager + three-dot + deep link + palette → `static/js/modal-manager.js` (2026-06-10)

- **M28 collapsed into M29:** the palette's movable surface is only ~88L
  (toggleCommandPalette + renderCommandResults) — its state
  (`cmdSelectedIndex`) is written by the BOOT-armed global keydown + input
  listeners which stay inline, so the decl stays inline beside them (the
  shared_state gate would have FATALed a move). A standalone module wasn't
  warranted.
- 5-segment carve [(2058,2172)+(2174,2243)+(2259,2693)+(2905,2992)+
  (2999,3012)]: prefs/snapshot machinery, deep-link handler (the SW
  message-listener ARM at 2244 stays inline), openProjectModal/close/
  minimize/showDesktop + the whole three-dot family + emoji picker,
  palette pair, restoreModal. 722 lines / 32,962 bytes. index.html
  7,634 → 6,913.
- **Cutter fix:** decl-statement extents now suppress false "OTHER
  top-level" hits from array-literal member lines (brace masker doesn't
  track bracket depth; EMOJI_CHOICES tripped it).
- Interop: 30 exposures (29 + pickEmoji promotion), 9 privates.
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; real server
  32,962B exact; exercise **8/8** — real open→minimize→restore→close cycle
  against store `openModals`, palette toggle via store `cmdPaletteOpen`,
  0 write hits, 0 errors.

### Landmines for M30 (render-core.js)

1. Tile HTML (1291) + Modal HTML (1595) + List View (~2790 now shifted) +
   sidebar/list glue stays vs moves: `render()`/`refreshModal`/
   `refreshModalById`/`sizeAgentChat`/`updateAgentStatusUI`/`guardianReset`
   remain INLINE (render engine + boot glue per design §7).
2. tileHTML/modalContentHTML emit MANY on*= handlers — expect a big
   promotion list; the whole-file on*= scan covers static HTML too.
3. FRIENDLY_TO_VOICE (private), undoStack/showDoneMap (STORE).

## Phase 4 — M30: extract render core → `static/js/render-core.js` (2026-06-10)

- 3 segments [(1291,1594)+(1595,2057)+(2170,2214)]: status resolver family
  (computeLiveStatus/friendlyStatus/friendlySummary + FRIENDLY_TO_VOICE),
  tileHTML, the 460L modalContentHTML, renderListView/listRowHTML. 812
  lines / 53,232 bytes. index.html 6,913 → 6,102. `render()` /
  `refreshModal(ById)` / `sizeAgentChat` stay inline (render engine glue,
  design §7).
- The templates' huge on*= emission list is all NON-region callees (inline
  or already-exposed module fns) — zero promotions needed; the one
  name-position dynamic handler (`onclick="${onclick}"` in the provider
  submenu) names showToast/setProjectProvider — both globally resolvable.
- Boot-order note: render() fires after the /api/projects fetch resolves —
  always after deferred-module eval (sync on main thread post-parse);
  empirically 5/5 boot scenarios with instant route.fulfill.
- Interop: 6 exposures, 2 privates. Gates: parse ×2; boot-smoke 5/5;
  bg-framing baseline; 53,232B exact; exercise 7/7 (grid via module
  tileHTML, modal via module modalContentHTML through M29's
  openProjectModal — cross-module render chain).

## Phase 4 — M31: extract interactions → `static/js/interactions.js` (2026-06-10)

- 2 segments [(1887,2072)+(5429,6070)]: DnD grid + Aero-Snap + multi-modal
  tiles + modal drag + separator drag + touch resize + ctrl+scroll zoom.
  828 lines / 35,767 bytes. index.html 6,102 → 5,275.
- **First whole-zone arm relocation (modules 16/17 precedent, scaled up):**
  29 top-level listener arms + 1 idempotent localStorage purge moved WITH
  their fns+state (`allow_toplevel` cutter flag). Arms now attach at module
  eval (pre-DOMContentLoaded) instead of parse — behavior-equivalent; the
  DCL listener inside still catches DCL (modules eval before it fires).
  Required because the arm closures wholesale-write family state
  (`dragState = {...}` in mousedown) — fns/arms/state are inseparable.
- Interop: 8 exposures, 39 privates (the entire drag/snap state).
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; 35,767B exact;
  exercise: ctrl+wheel zoom (+1 level via store modalZoomLevels) AND real
  modal drag (dx=120/dy=62) both through RELOCATED arms; 0 errors.
  Synthetic-event gotcha: dispatch mousedown ON the header (no hit-testing
  in synthetic dispatch — e.target is the dispatch node).

## Phase 4 — M32a: dead-copy deletion + project-forms → `static/js/project-forms.js` (2026-06-10)

- **Pre-cut commit `07ae962`:** deleted the dead shadowed `timeAgoShort`
  (console-zone copy, unreachable since birth — the parse-order winner at
  the plan-viewer zone overwrites it). Deletion was REQUIRED to move the
  winner: a leftover script-level decl would shadow the module's window
  exposure for bare inline callers and resurrect the "no-ago" variant.
- One span [(3621,4453)]: path editor, folder picker (fp*), shared-rules
  editor, plan viewer + planFileLabel + the WINNER timeAgoShort, new-project
  form + autoSlug, folder browser, domain picker, createProject. 833 lines /
  37,304 bytes. index.html 5,264 → 4,432.
- Interop: 25 exposures (8 ref-derived incl. timeAgoShort×6-module callers +
  17 handler promotions), 21 privates. newProjDomain decl stays inline near
  the store block (global-lexical resolution; sweep later).
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; 37,304B exact;
  exercise green (timeAgoShort variant check "5m ago" — the load-bearing
  assertion; new-project form renders; 0 write hits).

## Phase 4 — M32b: appearance + bg crop editor → `static/js/appearance.js` (2026-06-10)

- 2 segments [(2808,2846)+(2966,3156)]: tone/accent/density/voice setters +
  the interactive crop-box editor + bg setters/pickers. 230 lines / 10,557
  bytes. index.html 4,432 → 4,203.
- **The boot-paint path STAYS INLINE** (`_applyAppearanceOnInit` + its
  parse-time call + `applyDashboardBackground` + clamp/dims helpers + the
  bg resize arm): relocating it to module eval risks a default-bg flash —
  the exact FOUC class landmine #1 exists for. Only user-action surface
  moved.
- Interop: 14 exposures (all settings-drill.js callers), 5 privates.
- Gates: parse ×2; boot-smoke 5/5 (incl. the 3 bg scenarios); bg-framing
  baseline; 10,557B exact; exercise 5/5 (setTone flips body class;
  setBgMode drives the INLINE apply chain).

## Phase 4 — M32c: mic + image-attach + rules → `static/js/composer-extras.js` (2026-06-10)

- 2 segments [(3005,3333)+(3341,3390)] carved around autoSizeNameInput:
  mic/voice transcription family, the agent image-attach helpers
  (upload/build/clear/remove/previews — conversation.js + resume-preview.js
  callers), rules panel + flashSaved + openRulesModal. 379 lines / 18,556
  bytes. index.html 4,203 → 3,825.
- Interop: 13 exposures (5 handler promotions), 16 privates.
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; 18,556B exact;
  exercise 5/5 (micBtnHTML graceful outside Capacitor, buildTaskWithImages
  empty+image roundtrip).

### Store.js pass — status after M32c (2026-06-10, Fable session 1)

**Done this pass (13 commits, all on refactor/frontend):** design doc ratified
(Option A) → step-0 STORE consolidation (74 vars) → M22 rich-text → M23
conversation → M24 resume-preview → M25 agent-log → M26 hivemind → M27
agent-console → M29(+28) modal-manager → M30 render-core → M31 interactions
(arm relocation) → dead-timeAgoShort deletion → M32a project-forms → M32b
appearance → M32c composer-extras.
**index.html 11,761 → 3,825 lines** (start-of-track 25,165). 12 new modules
≈ 8,100L. ZERO bridges added across the entire pass — Option A held
everywhere. Every cut: byte-verbatim two-sided reassembly, formal scans
(shadowing/duplicates/handler-targets/parse-time code/`this`), boot-smoke
5/5, bg-framing baseline-only, throwaway real-server exact-bytes, headless
feature exercise, sw bump, harness route.fulfills.

**Remaining queue (~1,100L, same recipe, cutter ready at
`_scratch/cut_module.py`):**
- Mobile UI family (~937–1290: app bar, chat list, Android-back sentinels,
  nav drawer) — history/popstate arms are boot-coupled; expect arm
  relocation (M31 pattern) or stay-inline verdicts per arm.
- Feed (~2445–2628 current) + Feed Toggle; renderFeed called by render().
- Create-form attachments (~2073+) + GitHub sync (~1952+) + update-notif
  (~2007+) + code-sync (~2140+).
- THEN: the inline residue is boot skeleton + STORE + shared glue
  (esc/refreshSilent/render/refreshModal/sizeAgentChat/settings helpers/
  run-history renderers) ≈ inline script ~2,000 → target met.
**Dead-code deletion pass (deliberate, post-track):** hivemindTabHTML +
loadHiveminds (superseded by cross-project view), orphaned agentConvNew
comment fragment, empty section stubs.

## Phase 4 — M32d: project actions + BOOT-RACE FIX → `static/js/project-actions.js` (2026-06-10)

- 2 segments [(1887,1951)+(2007,2444)] carved around showToast/
  showActionToast (core shared glue — stays inline): create-form attachment
  family, update-notification, GitHub sync, code-sync, backlog undo/showDone,
  attachment/note panels + upload. 503 lines / 22,622 bytes. index.html
  3,825 → 3,323 (then +14 for the gate comment block below).
- **REAL BUG FOUND & FIXED — cold-boot module race.** The `fetchProjects()`
  boot continuation calls window-exposed module fns (`_loadOpenModalsSnapshot`,
  `openProjectModal`, `fetchAgentStatus`, `_handleDeepLinkFromUrl`). With 30+
  deferred modules the fetch can resolve BEFORE late modules evaluate →
  intermittent `ReferenceError` on cold load (reachable since ~M29; surfaced
  by M32d's real-server exercise — route.fulfilled smoke loads modules
  instantly and can't see it). Fix: `_modulesReady` promise ARMED AT PARSE
  TIME resolving on DOMContentLoaded (deferred modules are spec-guaranteed
  done by then); the continuation awaits it. **`document.readyState` is NOT
  a valid gate — it reads 'interactive' before deferred modules run** (first
  fix attempt failed exactly there; proven by a 600ms-throttled
  modal-manager.js test: 4/4 PASS post-fix, ReferenceError pre-fix).
- Interop: 20 exposures (3 promotions), 4 privates → 1 (renderCreatePreviews).
- Gates: parse ×2; boot-smoke 5/5; bg-framing baseline; 22,622B exact;
  exercise green + the forced-lateness race test.
