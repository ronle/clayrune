// ── Drag-and-drop grid reordering ────────────────────────────────────────────

let dragState = null;

function trimTrailingNulls() {
  while (projectOrder.length > 0 && projectOrder[projectOrder.length - 1] === null) projectOrder.pop();
}

function persistGridOrder() {
  localStorage.setItem(_orderKey, JSON.stringify(projectOrder));
  // Only persist to server from desktop — mobile layout is local-only
  if (!_isMobileDevice) {
    fetch(API_BASE + '/api/projects/order', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order: projectOrder})
    }).catch(() => {});
  }
}

function getSlotIndexAtPoint(clientX, clientY) {
  const col = document.getElementById('projects-col');
  const colRect = col.getBoundingClientRect();
  const children = col.children;
  if (children.length === 0) return -1;
  const firstRect = children[0].getBoundingClientRect();
  const cellW = firstRect.width;
  const cellH = firstRect.height;
  const gap = parseFloat(getComputedStyle(col).gap) || 16;
  const numCols = Math.max(1, Math.round((colRect.width + gap) / (cellW + gap)));
  const relX = clientX - colRect.left;
  const relY = clientY - colRect.top + col.scrollTop;
  const colIdx = Math.min(numCols - 1, Math.max(0, Math.floor(relX / (cellW + gap))));
  const rowIdx = Math.max(0, Math.floor(relY / (cellH + gap)));
  return rowIdx * numCols + colIdx;
}

function ensureSlotsUpTo(targetSlot) {
  const col = document.getElementById('projects-col');
  while (col.children.length <= targetSlot) {
    const spacer = document.createElement('div');
    spacer.className = 'grid-spacer';
    spacer.dataset.slot = col.children.length;
    col.appendChild(spacer);
  }
}

function startTileDrag(card, clientX, clientY) {
  const rect = card.getBoundingClientRect();
  dragState.offsetX = clientX - rect.left;
  dragState.offsetY = clientY - rect.top;
  dragState.started = true;
  card.classList.add('dragging');
  // Create ghost
  const ghost = card.cloneNode(true);
  ghost.classList.remove('dragging');
  ghost.classList.add('drag-ghost');
  ghost.style.width = rect.width + 'px';
  ghost.style.height = rect.height + 'px';
  ghost.style.left = (clientX - dragState.offsetX) + 'px';
  ghost.style.top = (clientY - dragState.offsetY) + 'px';
  document.body.appendChild(ghost);
  dragState.ghost = ghost;
}

function updateTileDrag(clientX, clientY) {
  if (dragState.ghost) {
    dragState.ghost.style.left = (clientX - dragState.offsetX) + 'px';
    dragState.ghost.style.top = (clientY - dragState.offsetY) + 'px';
  }
  const col = document.getElementById('projects-col');
  col.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
  const targetSlot = getSlotIndexAtPoint(clientX, clientY);
  if (targetSlot >= 0) {
    const maxSlot = Math.min(targetSlot, (allProjects.length * 3));
    ensureSlotsUpTo(maxSlot);
    const targetEl = col.children[maxSlot];
    if (targetEl) targetEl.classList.add('drop-target');
    dragState.targetSlot = maxSlot;
  }
}

function endTileDrag() {
  if (!dragState) return;
  const { id: srcId, started, targetSlot, ghost } = dragState;
  dragState = null;
  if (ghost) ghost.remove();
  const col = document.getElementById('projects-col');
  col.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
  col.querySelectorAll('.card').forEach(c => c.classList.remove('dragging'));
  if (!started) return;
  lastDragEnd = Date.now();
  if (targetSlot === undefined || targetSlot < 0) return;
  moveProjectToSlot(srcId, targetSlot);
}

function moveProjectToSlot(srcId, targetSlot) {
  // Remove src from current position
  const srcIdx = projectOrder.indexOf(srcId);
  if (srcIdx !== -1) projectOrder.splice(srcIdx, 1);

  // Remove nulls (compact) then insert at target position, pushing others right
  projectOrder = projectOrder.filter(e => e !== null);
  const insertAt = Math.min(targetSlot, projectOrder.length);
  projectOrder.splice(insertAt, 0, srcId);

  trimTrailingNulls();
  persistGridOrder();
  applyOrder();
  renderProjects();
}

function compactGrid() {
  projectOrder = projectOrder.filter(e => e !== null);
  persistGridOrder();
  applyOrder();
  renderProjects();
}

// Mouse events
document.getElementById('projects-col').addEventListener('mousedown', (e) => {
  const card = e.target.closest('.card');
  if (!card) return;
  if (e.target.closest('button, a, input, select, textarea, [contenteditable]')) return;
  dragState = { id: card.dataset.id, card, startX: e.clientX, startY: e.clientY, started: false };
});

