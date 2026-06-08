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
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');

const ORIGIN = 'http://mc.smoke.test';   // arbitrary; every request is intercepted
const BOOT_TIMEOUT_MS = 15000;

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();

  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/api/projects')
      return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config')
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    // Everything else → fail; the SPA's own try/catch fallbacks handle it.
    return route.abort();
  });

  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message || String(err)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });

  // THE ASSERTION: the project grid must populate with tiles. A boot-aborting
  // throw leaves #projects-col on its "Loading..." placeholder forever.
  let cardCount = 0;
  let timedOut = false;
  try {
    await page.waitForSelector('#projects-col .card', { timeout: BOOT_TIMEOUT_MS });
    cardCount = await page.locator('#projects-col .card').count();
  } catch {
    timedOut = true;
    cardCount = await page.locator('#projects-col .card').count().catch(() => 0);
  }

  if (cardCount > 0) {
    console.log(`✅ PASS — dashboard booted, project grid rendered ${cardCount} tile(s).`);
    if (pageErrors.length) {
      console.log(`   (note: ${pageErrors.length} non-fatal pageerror(s) during boot — grid still`);
      console.log('    rendered, so these are not boot-blocking:)');
      pageErrors.forEach((e) => console.log(`     • ${e}`));
    }
    exitCode = 0;
  } else {
    console.error('❌ FAIL — project grid never rendered (dashboard boot was aborted).');
    const colText = await page.locator('#projects-col').innerText().catch(() => '(unreadable)');
    console.error(`   #projects-col text: ${JSON.stringify(colText.slice(0, 140))}`);
    if (timedOut) console.error(`   (waited ${BOOT_TIMEOUT_MS}ms for "#projects-col .card")`);

    // Self-diagnosis: re-run render() in-page to surface a throw that the app's
    // own try/catch swallowed (fetchProjects() catches render() errors and shows
    // "Failed to load", hiding the real stack).
    const renderErr = await page.evaluate(() => {
      try { if (typeof render === 'function') { render(); return null; } return 'render() is not defined — boot aborted before it was reached'; }
      catch (e) { return (e && e.stack) || String(e); }
    }).catch((e) => `(evaluate failed: ${e})`);

    if (pageErrors.length) {
      console.error('   Uncaught exception(s) during boot — likely the cause:');
      pageErrors.forEach((e) => console.error(`     • ${e}`));
    }
    if (renderErr) console.error(`   render() throws: ${renderErr.split('\n').slice(0, 4).join('\n     ')}`);
    if (!pageErrors.length && !renderErr) {
      console.error('   No exception captured — check the /api/projects fetch path.');
      if (consoleErrors.length) consoleErrors.slice(0, 8).forEach((e) => console.error(`     console.error: ${e}`));
    }
    exitCode = 1;
  }
} catch (err) {
  console.error('❌ FAIL — smoke harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
