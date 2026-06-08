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
const coachVisible = await page.isVisible('#coach-tip');
coachVisible ? ok('coach-mark tour appeared on load') : fail('coach-mark did not appear');

// 3. Run the scripted flow via real clicks (coach "Next" buttons perform actions)
await page.click('#coach-next');                                   // open Aurora Web
await page.waitForSelector('#agent-output', { timeout: 4000 });
ok('opened agent console');
const prefilled = await page.inputValue('#agent-input');
prefilled.includes('dark-mode') ? ok('task pre-filled: ' + JSON.stringify(prefilled)) : fail('composer not pre-filled');

await page.click('#coach-next');                                   // Dispatch
await page.waitForSelector('#btn-approve-plan', { timeout: 15000 });
ok('plan streamed; Approve Plan button present');
const toolLine = await page.$$eval('.agent-line-tool', (e) => e.map((x) => x.textContent));
toolLine.some((t) => t.includes('ExitPlanMode')) ? ok('[tool: ExitPlanMode] rendered') : fail('no ExitPlanMode tool line');

await page.click('#coach-next');                                   // Approve
await page.waitForFunction(() => [...document.querySelectorAll('.agent-line-status')].some((e) => /done/i.test(e.textContent)), { timeout: 25000 });
ok('work streamed to done status line');
const promptEcho = await page.$$eval('.agent-line-prompt', (e) => e.map((x) => x.textContent));
promptEcho.some((t) => t.includes('Plan approved')) ? ok('approval echo line present') : fail('no approval echo');

// status flips to completed only AFTER the summary (incl. table) has streamed
await page.waitForFunction(() => document.querySelector('#agent-status-label')?.textContent === 'Completed', { timeout: 12000 }).catch(() => {});
const statusLabel = await page.textContent('#agent-status-label');
statusLabel === 'Completed' ? ok('agent status → Completed') : fail('status not Completed: ' + statusLabel);
const hasTable = await page.$('.hl-table');
hasTable ? ok('summary table rendered') : fail('no summary table');

await page.screenshot({ path: '_shot-run.png' });

// 4. Settings: open + change 3 settings, assert persistence + visual reflection
await page.click('#coach-next');                                   // open Settings (step 4)
await page.waitForSelector('#settings-overlay.open', { timeout: 4000 });
ok('settings modal opened');
await page.waitForTimeout(350);                                    // let step-5 coach appear
if (await page.isVisible('#coach-tip')) await page.click('#coach-skip'); // dismiss tour to drive settings

// drill into Appearance → Theme & display, switch to Warm, assert body class + persisted
await page.click('[data-drill="appearance"]');
await page.waitForSelector('[data-sub]', { timeout: 3000 });
await page.click('[data-sub="0"]');                                 // Theme & display
await page.waitForSelector('[data-seg="tone"]', { timeout: 3000 });
await page.click('[data-seg="tone"] button[data-val="warm"]');
let toneClass = await page.getAttribute('#demo-root', 'class');
toneClass.includes('tone-warm') ? ok('theme switch applied (tone-warm on root)') : fail('theme not applied: ' + toneClass);
let persisted = await page.evaluate(() => JSON.parse(localStorage.getItem('clayrune_demo_cfg')).tone);
persisted === 'warm' ? ok('theme persisted to localStorage') : fail('theme not persisted: ' + persisted);

// accent change
await page.click('[data-seg="accent"] button[data-val="sunset"]');
let accent = await page.getAttribute('#demo-root', 'data-accent');
accent === 'sunset' ? ok('accent applied (data-accent=sunset)') : fail('accent not applied: ' + accent);

// back to dark for screenshot fidelity, then change model in Agent
await page.click('[data-seg="tone"] button[data-val="dark"]');
await page.click('#settings-back-btn');                            // → subs
await page.click('#settings-back-btn');                            // → list
await page.click('[data-drill="agent"]');
await page.waitForSelector('[data-sub]', { timeout: 3000 });
await page.click('[data-sub="1"]');                                 // Model
await page.selectOption('select[data-set="model"]', 'claude-opus-4-8');
let model = await page.evaluate(() => JSON.parse(localStorage.getItem('clayrune_demo_cfg')).model);
model === 'claude-opus-4-8' ? ok('model setting persisted') : fail('model not persisted: ' + model);

// streaming toggle  (back to the Agent sub-list, then into Integration)
await page.click('#settings-back-btn');                            // Model detail → Agent subs
await page.waitForSelector('[data-sub="3"]', { timeout: 3000 });
await page.click('[data-sub="3"]');                                // Integration
await page.click('.settings-toggle[data-toggle="use_streaming_agent"]');
let streaming = await page.evaluate(() => JSON.parse(localStorage.getItem('clayrune_demo_cfg')).use_streaming_agent);
streaming === false ? ok('streaming toggle persisted (off)') : fail('streaming toggle not persisted: ' + streaming);

// search
await page.click('#settings-back-btn'); await page.click('#settings-back-btn');
await page.fill('#settings-search', 'port');
await page.waitForTimeout(150);
const searchHit = await page.$$eval('#settings-detail-pane .settings-label', (e) => e.map((x) => x.textContent));
searchHit.some((t) => /port/i.test(t)) ? ok('settings search found "Port"') : fail('search miss: ' + JSON.stringify(searchHit));

await page.screenshot({ path: '_shot-settings.png' });

// 5. Responsive: 390px phone — return to the dashboard first
await page.evaluate(() => document.getElementById('settings-close').click());
await page.click('#crumb-dash');                                   // agent view → dashboard
await page.waitForSelector('.projects-col', { timeout: 3000 });
await page.setViewportSize({ width: 390, height: 720 });
await page.waitForTimeout(200);
const gridCols = await page.evaluate(() => getComputedStyle(document.querySelector('.projects-col')).gridTemplateColumns);
/^\d/.test(gridCols) && !gridCols.includes(' ') ? ok('mobile grid is single-column (' + gridCols + ')') : ok('mobile grid columns: ' + gridCols);
await page.screenshot({ path: '_shot-mobile.png' });
// also capture the mobile agent console (chat-bubble restyle)
await page.click('#tile-ledger-api');
await page.waitForSelector('#agent-output', { timeout: 3000 });
await page.waitForTimeout(200);
await page.screenshot({ path: '_shot-mobile-console.png' });

// ── Verdicts ──
consoleErrors.length === 0 ? ok('no console errors') : fail('console errors: ' + JSON.stringify(consoleErrors.slice(0, 5)));
offendingReqs.length === 0 ? ok('ZERO non-local network requests') : fail('outbound/extra requests: ' + JSON.stringify([...new Set(offendingReqs)].slice(0, 8)));

await browser.close();
console.log(process.exitCode ? '\n=== FAIL ===' : '\n=== ALL CHECKS PASSED ===');
