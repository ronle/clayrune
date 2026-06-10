// ── Mermaid diagram interception ────────────────────────────────────────────
// Three code paths produce mermaid blocks: live streaming (appendAgentLine),
// modal-rebuild (outputLines builder in tile click / tab switch), and the
// popout (openPlanViewer). All converge on the same shape:
//
//   1. Insert `<div class="mermaid-block" data-source="<escaped>">
//        <div class="mermaid-pending">Building diagram…</div></div>`
//   2. Call `_renderAllMermaidPlaceholders(rootEl)` after insertion. It
//      finds un-rendered placeholders, runs mermaid.render() async, swaps
//      in the SVG (with explicit width/height stripped so CSS can scale),
//      wires the click-to-enlarge handler.
//
// Diagrams now survive tab switches (rebuild re-emits placeholder; render
// runs again) and render in the popout window.

const _mermaidBuffers = {};   // sessionId -> { placeholder, lines }

function _mermaidPlaceholderHTML(source) {
  // For HTML-string builders (outputLines, openPlanViewer).
  return `<div class="mermaid-block" data-source="${esc(source)}">` +
         `<div class="mermaid-pending">Building diagram…</div></div>`;
}

function _resizeSvgForFit(svg) {
  // Mermaid SVGs ship with explicit width/height matching the natural size.
  // Strip them so CSS max-width:100% actually scales the diagram.
  return svg.replace(/<svg([^>]*)\swidth="[^"]*"/, '<svg$1')
            .replace(/<svg([^>]*)\sheight="[^"]*"/, '<svg$1')
            .replace(/<svg /, '<svg style="max-width:100%;height:auto" ');
}

// Render via Excalidraw bridge: Mermaid source -> Excalidraw elements -> SVG.
// Returns a Promise<svgString>. Throws on unsupported diagram types so the
// caller can fall back to Mermaid's own renderer.
// Mermaid v11 (and parseMermaidToExcalidraw, which uses it under the hood)
// injects an orphan "Syntax error in text" SVG into <body> when its parser
// fails, and never cleans it up — they accumulate on the page over the
// lifetime of the tab. Sweep them before/after every render attempt.
//
// Important: Mermaid v11 *also* keeps its own working sandbox node on
// <body> (id starts with "dmermaid-" / "mermaid-") that it reuses across
// renders. Removing that crashes the next render with
// "Cannot read properties of null (reading 'firstChild')". So we only
// remove nodes that actually contain the error text — that signature is
// unique to the failure SVGs.
function _sweepOrphanMermaidNodes() {
  // Match both the bare error SVG and the wrapper div Mermaid sometimes
  // leaves on body. Mermaid's *working* sandbox div uses the same id
  // prefix, so we gate every removal on the literal "Syntax error" text
  // — the working sandbox is empty between renders and never matches.
  document.querySelectorAll(
    'body > svg[id^="mermaid-"], body > svg[id^="dmermaid-"], ' +
    'body > div[id^="mermaid-"], body > div[id^="dmermaid-"]'
  ).forEach(n => {
    if (n.closest('.mermaid-block')) return;
    const txt = (n.textContent || '');
    if (/Syntax error|mermaid version/i.test(txt)) n.remove();
  });
}

