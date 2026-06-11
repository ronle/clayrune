function setTone(tone) {
  currentTone = tone;
  localStorage.setItem('mc_tone', tone);
  document.body.classList.remove('tone-warm', 'tone-editorial');
  if (tone === 'warm') document.body.classList.add('tone-warm');
  else if (tone === 'editorial') document.body.classList.add('tone-editorial');
  // Re-render settings so segmented control highlight updates
  if (document.getElementById('settings-body')) _renderSettings();
}

function setAccent(accent) {
  currentAccent = accent;
  localStorage.setItem('mc_accent', accent);
  if (accent) document.body.setAttribute('data-accent', accent);
  else document.body.removeAttribute('data-accent');
  if (document.getElementById('settings-body')) _renderSettings();
}

function setDensity(mode) {
  const isCompact = (mode === 'compact');
  document.body.classList.toggle('compact', isCompact);
  localStorage.setItem('mc_tile_density', isCompact ? 'compact' : 'default');
  const btn = document.getElementById('density-toggle');
  if (btn) btn.style.color = isCompact ? 'var(--accent)' : '';
  if (document.getElementById('settings-body')) _renderSettings();
}

function setVoice(voice) {
  currentVoice = voice;
  localStorage.setItem('mc_voice', voice);
  const bc = document.getElementById('header-breadcrumb-label');
  if (bc) bc.textContent = vl('header_dash');
  if (typeof renderStats === 'function') renderStats();
  if (typeof renderProjects === 'function') renderProjects();
  if (typeof renderListView === 'function') renderListView();
  if (typeof refreshModal === 'function') refreshModal();
  if (document.getElementById('settings-body')) _renderSettings();
}

// ── Background framing: interactive crop-box editor ─────────────────────────
// Replaces the zoom/across/up-down sliders with a direct-manipulation box drawn
// over the image. The box marks the region kept in view; drag it to pan, drag the
// corner (or wheel) to zoom. It writes the SAME bgZoom/bgPosX/bgPosY values, and
// applyDashboardBackground() cover-fits that framing to whatever screen the
// dashboard is on — so one setting works across all screen sizes/devices. The box
// aspect = the live viewport, so what you frame is what you'll see on a screen
// shaped like this one. Zoom is held to [100,400] here (100 = fills the screen;
// you can only crop IN), so the editor never produces letterboxing.
let _bgCrop = null;   // {Dw,Dh,imgW,imgH} of the editor, set on image load (never read at init)

function _bgCropMetrics() {
  const c = _bgCrop; if (!c || !c.imgW || !c.imgH) return null;
  const vw = window.innerWidth, vh = window.innerHeight;
  const cover = Math.max(vw / c.imgW, vh / c.imgH);
  const scale = cover * (_bgClampZoom(bgZoom) / 100);
  const wFrac = Math.min(1, vw / (c.imgW * scale));
  const hFrac = Math.min(1, vh / (c.imgH * scale));
  return { vw, vh, cover, wFrac, hFrac };
}

function _bgCropRender() {
  const c = _bgCrop, box = document.getElementById('mc-crop-box');
  if (!c || !box) return;
  const m = _bgCropMetrics(); if (!m) return;
  const px = _bgClampPct(bgPosX), py = _bgClampPct(bgPosY);
  box.style.width  = (m.wFrac * c.Dw) + 'px';
  box.style.height = (m.hFrac * c.Dh) + 'px';
  box.style.left   = ((1 - m.wFrac) * px / 100 * c.Dw) + 'px';
  box.style.top    = ((1 - m.hFrac) * py / 100 * c.Dh) + 'px';
}

function _bgCropInit() {
  const img = document.getElementById('mc-crop-img');
  if (!img || !img.naturalWidth) return;
  if (!img.clientWidth) { requestAnimationFrame(_bgCropInit); return; } // not laid out yet
  _bgCrop = { Dw: img.clientWidth, Dh: img.clientHeight,
              imgW: img.naturalWidth, imgH: img.naturalHeight };
  // Persist natural dims so applyDashboardBackground() sizes correctly (also
  // back-fills legacy images saved before dims were stored).
  localStorage.setItem('mc_bg_imgw', String(img.naturalWidth));
  localStorage.setItem('mc_bg_imgh', String(img.naturalHeight));
  _bgCropRender();
}

function _bgSaveFraming() {
  localStorage.setItem('mc_bg_zoom', String(_bgClampZoom(bgZoom)));
  localStorage.setItem('mc_bg_posx', String(_bgClampPct(bgPosX)));
  localStorage.setItem('mc_bg_posy', String(_bgClampPct(bgPosY)));
}

// Re-center the focal point so the visible region keeps the same center when zoom
// changes (zoom around the middle of the box, not the image origin).
function _bgCropZoomTo(newZoom, startMetrics, startPosX, startPosY) {
  const cx = (1 - startMetrics.wFrac) * startPosX / 100 + startMetrics.wFrac / 2;
  const cy = (1 - startMetrics.hFrac) * startPosY / 100 + startMetrics.hFrac / 2;
  bgZoom = Math.max(100, Math.min(400, Math.round(newZoom)));
  const nm = _bgCropMetrics();
  bgPosX = nm.wFrac < 1 ? _bgClampPct((cx - nm.wFrac / 2) / (1 - nm.wFrac) * 100) : 50;
  bgPosY = nm.hFrac < 1 ? _bgClampPct((cy - nm.hFrac / 2) / (1 - nm.hFrac) * 100) : 50;
}

