// Reproduces the "icons don't always show" bug and proves the fix.
// Failure chain: appendAgentLine removes the indicator on every streamed line;
// its re-add is gated on a status cache that can be stale; the server only
// re-emits `activity` on CHANGE — so once dropped, nothing brought it back.
import { chromium } from 'playwright';
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1200, height: 800 } });
await page.goto('http://localhost:5199/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);

const r = await page.evaluate(() => {
  const sid = '__test_sid__';
  // Mount the output container the indicator attaches to.
  const out = document.createElement('div');
  out.id = `agent-output-${sid}`;
  document.body.appendChild(out);

  const ind = () => document.getElementById(`typing-${sid}`);
  const act = () => (ind() ? ind().dataset.act : null);
  const results = {};

  // ── 1. Agent is running → indicator shows (dots).
  agentStatusCache[sid] = { status: 'running', projectId: 'x' };
  setAgentActivity(sid, '');
  showTypingIndicator(sid);
  results.step1_shown = !!ind() && act() === 'writing';

  // ── 2. Activity says "tool" → spinner (repaint of the existing node).
  setAgentActivity(sid, 'tool');
  results.step2_spinner = act() === 'tool';

  // ── 3. THE BUG: a streamed line removes the node, and the status cache is
  //      stale (not 'running'), so the old re-add gate skipped it.
  hideTypingIndicator(sid);                    // what appendAgentLine does
  agentStatusCache[sid].status = 'idle';       // stale/absent cache mid-turn
  results.step3_gone = !ind();

  // ── 4. Server sends the next activity event (state unchanged in practice,
  //      but here a change) → OLD code painted nothing (no node). NEW code
  //      re-creates it, because a non-empty activity state proves running.
  setAgentActivity(sid, 'thinking');
  results.step4_healed = !!ind() && act() === 'thinking';

  // ── 5. Turn ends: activity cleared → must NOT resurrect on a trailing line.
  setAgentActivity(sid, '');
  hideTypingIndicator(sid);
  results.step5_hidden_after_turn = !ind();
  results.step5_not_generating = _isGenerating(sid) === false;

  // ── 6. Parked on a question → activity must not resurrect the indicator.
  agentStatusCache[sid] = { status: 'running', waitingForQuestion: true };
  setAgentActivity(sid, 'tool');
  results.step6_suppressed_when_parked = !ind();

  out.remove();
  delete agentStatusCache[sid];
  return results;
});

console.log(JSON.stringify(r, null, 1));
const pass = Object.values(r).every(Boolean);
console.log(pass ? '✅ PASS — indicator is self-healing' : '❌ FAIL');
await b.close();
process.exit(pass ? 0 : 1);
