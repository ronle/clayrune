#!/usr/bin/env python3
"""Refresh data/agent_reference/CLAYRUNE_API.md against the live `@app.route`
decorators in server.py.

This tool does NOT overwrite the curated reference. It does two things:

1. Lists every route registered in server.py.
2. Flags drift: routes that exist in code but not in the curated reference,
   and routes referenced in the doc but no longer present in code.

The agent_reference doc stays human-curated — agents don't need every internal
route (push, CF, walkthrough, /sw.js, etc.). This tool just makes drift
visible so the curated doc can be updated deliberately.

Usage:
  python tools/regen-api-reference.py                 # report only
  python tools/regen-api-reference.py --list-all      # print every route
  python tools/regen-api-reference.py --json          # machine-readable

Exit code 0 = clean, 1 = drift detected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / 'server.py'
DOC = ROOT / 'data' / 'agent_reference' / 'CLAYRUNE_API.md'

ROUTE_RE = re.compile(r"^@app\.route\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?")

# Routes intentionally NOT in the curated reference — UI-only, CF/remote
# internals, push notifications, walkthrough, internal callbacks. Keep this
# list narrow; default is "should be documented".
EXCLUDED_PREFIXES = (
    '/assets/',
    '/sw.js',
    '/manifest.json',
    '/_mc/',
    '/api/_mc/',
    '/api/_mock/',           # mock-connect (remote-access dev only)
    '/api/push/',
    '/api/remote/',
    '/api/tunnel-handshake',
    '/api/mc-callback',
    '/api/walkthrough/',
    '/api/browse/',
    '/api/list-directory',
    '/api/create-folder',
    '/api/grid-layout',
    '/api/projects/order',
    '/api/claude/',          # auth — user-managed via UI
    '/api/settings/domains',  # UI-only
    '/api/guide/',           # UI walkthrough
    '/api/plans/delete',     # UI-only (use a different mechanism)
    '/api/presence',         # passive heartbeat from UI
    '/v1/',                  # remote-access attestation
)

# Exact-match excludes (no prefix logic).
EXCLUDED_EXACT = frozenset({'/'})

# Path-param placeholders we want to normalize before comparing the doc to code.
# server.py uses Flask converters like `<project_id>`, `<int:pid>`, `<path:filename>`.
# The curated doc may use shorter names (`<ws>` vs `<ws_id>`); we compare shapes.
def _norm(path: str) -> str:
    path = re.sub(r'<[^>]*?:(\w+)>', r'<\1>', path)  # <int:pid> -> <pid>
    return path


def _shape(path: str) -> str:
    """Reduce path to its shape: all `<...>` placeholders become `<>`."""
    return re.sub(r'<[^>]*>', '<>', path)


def scan_routes() -> list[tuple[str, list[str]]]:
    """Return [(normalized_path, methods)] in source order."""
    out: list[tuple[str, list[str]]] = []
    text = SERVER_PY.read_text(encoding='utf-8')
    for line in text.splitlines():
        m = ROUTE_RE.match(line.strip())
        if not m:
            continue
        path = _norm(m.group(1))
        methods_raw = m.group(2) or ''
        methods = [t.strip().strip("'\"") for t in methods_raw.split(',') if t.strip()]
        if not methods:
            methods = ['GET']
        out.append((path, methods))
    return out


def _expand_alternations(path: str) -> list[str]:
    """Expand compact `[a|b|c]` and `[a\\|b\\|c]` syntax into separate paths.

    The curated doc groups related endpoints like
    `/api/skills/import/[paste|folder|git]` for human readability; this
    unfolds them so drift comparison works.
    """
    if '[' not in path:
        return [path]
    m = re.search(r'\[([^\[\]]+)\]', path)
    if not m:
        return [path]
    alts_raw = m.group(1)
    # Strip backslash escapes used in markdown tables.
    alts = [a.strip().replace('\\', '') for a in alts_raw.split('|') if a.strip()]
    out: list[str] = []
    for a in alts:
        expanded = path[:m.start()] + a + path[m.end():]
        out.extend(_expand_alternations(expanded))
    return out


def doc_paths() -> set[str]:
    """Approximate set of paths mentioned in the curated reference."""
    if not DOC.exists():
        return set()
    text = DOC.read_text(encoding='utf-8')
    # Match anything that looks like an API path inside backticks.
    raw = re.findall(r'`(/api/[^`]+?)`', text)
    raw += re.findall(r'`(/_mc/[^`]+?)`', text)
    out: set[str] = set()
    for p in raw:
        base = p.split('?', 1)[0]
        for expanded in _expand_alternations(base):
            out.add(_norm(expanded))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--list-all', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    code = scan_routes()
    def _excluded(p: str) -> bool:
        if p in EXCLUDED_EXACT:
            return True
        return any(p.startswith(pref) for pref in EXCLUDED_PREFIXES)

    code_paths_documentable = sorted({p for p, _ in code if not _excluded(p)})
    code_paths_all = sorted({p for p, _ in code})
    documented = doc_paths()

    documented_shapes = {_shape(p) for p in documented}
    code_shapes_all = {_shape(p) for p in code_paths_all}

    missing_in_doc = [p for p in code_paths_documentable if _shape(p) not in documented_shapes]
    stale_in_doc = sorted({p for p in documented if _shape(p) not in code_shapes_all})

    if args.json:
        print(json.dumps({
            'total_routes': len(code),
            'documentable_routes': len(code_paths_documentable),
            'documented': sorted(documented),
            'missing_in_doc': missing_in_doc,
            'stale_in_doc': stale_in_doc,
        }, indent=2))
    elif args.list_all:
        for p, methods in code:
            tag = '  ' if _excluded(p) else '* '
            print(f'{tag}{",".join(methods):20s} {p}')
    else:
        print(f'server.py routes: {len(code)} ({len(code_paths_documentable)} documentable, {len(code) - len(code_paths_documentable)} excluded)')
        print(f'CLAYRUNE_API.md mentions: {len(documented)} paths')
        if missing_in_doc:
            print(f'\nMissing in doc ({len(missing_in_doc)}):')
            for p in missing_in_doc:
                print(f'  + {p}')
        if stale_in_doc:
            print(f'\nIn doc but not in code ({len(stale_in_doc)}):')
            for p in stale_in_doc:
                print(f'  - {p}')
        if not missing_in_doc and not stale_in_doc:
            print('\nClean — reference is in sync with code.')

    return 1 if (missing_in_doc or stale_in_doc) else 0


if __name__ == '__main__':
    sys.exit(main())