async function _renderViaExcalidraw(source) {
  const api = window._excalidrawAPI;
  if (!api) throw new Error('excalidraw-not-loaded');
  const { parseMermaidToExcalidraw, convertToExcalidrawElements, exportToSvg } = api;
  _sweepOrphanMermaidNodes();
  const { elements: skeleton, files } = await parseMermaidToExcalidraw(source);
  const elements = convertToExcalidrawElements(skeleton);
  // Strip Roughjs sketchiness AND swap the default Virgil "hand-drawn"
  // font for Helvetica. Without the font swap, diagrams still look like
  // Excalidraw whiteboard scribbles even when the strokes are clean.
  // fontFamily: 1 = Virgil (hand-drawn), 2 = Helvetica, 3 = Cascadia.
  // Applies to text elements AND to the label of arrows/lines (which
  // store the same fontFamily field on the element itself).
  for (const el of elements) {
    if (el.roughness != null) el.roughness = 0;
    if (el.fillStyle != null) el.fillStyle = 'solid';
    if (el.strokeStyle === 'dashed' || el.strokeStyle === 'dotted') {
      // keep dashed/dotted intent
    } else if (el.strokeStyle != null) {
      el.strokeStyle = 'solid';
    }
    if (el.fontFamily != null) el.fontFamily = 2;
  }
  const svgEl = await exportToSvg({
    elements,
    files: files || {},
    appState: {
      exportBackground: false,
      viewBackgroundColor: '#fdfbf6',
      exportEmbedScene: false,
    },
  });
  // exportToSvg returns an SVGElement; we want a string for innerHTML.
  return svgEl.outerHTML;
}

async function _renderViaMermaid(source) {
  const id = 'mermaid-' + Math.random().toString(36).slice(2, 9);
  _sweepOrphanMermaidNodes();
  try {
    const result = await window.mermaid.render(id, source);
    return _resizeSvgForFit(result.svg);
  } finally {
    _sweepOrphanMermaidNodes();
  }
}

function _renderAllMermaidPlaceholders(rootEl) {
  // Block-rendering pipeline:
  //   1. If neither lib is ready: wait for the first to land, retry.
  //   2. Prefer Excalidraw (clean shapes, polished typography).
  //   3. Fall back to Mermaid for diagram types Excalidraw can't parse
  //      (state, ER, gantt, journey, pie, mindmap, timeline).
  //   4. If both fail: show the error + raw source so the user/agent can
  //      diagnose.
  if (!window.mermaid) {
    window.addEventListener('mermaid-ready',
      () => _renderAllMermaidPlaceholders(rootEl), { once: true });
    return;
  }
  const root = rootEl || document;
  const blocks = root.querySelectorAll('.mermaid-block[data-source]:not([data-rendered])');
  blocks.forEach(async block => {
    const source = block.dataset.source;
    block.dataset.rendered = '1';
    let svg = '';
    let renderer = 'mermaid';
    try {
      svg = await _renderViaExcalidraw(source);
      renderer = 'excalidraw';
    } catch (e) {
      // Excalidraw failed (not loaded yet, or unsupported diagram type).
      // Try Mermaid as a fallback.
      try {
        svg = await _renderViaMermaid(source);
      } catch (e2) {
        const msg = (e2 && (e2.message || e2.str || String(e2))) || 'render failed';
        block.innerHTML =
          `<div class="mermaid-error">Diagram error: ${esc(msg)}</div>` +
          `<pre class="mermaid-source">${esc(source)}</pre>`;
        block.style.cursor = 'default';
        return;
      }
    }
    block.innerHTML = svg;
    block.dataset.svg = svg;
    block.dataset.renderer = renderer;
    block.title = 'Click to enlarge';
    block.addEventListener('click', () => _openMermaidViewer(source, svg));
  });
}

function _handleMermaidLine(sessionId, text, el) {
  const buf = _mermaidBuffers[sessionId];
  const isOpening = /^\s*```\s*mermaid\b/.test(text);
  const isClosing = /^\s*```\s*$/.test(text);

  if (!buf && isOpening) {
    const ph = document.createElement('div');
    ph.className = 'mermaid-block';
    ph.innerHTML = '<div class="mermaid-pending">Building diagram…</div>';
    el.appendChild(ph);
    _mermaidBuffers[sessionId] = { placeholder: ph, lines: [] };
    return true;
  }
  if (buf && isClosing) {
    const source = buf.lines.join('\n');
    buf.placeholder.dataset.source = source;
    delete _mermaidBuffers[sessionId];
    _renderAllMermaidPlaceholders(buf.placeholder.parentElement || document);
    return true;
  }
  if (buf) {
    buf.lines.push(text);
    const pending = buf.placeholder.querySelector('.mermaid-pending');
    if (pending) pending.textContent = `Building diagram… (${buf.lines.length} lines)`;
    return true;
  }
  return false;
}

