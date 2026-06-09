# Frontend boot smoke test

A headless check that the Mission Control dashboard SPA (`static/index.html`)
actually **boots and renders the project grid** — not just that its JavaScript
parses.

## Why

`node --check` proves the inline `<script>` blocks parse, but it can't catch a
**runtime** throw during boot. On 2026-06-08 a temporal-dead-zone bug — a `let`
(`bgMode`) read by a function called *before* its declaration — threw

```
ReferenceError: Cannot access 'bgMode' before initialization
```

at the top level of the boot script. That aborted boot before `fetchProjects()`
ran, so the dashboard hung on its `Loading...` placeholder with an empty grid.
It shipped because the author's already-open tab kept the old JS (**server
restart ≠ tab reload**), so a fresh load was never exercised.

This test loads the real page in headless Chromium and asserts the grid
populates. The TDZ bug (and any future boot-aborting throw) makes the grid stay
empty → the test fails and prints the captured exception.

## How it works

`boot-smoke.mjs` intercepts all requests via Playwright:

- `/` → the real `static/index.html`
- `/api/projects` → `fixtures/projects.json` (two canned projects)
- `/api/config` → `{}`
- **everything else → aborted** — the SPA degrades on fetch failure (e.g.
  `loadDomains()` falls back to a default list), so aborting exercises those
  real fallbacks instead of guessing response shapes.

No running MC server, no real data, no network. It then waits for
`#projects-col .card` and asserts at least one tile rendered. On failure it
re-runs `render()` in-page to surface the throw that the app's own try/catch
swallowed.

## Run locally

```bash
cd tools/smoke
npm install
npx playwright install chromium   # one-time, downloads the browser (~110MB)
npm test
```

Exit `0` = grid rendered; exit `1` = boot failed (with diagnosis printed).

## CI

`.github/workflows/frontend-smoke.yml` runs this on every push/PR that touches
`static/index.html` or `tools/smoke/`.

## Maintenance

- If a new endpoint becomes **boot-critical** (its failure stops the grid from
  rendering), add a `route.fulfill` case for it in `boot-smoke.mjs`. Otherwise
  leave it aborted — that's the faithful "degrades gracefully" path.
- `fixtures/projects.json` mirrors the real project-object shape. If the
  renderer starts requiring a new field, add it here.
