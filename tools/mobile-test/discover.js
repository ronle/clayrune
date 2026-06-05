'use strict';
// Map the SPA's driving surface: which globals/functions are reachable from
// Runtime.evaluate (bare names — many are let/const, not on window), what
// projects are loaded, and what's currently rendered.
const { connectDashboard, evalIn } = require('./lib/cdp');

(async () => {
  const { client } = await connectDashboard();
  try {
    const out = await evalIn(client, `(() => {
      const t = {
        allProjects: typeof allProjects,
        switchAgentTab: typeof switchAgentTab,
        newAgentTab: typeof newAgentTab,
        dispatchAgent: typeof dispatchAgent,
        sendFollowup: typeof sendFollowup,
        openProjectModal: typeof openProjectModal,
        refreshModal: typeof refreshModal,
        connectAgentStream: typeof connectAgentStream,
        _reconcileAgentBuffer: typeof _reconcileAgentBuffer,
        appendAgentLine: typeof appendAgentLine,
        agentOutputBuffers: typeof agentOutputBuffers,
        agentServerLines: typeof agentServerLines,
        agentStatusCache: typeof agentStatusCache,
        activeAgentTab: typeof activeAgentTab,
        agentHistory: typeof agentHistory,
        openModals: typeof openModals,
        API_BASE: typeof API_BASE,
      };
      let projects = [];
      try { projects = (allProjects || []).map(p => ({ id: p.id, name: p.name, path: p.path || p.cwd || p.working_dir || p.dir })); } catch (e) {}
      const cards = document.querySelectorAll('[data-project-id], .project-card, .proj-card, .project-tile');
      return {
        types: t,
        apiBase: (typeof API_BASE !== 'undefined') ? API_BASE : '(undef)',
        projectCount: projects.length,
        projects: projects.slice(0, 20),
        domCardCount: cards.length,
        bodyHead: (document.body.innerText || '').slice(0, 400),
      };
    })()`);
    console.log(JSON.stringify(out, null, 2));
  } finally {
    await client.close();
  }
})().catch((e) => { console.error('ERR', e.message); process.exit(1); });