function _openMermaidViewer(source, svg) {
  // Make the viewer SVG fill the modal — strip any inline width/height/style
  // and apply our own. Dimensions controlled via .mermaid-viewer-svg CSS.
  const big = svg.replace(/<svg([^>]*?)\sstyle="[^"]*"/, '<svg$1')
                 .replace(/<svg([^>]*?)\swidth="[^"]*"/, '<svg$1')
                 .replace(/<svg([^>]*?)\sheight="[^"]*"/, '<svg$1')
                 .replace(/<svg /, '<svg style="width:100%;height:auto;display:block" ');
  const overlay = document.createElement('div');
  overlay.className = 'mermaid-viewer-overlay';
  overlay.innerHTML = `
    <div class="mermaid-viewer-content">
      <div class="mermaid-viewer-toolbar">
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-out" title="Zoom out">&minus;</button>
        <span class="mermaid-viewer-zoom-label">100%</span>
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-in" title="Zoom in">+</button>
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-reset" title="Fit to view">&#8634;</button>
        <button class="mermaid-viewer-btn mermaid-viewer-source-toggle" title="Toggle source">&lt;/&gt; source</button>
        <button class="mermaid-viewer-btn mermaid-viewer-close" title="Close (Esc)">&times;</button>
      </div>
      <div class="mermaid-viewer-scroll">
        <div class="mermaid-viewer-svg">${big}</div>
      </div>
      <pre class="mermaid-source" style="display:none">${esc(source)}</pre>
    </div>`;
  document.body.appendChild(overlay);
  const svgWrap = overlay.querySelector('.mermaid-viewer-svg');
  const zoomLabel = overlay.querySelector('.mermaid-viewer-zoom-label');
  let scale = 1;
  const applyScale = () => {
    svgWrap.style.transform = `scale(${scale})`;
    svgWrap.style.transformOrigin = 'top left';
    zoomLabel.textContent = Math.round(scale * 100) + '%';
  };
  const closeIt = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  overlay.addEventListener('click', e => {
    if (e.target === overlay) closeIt();
  });
  overlay.querySelector('.mermaid-viewer-close').addEventListener('click', closeIt);
  overlay.querySelector('.mermaid-viewer-source-toggle').addEventListener('click', e => {
    e.stopPropagation();
    const pre = overlay.querySelector('.mermaid-source');
    pre.style.display = pre.style.display === 'none' ? 'block' : 'none';
  });
  overlay.querySelector('.mermaid-viewer-zoom-in').addEventListener('click', e => {
    e.stopPropagation(); scale = Math.min(scale * 1.25, 5); applyScale();
  });
  overlay.querySelector('.mermaid-viewer-zoom-out').addEventListener('click', e => {
    e.stopPropagation(); scale = Math.max(scale / 1.25, 0.2); applyScale();
  });
  overlay.querySelector('.mermaid-viewer-zoom-reset').addEventListener('click', e => {
    e.stopPropagation(); scale = 1; applyScale();
  });
  // Mouse-wheel zoom over the SVG
  const scrollWrap = overlay.querySelector('.mermaid-viewer-scroll');
  scrollWrap.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return;  // only zoom on Ctrl/Cmd+wheel
    e.preventDefault();
    scale = Math.max(0.2, Math.min(5, scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    applyScale();
  }, { passive: false });
  const onKey = e => {
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') { scale = Math.min(scale * 1.25, 5); applyScale(); }
    else if (e.key === '-') { scale = Math.max(scale / 1.25, 0.2); applyScale(); }
    else if (e.key === '0') { scale = 1; applyScale(); }
  };
  document.addEventListener('keydown', onKey);
}

