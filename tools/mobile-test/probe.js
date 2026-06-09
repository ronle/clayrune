'use strict';
// Smoke test: attach to the WebView and read in-page state. Confirms the whole
// CDP-over-adb path works and the dashboard actually loaded from 10.0.2.2:5199.
const { connectDashboard, evalIn } = require('./lib/cdp');

(async () => {
  const { client, page } = await connectDashboard();
  try {
    const info = await evalIn(client, `(() => ({
      title: document.title,
      url: location.href,
      ready: document.readyState,
      hasAllProjects: typeof window.allProjects !== 'undefined',
      projectCount: (window.allProjects || []).length,
      projectNames: (window.allProjects || []).slice(0, 8).map(p => p && (p.name || p.id)),
      apk: window.__clayruneAPK || null,
      bodyTextLen: ((document.body && document.body.innerText) || '').length
    }))()`);
    console.log('PAGE_URL:', page.url);
    console.log('PROBE:', JSON.stringify(info, null, 2));
  } finally {
    await client.close();
  }
})().catch((e) => { console.error('PROBE_ERR', e.message); process.exit(1); });
