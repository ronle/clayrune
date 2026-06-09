# Clayrune — Public Interactive Demo

A self-contained, **fully simulated** replica of the Clayrune dashboard for the
marketing site (`clayrune.io/demo`). It looks and behaves like the real product,
walks a first-time visitor through a scripted agent run, and lets them play with
the real Settings panel — with **no backend, no network, and no real data**.

## Files (the shipped bundle)

| File | Purpose |
|------|---------|
| `demo-app.html` | Markup shell — sidebar, header, content mount, project / inventory / settings modals, coach-mark layer. No inline scripts, no inline event handlers. |
| `demo-app.css`  | Styling extracted verbatim from the real dashboard (`static/index.html`) and re-scoped from the viewport onto a bounded `.demo-root` frame. Dark theme is the default; warm/editorial light themes + 6 accents included. |
| `demo-app.js`   | The whole simulation: fake data, dashboard, agent console + streaming, plan→approve flow, the WhatsApp-style Settings drill-down, and the guided coach-mark tour. Vanilla JS, zero dependencies. |

> `_verify.mjs` is a **dev-only** Playwright harness (not part of the bundle —
> do not ship it). It drives the full run headless and asserts zero console
> errors and **zero network requests**. It expects Playwright at
> `../tools/smoke/node_modules`; run with `node _verify.mjs` from this folder.

## How to embed

The demo fills 100% of whatever box contains it and expects to live in a
**bounded frame (~1100×640)**, not full-screen. The simplest, most robust embed
is an `<iframe>` — it gives the demo its own viewport so the responsive
breakpoints track the *frame* size (so it goes single-column / chat-bubble on a
narrow frame even inside a wide page):

```html
<iframe src="/demo/demo-app.html"
        title="Clayrune interactive demo"
        style="width:100%; max-width:1100px; aspect-ratio:1100/640; border:0;
               border-radius:12px; overflow:hidden;"
        loading="lazy"></iframe>
```

You can also drop the three files inline into a `max-width` container and load
`demo-app.css` / `demo-app.js` on the page — but then the mobile breakpoints
follow the *page* viewport, not the container, so the iframe approach is
preferred for a bounded widget.

It runs over plain `http://` and over `file://` (open `demo-app.html` directly).

### Recommended Content-Security-Policy

The demo makes **no** network requests, uses **no** inline `<script>` and **no**
inline event handlers, and never calls `eval`. A strict policy it satisfies:

```
default-src 'none';
script-src  'self';
style-src   'self' 'unsafe-inline';
img-src     'self' data:;
font-src    'self';
base-uri    'none';
form-action 'none';
```

`style-src 'unsafe-inline'` is needed only because the UI uses inline `style="…"`
attributes for per-element colors (exactly as the real dashboard does). There is
no `connect-src` / `frame-src` — nothing reaches the network.

## The scripted walkthrough

A guided, 5-step coach-mark tour starts automatically (replay it anytime via the
amber **Demo** chip or the **?** button in the header). Each step advances on the
**real user action** — the “Next” button just performs that action for you:

1. **Your projects** — spotlight on the *Aurora Web* tile → open it. A project
   opens as a **centered floating window over the dimmed dashboard** (same
   theme-aware modal as the real app — light on the warm/editorial themes).
2. **Dispatch a task** — the composer is pre-filled with *“Add a dark-mode
   toggle to my site.”* → **Dispatch**.
3. The agent **streams a plan** (real `formatAgentText` markup: headers, bold,
   numbered list, file-path highlighting) and emits `[tool: ExitPlanMode]`, then
   waits → **Approve Plan**.
4. The agent **works** — authentic streamed `[tool: Read/Edit/Write/Bash]`
   markers, a code block, a `[✓ done …]` status line, then a **summary** with a
   files-changed table. The tile’s status pill flips to **Completed**.
5. **Make it yours** — opens **Settings** to explore.

Dispatching on the other (idle/working) projects runs a shorter generic turn so
nothing dead-ends.

## What is simulated / stubbed

- **Everything.** No `fetch`, no WebSocket, no `localhost`, no auth, no keys.
- **Fake data only** — 5 invented projects (Aurora Web, Ledger API, Trading
  Signals, Recipe Box, Docs Site), fake paths (`~/projects`), fake file names
  (`theme-toggle.js`, `styles.css`). No real users, projects, tokens, or paths.
- **The agent stream is a canned script** rendered through a faithful port of the
  product’s real line classifier (`agentLineCls`) and markdown highlighter
  (`formatAgentText`), so the output looks authentic. URLs are rendered as styled
  but **non-navigating** spans (a public page makes no outbound navigation).
- **The plan-approval flow** is the real UI pattern (green *Approve Plan* button +
  *Collapse Plan*), wired to advance the script — it talks to nothing.
- **Settings are real and interactive**, persisted to `localStorage` under the
  `clayrune_demo_*` namespace (never to a server). Interactively changeable:
  **Theme** (Dark / Warm / Editorial), **Accent** (6 options), **Density**,
  **Writing style**, **Background** (Theme / solid Color — applied live),
  **Model** (Auto / Opus 4.8 / Sonnet 4.6 / Haiku 4.5 —
  reflected live in the console’s model badge), **Effort**, **Permissions**,
  **Streaming (Mode B)** toggle, **Sticky settings**, **Enter-key** behavior,
  **Port**, **Auto-condense** + threshold, and the **Advanced features**
  checkboxes. Live search across all of them works. Provider/Connectivity panels
  are present but their actions are no-ops (“Disabled in demo”).
- **Skills**, **MCP**, and **Hivemind** open as **centered modals** — the same
  window type as a project (shared `.modal-content` chrome + dimmed backdrop,
  ✕ / minimize / backdrop / Esc to dismiss). Each modal leads with a “what is
  this?” **intro callout** explaining the feature. Skills/MCP then list sample
  rows rebuilt to match the real app: **name + scope badge** (`global` /
  `project: …`) **+ transport** (MCP), then the **command / description**, then
  the config **path · timestamp**, under a working **search + scope filter**
  bar. Sample data only — Edit / Delete / “＋ New” say “Disabled in demo.”
  **Backlog / Scheduler** still show a short “part of the full app” placeholder.

## Fidelity notes

The palette, layout shell (collapsible 52px→220px rail, 48px header), tiles +
status pills (`friendly-working/asking/stuck/done/idle`, pulsing live dot), the
agent console (`.agent-output` line classes + `hl-*` highlight markup), and the
Settings drill-down are all lifted from the real `static/index.html` so it is
visually accurate rather than an approximation.

Google Fonts are **not** loaded (CSP / no-network): the CSS keeps the real font
names (`Inter`, `JetBrains Mono`) first in each stack and falls back to system
fonts, so it picks up the brand fonts automatically if the host page already
serves them.
