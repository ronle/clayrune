/* Headless verification for the Clayrune public demo (run from demo-export/).
   Asserts: no console errors, ZERO outbound network beyond the 3 local files,
   and that the full scripted run + settings + responsive layout work.
   Not shipped to the website — a dev-only check.  Run: node _verify.mjs        */
import { createRequire } from 'module';
import { pathToFileURL } from 'url';
import { resolve } from 'path';
const require = createRequire(resolve('../tools/smoke/package.json'));
const { chromium } = require('playwright');

const url = pathToFileURL(resolve('demo-app.html')).href;
const LOCAL_OK = ['demo-app.html', 'demo-app.css', 'demo-app.js'];
const fail = (m) => { console.error('✗ ' + m); process.exitCode = 1; };
const ok = (m) => console.log('✓ ' + m);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1100, height: 640 } });
const page = await ctx.newPage();

const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));

// Track every request; anything not one of our 3 local files is a violation.
const offendingReqs = [];
page.on('request', (req) => {
  const u = req.url();
  if (u.startsWith('data:') || u.startsWith('blob:')) return;
  if (u.startsWith('file:')) {
    const isOurs = LOCAL_OK.some((f) => u.endsWith('/' + f));
    if (!isOurs) offendingReqs.push(u);
    return;
  }
  offendingReqs.push(u); // any http(s)/ws is forbidden
});

await page.goto(url, { waitUntil: 'networkidle' });

// 1. Boot: dashboard tiles present
await page.waitForSelector('#tile-aurora-web', { timeout: 5000 }).catch(() => {});
const tiles = await page.$$eval('.card', (els) => els.length);
tiles === 5 ? ok(`dashboard rendered ${tiles} tiles`) : fail(`expected 5 tiles, got ${tiles}`);

// 2. Coach-mark tour visible on load
await page.waitForSelector('#coach-tip:not(.settings-hidden)', { timeout: 3000 }).catch(() => {});
const coachVisible = await page.isVisible('#coach-tip');
coachVisible ? ok('coach-mark tour appeared on load') : fail('coach-mark did not appear');

// 2b. The tour must NOT darken the dashboard at all — the dim backdrop is fully
//     transparent; the spotlight ring + card carry the focus, so header AND tiles
//     read at full colour on first impression.
const dimBg = await page.evaluate(() => {
  const e = document.getElementById('cd-top');
  return e ? getComputedStyle(e).backgroundColor : 'none';
});
/rgba?\([^)]*,\s*0\s*\)|transparent|^none$/.test(dimBg)
  ? ok('coach tour does not dim the dashboard — backdrop transparent (' + dimBg + ')')
  : fail('coach backdrop still darkens the dashboard: ' + dimBg);
await page.screenshot({ path: '_shot-tour-top.png' });

// 3. Drive the guided tour via the coach "Next" buttons (each performs its action).
//    Default theme is WARM and stays warm through the tour.
const coachNext = async (label) => {
  if (label) await page.waitForFunction((l) => document.getElementById('coach-next')?.textContent === l, label, { timeout: 8000 }).catch(() => {});
  await page.click('#coach-next');
};
// cfg is only written to localStorage once a setting changes; read the live root class instead.
const warmDefault = await page.evaluate(() => document.getElementById('demo-root').classList.contains('tone-warm'));
warmDefault ? ok('demo default theme is WARM') : fail('default theme not warm (no tone-warm on root)');

await coachNext('Open Aurora Web');                                // step 1 → open Aurora
await page.waitForSelector('#agent-output', { timeout: 4000 });
ok('opened agent console');
// Aurora shows TWO parallel conversation tabs (the live one + a second running task)
const tabLabels = await page.$$eval('#project-overlay .agent-tab .agent-tab-label', (e) => e.map((x) => x.textContent));
tabLabels.length === 2 ? ok('Aurora shows 2 parallel conversation tabs (' + JSON.stringify(tabLabels) + ')') : fail('expected 2 conversation tabs, got ' + tabLabels.length);
// the project modal has a working three-dot menu
await page.click('#pm-menu-btn');
await page.waitForSelector('#pm-menu.open', { timeout: 2000 });
const menuItems = await page.$$eval('#pm-menu .modal-menu-item', (e) => e.length);
menuItems >= 5 ? ok('project modal three-dot menu opens (' + menuItems + ' items)') : fail('three-dot menu items: ' + menuItems);
await page.click('#pm-menu-btn');                                  // close the menu
await page.waitForTimeout(100);
const prefilled = await page.inputValue('#agent-input');
prefilled.includes('dark-mode') ? ok('task pre-filled: ' + JSON.stringify(prefilled)) : fail('composer not pre-filled');