document.addEventListener('mousemove', (e) => {
  if (!dragState) return;
  if (!dragState.started) {
    if (Math.abs(e.clientX - dragState.startX) + Math.abs(e.clientY - dragState.startY) < 8) return;
    startTileDrag(dragState.card, e.clientX, e.clientY);
  }
  updateTileDrag(e.clientX, e.clientY);
});

document.addEventListener('mouseup', () => endTileDrag());

// Touch events — long-press (300ms) required to start tile drag
// This prevents conflicts with scroll, pinch-to-zoom, and swipe gestures
let tileLongPressTimer = null;

function cancelTileLongPress() {
  if (tileLongPressTimer) { clearTimeout(tileLongPressTimer); tileLongPressTimer = null; }
}

document.getElementById('projects-col').addEventListener('touchstart', (e) => {
  cancelTileLongPress();
  if (e.touches.length > 1) return;  // multi-finger = pinch, ignore
  const card = e.target.closest('.card');
  if (!card) return;
  if (e.target.closest('button, a, input, select, textarea')) return;
  const t = e.touches[0];
  const startX = t.clientX, startY = t.clientY;
  tileLongPressTimer = setTimeout(() => {
    tileLongPressTimer = null;
    dragState = { id: card.dataset.id, card, startX, startY, started: false, isTouch: true };
    // Visual feedback: slight scale
    card.style.transform = 'scale(0.97)';
    card.style.transition = 'transform 0.1s';
    setTimeout(() => { card.style.transform = ''; card.style.transition = ''; }, 200);
  }, 300);
}, { passive: true });

document.addEventListener('touchmove', (e) => {
  // Cancel long-press if finger moves before timer fires
  if (tileLongPressTimer) {
    const t = e.touches[0];
    cancelTileLongPress();  // any movement cancels — let browser handle scroll/pinch
    return;
  }
  if (!dragState || !dragState.isTouch) return;
  const t = e.touches[0];
  if (!dragState.started) {
    if (Math.abs(t.clientX - dragState.startX) + Math.abs(t.clientY - dragState.startY) < 12) return;
    e.preventDefault();
    startTileDrag(dragState.card, t.clientX, t.clientY);
  }
  if (dragState.started) {
    e.preventDefault();
    updateTileDrag(t.clientX, t.clientY);
  }
}, { passive: false });

document.addEventListener('touchend', () => { cancelTileLongPress(); endTileDrag(); });

// ── Modal drag-to-move (multi-modal) ────────────────────────────────────────

// ── Aero-Snap engine ────────────────────────────────────────────────────────
// Drag a modal near a viewport edge / corner → translucent preview shows the
// target zone → release commits the snap. Dragging a snapped modal more than
// SNAP_UNSNAP_THRESHOLD px restores it to its pre-snap geometry and lets it
// follow the cursor. Mobile (< 961px) is full-screen-by-CSS and skipped.

const SNAP_TRIGGER = 24;                 // px from screen edge to activate a zone
const SNAP_UNSNAP_THRESHOLD = 24;        // px dragged before a snapped modal pops free

