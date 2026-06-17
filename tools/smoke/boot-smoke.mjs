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
// Beacon stylesheet (Phase 2 view) — same serve-or-the-shell-renders-unstyled rule.
const BEACON_CSS = readFileSync(resolve(REPO_ROOT, 'static', 'css', 'beacon.css'), 'utf8');
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
const FEED_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'feed.js'), 'utf8');
// Beacon ES module (Phase 2 view) — same rule as claydo.js above.
const BEACON_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'beacon.js'), 'utf8');
const MOBILE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mobile.js'), 'utf8');
const PROJECT_ACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'project-actions.js'), 'utf8');
const COMPOSER_EXTRAS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'composer-extras.js'), 'utf8');
const APPEARANCE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'appearance.js'), 'utf8');
const PROJECT_FORMS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'project-forms.js'), 'utf8');
const INTERACTIONS_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'interactions.js'), 'utf8');
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

// Static asset map (path → [contentType, body]) shared by the boot scenarios
// and the dispatch guard. Adding a new extracted /static/js/*.js module means
// adding ONE entry here — both code paths pick it up, and the boot scenarios
// self-validate the map (a missing/typo'd entry empties the grid and fails).
const STATIC_MAP = {
  '/static/css/app.css': ['text/css; charset=utf-8', APP_CSS],
  '/static/css/beacon.css': ['text/css; charset=utf-8', BEACON_CSS],
  '/static/js/claydo.js': ['text/javascript; charset=utf-8', CLAYDO_JS],
  '/static/js/mobile-pairing.js': ['text/javascript; charset=utf-8', MOBILE_PAIRING_JS],
  '/static/js/walkthrough.js': ['text/javascript; charset=utf-8', WALKTHROUGH_JS],
  '/static/js/skills-panel.js': ['text/javascript; charset=utf-8', SKILLS_PANEL_JS],
  '/static/js/settings-drill.js': ['text/javascript; charset=utf-8', SETTINGS_DRILL_JS],
  '/static/js/settings-sections.js': ['text/javascript; charset=utf-8', SETTINGS_SECTIONS_JS],
  '/static/js/terminal.js': ['text/javascript; charset=utf-8', TERMINAL_JS],
  '/static/js/mermaid.js': ['text/javascript; charset=utf-8', MERMAID_JS],
  '/static/js/search-chats.js': ['text/javascript; charset=utf-8', SEARCH_CHATS_JS],
  '/static/js/backlog-actions.js': ['text/javascript; charset=utf-8', BACKLOG_ACTIONS_JS],
  '/static/js/cross-backlog.js': ['text/javascript; charset=utf-8', CROSS_BACKLOG_JS],
  '/static/js/scheduler.js': ['text/javascript; charset=utf-8', SCHEDULER_JS],
  '/static/js/mcp.js': ['text/javascript; charset=utf-8', MCP_JS],
  '/static/js/system-status.js': ['text/javascript; charset=utf-8', SYSTEM_STATUS_JS],
  '/static/js/update-power.js': ['text/javascript; charset=utf-8', UPDATE_POWER_JS],
  '/static/js/provider-auth.js': ['text/javascript; charset=utf-8', PROVIDER_AUTH_JS],
  '/static/js/schedule-banner.js': ['text/javascript; charset=utf-8', SCHEDULE_BANNER_JS],
  '/static/js/provider-settings.js': ['text/javascript; charset=utf-8', PROVIDER_SETTINGS_JS],
  '/static/js/process-manager.js': ['text/javascript; charset=utf-8', PROCESS_MANAGER_JS],
  '/static/js/cross-hivemind.js': ['text/javascript; charset=utf-8', CROSS_HIVEMIND_JS],
  '/static/js/feed.js': ['text/javascript; charset=utf-8', FEED_JS],
  '/static/js/beacon.js': ['text/javascript; charset=utf-8', BEACON_JS],
  '/static/js/mobile.js': ['text/javascript; charset=utf-8', MOBILE_JS],
  '/static/js/project-actions.js': ['text/javascript; charset=utf-8', PROJECT_ACTIONS_JS],
  '/static/js/composer-extras.js': ['text/javascript; charset=utf-8', COMPOSER_EXTRAS_JS],
  '/static/js/appearance.js': ['text/javascript; charset=utf-8', APPEARANCE_JS],
  '/static/js/project-forms.js': ['text/javascript; charset=utf-8', PROJECT_FORMS_JS],
  '/static/js/interactions.js': ['text/javascript; charset=utf-8', INTERACTIONS_JS],
  '/static/js/render-core.js': ['text/javascript; charset=utf-8', RENDER_CORE_JS],
  '/static/js/modal-manager.js': ['text/javascript; charset=utf-8', MODAL_MANAGER_JS],
  '/static/js/agent-console.js': ['text/javascript; charset=utf-8', AGENT_CONSOLE_JS],
  '/static/js/hivemind.js': ['text/javascript; charset=utf-8', HIVEMIND_JS],
  '/static/js/agent-log.js': ['text/javascript; charset=utf-8', AGENT_LOG_JS],
  '/static/js/resume-preview.js': ['text/javascript; charset=utf-8', RESUME_PREVIEW_JS],
  '/static/js/conversation.js': ['text/javascript; charset=utf-8', CONVERSATION_JS],
  '/static/js/rich-text.js': ['text/javascript; charset=utf-8', RICH_TEXT_JS],
};

// Hermetic router: serve the page + every extracted module + canned
// /api/projects & /api/config; abort everything else so the SPA exercises its
// real fetch-failure fallbacks. Shared by all boot scenarios.
function fulfillStaticOrAbort(route) {
  const path = new URL(route.request().url()).pathname;
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC_MAP[path];
  if (hit)
    return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects')
    return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
  if (path === '/api/config')
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  return route.abort();  // CDNs + non-essential API → SPA fallbacks handle it
}

