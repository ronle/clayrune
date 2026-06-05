'use strict';
// Mobile chat-switching release-test harness. Drives the real Capacitor WebView
// (via CDP over adb) through chat-navigation scenarios and asserts that no agent
// output is silently dropped from the rendered chat.
//
// Oracle (per scenario), measured after returning to a chat:
//   transport: agentServerLines[sid] >= server log_lines.length  (client got all lines)
//   render:    DOM present AND domCount >= agentServerLines[sid]  (all blocks rendered)
// agentServerLines + server log_lines share the same granularity (output blocks),
// so this is apples-to-apples; the DOM splits blocks on \n, so domCount >= serverCount.
const { connectDashboard, evalIn } = require('./lib/cdp');
const adb = require('./lib/adb');
const app = require('./lib/app');

const PROJECT = process.env.MC_TEST_PROJECT || 'mobiletest';
const PROMPT = process.env.MC_PROMPT
  || 'You are a test fixture. Output the integers from 1 to 12, each on its own line, '
   + 'each followed by a short one-line description. Do NOT use any tools. Do NOT read or write files.';

const sleep = adb.sleep;
const serverLineCount = (s) => (s && Array.isArray(s.log_lines)) ? s.log_lines.length : -1;
const TERMINAL = new Set(['idle', 'completed', 'complete', 'done', 'error', 'stopped', 'failed']);

// Wait until the agent stops producing output: terminal status (with >1 line),
// or a stable line count as a fallback for providers that stay 'running'.
async function waitServerSettled(projectId, sid, { timeoutMs = 150000, stableMs = 12000 } = {}) {
  const t0 = Date.now();
  let lastCount = -1, lastChange = Date.now(), last = null;
  while (Date.now() - t0 < timeoutMs) {
    const s = await app.serverSession(projectId, sid).catch(() => null);
    if (s) {
      last = s;
      const c = serverLineCount(s);
      if (c !== lastCount) { lastCount = c; lastChange = Date.now(); }
      const st = (s.status || '').toLowerCase();
      if (c > 1 && TERMINAL.has(st)) return s;
      if (c > 1 && (Date.now() - lastChange) >= stableMs) return s;
    }
    await sleep(1500);
  }
  return last;
}

function assess(state, serverLines) {
  const transport = state.serverCount >= serverLines && serverLines > 1;
  const rendered = !!state.domPresent && state.domCount > 0 && state.domCount >= state.serverCount;
  // Catch over-rendering too: recovered lines must appear ONCE, not doubled by
  // two recovery paths racing. expectedDom is the buffer's true split-line count.
  const notDuplicated = state.domCount <= state.expectedDom + 2;
  return { transport, rendered, notDuplicated, pass: transport && rendered && notDuplicated };
}

// S1 — leave a responding chat (tab switch) with its SSE dropped, let the agent
// finish, switch back. Tests the switch-back recovery path.
async function S1_switch_away_sse_drop(client) {
  const name = 'S1_switch_away_then_back';
  const sid = await app.dispatchAgentTask(client, PROJECT, PROMPT);
  if (!sid) return { name, pass: false, error: 'dispatch returned no session id' };

  await app.leaveToNewTab(client, PROJECT);
  const sseKilled = await app.killSSE(client, sid);
  const afterLeave = await app.clientState(client, sid);

  const sess = await waitServerSettled(PROJECT, sid);
  const serverLines = serverLineCount(sess);

  await app.switchTo(client, PROJECT, sid);
  await sleep(5000);
  const afterReturn = await app.clientState(client, sid);
  const a = assess(afterReturn, serverLines);
  return { name, pass: a.pass, assess: a, sid, sseKilled, serverStatus: sess && sess.status, serverLines, afterLeave, afterReturn };
}

// S2 — background the app (Doze-style) while a chat is responding, with its SSE
// dropped, let the agent finish, then foreground. Tests the visibility-restore
// reconcile path (the chat stays the active tab the whole time).
async function S2_background_restore(client) {
  const name = 'S2_background_then_foreground';
  const sid = await app.dispatchAgentTask(client, PROJECT, PROMPT);
  if (!sid) return { name, pass: false, error: 'dispatch returned no session id' };

  adb.pressHome();
  await sleep(1800);
  const hidden = await app.clientState(client, sid);
  const sseKilled = await app.killSSE(client, sid);

  const sess = await waitServerSettled(PROJECT, sid);
  const serverLines = serverLineCount(sess);

  adb.bringToForeground();
  await sleep(7000);                       // grace for resync + reconcile + replay
  const afterReturn = await app.clientState(client, sid);
  const a = assess(afterReturn, serverLines);
  return { name, pass: a.pass, assess: a, sid, sseKilled, serverStatus: sess && sess.status, serverLines, hidden, afterReturn };
}

