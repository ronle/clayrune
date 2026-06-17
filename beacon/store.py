"""Beacon heartbeat persistence — JSON-on-disk, last-write-wins.

Heartbeats live at `data/beacon/<id>.json` — OUTSIDE DATA_DIR (data/projects/)
by deliberate choice: DATA_DIR's load_projects() treats every stray *.json as a
project (the load-bearing DATA_DIR-pollution rule), so per-project sidecar state
belongs outside it. See CLAUDE.md "LOAD-BEARING RULE — DATA_DIR pollution".
"""
import json
import os
from pathlib import Path

from ._config import CFG, _log


def beacon_dir() -> Path:
    root = CFG.data_root or Path('data')
    d = root / 'beacon'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _log(f"[beacon] could not create {d}: {e}")
    return d


def heartbeat_path(project_id: str) -> Path:
    return beacon_dir() / f'{project_id}.json'


def read_heartbeat(project_id: str):
    p = heartbeat_path(project_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f"[beacon] read_heartbeat {project_id} failed: {e}")
        return None


def read_all_heartbeats() -> dict:
    """{project_id: heartbeat} for every heartbeat on disk. Best-effort: a
    single malformed file is skipped, never fails the whole digest."""
    out = {}
    d = beacon_dir()
    try:
        files = list(d.glob('*.json'))
    except Exception as e:
        _log(f"[beacon] list heartbeats failed: {e}")
        return out
    for f in files:
        try:
            obj = json.loads(f.read_text(encoding='utf-8'))
            if isinstance(obj, dict):
                out[f.stem] = obj
        except Exception as e:
            _log(f"[beacon] skip malformed heartbeat {f.name}: {e}")
            continue
    return out


def write_heartbeat(project_id: str, hb: dict) -> None:
    """Atomic last-write-wins write (tmp + os.replace), mirroring the
    _atomic_write_text discipline used by the memory writers."""
    p = heartbeat_path(project_id)
    tmp = p.parent / (p.name + '.tmp')
    tmp.write_text(json.dumps(hb, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)