// Live workspace rect = viewport minus the page header and the sidebar.
// Sidebar width is pinned to its BASE (collapsed) value — not the live
// getBoundingClientRect — so transient `:hover` expansion (52 → 220 px)
// doesn't shift the snap target out from under the cursor mid-drag. If the
// user explicitly toggles `.sidebar.expanded`, we honor that as the new base.
function _workspaceRect() {
  const headerEl = document.querySelector('.header') || document.querySelector('.mc-app-bar');
  const headerH = headerEl ? headerEl.getBoundingClientRect().height : 48;
  const sidebar = document.querySelector('.sidebar');
  let sidebarW = 0;
  if (sidebar && getComputedStyle(sidebar).display !== 'none') {
    if (sidebar.classList.contains('expanded')) {
      sidebarW = sidebar.getBoundingClientRect().width;
    } else {
      const baseW = parseFloat(
        getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w'));
      sidebarW = baseW || 52;
    }
  }
  return {
    left: sidebarW,
    top: headerH,
    width: Math.max(0, window.innerWidth - sidebarW),
    height: Math.max(0, window.innerHeight - headerH),
  };
}

function _snapEnabled() {
  return !_isMobileDevice && window.innerWidth > 960;
}

// Compute the rect that zone `id` should occupy within the current workspace.
function _zoneRect(zoneId) {
  const w = _workspaceRect();
  const halfW = Math.round(w.width / 2);
  const halfH = Math.round(w.height / 2);
  const thirdW = Math.round(w.width / 3);
  switch (zoneId) {
    case 'full':         return { left: w.left,             top: w.top,         width: w.width,             height: w.height };
    case 'left-half':    return { left: w.left,             top: w.top,         width: halfW,               height: w.height };
    case 'right-half':   return { left: w.left + halfW,     top: w.top,         width: w.width - halfW,     height: w.height };
    case 'top-half':     return { left: w.left,             top: w.top,         width: w.width,             height: halfH };
    case 'bottom-half':  return { left: w.left,             top: w.top + halfH, width: w.width,             height: w.height - halfH };
    case 'tl-quarter':   return { left: w.left,             top: w.top,         width: halfW,               height: halfH };
    case 'tr-quarter':   return { left: w.left + halfW,     top: w.top,         width: w.width - halfW,     height: halfH };
    case 'bl-quarter':   return { left: w.left,             top: w.top + halfH, width: halfW,               height: w.height - halfH };
    case 'br-quarter':   return { left: w.left + halfW,     top: w.top + halfH, width: w.width - halfW,     height: w.height - halfH };
    case 'left-third':   return { left: w.left,             top: w.top,         width: thirdW,              height: w.height };
    case 'center-third': return { left: w.left + thirdW,    top: w.top,         width: thirdW,              height: w.height };
    case 'right-third':  return { left: w.left + 2*thirdW,  top: w.top,         width: w.width - 2*thirdW,  height: w.height };
    default: return null;
  }
}

// Map pointer position (x,y) to a snap zone, or null if not inside any.
// Detection uses VIEWPORT edges (0, 0, innerWidth, innerHeight) — not the
// workspace rect — so the cursor naturally crossing into the sidebar strip
// or above the page header (as it must, to drag to a screen edge) still
// triggers a snap. The zone TARGET is computed by _zoneRect against the
// workspace rect, so the modal lands inside the page chrome.
// Layout: a 24×24 px square in each corner triggers a quarter; a 24 px strip
// along each edge (outside the corners) triggers a half / maximize. There's
// intentionally no bottom-edge full-width snap (matches Aero / Windows).
function _zoneFromPointer(x, y) {
  if (x < -8 || x > window.innerWidth + 8) return null;
  if (y < -8 || y > window.innerHeight + 8) return null;
  const nearTop    = y <= SNAP_TRIGGER;
  const nearBottom = y >= window.innerHeight - SNAP_TRIGGER;
  const nearLeft   = x <= SNAP_TRIGGER;
  const nearRight  = x >= window.innerWidth - SNAP_TRIGGER;
  if (nearTop && nearLeft)     return 'tl-quarter';
  if (nearTop && nearRight)    return 'tr-quarter';
  if (nearBottom && nearLeft)  return 'bl-quarter';
  if (nearBottom && nearRight) return 'br-quarter';
  if (nearTop)    return 'full';        // top edge → maximize
  if (nearLeft)   return 'left-half';
  if (nearRight)  return 'right-half';
  return null;                          // bottom edge alone: no snap
}

let _snapPreviewEl = null;
function _ensureSnapPreview() {
  if (_snapPreviewEl && document.body.contains(_snapPreviewEl)) return _snapPreviewEl;
  _snapPreviewEl = document.createElement('div');
  _snapPreviewEl.className = 'mc-snap-preview';
  document.body.appendChild(_snapPreviewEl);
  return _snapPreviewEl;
}
function _drawSnapPreview(zoneId) {
  const el = _ensureSnapPreview();
  const r = _zoneRect(zoneId);
  if (!r) { _clearSnapPreview(); return; }
  el.style.left = r.left + 'px';
  el.style.top = r.top + 'px';
  el.style.width = r.width + 'px';
  el.style.height = r.height + 'px';
  el.classList.add('visible');
}
function _clearSnapPreview() {
  if (!_snapPreviewEl) return;
  _snapPreviewEl.classList.remove('visible');
}

// Apply a snap zone to a modal window. Captures pre-snap geometry on the
// first snap so unSnap can restore it. Persists snap + preSnap in
// mc_modal_prefs and the open-modals snapshot for restore-on-reload.
function applySnap(modalId, zoneId) {
  if (!_snapEnabled()) return;
  const entry = openModals.get(modalId);
  if (!entry) return;
  const win = entry.element;
  const content = win.querySelector('.modal-content');
  if (!content) return;
  const r = _zoneRect(zoneId);
  if (!r) return;

  // Capture pre-snap geometry the first time we snap (so unsnap restores).
  if (!entry.snap) {
    const cr = win.getBoundingClientRect();
    entry.preSnap = {
      left: Math.round(cr.left),
      top: Math.round(cr.top),
      width: content.offsetWidth,
      height: content.offsetHeight,
    };
  }
  entry.snap = zoneId;
  win.classList.add('is-snapped');
  win.style.left = r.left + 'px';
  win.style.top = r.top + 'px';
  content.style.width = r.width + 'px';
  content.style.height = r.height + 'px';

  // Persist (skip for synthetic modals; _setModalPref already guards mobile).
  if (entry.projectId && !modalId.startsWith('__')) {
    _setModalPref(entry.projectId, {
      snap: zoneId,
      preSnap: entry.preSnap,
      // Don't overwrite width/height — those are the user-resized "free" size.
    });
  }
  _saveOpenModalsSnapshot();
}

// Toggle the modal "pin" state. Pinned (default) shows the full data sheet;
// unpinned collapses everything between the name row and the tab body — handy
// when several modals are tiled and you only need the title bars visible.
// State persists in mc_modal_prefs.unpinned (per-project).
function toggleModalPin(modalId) {
  const entry = openModals.get(modalId);
  if (!entry) return;
  const win = entry.element;
  const becomingUnpinned = !win.classList.contains('is-unpinned');
  win.classList.toggle('is-unpinned', becomingUnpinned);
  entry.unpinned = becomingUnpinned;
  if (entry.projectId && !modalId.startsWith('__')) {
    _setModalPref(entry.projectId, { unpinned: becomingUnpinned });
  }
  _saveOpenModalsSnapshot();
}

// Restore a snapped modal to its pre-snap geometry. If preSnap is missing
// (shouldn't happen, but defensive), center the modal at its previous size.
function unSnap(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.snap) return;
  const win = entry.element;
  const content = win.querySelector('.modal-content');
  const ps = entry.preSnap;
  if (ps && content) {
    win.style.left = ps.left + 'px';
    win.style.top = ps.top + 'px';
    content.style.width = ps.width + 'px';
    content.style.height = ps.height + 'px';
  }
  entry.snap = null;
  entry.preSnap = null;
  win.classList.remove('is-snapped');
  if (entry.projectId && !modalId.startsWith('__')) {
    _setModalPref(entry.projectId, { snap: null, preSnap: null });
  }
  _saveOpenModalsSnapshot();
}