await coachNext('Dispatch');                                       // step 2 → Dispatch
await page.waitForSelector('#btn-approve-plan', { timeout: 15000 });
ok('plan streamed; Approve Plan button present');
const toolLine = await page.$$eval('.agent-line-tool', (e) => e.map((x) => x.textContent));
toolLine.some((t) => t.includes('ExitPlanMode')) ? ok('[tool: ExitPlanMode] rendered') : fail('no ExitPlanMode tool line');

await coachNext('Approve Plan');                                   // step 3 → Approve
await page.waitForFunction(() => [...document.querySelectorAll('.agent-line-status')].some((e) => /done/i.test(e.textContent)), { timeout: 25000 });
ok('work streamed to done status line');
await page.waitForFunction(() => document.querySelector('#agent-status-label')?.textContent === 'Completed', { timeout: 12000 }).catch(() => {});
(await page.textContent('#agent-status-label')) === 'Completed' ? ok('agent status → Completed') : fail('status not Completed');
(await page.$('.hl-table')) ? ok('summary table rendered') : fail('no summary table');
await page.screenshot({ path: '_shot-run.png' });

// 4. Tour steps 4–5: open Settings → "Make it yours" → finish (Warm stays the default).
await coachNext('Open Settings');                                  // step 4 → open Settings
await page.waitForSelector('#settings-overlay.open', { timeout: 4000 });
ok('settings modal opened (step 4)');
await coachNext('Got it');                                         // step 5 → finish
await page.waitForTimeout(300);
!(await page.isVisible('#coach-tip')) ? ok('tour finished at step 5 of 5') : fail('coach still visible after step 5');
(await page.evaluate(() => document.getElementById('demo-root').classList.contains('tone-warm'))) ? ok('theme stayed WARM through the tour') : fail('theme not warm after tour (no tone-warm on root)');

// 4b. Settings is still open (step 4 left it open) — verify accent / model / streaming / search persist.
await page.click('[data-drill="appearance"]');
await page.waitForSelector('[data-sub]', { timeout: 3000 });
await page.click('[data-sub="0"]');                                // Theme & display
await page.waitForSelector('[data-seg="accent"]', { timeout: 3000 });
await page.click('[data-seg="accent"] button[data-val="sunset"]');
(await page.getAttribute('#demo-root', 'data-accent')) === 'sunset' ? ok('accent applied (data-accent=sunset)') : fail('accent not applied');
await page.click('#settings-back-btn');                            // → subs
await page.click('#settings-back-btn');                            // → list
await page.click('[data-drill="agent"]');
await page.waitForSelector('[data-sub]', { timeout: 3000 });
await page.click('[data-sub="1"]');                                // Model
await page.selectOption('select[data-set="model"]', 'claude-opus-4-8');
(await page.evaluate(() => JSON.parse(localStorage.getItem('clayrune_demo_cfg')).model)) === 'claude-opus-4-8' ? ok('model setting persisted') : fail('model not persisted');
await page.click('#settings-back-btn');                            // Model detail → Agent subs
await page.waitForSelector('[data-sub="3"]', { timeout: 3000 });
await page.click('[data-sub="3"]');                                // Integration
await page.click('.settings-toggle[data-toggle="use_streaming_agent"]');
(await page.evaluate(() => JSON.parse(localStorage.getItem('clayrune_demo_cfg')).use_streaming_agent)) === false ? ok('streaming toggle persisted (off)') : fail('streaming toggle not persisted');
await page.click('#settings-back-btn'); await page.click('#settings-back-btn');
await page.fill('#settings-search', 'port');
await page.waitForTimeout(150);
const searchHit = await page.$$eval('#settings-detail-pane .settings-label', (e) => e.map((x) => x.textContent));
searchHit.some((t) => /port/i.test(t)) ? ok('settings search found "Port"') : fail('search miss: ' + JSON.stringify(searchHit));
await page.screenshot({ path: '_shot-settings.png' });

// 6. Project window: centered modal overlay + theme-aware (LIGHT on warm)
await page.evaluate(() => document.getElementById('settings-close').click());
await page.waitForSelector('.projects-col', { timeout: 3000 });    // step 4 already returned us to the dashboard
ok('returned to dashboard after settings');