function _bgCropDragStart(e, mode) {
  e.preventDefault(); e.stopPropagation();
  const c = _bgCrop, m = _bgCropMetrics();
  if (!c || !m) return;
  const startX = e.clientX, startY = e.clientY;
  const startPosX = _bgClampPct(bgPosX), startPosY = _bgClampPct(bgPosY);
  const startBoxW = m.wFrac * c.Dw;
  const maxBoxW = Math.min(1, m.vw / (c.imgW * m.cover)) * c.Dw; // box width at zoom 100
  const onMove = (ev) => {
    const dx = ev.clientX - startX, dy = ev.clientY - startY;
    if (mode === 'move') {
      const mm = _bgCropMetrics();
      if (mm.wFrac < 1) bgPosX = _bgClampPct(startPosX + (dx / ((1 - mm.wFrac) * c.Dw)) * 100);
      if (mm.hFrac < 1) bgPosY = _bgClampPct(startPosY + (dy / ((1 - mm.hFrac) * c.Dh)) * 100);
    } else { // resize the box → zoom (box width = maxBoxW * 100 / zoom)
      const newBoxW = Math.max(8, startBoxW + dx);
      _bgCropZoomTo(maxBoxW * 100 / newBoxW, m, startPosX, startPosY);
    }
    _bgCropRender(); _bgSaveFraming(); applyDashboardBackground();
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function _bgCropWheel(e) {
  if (!_bgCrop) return;
  e.preventDefault();
  const m = _bgCropMetrics(); if (!m) return;
  _bgCropZoomTo(_bgClampZoom(bgZoom) + (e.deltaY < 0 ? 8 : -8), m,
                _bgClampPct(bgPosX), _bgClampPct(bgPosY));
  _bgCropRender(); _bgSaveFraming(); applyDashboardBackground();
}

function setBgMode(mode) {
  bgMode = mode;
  localStorage.setItem('mc_bg_mode', mode);
  applyDashboardBackground();
  if (document.getElementById('settings-body')) _renderSettings();
}

function setBgColor(color) {
  bgColor = color;
  localStorage.setItem('mc_bg_color', color);
  applyDashboardBackground();
  // live <input type=color> — don't re-render (would drop the open picker)
}

function setBgDim(v) {
  bgDim = parseInt(v, 10) || 0;
  localStorage.setItem('mc_bg_dim', String(bgDim));
  applyDashboardBackground();
  const lbl = document.getElementById('mc-bg-dim-val');
  if (lbl) lbl.textContent = bgDim + '%';
}

function resetBgFraming() {
  bgZoom = 100; bgPosX = 50; bgPosY = 50;
  localStorage.setItem('mc_bg_zoom', '100');
  localStorage.setItem('mc_bg_posx', '50');
  localStorage.setItem('mc_bg_posy', '50');
  applyDashboardBackground();
  if (document.getElementById('settings-body')) _renderSettings();
}

function pickBgImage() {
  const inp = document.getElementById('mc-bg-file');
  if (inp) inp.click();
}

function onBgImageChosen(input) {
  const file = input.files && input.files[0];
  input.value = ''; // allow re-choosing the same file later
  if (!file) return;
  if (!/^image\//.test(file.type)) { alert('Please choose an image file.'); return; }
  const reader = new FileReader();
  reader.onload = (e) => {
    const tmp = new Image();
    tmp.onload = () => {
      // Downscale to <=2560px on the long edge + re-encode JPEG so the data URL
      // fits comfortably in localStorage (~5MB origin quota, shared with MC keys).
      const MAX = 2560;
      let w = tmp.width, h = tmp.height;
      if (Math.max(w, h) > MAX) {
        const s = MAX / Math.max(w, h);
        w = Math.round(w * s); h = Math.round(h * s);
      }
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d').drawImage(tmp, 0, 0, w, h);
      let dataUrl;
      try { dataUrl = c.toDataURL('image/jpeg', 0.82); }
      catch (_) { dataUrl = e.target.result; }
      try {
        localStorage.setItem('mc_bg_image', dataUrl);
      } catch (err) {
        alert('That image is too large to save on this device. Try a smaller one.');
        return;
      }
      // Remember the stored image's natural size (w,h are the re-encoded canvas
      // dims) so zoom/framing can size it against the viewport, and reset framing
      // to fill/center for the fresh image.
      localStorage.setItem('mc_bg_imgw', String(w));
      localStorage.setItem('mc_bg_imgh', String(h));
      bgZoom = 100; bgPosX = 50; bgPosY = 50;
      localStorage.setItem('mc_bg_zoom', '100');
      localStorage.setItem('mc_bg_posx', '50');
      localStorage.setItem('mc_bg_posy', '50');
      bgMode = 'image'; localStorage.setItem('mc_bg_mode', 'image');
      applyDashboardBackground();
      if (document.getElementById('settings-body')) _renderSettings();
    };
    tmp.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

function clearBgImage() {
  localStorage.removeItem('mc_bg_image');
  localStorage.removeItem('mc_bg_imgw');
  localStorage.removeItem('mc_bg_imgh');
  if (bgMode === 'image') { bgMode = 'default'; localStorage.setItem('mc_bg_mode', 'default'); }
  applyDashboardBackground();
  if (document.getElementById('settings-body')) _renderSettings();
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.setTone = setTone;
window.setAccent = setAccent;
window.setDensity = setDensity;
window.setVoice = setVoice;
window._bgCropInit = _bgCropInit;
window._bgCropDragStart = _bgCropDragStart;
window._bgCropWheel = _bgCropWheel;
window.setBgMode = setBgMode;
window.setBgColor = setBgColor;
window.setBgDim = setBgDim;
window.resetBgFraming = resetBgFraming;
window.pickBgImage = pickBgImage;
window.onBgImageChosen = onBgImageChosen;
window.clearBgImage = clearBgImage;