// ── Multi-modal tile templates (header "Tile" button) ──────────────────────
// Templates are keyed by the count of currently-visible modals. Each template
// is `{ id, label, zones }` — `zones` is an ordered array; the modal with the
// highest zIndex (most-recently focused) goes to zones[0], next to zones[1],
// etc. A template only shows in the popover if its zones.length === modalCount
// (so the user can't half-tile a set; we keep it deterministic).
//
// Extra entries with N+1 zones are added for "primary + leftovers stacked"
// scenarios, but we keep Phase 1 strict-match for simplicity.
const _TILE_TEMPLATES = {
  1: [
    { id: 'full',        label: 'Maximize',          zones: ['full'] },
  ],
  2: [
    { id: '2-h',         label: 'Side by side',      zones: ['left-half', 'right-half'] },
    { id: '2-v',         label: 'Top / bottom',      zones: ['top-half', 'bottom-half'] },
  ],
  3: [
    { id: '3-cols',      label: 'Three columns',     zones: ['left-third', 'center-third', 'right-third'] },
    { id: '3-l-stack',   label: 'Large left + stack',zones: ['left-half', 'tr-quarter', 'br-quarter'] },
    { id: '3-r-stack',   label: 'Stack + large right',zones:['right-half', 'tl-quarter', 'bl-quarter'] },
  ],
  4: [
    { id: '2x2',         label: '2 × 2 quadrants',   zones: ['tl-quarter', 'tr-quarter', 'bl-quarter', 'br-quarter'] },
  ],
};

// Returns visible (non-minimized) project modal entries ordered by zIndex
// descending so the most-recently-focused modal is first.
function _visibleModalsByFocus() {
  const list = [];
  for (const [modalId, entry] of openModals) {
    if (!entry || entry.minimized) continue;
    if (modalId.startsWith('__')) continue;  // skip synthetic (terminal pop-outs, etc.)
    list.push({ modalId, zIndex: entry.zIndex || 0 });
  }
  list.sort((a, b) => b.zIndex - a.zIndex);
  return list.map(x => x.modalId);
}

function _templatesForCount(n) {
  return _TILE_TEMPLATES[n] || [];
}

// Apply a template by id. The N most-recently-focused modals get assigned to
// the template's zones in order. Idempotent — re-applies cleanly even if some
// modals are already snapped.
function tileAllModals(templateId) {
  if (!_snapEnabled()) return;
  const ids = _visibleModalsByFocus();
  const tpl = (_TILE_TEMPLATES[ids.length] || []).find(t => t.id === templateId);
  if (!tpl) return;
  tpl.zones.forEach((zone, i) => {
    if (ids[i]) applySnap(ids[i], zone);
  });
}