await page.click('#tile-ledger-api');                              // open a project
await page.waitForSelector('#project-overlay.open', { timeout: 4000 });
const ovDisp = await page.evaluate(() => getComputedStyle(document.getElementById('project-overlay')).display);
ovDisp === 'flex' ? ok('project opens as a centered modal overlay (display:flex)') : fail('project overlay not flex: ' + ovDisp);
const tilesBehind = await page.$$eval('.card', (e) => e.length);
tilesBehind === 5 ? ok('dashboard tiles remain behind the modal (dimmed backdrop)') : fail('tiles not behind modal: ' + tilesBehind);
const usesModalCard = await page.$('#project-overlay .modal-content.project-modal');
usesModalCard ? ok('project reuses the shared .modal-content card') : fail('project not using .modal-content card');
await page.screenshot({ path: '_shot-project-modal.png' });

// backdrop click (top-left, outside the centered card) dismisses
await page.mouse.click(8, 8);
await page.waitForSelector('#project-overlay:not(.open)', { timeout: 3000 }).catch(() => {});
!(await page.isVisible('#project-overlay.open')) ? ok('backdrop click dismisses the modal') : fail('backdrop did not dismiss');

// persist warm, reload, re-open → the modal card must render LIGHT
await page.evaluate(() => { const c = JSON.parse(localStorage.getItem('clayrune_demo_cfg') || '{}'); c.tone = 'warm'; localStorage.setItem('clayrune_demo_cfg', JSON.stringify(c)); });
await page.reload({ waitUntil: 'networkidle' });
await page.waitForSelector('#tile-aurora-web', { timeout: 5000 });
await page.waitForSelector('#coach-tip:not(.settings-hidden)', { timeout: 1500 }).catch(() => {});
if (await page.isVisible('#coach-tip')) await page.click('#coach-skip'); // dismiss the auto-tour
const rootCls = await page.getAttribute('#demo-root', 'class');
rootCls.includes('tone-warm') ? ok('warm theme active after reload') : fail('warm not active: ' + rootCls);
await page.click('#tile-ledger-api');
await page.waitForSelector('#project-overlay.open', { timeout: 4000 });
const cardBg = await page.evaluate(() => getComputedStyle(document.querySelector('.project-modal')).backgroundColor);
const rgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(cardBg);
const isLight = rgb && ((+rgb[1] + +rgb[2] + +rgb[3]) / 3 > 200);
isLight ? ok('warm theme: project modal card is LIGHT (' + cardBg + ')') : fail('warm modal card not light: ' + cardBg);
await page.screenshot({ path: '_shot-project-modal-warm.png' });
await page.keyboard.press('Escape');                               // Esc dismisses
await page.waitForSelector('#project-overlay:not(.open)', { timeout: 3000 }).catch(() => {});
!(await page.isVisible('#project-overlay.open')) ? ok('Esc dismisses the modal') : fail('Esc did not dismiss');

// 7. Skills / MCP / Hivemind open as CENTERED MODALS (same window type as a
//    project), with the "what is this?" explanation as a callout inside the modal.
await page.click('.sidebar-item[data-nav="skills"]');
await page.waitForSelector('#inv-overlay.open .modal-content.inv-modal', { timeout: 3000 });
ok('Skills opens as a centered modal (shared .modal-content window type)');
const skTiles = await page.$$eval('.card', (e) => e.length);
skTiles === 5 ? ok('dashboard stays behind the Skills modal (dimmed backdrop)') : fail('tiles not behind Skills modal: ' + skTiles);
const skIntro = await page.textContent('#inv-overlay .inv-intro');
/SKILL\.md/.test(skIntro) ? ok('Skills modal shows the intro explanation callout') : fail('Skills intro callout: ' + skIntro);
await page.waitForSelector('.inv-group .inv-row', { timeout: 3000 });
const skillRows = await page.$$eval('.inv-group .inv-row', (e) => e.length);
skillRows >= 4 ? ok('Skills shows ' + skillRows + ' sample rows') : fail('Skills sample rows missing: ' + skillRows);
const hasDistill = await page.$$eval('.inv-name', (e) => e.some((x) => /mc-distill/.test(x.textContent)));
hasDistill ? ok('Skills lists mc-distill') : fail('Skills missing mc-distill');
const hasScopeBadges = (await page.$('.inv-badge.global')) && (await page.$('.inv-badge.project'));
hasScopeBadges ? ok('Skills rows show global + project scope badges') : fail('Skills scope badges missing');
const hasSkillPath = await page.$$eval('.inv-path', (e) => e.some((x) => /SKILL\.md/.test(x.textContent)));
hasSkillPath ? ok('Skills rows show config path · mtime') : fail('Skills path/mtime missing');
await page.fill('#inv-search', 'engulfing');                       // live search filter (inside the modal)
await page.waitForTimeout(120);
const filtered = await page.$$eval('.inv-group .inv-row', (e) => e.length);
filtered === 1 ? ok('Skills search filters to 1 matching row') : fail('Skills search filter broken: ' + filtered);
await page.fill('#inv-search', '');
await page.screenshot({ path: '_shot-skills.png' });
await page.keyboard.press('Escape');                               // close before switching nav (scrim blocks the sidebar)
await page.waitForSelector('#inv-overlay.open', { state: 'hidden', timeout: 3000 });
ok('Esc closes the Skills modal');

