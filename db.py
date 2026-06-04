"""db.py — embedded SQLite foundation for Mission Control state.

Phase 0 of ``docs/SQLITE_MIGRATION_SPEC.md``. This module is the DATA-ACCESS
FOUNDATION only: connection management, WAL + pragmas, schema versioning with a
migration runner, and transaction/query helpers. It wires into NO existing
read/write path — domain DALs (agent_log, projects, …) land in later phases
behind per-domain feature flags. Importing this module has no effect on current
behavior; nothing in server.py imports it until Phase 1.

Concurrency model: MC is single-instance (one process, threaded Flask — see the
single-instance invariant). WAL gives concurrent readers + a single writer.
Connections are thread-local; any read-modify-write goes through ``tx()``
(``BEGIN IMMEDIATE``) so multi-step updates are atomic. That transaction is what
replaces the hand-rolled per-project ``threading.Lock`` once a domain migrates.

The DB file lives at ``<data_root>/data/mc.db`` — in ``data/``, never
``data/projects/`` — so ``load_projects()`` never globs it as a project.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()
_db_path = None
_init_lock = threading.Lock()


def _resolve_default_path():
    """Derive the default DB path WITHOUT importing server.py (circular-import
    safe). Mirrors server._resolve_dirs(): MC_DATA_DIR wins (tests + frozen),
    else the current working directory. Callers in server.py pass the resolved
    path explicitly via init(); this is only the standalone/test fallback.
    """
    root = os.environ.get('MC_DATA_DIR')
    base = Path(root) if root else Path.cwd()
    return base / 'data' / 'mc.db'


# ── Connection management ────────────────────────────────────────────────────

def _new_conn(path):
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL: concurrent readers + one writer, the single-instance shape.
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA synchronous=NORMAL')  # durable under WAL, faster than FULL
    return conn


def _get_conn():
    """Return this thread's connection, bound to the current _db_path. Lazily
    initializes with the default path if init() was never called."""
    if _db_path is None:
        init()
    conn = getattr(_local, 'conn', None)
    if conn is not None and getattr(_local, 'path', None) == str(_db_path):
        return conn
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    conn = _new_conn(_db_path)
    _local.conn = conn
    _local.path = str(_db_path)
    return conn


def init(db_path=None):
    """Set the DB path, open it, and run migrations. Idempotent. server.py calls
    this once at startup with the resolved path; tests call it with a tmp path.
    Returns the resolved Path.
    """
    global _db_path
    with _init_lock:
        # Close any connection this thread bound to a previous path (test re-init).
        old = getattr(_local, 'conn', None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
            _local.conn = None
            _local.path = None
        _db_path = Path(db_path) if db_path else _resolve_default_path()
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        _migrate(_get_conn())
    return _db_path


# ── Query / transaction helpers ──────────────────────────────────────────────

def query(sql, params=()):
    """Run a read, return all rows (list[sqlite3.Row])."""
    return _get_conn().execute(sql, params).fetchall()


def query_one(sql, params=()):
    """Run a read, return the first row or None."""
    return _get_conn().execute(sql, params).fetchone()


def execute(sql, params=()):
    """Run a single write in autocommit mode. For multi-statement atomic
    updates use tx()."""
    return _get_conn().execute(sql, params)


@contextmanager
def tx():
    """Atomic read-modify-write: BEGIN IMMEDIATE … COMMIT/ROLLBACK. Yields the
    connection. Use for any update that reads then writes (the SQLite-native
    replacement for the per-project threading.Lock)."""
    conn = _get_conn()
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
        conn.execute('COMMIT')
    except Exception:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        raise


# ── Schema versioning + migration runner ─────────────────────────────────────
# Each migration is a callable(conn) run inside its own BEGIN IMMEDIATE; on
# success PRAGMA user_version is bumped to the migration's 1-based index. Append
# only — never reorder or rewrite a shipped migration (it changes the version
# math for already-migrated DBs). DDL in SQLite is transactional, so a failed
# migration rolls back cleanly. We use callables (not executescript) because
# executescript force-commits any pending transaction, defeating atomicity.

def _m001_baseline(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
    )


def _m002_agent_log(conn):
    # Phase 1 pilot schema, created ahead of the DAL (unused until the flag
    # flips). data_json holds the full original entry so the DAL round-trips
    # byte-identical dicts; the scalar columns exist for indexed queries that
    # today scan + filter every *_agent_log.json in Python (/usage, /telemetry).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id        TEXT NOT NULL,
            ts                TEXT,
            started_at        TEXT,
            claude_session_id TEXT,
            session_id        TEXT,
            status            TEXT,
            task              TEXT,
            summary           TEXT,
            provider          TEXT,
            model             TEXT,
            cost_usd          REAL,
            usage_json        TEXT,
            scribed           INTEGER DEFAULT 0,
            hivemind_ws_id    TEXT,
            data_json         TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_log_pid_ts ON agent_log(project_id, ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_log_ts ON agent_log(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_log_csid ON agent_log(claude_session_id)")


_MIGRATIONS = [_m001_baseline, _m002_agent_log]


def _migrate(conn):
    cur_v = conn.execute('PRAGMA user_version').fetchone()[0]
    target = len(_MIGRATIONS)
    for i in range(cur_v, target):
        migration = _MIGRATIONS[i]
        v = i + 1
        conn.execute('BEGIN IMMEDIATE')
        try:
            migration(conn)
            conn.execute(f'PRAGMA user_version={v}')  # v is our own int counter
            conn.execute('COMMIT')
        except Exception:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            raise


# ── Diagnostics / test helpers ───────────────────────────────────────────────

def schema_version():
    """Current applied schema version (== len(_MIGRATIONS) once migrated)."""
    return _get_conn().execute('PRAGMA user_version').fetchone()[0]


def db_path():
    return _db_path


def _reset_for_tests():
    """Close this thread's connection and clear the bound path. Tests call this
    between cases that point at different tmp DBs."""
    global _db_path
    old = getattr(_local, 'conn', None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    _local.conn = None
    _local.path = None
    _db_path = None