// Render a mini-thumbnail of a template by stacking labelled cells in the
// same proportions as their zones. The number on each cell indicates which
// focused modal goes there (1 = most-recently-focused, 2 = next, etc.).
function _renderTileThumbnail(zones) {
  const ws = _workspaceRect();
  if (!ws.width || !ws.height) return '<div class="tile-thumb"></div>';
  const cells = zones.map((zone, i) => {
    const r = _zoneRect(zone);
    if (!r) return '';
    const left = ((r.left - ws.left) / ws.width * 100).toFixed(2);
    const top = ((r.top - ws.top) / ws.height * 100).toFixed(2);
    const w = (r.width / ws.width * 100).toFixed(2);
    const h = (r.height / ws.height * 100).toFixed(2);
    return `<div class="ttb-cell" style="left:${left}%;top:${top}%;width:${w}%;height:${h}%">${i + 1}</div>`;
  }).join('');
  return `<div class="tile-thumb">${cells}</div>`;
}

// Open/close the popover. Renders templates based on the live visible-modal
// count each time it opens — so it always reflects the current state.
function toggleTileModalsPopover(event) {
  if (event) event.stopPropagation();
  const pop = document.getElementById('tile-modals-popover');
  if (!pop) return;
  if (pop.classList.contains('open')) {
    pop.classList.remove('open');
    return;
  }
  const ids = _visibleModalsByFocus();
  const tpls = _templatesForCount(ids.length);
  if (ids.length === 0) {
    pop.innerHTML = '<div class="tmp-empty">No open modals to tile</div>';
  } else if (tpls.length === 0) {
    pop.innerHTML = `<div class="tmp-empty">No tile layout for ${ids.length} modals<br><span style="font-size:11px;opacity:0.7">(supported: 1–4)</span></div>`;
  } else {
    const heading = `<div class="tmp-title">${ids.length} modal${ids.length === 1 ? '' : 's'} open</div>`;
    pop.innerHTML = heading + tpls.map(t => `
      <button class="tile-tpl-btn" onclick="tileAllModals('${t.id}'); toggleTileModalsPopover()">
        ${_renderTileThumbnail(t.zones)}
        <span>${t.label}</span>
      </button>
    `).join('');
  }
  pop.classList.add('open');
}

// Close popover on outside click / Escape
document.addEventListener('click', (e) => {
  const pop = document.getElementById('tile-modals-popover');
  if (!pop || !pop.classList.contains('open')) return;
  if (e.target.closest('#tile-modals-btn')) return;
  pop.classList.remove('open');
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const pop = document.getElementById('tile-modals-popover');
  if (pop && pop.classList.contains('open')) pop.classList.remove('open');
});

// Re-apply current snap zones after a viewport resize. Debounced so a continuous
// resize drag doesn't thrash. Sidebar hover-expand transitions take ~200ms;
// we listen on `.sidebar mouseleave` separately to recompute once the sidebar
// has settled, avoiding mid-transition jitter.
let _snapResizeTimer = null;
function _reapplyAllSnaps() {
  if (!_snapEnabled()) return;
  for (const [, entry] of openModals) {
    if (entry.snap) {
      const r = _zoneRect(entry.snap);
      if (!r) continue;
      const content = entry.element.querySelector('.modal-content');
      entry.element.style.left = r.left + 'px';
      entry.element.style.top = r.top + 'px';
      if (content) {
        content.style.width = r.width + 'px';
        content.style.height = r.height + 'px';
      }
    }
  }
}
window.addEventListener('resize', () => {
  clearTimeout(_snapResizeTimer);
  _snapResizeTimer = setTimeout(_reapplyAllSnaps, 100);
});
// Sidebar collapses back from 220→52 on mouseleave; recompute after the
// 200ms CSS transition has settled so snapped modals don't sit on stale
// geometry.
document.addEventListener('DOMContentLoaded', () => {
  const sb = document.querySelector('.sidebar');
  if (sb) sb.addEventListener('mouseleave', () => setTimeout(_reapplyAllSnaps, 220));
});

// Purge legacy per-project prompt-history entries (feature removed 2026-05-22).
try {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const k = localStorage.key(i);
    if (k && k.startsWith('prompt_history_')) localStorage.removeItem(k);
  }
} catch (e) {}

let modalDrag = null;