async function runScenario(browser, sc) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, sc.ls);
  await page.route('**/*', fulfillStaticOrAbort);
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

// Dispatch guard — boots the page, opens the +New composer, picks a persona, and
// drives dispatchAgent(). This is the cross-module path that broke on 2026-06-12:
// resume-preview.js (the dispatch code) referenced `pendingDispatchCharacter`, a
// module-scoped `let` in conversation.js — a ReferenceError across the ES-module
// boundary that aborted EVERY dispatch (new + resumed chats flipped to STOPPED
// with no agent spawned). The boot test never caught it because it never opened
// the composer. A clean dispatch must (a) raise no uncaught exception and (b)
// promote the optimistic tab to the server session id — proving the whole
// read + clear cross-module persona path executed.
async function runDispatchGuard(browser) {
  const PID = 'smoke_alpha';
  const SESS = 'smoke_sess_1';
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  // The POST firing is the key signal: the regression threw inside dispatchAgent
  // (clearPendingCharacter, line ~294) BEFORE the fetch, so the dispatch endpoint
  // was never hit. If this goes true, the whole cross-module persona path ran.
  let dispatchHit = false;
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    // Canned persona so the picker + cross-module character resolution run.
    if (path === '/api/characters')
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify([{ name: 'analyst', scope: 'global', display_name: 'Analyst', description: 'x', file: 'analyst.md', size: 10 }]) });
    // Canned dispatch success so the post-POST promotion path (incl.
    // clearPendingCharacter) executes end-to-end.
    if (/\/agent\/dispatch$/.test(path)) {
      dispatchHit = true;
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, session_id: SESS }) });
    }
    // The shared fixture has project_path="" (agentPanelHTML then shows the
    // "set project_path" notice, not the composer). Patch a non-empty path for
    // the guard only — the boot scenarios keep the byte-identical fixture.
    if (path === '/api/projects') {
      const patched = JSON.parse(PROJECTS_JSON);
      if (patched[0]) patched[0].project_path = '/smoke/alpha';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(patched) });
    }
    return fulfillStaticOrAbort(route);
  });
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
  } catch {
    console.error('❌ dispatch guard: grid never rendered (boot failed before the guard could run).');
    await ctx.close();
    return false;
  }

  const result = await page.evaluate(async ({ pid }) => {
    const out = { err: null, finalTab: null };
    try {
      if (typeof openProjectModal !== 'function') throw new Error('openProjectModal not defined');
      if (typeof dispatchAgent !== 'function') throw new Error('dispatchAgent not defined');
      openProjectModal(pid);
      window.agentConvNew = window.agentConvNew || {};
      agentConvNew[pid] = true;                       // force the +New composer
      if (typeof refreshModal === 'function') refreshModal();
      await new Promise((r) => setTimeout(r, 600));   // let _ensureCharacters resolve
      // Pick the persona — exercises setComposerCharacter plus the cross-module
      // getPendingCharacter/resolveCharacterMeta reads inside dispatchAgent.
      if (typeof setComposerCharacter === 'function') setComposerCharacter(pid, 'global:analyst');
      let ta = document.getElementById('agent-task-' + pid);
      if (!ta && typeof newAgentTab === 'function') {
        newAgentTab(pid);                              // the real "+ New" action
        await new Promise((r) => setTimeout(r, 400));
        ta = document.getElementById('agent-task-' + pid);
      }
      if (!ta) {
        out.diag = {
          modalWindows: document.querySelectorAll('.modal-window').length,
          textareas: Array.from(document.querySelectorAll('textarea')).map((e) => e.id || e.className),
        };
        throw new Error('composer textarea (#agent-task-' + pid + ') not rendered');
      }
      ta.value = 'smoke dispatch ping';
      await dispatchAgent(pid);
      await new Promise((r) => setTimeout(r, 300));
      out.finalTab = (window.activeAgentTab || {})[pid] || null;
    } catch (e) {
      out.err = e.message + ' | ' + ((e.stack || '').split('\n')[1] || '').trim();
    }
    return out;
  }, { pid: PID });

  await ctx.close();

  // EventSource/aborted-fetch noise (SSE + non-essential API are aborted) is
  // expected and not a real failure; only genuine uncaught exceptions count.
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  let ok = true;
  if (result.err) { ok = false; console.error(`❌ dispatch guard: dispatchAgent threw — ${result.err}`); if (result.diag) console.error('     diag: ' + JSON.stringify(result.diag)); }
  if (uncaught.length) {
    ok = false;
    console.error('❌ dispatch guard: uncaught exception(s) during dispatch:');
    uncaught.forEach((e) => console.error(`       • ${e}`));
  }
  if (ok && !dispatchHit) {
    ok = false;
    console.error('❌ dispatch guard: dispatchAgent never reached the POST — it aborted mid-path ' +
      '(the cross-module persona code throws before the fetch in the regression this guards).');
  }
  if (ok) console.log('✅ dispatch guard: +New composer dispatched cleanly to the server (persona path, no cross-module throw).');
  return ok;
}

let browser, allOk = false;
try {
  browser = await chromium.launch();
  const results = [];
  for (const sc of SCENARIOS) results.push(await runScenario(browser, sc));
  // Cross-module dispatch guard — runs after the boot scenarios so a boot
  // regression is reported on its own first.
  results.push(await runDispatchGuard(browser));
  allOk = results.every(Boolean);
  console.log(allOk
    ? `\n✅ PASS — ${SCENARIOS.length} boot scenarios + dispatch guard all green.`
    : `\n❌ FAIL — ${results.filter((r) => !r).length}/${results.length} check(s) failed.`);
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(allOk ? 0 : 1);
}
