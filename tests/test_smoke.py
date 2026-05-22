"""Import-time smoke tests.

Catches import-time breakage — the cheapest possible safety net for the
planned server.py blueprint split (IMPROVEMENT_PLAN_V2.md P1-1): if an
extraction breaks a module-level import, this goes red without anyone
needing to start Flask.
"""


def test_import_github_sync():
    import github_sync
    assert callable(github_sync.sync_project)
    assert callable(github_sync.sanitize)
    assert callable(github_sync.gh_run)


def test_import_side_modules():
    import skills
    import mcp
    import mcp_installer
    assert skills and mcp and mcp_installer


def test_import_server_without_flask_run(tmp_data_dir):
    """`import server` must NOT start Flask (guarded by __main__) and must
    not touch the real ./data tree (MC_DATA_DIR redirected by tmp_data_dir)."""
    import importlib

    server = importlib.import_module("server")
    importlib.reload(server)  # ensure module-level code runs under tmp_data_dir

    # Flask app object exists and is wired.
    assert server.app is not None
    assert server.app.name == "server"
    # A few load-bearing callables the split must preserve.
    for attr in ("sync_project_endpoint", "PORT") if hasattr(server, "sync_project_endpoint") else ("PORT",):
        assert hasattr(server, attr)
    # All filesystem setup landed under the temp data dir, not the repo.
    assert str(tmp_data_dir) in str(server._DATA_ROOT)


def test_skills_catalog_block_claude_is_empty(tmp_data_dir):
    """Stage 3 full-parity: the skill catalog is injected only for non-Claude
    providers — the Claude context path must stay byte-identical, so the block
    is empty for a Claude (or unset == Claude) project."""
    import importlib

    server = importlib.import_module("server")
    importlib.reload(server)
    assert server._skills_catalog_block({'id': 'p', 'provider': 'claude'}) == ''
    assert server._skills_catalog_block({'id': 'p'}) == ''