function modalDragStart(x, y, target) {
  const header = target.closest('.modal-header');
  if (!header) return false;
  if (target.closest('input, button, select, textarea, [contenteditable]')) return false;
  const win = target.closest('.modal-window');
  if (!win) return false;
  const rect = win.getBoundingClientRect();
  // Find the modal entry so we can read/clear snap state during the drag.
  let entryRef = null, modalIdRef = null;
  for (const [mid, ent] of openModals) {
    if (ent.element === win) { entryRef = ent; modalIdRef = mid; break; }
  }
  modalDrag = {
    element: win,
    entry: entryRef,
    modalId: modalIdRef,
    startX: x, startY: y,
    origLeft: rect.left, origTop: rect.top,
    wasSnapped: !!(entryRef && entryRef.snap),
    activeZone: null,
    unsnapped: false,
  };
  header.classList.add('dragging');
  return true;
}

function modalDragMove(x, y) {
  if (!modalDrag) return;
  const dx = x - modalDrag.startX;
  const dy = y - modalDrag.startY;

  // If we started on a snapped modal, hold position until the user has dragged
  // far enough to "tear it off" — then pop it free at the cursor (Aero-Snap
  // tear-off: the modal recenters on the cursor at its pre-snap size, not its
  // snapped size, so it doesn't visually leap).
  if (modalDrag.wasSnapped && !modalDrag.unsnapped) {
    if (Math.hypot(dx, dy) < SNAP_UNSNAP_THRESHOLD) return;
    const entry = modalDrag.entry;
    const ps = entry && entry.preSnap;
    if (entry) {
      entry.snap = null;
      modalDrag.element.classList.remove('is-snapped');
      const content = modalDrag.element.querySelector('.modal-content');
      if (ps && content) {
        content.style.width = ps.width + 'px';
        content.style.height = ps.height + 'px';
      }
      entry.preSnap = null;
      // Tear-off must clear the persisted snap too, else the per-project pref
      // (mc_modal_prefs) re-snaps the modal on every reopen. Mirrors unSnap().
      if (entry.projectId && modalDrag.modalId && !modalDrag.modalId.startsWith('__')) {
        _setModalPref(entry.projectId, { snap: null, preSnap: null });
      }
    }
    // Recenter the un-snapped window under the cursor so it tracks naturally.
    const w = modalDrag.element.offsetWidth;
    const h = modalDrag.element.offsetHeight;
    modalDrag.origLeft = x - w / 2;
    modalDrag.origTop = y - 12;
    modalDrag.startX = x;
    modalDrag.startY = y;
    modalDrag.unsnapped = true;
    return;
  }

  modalDrag.element.style.left = Math.max(0, Math.min(window.innerWidth - 100, modalDrag.origLeft + dx)) + 'px';
  modalDrag.element.style.top = Math.max(0, Math.min(window.innerHeight - 50, modalDrag.origTop + dy)) + 'px';

  // Update snap-zone preview based on cursor position
  if (_snapEnabled()) {
    const zone = _zoneFromPointer(x, y);
    if (zone !== modalDrag.activeZone) {
      modalDrag.activeZone = zone;
      if (zone) _drawSnapPreview(zone); else _clearSnapPreview();
    }
  }
}

function modalDragEnd() {
  if (!modalDrag) return;
  modalDrag.element.querySelector('.modal-header.dragging')?.classList.remove('dragging');
  const zone = modalDrag.activeZone;
  const modalId = modalDrag.modalId;
  _clearSnapPreview();
  modalDrag = null;
  if (zone && modalId) {
    applySnap(modalId, zone);
  } else {
    _saveOpenModalsSnapshot();
  }
}

document.getElementById('modal-layer').addEventListener('mousedown', (e) => {
  if (modalDragStart(e.clientX, e.clientY, e.target)) e.preventDefault();
});
document.addEventListener('mousemove', (e) => modalDragMove(e.clientX, e.clientY));
document.addEventListener('mouseup', modalDragEnd);

document.getElementById('modal-layer').addEventListener('touchstart', (e) => {
  const t = e.touches[0];
  if (modalDragStart(t.clientX, t.clientY, e.target)) e.preventDefault();
}, { passive: false });
document.addEventListener('touchmove', (e) => {
  if (!modalDrag) return;
  e.preventDefault();
  modalDragMove(e.touches[0].clientX, e.touches[0].clientY);
}, { passive: false });
document.addEventListener('touchend', modalDragEnd);

// ── Agent chat separator drag (resize input area) ───────────────────────────

let chatResize = null;

function separatorDragStart(y, target) {
  const handle = target.closest('.agent-chat-separator');
  if (!handle) return false;
  const chat = handle.closest('.agent-chat');
  const textarea = handle.nextElementSibling?.querySelector('.agent-task-input');
  if (!textarea || !chat) return false;
  const chatH = chat.offsetHeight;
  // Capture the modal+session so we can re-run sizeAgentChat live during the drag.
  const win = chat.closest('.modal-window');
  const sessionId = textarea.id ? textarea.id.replace(/^agent-followup-/, '') : null;
  chatResize = { textarea, chat, win, sessionId, startY: y, origH: textarea.offsetHeight, chatH };
  document.body.style.cursor = 'ns-resize';
  document.body.style.userSelect = 'none';
  return true;
}

