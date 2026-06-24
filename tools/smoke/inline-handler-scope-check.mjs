#!/usr/bin/env node
/**
 * Inline-handler scope check for the ES-module SPA.
 *
 * WHY THIS EXISTS
 * ---------------
 * static/js/*.js load as <script type="module">. A top-level `let`/`const`/
 * `function` in a module is MODULE-SCOPED, not global. But a generated inline
 * handler — `onclick="_distillerExplorationsOpen=!_distillerExplorationsOpen"` —
 * resolves its identifiers against the GLOBAL object at click time. So an inline
 * handler that names a module binding which was never bridged onto `window`
 * fails SILENTLY: the click writes/reads a dead global, the render keeps reading
 * the real module binding, and the control just does nothing. No exception, no
 * log — the worst kind of bug.
 *
 * This has bitten twice: the per-chat persona dispatch (2026-06-12) and the
 * Learning-queue "expand to prune" drawer toggle (2026-06-24). Both were a
 * module identifier referenced from code that runs in global scope, missing its
 * `window.` bridge.
 *
 * WHAT IT CHECKS
 * --------------
 * For every inline `on*="..."` handler in static/js/*.js, it extracts the
 * identifiers the handler references (ignoring `${...}` interpolations, which
 * are evaluated at template-build time in module scope and are fine), and flags
 * any identifier that IS a top-level declaration in some module but is NOT
 * exposed on `window` (via `window.X =`, `window['X'] =`, or
 * `Object.defineProperty(window, 'X', …)`). That is exactly the silent-failure
 * pattern; a correctly-bridged toggle or a window-exposed function is clean.
 *
 * RUN
 *   cd tools/smoke && node inline-handler-scope-check.mjs
 * Exit 0 = clean; 1 = at least one unbridged inline-handler reference.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const JS_DIR = resolve(__dirname, '..', '..', 'static', 'js');

// Identifiers that are legitimately global at click time — never flag these.
const BUILTINS = new Set([
  'window', 'document', 'event', 'this', 'console', 'Math', 'JSON', 'Object',
  'Array', 'String', 'Number', 'Boolean', 'Date', 'RegExp', 'Map', 'Set',
  'Promise', 'parseInt', 'parseFloat', 'isNaN', 'encodeURIComponent',
  'decodeURIComponent', 'setTimeout', 'setInterval', 'clearTimeout', 'localStorage',
  'sessionStorage', 'navigator', 'location', 'history', 'alert', 'confirm',
  'prompt', 'fetch', 'true', 'false', 'null', 'undefined', 'NaN', 'Infinity',
  'typeof', 'new', 'delete', 'void', 'in', 'of', 'return', 'if', 'else', 'function',
  'let', 'const', 'var', 'await', 'async', 'try', 'catch', 'throw',
]);

const files = readdirSync(JS_DIR).filter((f) => f.endsWith('.js'));

// Pass 1 — collect, across ALL modules: top-level declarations + window exposures.
const topLevelDecls = new Map();   // name -> declaring file (first seen)
const windowExposed = new Set();
const sources = {};
for (const f of files) {
  const src = readFileSync(join(JS_DIR, f), 'utf8');
  sources[f] = src;
  // Top-level decls: keyword at column 0 (indented = nested = not module scope).
  const declRe = /^(?:export\s+)?(?:async\s+)?(?:function\*?|let|const|var)\s+([A-Za-z_$][\w$]*)/gm;
  let m;
  while ((m = declRe.exec(src))) if (!topLevelDecls.has(m[1])) topLevelDecls.set(m[1], f);
  // window.X = … / window['X'] = …
  const winAssignRe = /\bwindow\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*['"]([A-Za-z_$][\w$]*)['"]\s*\])\s*=/g;
  while ((m = winAssignRe.exec(src))) windowExposed.add(m[1] || m[2]);
  // Object.defineProperty(window, 'X', …)
  const dpRe = /Object\.defineProperty\s*\(\s*window\s*,\s*['"]([A-Za-z_$][\w$]*)['"]/g;
  while ((m = dpRe.exec(src))) windowExposed.add(m[1]);
}

// Pass 2 — every inline on*="…" handler, flag unbridged module identifiers.
const findings = [];
for (const f of files) {
  const src = sources[f];
  const lines = src.split('\n');
  const handlerRe = /\bon[a-z]+\s*=\s*(["'])([\s\S]*?)\1/gi;
  let m;
  while ((m = handlerRe.exec(src))) {
    const body = m[2].replace(/\$\{[\s\S]*?\}/g, ' ');   // drop template interpolations
    const line = src.slice(0, m.index).split('\n').length;
    const idRe = /[A-Za-z_$][\w$]*/g;
    let idm;
    const seen = new Set();
    while ((idm = idRe.exec(body))) {
      const id = idm[0];
      if (seen.has(id) || BUILTINS.has(id)) continue;
      seen.add(id);
      if (topLevelDecls.has(id) && !windowExposed.has(id)) {
        findings.push({ file: f, line, id, decl: topLevelDecls.get(id),
          snippet: lines[line - 1].trim().slice(0, 110) });
      }
    }
  }
}

if (findings.length === 0) {
  console.log(`✅ inline-handler scope check: ${files.length} modules clean — ` +
    `no inline handler references an unbridged module binding.`);
  process.exit(0);
}
console.error(`❌ inline-handler scope check: ${findings.length} unbridged reference(s):\n`);
for (const x of findings) {
  console.error(`  ${x.file}:${x.line}  →  '${x.id}' (module-scoped in ${x.decl}, not on window)`);
  console.error(`     ${x.snippet}`);
}
console.error(`\n  Fix: expose '${findings[0].id}' on window — either ` +
  `\`window.${findings[0].id} = ${findings[0].id};\` (functions) or an ` +
  `Object.defineProperty(window, '…', {get,set}) accessor (mutable state toggles).`);
process.exit(1);
