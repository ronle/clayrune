# Clayrune main-app tests

A **safety net for refactoring, not full coverage** (per
`IMPROVEMENT_PLAN_V2.md` P1-5).

## Run

```bash
pip install -r requirements-dev.txt
pytest                      # this suite (./tests)
pytest control_plane/tests  # separate, pre-existing control-plane suite
```

`pytest` with no args uses `pytest.ini` → `testpaths = tests`.

## What's here

| File | Purpose |
|------|---------|
| `conftest.py` | `tmp_data_dir` (isolates `MC_DATA_DIR`), `fake_gh` (recording `gh` CLI stub), `gs` (github_sync wired to an in-memory project store) |
| `test_smoke.py` | `import server` / `github_sync` / `skills` / `mcp` without starting Flask — catches import-time breakage from the planned server.py split |
| `test_github_sync_harness.py` | sanity checks that the gh harness dispatches & records correctly |
| `test_github_sync_p0.py` | one regression test per P0-1…P0-7 fix (added in Sprint 2; each fails on unfixed code, passes after) |

## Out of scope (deliberately)

Selenium/Playwright frontend tests, Tauri/pywebview integration, real
network calls to GitHub or Cloudflare. The goal is to make the
github_sync correctness fixes and the server.py split *safe*, nothing more.
