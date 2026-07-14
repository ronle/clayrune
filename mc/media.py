"""Media index — diagrams and images the agent produced, per project.

WHY AN INDEX AND NOT A SCAN
---------------------------
The obvious implementation is "grep the transcripts when the panel opens."
Measured on this machine (2026-07-14): ONE project's CC transcripts are 155 MB
across 98 files, with a single 47 MB file. A regex sweep of just the 40 most
recent blew past 100 seconds. That is not a request-time operation. So media is
recorded as it streams past, into a small append-only sidecar.

WHY assistant TEXT AND NOT THE RAW TRANSCRIPT
---------------------------------------------
Same measurement found 96 "image paths" in the raw JSONL — most of them junk
like `/static/icon-badge-72.png`, strings sitting inside source code and tool
output that were never images anyone saw. Half didn't exist on disk. So the
recorder is fed ONLY the assistant's visible text (post-fence-strip, exactly
what the chat renders), and the path regex is the SAME one the renderer uses
(static/js/rich-text.js). If the chat would draw it, we index it; otherwise we
don't. That is the whole precision story.

FORWARD-ONLY (Ron's call, 2026-07-14): there is no backfill of history. The
index starts empty and fills as sessions run.

LOAD-BEARING — NOT IN DATA_DIR. `data/projects/` is the project-records dir and
`load_projects()` treats every *.json in it as a project; a stray file there
becomes a malformed "project" and 500s the restart endpoints. This writes to
`data/media/` instead. Do not move it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

MEDIA_DIR: Path = None  # type: ignore[assignment]

# One writer lock per project — the two stream readers (Mode A / Mode B) can be
# appending for different sessions of the same project concurrently.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# Entries per project file. Old media falls off the end rather than growing
# without bound; the gallery is a recent-work surface, not an archive.
MAX_ENTRIES = 500

# Mirrors the image regex in static/js/rich-text.js — an absolute POSIX or
# Windows path ending in an image extension. Keep the two in sync: the contract
# is "if the chat renders it as an image, it belongs in the gallery."
# The lookbehind also excludes a leading `.` — the renderer's class lets
# `./assets/x.png` and `../up/y.png` match as the phantom ABSOLUTE paths
# `/assets/x.png` and `/up/y.png`. The chat gets away with that (a broken <img>
# just hides itself via onerror); a gallery cannot — it would show dead tiles.
_IMG_RE = re.compile(
    r'(?<![\w:/%.])((?:[A-Za-z]:(?!//)[\\/]|/)[^\s"\'`<>|]+?'
    r'\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif))(?![A-Za-z0-9])',
    re.IGNORECASE,
)
# A ```mermaid fenced block. DOTALL so the diagram body spans lines.
_MERMAID_RE = re.compile(r'```mermaid[ \t]*\r?\n(.*?)```', re.DOTALL | re.IGNORECASE)


def wire(data_root: Path) -> None:
    global MEDIA_DIR
    MEDIA_DIR = Path(data_root) / 'data' / 'media'
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[media] could not create {MEDIA_DIR}: {e}", flush=True)


def _lock_for(project_id: str) -> threading.Lock:
    with _locks_guard:
        if project_id not in _locks:
            _locks[project_id] = threading.Lock()
        return _locks[project_id]


def _index_path(project_id: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', project_id or 'unknown')
    return MEDIA_DIR / f'{safe}.jsonl'


def extract(text: str) -> list[dict]:
    """Pull renderable media out of one chunk of assistant text.

    Pure + side-effect free so it can be unit-tested without touching disk.
    """
    if not text:
        return []
    out: list[dict] = []
    for src in _MERMAID_RE.findall(text):
        body = src.strip()
        if body:
            out.append({'kind': 'diagram', 'source': body})
    # Don't index an image path that only appears INSIDE a mermaid body (it's
    # diagram source, not a rendered image).
    without_diagrams = _MERMAID_RE.sub('', text)
    for path in _IMG_RE.findall(without_diagrams):
        out.append({'kind': 'image', 'path': path})
    return out


def record_from_text(project_id: str, session_id: str, text: str,
                     task: str = '') -> int:
    """Index any media in this assistant message. Best-effort; never raises.

    Returns the number of entries written (0 is the overwhelmingly common case
    — most assistant messages contain no media at all, so the fast path is one
    regex miss).
    """
    if MEDIA_DIR is None or not text:
        return 0
    try:
        found = extract(text)
        if not found:
            return 0
        now = time.time()
        rows = []
        for item in found:
            # An image is indexed ONLY if it is really on disk. This is the
            # decisive precision filter: the raw-transcript scan's junk
            # (`/static/icon-badge-72.png`, `/../docs/.../icon-512.png` — code
            # strings, not pictures) does not exist as a file, so it never
            # reaches the gallery. Diagrams have no file, so they skip the check.
            if item.get('kind') == 'image':
                try:
                    if not Path(item['path']).is_file():
                        continue
                except OSError:
                    continue        # unreadable / malformed path → not media
            row = dict(item)
            row['session_id'] = session_id or ''
            row['ts'] = now
            if task:
                row['task'] = task[:120]
            rows.append(row)
        path = _index_path(project_id)
        with _lock_for(project_id):
            with open(path, 'a', encoding='utf-8') as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return len(rows)
    except Exception as e:
        # Media indexing is cosmetic — it must never break a live agent turn.
        print(f"[media] record failed for {project_id}: {e}", flush=True)
        return 0


def _key(row: dict) -> str:
    if row.get('kind') == 'image':
        return 'image:' + str(row.get('path', ''))
    return 'diagram:' + str(row.get('source', ''))


def list_media(project_id: str) -> list[dict]:
    """Newest first, de-duplicated.

    Dedup happens on READ, not on write: the same diagram re-rendered across a
    retry, or an image re-mentioned later in the conversation, would otherwise
    stack up identical tiles. Keeping the write path append-only keeps it cheap
    and lock-light on the hot streaming path.
    """
    if MEDIA_DIR is None:
        return []
    path = _index_path(project_id)
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue    # a torn line from a crash mid-write: skip it
    except OSError as e:
        print(f"[media] read failed for {project_id}: {e}", flush=True)
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for row in reversed(rows):          # newest first
        k = _key(row)
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
        if len(out) >= MAX_ENTRIES:
            break
    return out


def clear(project_id: str) -> bool:
    if MEDIA_DIR is None:
        return False
    try:
        p = _index_path(project_id)
        if p.exists():
            with _lock_for(project_id):
                p.unlink()
        return True
    except OSError as e:
        print(f"[media] clear failed for {project_id}: {e}", flush=True)
        return False