function separatorDragMove(y) {
  if (!chatResize) return;
  const dy = chatResize.startY - y;  // drag up = positive = grow textarea
  const maxH = chatResize.chatH - 80;  // leave at least 80px for output + separator
  const newH = Math.max(38, Math.min(maxH, chatResize.origH + dy));
  chatResize.textarea.style.height = newH + 'px';
  // Keep the persisted cache in lock-step with the live drag so any refresh
  // that fires mid-drag (SSE event, status update, etc.) restores the
  // in-progress height instead of snapping back to the default rows="1".
  if (chatResize.textarea.id) {
    textareaHeights[chatResize.textarea.id] = newH + 'px';
  }
  // Re-run the chat geometry pass live. Without this the flex children only
  // recompute when the next periodic refresh fires (SSE tick, status update),
  // which the user sees as the conversation suddenly jumping up by the amount
  // they grew the textarea — the deferred layout finally catching up.
  if (chatResize.win && chatResize.sessionId) {
    sizeAgentChat(chatResize.win, chatResize.sessionId);
  }
}

function separatorDragEnd() {
  if (!chatResize) return;
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
  // Persist the user's drag explicitly so it survives every refresh path,
  // not just refreshModalById's snapshot/restore cycle. Keyed by textarea id
  // (which encodes the session id), shared with refreshModalById's restore loop.
  const ta = chatResize.textarea;
  if (ta && ta.id) {
    const h = ta.style.height || (ta.offsetHeight + 'px');
    textareaHeights[ta.id] = h;
  }
  chatResize = null;
}

document.getElementById('modal-layer').addEventListener('mousedown', (e) => {
  if (separatorDragStart(e.clientY, e.target)) e.preventDefault();
});
document.addEventListener('mousemove', (e) => separatorDragMove(e.clientY));
document.addEventListener('mouseup', separatorDragEnd);

document.getElementById('modal-layer').addEventListener('touchstart', (e) => {
  if (separatorDragStart(e.touches[0].clientY, e.target)) e.preventDefault();
}, { passive: false });
document.addEventListener('touchmove', (e) => {
  if (!chatResize) return;
  e.preventDefault();
  separatorDragMove(e.touches[0].clientY);
}, { passive: false });
document.addEventListener('touchend', separatorDragEnd);

// ── Touch resize for modal windows (mobile) ─────────────────────────────────

let modalTouchResize = null;
const RESIZE_ZONE = 40; // px from bottom-right corner

// Corner drag resize (single finger)
document.getElementById('modal-layer').addEventListener('touchstart', (e) => {
  if (modalDrag || chatResize) return;
  const content = e.target.closest('.modal-content');
  if (!content) return;
  const t = e.touches[0];
  const rect = content.getBoundingClientRect();
  if (t.clientX < rect.right - RESIZE_ZONE || t.clientY < rect.bottom - RESIZE_ZONE) return;
  e.preventDefault();
  modalTouchResize = { content, startX: t.clientX, startY: t.clientY, origW: content.offsetWidth, origH: content.offsetHeight };
}, { passive: false });

// Pinch-to-resize (two fingers anywhere on modal)
let modalPinch = null;

