// ── Rich text formatting for agent output ────────────────────────────────────
function formatAgentText(raw) {
  // Already escaped by esc() before calling — we operate on safe HTML
  let t = esc(raw);

  // Code fence blocks (``` ... ```)
  if (t.match(/^```/)) {
    return `<span class="hl-codeblock">${t.replace(/^```\w*/, '').replace(/```$/, '')}</span>`;
  }

  // Markdown headers: ## Heading, ### Heading
  if (t.match(/^#{1,4}\s/)) {
    return `<span class="hl-h">${t}</span>`;
  }

  // Inline image embeds: an absolute path to an image file becomes a
  // thumbnail (click to enlarge). Tokenized out FIRST so the path / code
  // / bold regexes below don't shred the produced <img> markup; swapped
  // back in just before return. Only absolute paths (Win `X:\` / `X:/`
  // or POSIX `/`) — relative paths can't be resolved server-side without
  // the agent's cwd.
  const _imgTokens = [];
  // Two guards prevent URL-shaped strings from being matched as filesystem
  // paths:
  //   1. Negative lookbehind `(?<![\w:/%])` — leading drive-letter / slash
  //      must NOT be preceded by a word char, `:`, `/`, or `%`. Stops the
  //      regex from biting mid-URL (e.g. matching `p:/` inside `http://`).
  //   2. Negative lookahead `(?!:\/\/)` after the drive-letter colon — forbids
  //      `://` (URL scheme). Windows paths are `C:\` or `C:/single-slash`;
  //      a URL like `p://...png` would otherwise match as a phantom drive.
  // Trailing `(?![A-Za-z0-9])` keeps the extension bounded.
  t = t.replace(
    /(?<![\w:/%])((?:[A-Za-z]:(?!\/\/)[\\/]|\/)[^\s"'`<>|]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif))(?![A-Za-z0-9])/gi,
    (m, p) => {
      const rawPath = p.replace(/&amp;/g, '&').replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      const src = API_BASE + '/api/serve-image?path=' + encodeURIComponent(rawPath);
      const tok = '@@CLImg' + _imgTokens.length + '@@';
      // NOTE: `loading="lazy"` MUST NOT be set here. The img starts at
      // `display:none` (CSS .agent-img) and only becomes `display:block`
      // when onload adds `.agent-img-ok`. With lazy loading, the browser
      // waits for the element to enter the viewport before starting the
      // request — but a display:none element has no bounding box and
      // never "intersects", so the load never starts, onload never fires,
      // and the img stays hidden forever. Eager loading (the default)
      // breaks the deadlock: load starts immediately, onload fires,
      // class flips, image appears.
      _imgTokens.push(
        `<span class="agent-img-wrap">` +
        `<a class="agent-img-path" href="${src}" target="_blank" rel="noopener">${p}</a>` +
        `<img class="agent-img" src="${src}" alt="" ` +
        `onload="this.classList.add('agent-img-ok')" ` +
        `onerror="this.closest('.agent-img-wrap').classList.add('agent-img-failed');this.remove()" ` +
        `onclick="_openImageViewer(this.src)"></span>`);
      return tok;
    });

  // URLs → real clickable <a> links. Tokenized out BEFORE the path / code /
  // bold regexes below so they can't shred the markup. The absolute-path
  // regex in particular ("/seg/seg" ×2+) matches the "//host/path" tail of
  // an https:// URL and steals its second slash, leaving "https:/…" which the
  // URL pass can no longer recognise — which is why links *with a path*
  // silently stopped being linkified. Swapped back in just before return;
  // trailing sentence punctuation is kept outside the anchor.
  const _urlTokens = [];
  t = t.replace(/(https?:\/\/[^\s<]+)/g, (m) => {
    let url = m, trail = '';
    const tm = url.match(/[.,;:!?)\]]+$/);
    if (tm) { trail = tm[0]; url = url.slice(0, -trail.length); }
    const tok = '@@CLUrl' + _urlTokens.length + '@@';
    _urlTokens.push('<a class="hl-url" href="' + url + '" target="_blank" rel="noopener">' + url + '</a>');
    return tok + trail;
  });

  // Numbered list items: 1. item, 2. item
  t = t.replace(/^(\d+\.)\s/, '<span class="hl-num">$1</span> ');

  // Bullet points: - item, * item
  t = t.replace(/^([-*])\s/, '<span class="hl-bullet">$1</span> ');

  // Inline code: `something`
  t = t.replace(/`([^`]+)`/g, '<span class="hl-code">$1</span>');

  // Bold: **text**
  t = t.replace(/\*\*([^*]+)\*\*/g, '<span class="hl-bold">$1</span>');

  // File paths: word.ext patterns (common code file extensions)
  t = t.replace(/(?<![&\w])([A-Za-z_][\w.-]*\.(py|js|ts|tsx|jsx|html|css|json|md|yml|yaml|toml|rs|go|java|c|cpp|h|sh|sql|vue|svelte|rb|php))(?![&\w])/g,
    '<span class="hl-path">$1</span>');

  // Absolute paths: /path/to/file or C:\path\to\file
  t = t.replace(/((?:\/[\w.-]+){2,}|(?:[A-Z]:\\[\w.-\\]+))/g, '<span class="hl-path">$1</span>');

  // Swap image + URL tokens back in (kept opaque through the regexes above).
  if (_imgTokens.length) {
    t = t.replace(/@@CLImg(\d+)@@/g, (_, i) => _imgTokens[+i] || '');
  }
  if (_urlTokens.length) {
    t = t.replace(/@@CLUrl(\d+)@@/g, (_, i) => _urlTokens[+i] || '');
  }

  return t;
}

function isTableLine(text) {
  // Pipe-delimited table row: | col1 | col2 | or starts/ends with pipe
  if (/^\s*\|.*\|/.test(text)) return true;
  // Separator lines: +---+---+, +===+===+, |---|---|
  if (/^\s*[+|][-=]+[+|]/.test(text)) return true;
  // Unicode box drawing: ┌─┬─┐, ├─┼─┤, └─┴─┘, │ etc
  if (/[┌┐└┘├┤┬┴┼─│═║╔╗╚╝╠╣╦╩╬]/.test(text)) return true;
  return false;
}

function formatTableLine(escaped) {
  // Colorize pipes and border characters (for box-drawing / non-pipe tables)
  return escaped
    .replace(/([│|])/g, '<span class="table-pipe">$1</span>')
    .replace(/([┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬+])/g, '<span class="table-border">$1</span>');
}

function isPipeTable(lines) {
  // True if at least one line has pipe-delimited columns (not just box-drawing)
  return lines.some(l => /^\s*\|[^┌┐└┘├┤┬┴┼─│═║╔╗╚╝╠╣╦╩╬]*\|/.test(l));
}

function isSeparatorLine(text) {
  return /^\s*\|?[-=|:+\s]+\|?\s*$/.test(text) && /[-=]{2,}/.test(text);
}

function buildPipeTable(rawLines) {
  // Parse pipe-delimited lines into HTML <table>
  const dataRows = [];
  let headerIdx = -1;
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].trim();
    if (!line) continue;
    if (isSeparatorLine(line)) {
      // The row before the first separator is the header
      if (headerIdx < 0 && dataRows.length > 0) headerIdx = dataRows.length - 1;
      continue;
    }
    // Split by pipe, trim each cell
    const cells = line.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
    dataRows.push(cells);
  }
  if (dataRows.length === 0) return '';
  // If we found a header separator, first row is header
  const hasHeader = headerIdx === 0;
  let html = '<table>';
  dataRows.forEach((cells, i) => {
    const tag = (hasHeader && i === 0) ? 'th' : 'td';
    html += '<tr>' + cells.map(c => `<${tag}>${esc(c)}</${tag}>`).join('') + '</tr>';
  });
  html += '</table>';
  return html;
}

function agentLineCls(text) {
  const t = text.trim();
  if (t.startsWith('> [queued]')) return 'agent-line agent-line-queued';
  if (t.startsWith('> ')) return 'agent-line agent-line-prompt';
  if (t.startsWith('[tool:')) return 'agent-line agent-line-tool';
  if (t.startsWith('[') && t.endsWith(']')) return 'agent-line agent-line-status';
  if (t.startsWith('[exited') || t.startsWith('[stream error')) return 'agent-line agent-line-error';
  return 'agent-line';
}

function collapseIntoPlanButton(sessionId, container) {
  // Walk backwards from end, collecting non-tool text lines until we hit a [tool:] line
  const children = Array.from(container.children);
  const planLines = [];
  const planElements = [];

  for (let i = children.length - 1; i >= 0; i--) {
    const child = children[i];
    const txt = (child.textContent || '').trim();
    // Stop at tool lines, prompt lines, or existing plan buttons
    if (child.classList.contains('agent-line-tool') ||
        child.classList.contains('agent-line-prompt') ||
        child.classList.contains('plan-show-btn')) break;
    // Skip the ExitPlanMode tool line itself (already handled above)
    if (txt === '[tool: ExitPlanMode]') continue;
    planLines.unshift(child.textContent || child.innerText || '');
    planElements.unshift(child);
  }

  if (planLines.length < 2) return; // Too few lines, not a real plan

  // Store plan content
  planViewerContent[sessionId] = planLines;

  // Wrap plan elements in a hidden container
  const wrapper = document.createElement('div');
  wrapper.className = 'plan-hidden-block';
  wrapper.dataset.sessionId = sessionId;
  const insertBefore = planElements[0];
  container.insertBefore(wrapper, insertBefore);
  for (const el of planElements) wrapper.appendChild(el);

  // Insert "Show Plan" button before the hidden block
  const btn = document.createElement('button');
  btn.className = 'plan-show-btn';
  btn.innerHTML = '&#128196; Show Plan';
  btn.onclick = () => openPlanViewer(sessionId);
  container.insertBefore(btn, wrapper);
}


function expandAgentOutput(sessionId) {
  expandedOutputSessions.add(sessionId);
  refreshModal();
}

// "Pinned to bottom" detection: only auto-scroll when the user is already within
// ~80 px of the bottom. Otherwise leave their scroll position alone — they're
// reading earlier output and don't want to be yanked back.
// rAF-batched scroll-to-bottom for streaming agent output.
// Each `appendAgentLine` call used to do `el.scrollTop = el.scrollHeight`
// synchronously — that's a forced layout reflow per line. On Android WebView
// during a 50-line streaming burst that's 50 reflows back-to-back, blocking
// the input handler for hundreds of ms (keyboard delete drops, typing lag).
// Coalescing all writes within a frame into one rAF callback collapses 50
// reflows into 1.
const _pinScrollQueue = new Map();  // sessionId -> {el, freshMount}

function _isAgentOutputPinned(el, sessionId) {
  if (!el) return true;
  // If we have a pending pin-scroll for this session, treat as still pinned —
  // the DOM read below would return the pre-write state during a streaming
  // burst and report "not at bottom" even though we're about to scroll there.
  if (sessionId && _pinScrollQueue.has(sessionId)) return true;
  return (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
}

function _scheduleAgentPinScroll(sessionId, el, freshMount) {
  if (!el) return;
  const wasQueued = _pinScrollQueue.has(sessionId);
  _pinScrollQueue.set(sessionId, { el, freshMount: !!freshMount });
  if (wasQueued) return;
  requestAnimationFrame(() => {
    const entry = _pinScrollQueue.get(sessionId);
    _pinScrollQueue.delete(sessionId);
    if (!entry) return;
    const target = entry.el;
    if (!target || !target.isConnected) return;
    target.scrollTop = target.scrollHeight;
    if (entry.freshMount && target.scrollHeight > 0) {
      target.dataset.scrollInitialized = '1';
    }
  });
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.formatAgentText = formatAgentText;
window.isTableLine = isTableLine;
window.formatTableLine = formatTableLine;
window.isPipeTable = isPipeTable;
window.buildPipeTable = buildPipeTable;
window.agentLineCls = agentLineCls;
window.collapseIntoPlanButton = collapseIntoPlanButton;
window.expandAgentOutput = expandAgentOutput;
window._isAgentOutputPinned = _isAgentOutputPinned;
window._scheduleAgentPinScroll = _scheduleAgentPinScroll;
