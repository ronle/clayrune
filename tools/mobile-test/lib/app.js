'use strict';
// SPA driving primitives (via CDP eval) + authoritative server truth (via the
// host API). The oracle for every scenario is: rendered DOM lines for a chat
// == that session's server log_lines. Content-agnostic, so a real agent's
// nondeterministic output is fine.
const http = require('http');
const fs = require('fs');
const { evalIn } = require('./cdp');

const HOST_API = process.env.MC_HOST_API || 'http://localhost:5199';
const TEST_DIR = process.env.MC_TEST_DIR
  || require('path').join(require('os').tmpdir(), 'mc-mobiletest');

function httpJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let s = '';
      res.on('data', (d) => (s += d));
      res.on('end', () => {
        try { resolve(JSON.parse(s)); }
        catch (e) { reject(new Error('bad json from ' + url + ': ' + s.slice(0, 200))); }
      });
    }).on('error', reject);
  });
}

function httpPostJson(url, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const u = new URL(url);
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
    }, (res) => {
      let s = '';
      res.on('data', (d) => (s += d));
      res.on('end', () => { try { resolve(JSON.parse(s)); } catch (e) { resolve({ raw: s, status: res.statusCode }); } });
    });
    req.on('error', reject);
    req.write(payload); req.end();
  });
}

// Create the scratch test project (empty dir → no CLAUDE.md/MEMORY.md → no
// context auto-condense → fast, cheap dispatches). Idempotent.
async function ensureProject(projectId) {
  const data = await httpJson(`${HOST_API}/api/projects`).catch(() => ({}));
  const arr = Array.isArray(data) ? data : (data.projects || []);
  if (arr.some((p) => p.id === projectId)) return false;
  try { fs.mkdirSync(TEST_DIR, { recursive: true }); } catch (_) {}
  await httpPostJson(`${HOST_API}/api/project/${projectId}`, {
    name: 'Mobile Test', domain: 'general', status: 'active', project_path: TEST_DIR,
  }).catch(() => {});
  return true;
}

// Authoritative server view of a session (includes log_lines[]).
async function serverSession(projectId, sessionId) {
  const data = await httpJson(`${HOST_API}/api/project/${projectId}/agent/status`);
  const sess = (data.sessions || []).find((s) => s.session_id === sessionId);
  return sess || null;
}

// In-page state for a session: client buffer, server-line cursor, DOM render,
// live SSE handle, status.
async function clientState(client, sessionId) {
  return evalIn(client, `(() => {
    const sid = ${JSON.stringify(sessionId)};
    const has = (n) => typeof window[n] !== 'undefined';
    const g = (n) => { try { return eval(n); } catch(e){ return undefined; } };
    const buffers = g('agentOutputBuffers') || {};
    const serverLines = g('agentServerLines') || {};
    const statusCache = g('agentStatusCache') || {};
    const sources = g('agentEventSources') || {};
    const buf = buffers[sid] || [];
    const el = document.getElementById('agent-output-' + sid);
    let domLines = null;
    if (el) domLines = Array.from(el.children).map(c => (c.textContent||'').trim()).filter(x => x.length);
    // What the buffer SHOULD render to (each block split on newlines) — lets a
    // scenario detect both under-render (missing) and over-render (duplication).
    const expectedDom = buf.reduce((n, l) => n + String(l||'').split('\\n').filter(x => x.trim().length).length, 0);
    return {
      bufLen: buf.length,
      serverCount: serverLines[sid] || 0,
      domPresent: !!el,
      domCount: domLines ? domLines.length : -1,
      expectedDom,
      status: (statusCache[sid] && statusCache[sid].status) || null,
      sseOpen: !!sources[sid],
    };
  })()`);
}

async function openProject(client, projectId) {
  return evalIn(client, `(() => { openProjectModal(${JSON.stringify(projectId)}); return true; })()`);
}

// Drive a fresh dispatch through the real UI path; resolve the promoted (real)
// session id from activeAgentTab once dispatchAgent finishes its POST.
async function dispatchAgentTask(client, projectId, prompt) {
  return evalIn(client, `(async () => {
    const pid = ${JSON.stringify(projectId)};
    openProjectModal(pid);
    newAgentTab(pid);
    const ta = document.getElementById('agent-task-' + pid);
    if (!ta) throw new Error('no dispatch textarea for ' + pid);
    ta.value = ${JSON.stringify(prompt)};
    await dispatchAgent(pid);
    return (typeof activeAgentTab !== 'undefined' && activeAgentTab[pid]) || null;
  })()`);
}

async function switchTo(client, projectId, sessionId) {
  return evalIn(client, `(() => { switchAgentTab(${JSON.stringify(projectId)}, ${JSON.stringify(sessionId)}); return true; })()`);
}

async function leaveToNewTab(client, projectId) {
  return evalIn(client, `(() => { newAgentTab(${JSON.stringify(projectId)}); return true; })()`);
}

// Inject the failure that Doze/socket-parking causes organically: kill the
// session's SSE so the inactive chat stops receiving the agent's output.
async function killSSE(client, sessionId) {
  return evalIn(client, `(() => {
    const sid = ${JSON.stringify(sessionId)};
    let closed = false;
    try { const es = eval('agentEventSources'); if (es && es[sid]) { es[sid].close(); delete es[sid]; closed = true; } } catch(e){}
    try { const wd = eval('agentSSEWatchdogs'); if (wd && wd[sid]) { clearInterval(wd[sid]); delete wd[sid]; } } catch(e){}
    return closed;
  })()`);
}

module.exports = {
  HOST_API, httpJson, httpPostJson, ensureProject, serverSession, clientState,
  openProject, dispatchAgentTask, switchTo, leaveToNewTab, killSSE,
};