function pinchDist(e) {
  const a = e.touches[0], b = e.touches[1];
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

document.getElementById('modal-layer').addEventListener('touchstart', (e) => {
  if (e.touches.length === 2) {
    const modal = e.target.closest('.modal-window');
    const content = e.target.closest('.modal-content');
    if (!content || !modal) return;
    e.preventDefault();
    if (_isMobileDevice || window.innerWidth <= 960) {
      // On mobile: pinch = text zoom
      const modalId = modal.dataset.modalId;
      if (!modalZoomLevels[modalId]) modalZoomLevels[modalId] = 13;
      modalPinch = { modal, modalId, startDist: pinchDist(e), startZoom: modalZoomLevels[modalId], isZoom: true };
    } else {
      // On desktop: pinch = resize modal
      modalPinch = { content, startDist: pinchDist(e), origW: content.offsetWidth, origH: content.offsetHeight, isZoom: false };
    }
  }
}, { passive: false });

document.addEventListener('touchmove', (e) => {
  // Corner drag
  if (modalTouchResize && e.touches.length === 1) {
    e.preventDefault();
    const t = e.touches[0];
    const dx = t.clientX - modalTouchResize.startX;
    const dy = t.clientY - modalTouchResize.startY;
    const minW = _isMobileDevice ? 260 : 380, minH = _isMobileDevice ? 200 : 300;
    modalTouchResize.content.style.width = Math.max(minW, modalTouchResize.origW + dx) + 'px';
    modalTouchResize.content.style.height = Math.max(minH, modalTouchResize.origH + dy) + 'px';
    return;
  }
  // Pinch
  if (modalPinch && e.touches.length === 2) {
    e.preventDefault();
    const scale = pinchDist(e) / modalPinch.startDist;
    if (modalPinch.isZoom) {
      // Text zoom: scale font size from start zoom level
      const newSize = Math.max(8, Math.min(28, Math.round(modalPinch.startZoom * scale)));
      modalZoomLevels[modalPinch.modalId] = newSize;
      applyModalZoom(modalPinch.modal, newSize);
    } else {
      // Modal resize
      const minW = 380, minH = 300;
      modalPinch.content.style.width = Math.max(minW, Math.round(modalPinch.origW * scale)) + 'px';
      modalPinch.content.style.height = Math.max(minH, Math.round(modalPinch.origH * scale)) + 'px';
    }
  }
}, { passive: false });

document.addEventListener('touchend', (e) => {
  modalTouchResize = null;
  // #3: persist a pinch-zoom text-size change so it survives a modal reopen.
  // (Button/keyboard zoom already saves via _setModalPref at applyZoom; the
  // pinch handler updated modalZoomLevels + applied it live but never saved the
  // pref, so reopening the modal reverted to the default size.)
  if (modalPinch && modalPinch.isZoom) {
    // Persist via the mobile-inclusive zoom store (the geometry pref no-ops on
    // mobile, which is exactly where pinch-zoom lives). Keep the geometry-pref
    // write too so existing desktop prefs stay consistent.
    if (typeof _setModalZoom === 'function') _setModalZoom(modalPinch.modalId, modalZoomLevels[modalPinch.modalId]);
    if (typeof _setModalPref === 'function') _setModalPref(modalPinch.modalId, { zoom: modalZoomLevels[modalPinch.modalId] });
  }
  if (e.touches.length < 2) modalPinch = null;
});

// ── Ctrl+Scroll zoom on all modal content ───────────────────────────────────


function applyModalZoom(modal, size) {
  // Set on modal-content for elements that inherit
  const content = modal.querySelector('.modal-content');
  if (content) {
    content.style.fontSize = size + 'px';
    // Drive the zoomable agent-chat text via a CSS var too. Streamed/appended
    // agent lines never pass back through this function, and on mobile the
    // explicit `.agent-line { font-size: 12.5px }` rule blocks inheritance — so
    // a follow-up message kept its CSS default size instead of the user's zoom.
    // The var cascades from .modal-content to every current AND future line, so
    // new bubbles match without a per-append hook. See app.css var(--mc-zoom-font).
    content.style.setProperty('--mc-zoom-font', size + 'px');
  }
  // Also set directly on elements with explicit font-size in CSS (overrides inheritance)
  modal.querySelectorAll(
    '.agent-output, .ac-session-output, .plan-viewer-body, ' +
    'textarea, pre, code, .agent-line, .agent-activity, ' +
    '.memory-hint, .rules-hint, .rules-label, ' +
    '.backlog-text, .backlog-input, .log-entry, ' +
    '.hm-overview, .hm-ws-detail, .agent-status-label'
  ).forEach(el => { el.style.fontSize = size + 'px'; });
}

document.getElementById('modal-layer').addEventListener('wheel', (e) => {
  if (!e.ctrlKey) return;
  const modal = e.target.closest('.modal-window');
  if (!modal) return;
  e.preventDefault();
  const modalId = modal.dataset.modalId;
  if (!modalZoomLevels[modalId]) modalZoomLevels[modalId] = 13;
  const delta = e.deltaY > 0 ? -1 : 1;
  modalZoomLevels[modalId] = Math.max(8, Math.min(24, modalZoomLevels[modalId] + delta));
  applyModalZoom(modal, modalZoomLevels[modalId]);
  if (typeof _setModalZoom === 'function') _setModalZoom(modalId, modalZoomLevels[modalId]);
  _setModalPref(modalId, { zoom: modalZoomLevels[modalId] });
}, { passive: false });

// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.trimTrailingNulls = trimTrailingNulls;
window.persistGridOrder = persistGridOrder;
window._snapEnabled = _snapEnabled;
window._zoneRect = _zoneRect;
window.toggleModalPin = toggleModalPin;
window.toggleTileModalsPopover = toggleTileModalsPopover;
window.applyModalZoom = applyModalZoom;
window.tileAllModals = tileAllModals;
