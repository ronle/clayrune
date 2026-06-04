"""Phase 0 foundation tests for db.py — docs/SQLITE_MIGRATION_SPEC.md.

Covers the migration runner (applies, idempotent, versioned), the schema it
creates, the tx() commit/rollback contract, the query helpers, and clean
re-init across DB paths. No server.py import — db.py is standalone in Phase 0.
"""
import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import db as dbmod  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Point db.py at a throwaway file and run migrations; reset after."""
    dbmod._reset_for_tests()
    p = dbmod.init(tmp_path / "data" / "mc.db")
    yield p
    dbmod._reset_for_tests()


def test_init_creates_file_and_migrates(fresh_db):
    assert fresh_db.exists()
    assert dbmod.schema_version() == len(dbmod._MIGRATIONS)


def test_migrations_idempotent(fresh_db):
    v1 = dbmod.schema_version()
    # Re-running init must not error or change the version.
    dbmod.init(fresh_db)
    assert dbmod.schema_version() == v1


def test_baseline_meta_table(fresh_db):
    dbmod.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("k", "v"))
    row = dbmod.query_one("SELECT value FROM _meta WHERE key=?", ("k",))
    assert row["value"] == "v"


def test_agent_log_schema(fresh_db):
    cols = {r["name"] for r in dbmod.query("PRAGMA table_info(agent_log)")}
    for expected in ("id", "project_id", "ts", "claude_session_id", "status",
                     "cost_usd", "usage_json", "scribed", "data_json"):
        assert expected in cols, f"agent_log missing column {expected}"
    idx = {r["name"] for r in dbmod.query("PRAGMA index_list(agent_log)")}
    assert "ix_agent_log_pid_ts" in idx
    assert "ix_agent_log_ts" in idx


def test_tx_commits_on_success(fresh_db):
    with dbmod.tx() as conn:
        conn.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("a", "1"))
        conn.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("b", "2"))
    assert dbmod.query_one("SELECT COUNT(*) c FROM _meta")["c"] == 2


def test_tx_rolls_back_on_error(fresh_db):
    dbmod.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("keep", "1"))
    with pytest.raises(ValueError):
        with dbmod.tx() as conn:
            conn.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("gone", "2"))
            raise ValueError("boom")
    keys = {r["key"] for r in dbmod.query("SELECT key FROM _meta")}
    assert keys == {"keep"}  # the aborted insert was rolled back


def test_reinit_switches_db_cleanly(tmp_path):
    dbmod._reset_for_tests()
    dbmod.init(tmp_path / "a" / "mc.db")
    dbmod.execute("INSERT INTO _meta(key, value) VALUES (?, ?)", ("x", "A"))
    # Point at a second, independent DB.
    dbmod.init(tmp_path / "b" / "mc.db")
    assert dbmod.query_one("SELECT COUNT(*) c FROM _meta")["c"] == 0
    dbmod._reset_for_tests()


def test_wal_mode_enabled(fresh_db):
    mode = dbmod.query_one("PRAGMA journal_mode")[0]
    assert str(mode).lower() == "wal"
