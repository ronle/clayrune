#!/usr/bin/env node
/**
 * Mission Control dashboard — headless boot smoke test.
 *
 * WHY THIS EXISTS
 * ---------------
 * `node --check` proves the SPA's inline <script> blocks *parse*, but it cannot
 * catch a RUNTIME throw during boot. On 2026-06-08 a temporal-dead-zone bug
 * (a `let` referenced by a function called before its declaration) threw a
 * ReferenceError at the top level of the boot script, aborting it before
 * fetchProjects() ran. The dashboard hung on its "Loading..." placeholder with
 * an empty project grid — and it shipped, because the author's open tab kept
 * the old JS (server restart != tab reload), so nobody hit a fresh load.
 *
 * This test loads the REAL static/index.html in headless Chromium and asserts
 * the project grid actually populates. A boot-aborting throw leaves the grid
 * empty, which fails the test loudly.
 *
 * It is hermetic: it fulfills the page + a canned /api/projects via Playwright
 * request interception and ABORTS every other request (CDNs + non-essential
 * API). The SPA is written to degrade on fetch failure (e.g. loadDomains()
 * falls back to a default list), so aborting exercises those real fallbacks
 * rather than guessing response shapes. No running MC server, no data, no net.
 *
 * RUN
 *   cd tools/smoke
 *   npm install
 *   npx playwright install chromium      # one-time, downloads the browser
 *   npm test
 *
 * Exit code 0 = grid rendered; 1 = boot failed / grid empty / harness error.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
// The SPA's styles were extracted from the inline <style> into this file
// (modernization Phase 3 module 1) — serve the real one or the shell under
// test renders unstyled (and any future CSS-dependent assertion lies).
const APP_CSS = readFileSync(resolve(REPO_ROOT, 'static', 'css', 'app.css'), 'utf8');
// Ask Claydo ES module, extracted from the inline <script> (Phase 3 module 2).
// Every extracted /static/js/*.js must be fulfilled here or the hermetic
// harness aborts its request and the SPA boots without that feature.
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
// Mermaid render pipeline ES module (Phase 3 module 9) — same rule as claydo.js above.
const MERMAID_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mermaid.js'), 'utf8');
// Search-past-chats ES module (Phase 3 module 10) — same rule as claydo.js above.
const SEARCH_CHATS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'search-chats.js'), 'utf8');
// Backlog actions ES module (Phase 3 module 11) — same rule as claydo.js above.
const BACKLOG_ACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'backlog-actions.js'), 'utf8');
// Cross-project backlog ES module (Phase 3 module 12) — same rule as claydo.js above.
const CROSS_BACKLOG_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'cross-backlog.js'), 'utf8');
// Scheduler ES module (Phase 3 module 13) — same rule as claydo.js above.
const SCHEDULER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'scheduler.js'), 'utf8');
// MCP servers ES module (Phase 3 module 14) — same rule as claydo.js above.
const MCP_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mcp.js'), 'utf8');
// System status ES module (Phase 3 module 15) — same rule as claydo.js above.
const SYSTEM_STATUS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'system-status.js'), 'utf8');
// Update/Power/restart ES module (Phase 3 module 16) — same rule as claydo.js above.
const UPDATE_POWER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'update-power.js'), 'utf8');
// Provider-auth ES module (Phase 3 module 17) — same rule as claydo.js above.
const PROVIDER_AUTH_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'provider-auth.js'), 'utf8');
// Schedule-banner ES module (Phase 3 module 18) — same rule as claydo.js above.
const SCHEDULE_BANNER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'schedule-banner.js'), 'utf8');
// Provider-settings ES module (Phase 3 module 19) — same rule as claydo.js above.
const PROVIDER_SETTINGS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'provider-settings.js'), 'utf8');
// Process-manager ES module (Phase 3 module 20) — same rule as claydo.js above.
const PROCESS_MANAGER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'process-manager.js'), 'utf8');
// Cross-project Hivemind ES module (Phase 3 module 21) — same rule as claydo.js above.
const CROSS_HIVEMIND_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'cross-hivemind.js'), 'utf8');
const RENDER_CORE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'render-core.js'), 'utf8');
const MODAL_MANAGER_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'modal-manager.js'), 'utf8');
const AGENT_CONSOLE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'agent-console.js'), 'utf8');
const HIVEMIND_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'hivemind.js'), 'utf8');
const AGENT_LOG_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'agent-log.js'), 'utf8');
const RESUME_PREVIEW_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'resume-preview.js'), 'utf8');
const CONVERSATION_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'conversation.js'), 'utf8');
const RICH_TEXT_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'rich-text.js'), 'utf8');
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');

const ORIGIN = 'http://mc.smoke.test';   // arbitrary; every request is intercepted
const BOOT_TIMEOUT_MS = 15000;

// 4x4 PNG — a stand-in custom background image.
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAA//8DABjcA0/9b3pPAAAAAElFTkSuQmCC';

// Boot scenarios. Each is a fresh page with a different localStorage appearance
// state (set BEFORE first paint via addInitScript). Every one must boot and
// render the grid. These cover the appearance code paths that run during boot —
// the area that has now produced TWO boot-aborting TDZ bugs (bgMode, then
// _bgDimsLoading). The "image, NO dims" case is the one that hung the whole UI
// on 2026-06-08: a legacy image with no stored dims makes applyDashboardBackground
// call _bgLoadImageDims at boot. Without it here the test was falsely green.
const SCENARIOS = [
  { name: 'default theme (no bg)', ls: {} },
  { name: 'image bg, NO stored dims (legacy)', ls: { mc_bg_mode: 'image', mc_bg_image: PNG } },
  { name: 'image bg, with dims + framing', ls: { mc_bg_mode: 'image', mc_bg_image: PNG, mc_bg_imgw: '4', mc_bg_imgh: '4', mc_bg_zoom: '140', mc_bg_posx: '30', mc_bg_posy: '70' } },
  { name: 'solid color bg', ls: { mc_bg_mode: 'color', mc_bg_color: '#123456' } },
  { name: 'warm tone', ls: { mc_tone: 'warm' } },
];

async function runScenario(browser, sc) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, sc.ls);
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/static/css/app.css')
      return route.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: APP_CSS });
    if (path === '/static/js/claydo.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: CLAYDO_JS });
    if (path === '/static/js/mobile-pairing.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: MOBILE_PAIRING_JS });
    if (path === '/static/js/walkthrough.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: WALKTHROUGH_JS });
    if (path === '/static/js/skills-panel.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SKILLS_PANEL_JS });
    if (path === '/static/js/settings-drill.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SETTINGS_DRILL_JS });
    if (path === '/static/js/settings-sections.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SETTINGS_SECTIONS_JS });
    if (path === '/static/js/terminal.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: TERMINAL_JS });
    if (path === '/static/js/mermaid.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: MERMAID_JS });
    if (path === '/static/js/search-chats.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SEARCH_CHATS_JS });
    if (path === '/static/js/backlog-actions.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: BACKLOG_ACTIONS_JS });
    if (path === '/static/js/cross-backlog.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: CROSS_BACKLOG_JS });
    if (path === '/static/js/scheduler.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SCHEDULER_JS });
    if (path === '/static/js/mcp.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: MCP_JS });
    if (path === '/static/js/system-status.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SYSTEM_STATUS_JS });
    if (path === '/static/js/update-power.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: UPDATE_POWER_JS });
    if (path === '/static/js/provider-auth.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: PROVIDER_AUTH_JS });
    if (path === '/static/js/schedule-banner.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: SCHEDULE_BANNER_JS });
    if (path === '/static/js/provider-settings.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: PROVIDER_SETTINGS_JS });
    if (path === '/static/js/process-manager.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: PROCESS_MANAGER_JS });
    if (path === '/static/js/cross-hivemind.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: CROSS_HIVEMIND_JS });
    if (path === '/static/js/render-core.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: RENDER_CORE_JS });
    if (path === '/static/js/modal-manager.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: MODAL_MANAGER_JS });
    if (path === '/static/js/agent-console.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: AGENT_CONSOLE_JS });
    if (path === '/static/js/hivemind.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: HIVEMIND_JS });
    if (path === '/static/js/agent-log.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: AGENT_LOG_JS });
    if (path === '/static/js/resume-preview.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: RESUME_PREVIEW_JS });
    if (path === '/static/js/conversation.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: CONVERSATION_JS });
    if (path === '/static/js/rich-text.js')
      return route.fulfill({ status: 200, contentType: 'text/javascript; charset=utf-8', body: RICH_TEXT_JS });
    if (path === '/api/projects')
      return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config')
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    return route.abort();  // CDNs + non-essential API → SPA fallbacks handle it
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });

  let cardCount = 0, timedOut = false;
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
    cardCount = await page.locator('#projects-col .card').count();
  } catch { timedOut = true; cardCount = await page.locator('#projects-col .card').count().catch(() => 0); }

  const ok = cardCount > 0;
  if (ok) {
    console.log(`✅ ${sc.name}: booted, grid rendered ${cardCount} tile(s).`);
  } else {
    console.error(`❌ ${sc.name}: project grid never rendered (boot aborted).`);
    const colText = await page.locator('#projects-col').innerText().catch(() => '(unreadable)');
    console.error(`     #projects-col text: ${JSON.stringify(colText.slice(0, 120))}`);
    if (timedOut) console.error(`     (waited ${BOOT_TIMEOUT_MS}ms for "#projects-col .card")`);
    if (pageErrors.length) {
      console.error('     Uncaught exception(s) during boot — likely the cause:');
      pageErrors.forEach((e) => console.error(`       • ${e}`));
    } else {
      console.error('     No uncaught exception captured — check the /api/projects fetch path.');
    }
  }
  await ctx.close();
  return ok;
}

let browser, allOk = false;
try {
  browser = await chromium.launch();
  const results = [];
  for (const sc of SCENARIOS) results.push(await runScenario(browser, sc));
  allOk = results.every(Boolean);
  console.log(allOk
    ? `\n✅ PASS — all ${results.length} boot scenarios rendered the grid.`
    : `\n❌ FAIL — ${results.filter((r) => !r).length}/${results.length} boot scenario(s) failed.`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(allOk ? 0 : 1);
}