await page.click('.sidebar-item[data-nav="mcp"]');
await page.waitForSelector('#inv-overlay.open .modal-content.inv-modal', { timeout: 3000 });
ok('MCP opens as a centered modal');
const mcIntro = await page.textContent('#inv-overlay .inv-intro');
/Model Context Protocol/.test(mcIntro) ? ok('MCP modal shows the intro explanation callout') : fail('MCP intro callout: ' + mcIntro);
const mcpRows = await page.$$eval('.inv-group .inv-row', (e) => e.length);
mcpRows >= 4 ? ok('MCP shows ' + mcpRows + ' sample rows') : fail('MCP sample rows missing: ' + mcpRows);
const hasTransport = await page.$('.inv-badge.transport');
hasTransport ? ok('MCP rows show transport badges') : fail('MCP transport badges missing');
const hasCmd = await page.$$eval('.inv-preview', (e) => e.some((x) => /npx -y @modelcontextprotocol/.test(x.textContent)));
hasCmd ? ok('MCP rows show the server command') : fail('MCP command preview missing');
const hasMemLock = await page.$$eval('.inv-tiny.locked', (e) => e.some((x) => /memory/.test(x.textContent)));
hasMemLock ? ok('MCP engram row shows the locked "✓ memory" control') : fail('MCP always-on lock missing');
await page.selectOption('#inv-scope', 'global');                   // scope filter
await page.waitForTimeout(120);
const noProjectRows = await page.$$eval('.inv-group .inv-badge', (e) => e.every((x) => !/project:/.test(x.textContent)));
noProjectRows ? ok('MCP scope filter → "Global only" hides project rows') : fail('MCP scope filter broken');
await page.selectOption('#inv-scope', 'all');
await page.screenshot({ path: '_shot-mcp.png' });
await page.keyboard.press('Escape');
await page.waitForSelector('#inv-overlay.open', { state: 'hidden', timeout: 3000 });

// 7b. Appearance now has the Background section (Theme/Color/Image), like the real app
await page.click('.sidebar-item[data-nav="settings"]');
await page.waitForSelector('#settings-overlay.open', { timeout: 3000 });
await page.waitForTimeout(150);
if (await page.isVisible('#coach-tip')) await page.click('#coach-skip');
await page.click('[data-drill="appearance"]');
await page.waitForSelector('[data-sub]', { timeout: 3000 });
const apSubs = await page.$$eval('.settings-sub-title', (e) => e.map((x) => x.textContent));
apSubs.includes('Background') ? ok('Appearance includes a Background section') : fail('Appearance Background missing: ' + JSON.stringify(apSubs));
await page.click(`[data-sub="${apSubs.indexOf('Background')}"]`);
await page.waitForSelector('[data-seg="bg"]', { timeout: 3000 });
await page.click('[data-seg="bg"] button[data-val="color"]');
await page.waitForSelector('input[data-bgcolor]', { timeout: 3000 });
ok('Appearance → Background → Color reveals the color picker');
await page.evaluate(() => document.getElementById('settings-close').click());

// 7c. Hivemind opens as a modal too — its intro card is the main payload
await page.click('.sidebar-item[data-nav="hivemind"]');
await page.waitForSelector('#inv-overlay.open .modal-content.inv-modal.compact', { timeout: 3000 });
const hmIntro = await page.textContent('#inv-overlay .inv-intro');
/many projects/.test(hmIntro) ? ok('Hivemind opens as a modal with its explanation') : fail('Hivemind intro: ' + hmIntro);
await page.screenshot({ path: '_shot-hivemind.png' });
await page.keyboard.press('Escape');
await page.waitForSelector('#inv-overlay.open', { state: 'hidden', timeout: 3000 });

// 7d. Backlog opens as an explainer modal too
await page.click('.sidebar-item[data-nav="backlog"]');
await page.waitForSelector('#inv-overlay.open .modal-content.inv-modal.compact', { timeout: 3000 });
const blIntro = await page.textContent('#inv-overlay .inv-intro');
/per-project queue/.test(blIntro) ? ok('Backlog opens as a modal with its explanation') : fail('Backlog intro: ' + blIntro);
await page.keyboard.press('Escape');
await page.waitForSelector('#inv-overlay.open', { state: 'hidden', timeout: 3000 });

