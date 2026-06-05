'use strict';
// CDP connection to the Capacitor WebView's dashboard page, with retry while
// the WebView spins up. Everything is driven by evaluating JS in-page, which is
// far more reliable than pixel taps for reading/asserting SPA state.
const CDP = require('chrome-remote-interface');
const { findWebviewSocket, forwardDevtools, CDP_PORT, sleep } = require('./adb');

const DASH_RE = /10\.0\.2\.2:5199/;

async function connectDashboard({ retries = 25, delay = 1000 } = {}) {
  let lastErr = null;
  for (let i = 0; i < retries; i++) {
    const sock = findWebviewSocket();
    if (!sock) { lastErr = new Error('no webview devtools socket yet'); await sleep(delay); continue; }
    try {
      forwardDevtools(sock, CDP_PORT);
      // Pick the dashboard page target explicitly, then connect by id. Using
      // `target: <id>` (not the full ws:// URL) is the most robust form for the
      // Android WebView inspector.
      const targets = await CDP.List({ port: CDP_PORT });
      const page = targets.find((t) => t.type === 'page' && DASH_RE.test(t.url || ''))
        || targets.find((t) => t.type === 'page' && !/about:blank/.test(t.url || ''));
      if (!page) { lastErr = new Error('no dashboard page yet: ' + JSON.stringify(targets.map((t) => t.url))); await sleep(delay); continue; }
      // `local: true` is REQUIRED for the Android WebView inspector: without it
      // chrome-remote-interface tries to fetch the protocol descriptor from the
      // target and hangs forever. The bundled protocol works fine.
      const client = await CDP({ port: CDP_PORT, target: page.id, local: true });
      await client.Runtime.enable();
      await client.Page.enable().catch(() => {});
      // Confirm the SPA actually finished loading before handing back.
      const href = await evalIn(client, 'location.href');
      if (!DASH_RE.test(href)) { await client.close().catch(() => {}); lastErr = new Error('page not dashboard: ' + href); await sleep(delay); continue; }
      return { client, page };
    } catch (e) { lastErr = e; await sleep(delay); }
  }
  throw lastErr || new Error('connectDashboard failed');
}

async function evalIn(client, expression) {
  const { result, exceptionDetails } = await client.Runtime.evaluate({
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (exceptionDetails) {
    const ex = exceptionDetails.exception || {};
    throw new Error('eval failed: ' + (ex.description || ex.value || exceptionDetails.text));
  }
  return result.value;
}

module.exports = { connectDashboard, evalIn, DASH_RE };
