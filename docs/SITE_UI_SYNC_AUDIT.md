# clayrune.io — UI sync audit

**Owner:** Steward (charter `2375e16b`). **2026-07-11.**
Site project: `clayrune_website` → `<clayrune_website checkout>`
(not a git repo; deployed to Cloudflare via `_build_deploy.sh` / wrangler).

The app's conversation UI was redesigned (3-pane rail | thread | Surfaces, cream
`tone-warm` palette, per-chat model picker, activity states). The public site
still showed the **pre-redesign** product. This is the inventory and its status.

---

## 1. Interactive demo — FIXED

`demo/demo-app.{html,css,js}` was dated **Jun 8** and modelled the *old*
dashboard (`mc-app-bar` / `content-area` / `bottom-tab-bar`). Rebuilt against the
current 3-pane UI.

- `demo/_real-ui.css` — **extracted verbatim** from `static/css/app.css`
  (`:root`, `body.tone-warm`, `.agent-*`, `.conv-*`, `.act-*`, `.composer-*`,
  `.surface-*`, `.typing-indicator`, `.btn-*`), including `@media` **and
  `@container`** blocks. Fidelity per `DEMO_REQUIREMENTS.md` §3 comes from
  reusing real CSS, not re-approximating it.
- `demo/demo-shell.css` — demo-only chrome (bounded frame, coach-marks,
  settings modal, replay).
- `demo/demo-app.{html,js}` — rebuilt; scripted run is
  dispatch → plan → **approval gate** → 6 tool lines → summary → completion.
- `demo.html` iframe bumped `?v=r10` → `?v=r11`.
- `demo/README.md` — files, embedding, what's simulated, how to re-sync.
- `demo/demo-app.css` — now **unused** (old bundle's stylesheet). Left in place
  deliberately (not mine to delete); safe to remove.

### Two real bugs caught during verification

1. **Coach-mark backdrop ate the click** it was telling the user to make. The
   dim comes entirely from `.coach-spot`'s `9999px` box-shadow, so the separate
   backdrop must be `pointer-events: none` — otherwise "Approve & run" is
   unclickable and the demo dead-ends. Now commented as load-bearing.
2. **`@container` rules were being dropped.** The 3-pane is driven by a
   `convpane` container query (`container-type: inline-size` on
   `.agent-panel.agent-3pane`). The first extractor only walked `@media`, so the
   rail width, the centered reading column, and the Surfaces panel silently
   never applied — a layout that looks *subtly* wrong with no error. Any future
   re-extraction MUST walk `@container`.

**Not a bug (verified, left alone):** tool/status lines render as *centered*
mono chips. That is the product's real styling (`app.css:3430-3440`), not demo
drift.

### Verification (Playwright, served over plain http)

3-pane renders · warm `rgb(246,240,228)` ↔ dark `rgb(12,14,20)` · full scripted
run completes · approval gate blocks until clicked · 5 settings interactive and
persisted · 390px no h-overflow · **zero console errors, zero external network
requests** (CSP-safe).

---

## 2. Product screenshots — STALE, needs human capture

All three product stills on the live site pre-date the redesign:

| Asset | Date | Used by |
|---|---|---|
| `assets/clayrune-chat-open.png` | Jun 2 | index (×2) |
| `assets/clayrune-dashboard-light.png` | Jun 2 | index (×1) |
| `assets/mobile-home-dark.jpg` | Jun 1 | index (×2) |

**Partially unblocked:** `assets/clayrune-chat-open-v2.png` was generated from
the rebuilt demo at 2× (1960px wide, so the Surfaces panel is visible). It is
the *real* current UI with *fake* data — safe to publish, no private project
names. It is a **new file**; the live `clayrune-chat-open.png` was not
overwritten. Swapping it in is a one-line edit + deploy.

**Still human-only:** the dashboard/project-grid still and the mobile still —
the demo has no project grid, and the mobile shot needs a real phone. These are
the same capture session as `MARKETING_ASSETS_SPEC.md` A1–A3; do them together.

---

## 3. Copy — mostly fine

`index.html` title is already *"One dashboard for all your AI agents"*, which is
consistent with the campaign plan's positioning (fleet, not another coding
agent). No rewrite needed this pass. The **"Isn't this just Claude Code?"**
comparison table now in the README is the one thing worth porting to the landing
page — it kills the #1 visitor confusion.

---

## 4. Deploying

Nothing here is live yet. Publishing is irreversible → raised as
`DECISION NEEDED` on the charter, not executed. Deploy is
`_build_deploy.sh` (wrangler → Cloudflare Pages).