// S3 — happy path: dispatch and watch to completion with no navigation. Guards
// against the fetchAgentStatus repaint fix double-rendering or duplicating the
// buffer on the normal streaming path.
async function S3_happy_path(client) {
  const name = 'S3_happy_path_no_duplication';
  const sid = await app.dispatchAgentTask(client, PROJECT, PROMPT);
  if (!sid) return { name, pass: false, error: 'dispatch returned no session id' };

  const sess = await waitServerSettled(PROJECT, sid);
  const serverLines = serverLineCount(sess);
  await sleep(3000);
  const st = await app.clientState(client, sid);

  const cleanBuffer = st.bufLen === serverLines;                 // no buffer doubling
  const rendered = !!st.domPresent && st.domCount >= st.serverCount && st.domCount > 0;
  const notDuplicated = st.domCount <= st.expectedDom + 2;       // grouping slack for tables/mermaid
  const pass = serverLines > 1 && cleanBuffer && rendered && notDuplicated;
  return { name, pass, sid, serverLines, state: st, checks: { cleanBuffer, rendered, notDuplicated } };
}

// S4 — Ron's "moving between them": two chats, B left active so A goes inactive
// while it's still producing; after both finish, switch back and forth and assert
// each chat shows its OWN complete output.
async function S4_two_chats_switch(client) {
  const name = 'S4_two_chats_switch_between';
  const sidA = await app.dispatchAgentTask(client, PROJECT, PROMPT);
  await sleep(1200);
  const sidB = await app.dispatchAgentTask(client, PROJECT, PROMPT);  // newAgentTab leaves A inactive
  if (!sidA || !sidB) return { name, pass: false, error: 'dispatch failed', sidA, sidB };

  const sa = await waitServerSettled(PROJECT, sidA);
  const sb = await waitServerSettled(PROJECT, sidB);
  const slA = serverLineCount(sa), slB = serverLineCount(sb);

  await app.switchTo(client, PROJECT, sidA);
  await sleep(4000);
  const stA = await app.clientState(client, sidA);
  await app.switchTo(client, PROJECT, sidB);
  await sleep(4000);
  const stB = await app.clientState(client, sidB);

  const aOk = stA.serverCount >= slA && !!stA.domPresent && stA.domCount >= stA.serverCount;
  const bOk = stB.serverCount >= slB && !!stB.domPresent && stB.domCount >= stB.serverCount;
  const pass = slA > 1 && slB > 1 && aOk && bOk;
  return { name, pass, sidA, sidB, slA, slB, stA, stB, checks: { aOk, bOk } };
}

const SCENARIOS = [S1_switch_away_sse_drop, S2_background_restore, S3_happy_path, S4_two_chats_switch];

(async () => {
  const only = process.env.MC_ONLY;
  // Self-contained: make sure the scratch test project exists before we start.
  const created = await app.ensureProject(PROJECT).catch(() => false);
  if (created) console.log(`created scratch project '${PROJECT}'`);
  const { client } = await connectDashboard();
  const results = [];
  try {
    // Pick up the latest static/index.html — the SPA is long-lived and won't
    // otherwise see server-side edits, so each loop iteration must reload to
    // test the current code.
    if (!process.env.MC_NORELOAD) {
      try { await client.Page.reload({ ignoreCache: true }); }
      catch (e) { console.log('reload warn:', e.message); }
      let ready = false;
      for (let i = 0; i < 30 && !ready; i++) {
        await sleep(1000);
        try { ready = await evalIn(client, `(document.readyState === 'complete' && typeof allProjects !== 'undefined')`); }
        catch (e) { /* execution context swapping during reload */ }
      }
      console.log('SPA ready after reload:', ready);
    }

    // The test project may have just been created — make the SPA refresh its
    // list and confirm it's visible before dispatching into it.
    let haveProj = false;
    for (let i = 0; i < 15 && !haveProj; i++) {
      haveProj = await evalIn(client, `(async () => {
        const pid = ${JSON.stringify(PROJECT)};
        let has = (typeof allProjects !== 'undefined') && allProjects.some(p => p.id === pid);
        if (!has && typeof refreshSilent === 'function') { try { await refreshSilent(); } catch (e) {} has = (allProjects || []).some(p => p.id === pid); }
        return has;
      })()`);
      if (!haveProj) await sleep(1000);
    }
    console.log('test project visible in SPA:', haveProj);

    for (const fn of SCENARIOS) {
      if (only && !fn.name.toLowerCase().includes(only.toLowerCase())) continue;
      let r;
      try { r = await fn(client); }
      catch (e) { r = { name: fn.name, pass: false, error: e.message, stack: (e.stack || '').split('\n').slice(0, 4) }; }
      results.push(r);
      console.log(`\n[${r.pass ? 'PASS' : 'FAIL'}] ${r.name}`);
      console.log(JSON.stringify(r, null, 2));
    }
  } finally {
    await client.close().catch(() => {});
  }
  const passed = results.filter((r) => r.pass).length;
  console.log(`\nSUMMARY ${passed}/${results.length} passed :: `
    + results.map((r) => `${r.name}=${r.pass ? 'PASS' : 'FAIL'}`).join(' '));
  process.exit(results.length && results.every((r) => r.pass) ? 0 : 1);
})().catch((e) => { console.error('RUN_ERR', e.message); process.exit(2); });
