#!/usr/bin/env node
/**
 * Focused check for Settings → Background "Fit & framing" (zoom + position).
 *
 * Verifies that applyDashboardBackground() sizes the body background from the
 * cover baseline × zoom and positions it at the chosen focal point, that the
 * choice persists to localStorage, that it recomputes on viewport resize, and
 * that the Settings live-preview <img> mirrors the crop. Hermetic: the page +
 * /api/projects + /api/config are fulfilled from fixtures, everything else is
 * aborted (same approach as boot-smoke.mjs). No server, no network, no data.
 *
 *   cd tools/smoke && node bg-framing-check.mjs
 */
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
// Real extracted stylesheet (modernization Phase 3 module 1) — without it the
// shell under test renders unstyled.
const APP_CSS = readFileSync(resolve(REPO_ROOT, 'static', 'css', 'app.css'), 'utf8');
// Ask Claydo ES module (Phase 3 module 2) — fulfilled so the hermetic harness
// doesn't abort the request (every extracted /static/js/*.js needs this).
const CLAYDO_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'claydo.js'), 'utf8');
// Mobile pairing ES module (Phase 3 module 3) — same rule as claydo.js above.
const MOBILE_PAIRING_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mobile-pairing.js'), 'utf8');
// Walkthrough / tour ES module (Phase 3 module 4) — same rule as claydo.js above.
const WALKTHROUGH_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'walkthrough.js'), 'utf8');
// Skills panel ES module (Phase 3 module 5) — same rule as claydo.js above.
const SKILLS_PANEL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'skills-panel.js'), 'utf8');
// Settings drill-down ES module (Phase 3 module 6) — same rule as claydo.js above.
const SETTINGS_DRILL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'settings-drill.js'), 'utf8');
// Settings sections ES module (Phase 3 module 7) — same rule as claydo.js above.
const SETTINGS_SECTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'settings-sections.js'), 'utf8');
// Terminal pop-out ES module (Phase 3 module 8) — same rule as claydo.js above.
const TERMINAL_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'terminal.js'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => fail('pageerror: ' + (e.message || e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/static/css/app.css') return route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: APP_CSS });
    if (path === '/static/js/claydo.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: CLAYDO_JS });
    if (path === '/static/js/mobile-pairing.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: MOBILE_PAIRING_JS });
    if (path === '/static/js/walkthrough.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: WALKTHROUGH_JS });
    if (path === '/static/js/skills-panel.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SKILLS_PANEL_JS });
    if (path === '/static/js/settings-drill.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SETTINGS_DRILL_JS });
    if (path === '/static/js/settings-sections.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SETTINGS_SECTIONS_JS });
    if (path === '/static/js/terminal.js') return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: TERMINAL_JS });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  // Inject a 400×400 image and drive the public setters, exactly as the UI does.
  const applied = await page.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 400; c.height = 400;
    const g = c.getContext('2d'); g.fillStyle = '#3a6bb5'; g.fillRect(0, 0, 400, 400);
    const url = c.toDataURL('image/png');
    localStorage.setItem('mc_bg_image', url);
    localStorage.setItem('mc_bg_imgw', '400');
    localStorage.setItem('mc_bg_imgh', '400');
    setBgMode('image');     // globals defined by the SPA
    setBgZoom(150);
    setBgPosX(20);
    setBgPosY(80);
    return { size: document.body.style.backgroundSize, pos: document.body.style.backgroundPosition };
  });

  // cover for 400×400 at 1280×800 = max(1280/400, 800/400) = 3.2; ×1.5 = 4.8 → 1920px.
  applied.size === 'cover, 1920px 1920px' ? ok('body background-size = cover baseline × zoom (' + applied.size + ')')
    : fail('unexpected background-size: ' + applied.size);
  applied.pos === 'center center, 20% 80%' ? ok('body background-position = focal point (' + applied.pos + ')')
    : fail('unexpected background-position: ' + applied.pos);

  const persisted = await page.evaluate(() => ({
    zoom: localStorage.getItem('mc_bg_zoom'), x: localStorage.getItem('mc_bg_posx'), y: localStorage.getItem('mc_bg_posy'),
  }));
  (persisted.zoom === '150' && persisted.x === '20' && persisted.y === '80')
    ? ok('framing persisted to localStorage (zoom=150 x=20 y=80)')
    : fail('framing not persisted: ' + JSON.stringify(persisted));

  // Resize → recompute. At 800×800, cover = max(2, 2) = 2; ×1.5 = 3.0 → 1200px.
  await page.setViewportSize({ width: 800, height: 800 });
  await page.waitForTimeout(220); // debounced 120ms
  const resized = await page.evaluate(() => document.body.style.backgroundSize);
  resized === 'cover, 1200px 1200px' ? ok('background-size recomputed on resize (' + resized + ')')
    : fail('resize did not recompute: ' + resized);

  // Live preview <img> reflects scale + object-position (open Settings → Appearance → Background).
  const preview = await page.evaluate(() => {
    if (typeof openSettings === 'function') openSettings();
    if (typeof drillSettings === 'function') drillSettings('appearance');
    // appearance is multi-section; jump straight to the Background sub-section if possible
    if (typeof _settingsActiveCat !== 'undefined') {}
    return null;
  });
  // The preview only exists once the Background detail pane renders; drive via the
  // search view which flattens all panes into the DOM at once.
  await page.evaluate(() => { if (typeof filterSettings === 'function') filterSettings('framing'); });
  const pv = await page.$('#mc-bg-preview-img');
  if (pv) {
    const t = await pv.evaluate((el) => ({ tr: el.style.transform, op: el.style.objectPosition }));
    /scale\(1\.5\)/.test(t.tr) ? ok('preview img transform mirrors zoom (' + t.tr + ')') : fail('preview transform wrong: ' + t.tr);
    /20%\s+80%/.test(t.op) ? ok('preview img object-position mirrors focal point (' + t.op + ')') : fail('preview object-position wrong: ' + t.op);
  } else {
    fail('preview img (#mc-bg-preview-img) did not render');
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS — background framing works.' : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