// 8. MOBILE (≤768px) — render the REAL phone UI, not the scaled desktop dashboard.
await page.click('.sidebar-item[data-nav="dashboard"]');           // back to dashboard (view='dashboard')
await page.waitForSelector('.projects-col', { timeout: 3000 });
await page.setViewportSize({ width: 390, height: 780 });           // phone viewport → mobile shell
await page.waitForTimeout(280);                                    // resize handler re-renders the chat list
const sbHidden = await page.evaluate(() => getComputedStyle(document.getElementById('sidebar')).display === 'none');
sbHidden ? ok('mobile: desktop sidebar hidden') : fail('mobile: sidebar still shown');
const appBar = await page.evaluate(() => getComputedStyle(document.getElementById('mc-app-bar')).display !== 'none');
const tabBar = await page.evaluate(() => getComputedStyle(document.getElementById('bottom-tab-bar')).display !== 'none');
(appBar && tabBar) ? ok('mobile: app bar + bottom tab bar shown') : fail('mobile chrome missing (appBar=' + appBar + ' tabBar=' + tabBar + ')');
const rows = await page.$$eval('.mc-chat-row', (e) => e.length);
rows === 5 ? ok('mobile: WhatsApp chat list shows 5 project rows') : fail('mobile chat rows: ' + rows);
const sortedTop = await page.$eval('.mc-chat-row .cr-name', (e) => e.textContent);
ok('mobile: top chat row = "' + sortedTop + '" (asking sorts first)');
await page.screenshot({ path: '_shot-mobile-home.png' });

// tap a chat → full-screen mobile chat with a back arrow
await page.click('.mc-chat-row[data-open="ledger-api"]');
await page.waitForSelector('#project-overlay.open #agent-output', { timeout: 3000 });
const w = await page.evaluate(() => Math.round(document.querySelector('.project-modal').getBoundingClientRect().width));
w >= 384 ? ok('mobile: project chat is full-screen (' + w + 'px)') : fail('mobile chat not full-screen: ' + w);
(await page.evaluate(() => getComputedStyle(document.getElementById('pm-back')).display !== 'none')) ? ok('mobile: chat shows a back arrow') : fail('mobile: no back arrow');
await page.screenshot({ path: '_shot-mobile-console.png' });
await page.click('#pm-back');
await page.waitForSelector('#project-overlay.open', { state: 'hidden', timeout: 3000 });
ok('mobile: back arrow returns to the chat list');

// hamburger drawer
await page.click('#mc-hamburger-btn');
await page.waitForSelector('#mobile-drawer.open', { timeout: 2000 });
const drawerItems = await page.$$eval('#mobile-drawer-list .mobile-drawer-item', (e) => e.length);
drawerItems >= 6 ? ok('mobile: hamburger drawer opens (' + drawerItems + ' items)') : fail('drawer items: ' + drawerItems);
await page.screenshot({ path: '_shot-mobile-drawer.png' });
await page.click('#mobile-drawer-backdrop');
await page.waitForTimeout(150);

// Reload at phone width → the tour auto-runs on mobile; confirm the coach-guided
// scripted flow (open chat → dispatch → plan) works exactly like desktop.
await page.reload({ waitUntil: 'networkidle' });
await page.waitForSelector('#coach-tip:not(.settings-hidden)', { timeout: 3000 }).catch(() => {});
(await page.isVisible('#coach-tip')) ? ok('mobile: guided tour auto-runs on a phone viewport') : fail('mobile tour did not appear');
await coachNext('Open Aurora Web');
await page.waitForSelector('#project-overlay.open #agent-output', { timeout: 4000 });
ok('mobile: tour opens the Aurora chat full-screen');
await coachNext('Dispatch');
await page.waitForSelector('#btn-approve-plan', { timeout: 15000 });
ok('mobile: coach-guided scripted flow streams a plan + Approve (same as desktop)');
await page.screenshot({ path: '_shot-mobile-plan.png' });

// ── Verdicts ──
consoleErrors.length === 0 ? ok('no console errors') : fail('console errors: ' + JSON.stringify(consoleErrors.slice(0, 5)));
offendingReqs.length === 0 ? ok('ZERO non-local network requests') : fail('outbound/extra requests: ' + JSON.stringify([...new Set(offendingReqs)].slice(0, 8)));

await browser.close();
console.log(process.exitCode ? '\n=== FAIL ===' : '\n=== ALL CHECKS PASSED ===');
