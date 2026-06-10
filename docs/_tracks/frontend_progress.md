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