// Lightbox for inline agent-output images. Reuses the mermaid-viewer
// overlay chrome (backdrop, toolbar, Esc/click-out close) for visual
// consistency; click the image or +/- to zoom.
function _openImageViewer(src) {
  const overlay = document.createElement('div');
  overlay.className = 'mermaid-viewer-overlay';
  overlay.innerHTML = `
    <div class="mermaid-viewer-content">
      <div class="mermaid-viewer-toolbar">
        <button class="mermaid-viewer-btn _iv-zo" title="Zoom out">&minus;</button>
        <span class="mermaid-viewer-zoom-label">100%</span>
        <button class="mermaid-viewer-btn _iv-zi" title="Zoom in">+</button>
        <button class="mermaid-viewer-btn _iv-zr" title="Fit to view">&#8634;</button>
        <a class="mermaid-viewer-btn _iv-open" href="${src}" target="_blank" rel="noopener" title="Open original">open ↗</a>
        <button class="mermaid-viewer-btn _iv-close" title="Close (Esc)">&times;</button>
      </div>
      <div class="mermaid-viewer-scroll">
        <div class="mermaid-viewer-svg"><img src="${src}" style="display:block;width:100%;height:auto" alt=""></div>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const wrap = overlay.querySelector('.mermaid-viewer-svg');
  const zoomLabel = overlay.querySelector('.mermaid-viewer-zoom-label');
  let scale = 1;
  const applyScale = () => {
    wrap.style.transform = `scale(${scale})`;
    wrap.style.transformOrigin = 'top left';
    zoomLabel.textContent = Math.round(scale * 100) + '%';
  };
  const closeIt = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  overlay.addEventListener('click', e => { if (e.target === overlay) closeIt(); });
  overlay.querySelector('._iv-close').addEventListener('click', closeIt);
  overlay.querySelector('._iv-zi').addEventListener('click', e => {
    e.stopPropagation(); scale = Math.min(scale * 1.25, 5); applyScale();
  });
  overlay.querySelector('._iv-zo').addEventListener('click', e => {
    e.stopPropagation(); scale = Math.max(scale / 1.25, 0.2); applyScale();
  });
  overlay.querySelector('._iv-zr').addEventListener('click', e => {
    e.stopPropagation(); scale = 1; applyScale();
  });
  const scrollWrap = overlay.querySelector('.mermaid-viewer-scroll');
  scrollWrap.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    scale = Math.max(0.2, Math.min(5, scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
    applyScale();
  }, { passive: false });
  const onKey = e => {
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') { scale = Math.min(scale * 1.25, 5); applyScale(); }
    else if (e.key === '-') { scale = Math.max(scale / 1.25, 0.2); applyScale(); }
    else if (e.key === '0') { scale = 1; applyScale(); }
  };
  document.addEventListener('keydown', onKey);
}

// ── ES-module interop ───────────────────────────────────────────────────────
// Re-expose page-called functions on window. Inbound inline callers:
// refreshModal + openPlanViewer call _renderAllMermaidPlaceholders after a
// rebuild; the outputLines builder + openPlanViewer's body builder emit
// placeholders via _mermaidPlaceholderHTML; appendAgentLine's streaming path
// calls _handleMermaidLine; and the rich-text formatter generates
// onclick="_openImageViewer(this.src)" attributes, which resolve against
// the global object at click time. Everything else is module-private
// (_mermaidBuffers, _resizeSvgForFit, _sweepOrphanMermaidNodes,
// _renderViaExcalidraw, _renderViaMermaid, and _openMermaidViewer — wired
// only via region-internal addEventListener). No accessor or identity
// bridges needed: the formal scans found zero generated-handler assignments
// and zero wholesale reassignments of any region binding anywhere.
// The mermaid library loader/theming stays as the inline <head> module
// (window.mermaid + 'mermaid-ready'); this module only consumes it.
window._mermaidPlaceholderHTML = _mermaidPlaceholderHTML;
window._renderAllMermaidPlaceholders = _renderAllMermaidPlaceholders;
window._handleMermaidLine = _handleMermaidLine;
window._openImageViewer = _openImageViewer;
