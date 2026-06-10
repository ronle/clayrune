"""mc/memory.py — the mop-up engine extraction (no behavior change).

Guards the PURE MOVE: an import smoke, the MEMORY.md Leg-0 format round-trip
(_mem_split / _mem_compose / _mem_migrate idempotence + sentinel/watermark
preservation), and a real _commit_managed_entry write under an isolated tmp
project proving the managed-region sentinels AND the Step-6 wm marker survive
the leaf-locked atomic write (the load-bearing discipline from CLAUDE.md).

The engine is wired by server.py; _server() reloads it so memory.wire() binds
against tmp_data_dir, then we drive mc.memory directly.
"""
import importlib
import importlib.util


def _mem(tmp_data_dir):
    """Reload server (runs memory.wire() against the isolated tmp data dir),
    return the mc.memory engine module."""
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


# ── import smoke ─────────────────────────────────────────────────────────────

def test_import_smoke():
    import mc.memory as m
    # the engine surface is present
    for name in ("_mem_split", "_mem_split_full", "_mem_compose", "_mem_migrate",
                 "_commit_managed_entry", "_write_session_memory", "_scribe_call",
                 "_dispatch_condense", "_should_condense", "_memory_search",
                 "_maybe_checkpoint", "_condense_apply", "_get_memory_path",
                 "wire"):
        assert hasattr(m, name), name
    # NO import cycle: mc.memory must never pull in server or a blueprint.
    src = importlib.util.find_spec("mc.memory").origin
    text = open(src, encoding="utf-8").read()
    assert "import server" not in text
    assert "from server" not in text
    assert "mc.blueprints" not in text


# ── Leg-0 MEMORY.md format round-trip ────────────────────────────────────────

def test_mem_split_compose_roundtrip(tmp_data_dir):
    m = _mem(tmp_data_dir)
    curated = "# Index\n\n## Topic\n- [a](a.md) — hook"
    entries = ["- [2026-06-10] **task one** — did a thing",
               "- [2026-06-10] **task two** — did another"]
    composed = m._mem_compose(curated, entries)
    cur, ents = m._mem_split(composed)
    assert cur == curated
    assert ents == entries
    # canonical form: exactly one sentinel-delimited managed region
    assert composed.count(m._MEM_BEGIN) == 1
    assert composed.count(m._MEM_END) == 1
    assert m._MEM_LOG_HEADER in composed


def test_mem_migrate_idempotent(tmp_data_dir):
    m = _mem(tmp_data_dir)
    curated = "# Curated\n\n## A\n- [x](x.md)"
    entries = ["- [2026-06-10] **e** — z"]
    wm = ['<!-- clayrune:wm:sidA {"session_id":"sidA",'
          '"running_summary":"live work"} -->']
    composed = m._mem_compose(curated, entries, wm)
    once = m._mem_migrate(composed)
    twice = m._mem_migrate(once)
    # already-canonical content round-trips byte-identically
    assert once == twice
    # wm marker survives split_full
    c, e, gotwm = m._mem_split_full(once)
    assert c == curated
    assert e == entries
    assert gotwm == wm


def test_mem_migrate_wraps_legacy_bare_header(tmp_data_dir):
    m = _mem(tmp_data_dir)
    # Legacy file: bare '## Session Log' with no sentinels.
    legacy = ("# Curated index\n\n## Session Log\n"
              "- [2026-06-01] **old** — legacy entry")
    migrated = m._mem_migrate(legacy)
    assert m._MEM_BEGIN in migrated and m._MEM_END in migrated
    cur, ents = m._mem_split(migrated)
    assert cur == "# Curated index"
    assert ents == ["- [2026-06-01] **old** — legacy entry"]
    # idempotent thereafter
    assert m._mem_migrate(migrated) == migrated


# ── _commit_managed_entry: leaf-locked atomic write preserves sentinels + wm ──

def test_commit_managed_entry_preserves_sentinel_and_watermark(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "memproj"}  # no project_path → MEMORY_DIR/<id>.md (tmp-isolated)
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    # Seed a curated index + one live-session watermark marker.
    wm = ['<!-- clayrune:wm:sidLIVE {"session_id":"sidLIVE",'
          '"running_summary":"in flight"} -->']
    mp.write_text(m._mem_compose("# Idx\n\n## Notes\n- [k](k.md)",
                                 ["- [2026-06-10] **seed** — pre-existing"], wm),
                  encoding="utf-8")

    # Append a new managed entry; the watermark for the OTHER live session must
    # be carried through untouched (we don't remove sidLIVE here).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **fresh** — appended this turn")

    out = mp.read_text(encoding="utf-8")
    # sentinels intact
    assert out.count(m._MEM_BEGIN) == 1 and out.count(m._MEM_END) == 1
    # curated region byte-preserved
    cur, ents, gotwm = m._mem_split_full(out)
    assert cur == "# Idx\n\n## Notes\n- [k](k.md)"
    # both the seed and the fresh entry are present, in order
    assert ents == ["- [2026-06-10] **seed** — pre-existing",
                    "- [2026-06-10] **fresh** — appended this turn"]
    # the live watermark survived the atomic write (load-bearing)
    assert gotwm == wm
    rec = m._wm_find(gotwm, "sidLIVE")
    assert rec and rec.get("running_summary") == "in flight"


def test_commit_managed_entry_wm_remove_on_teardown(tmp_data_dir):
    m = _mem(tmp_data_dir)
    p = {"id": "memproj2"}
    mp = m._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    wm = ['<!-- clayrune:wm:sidGONE {"session_id":"sidGONE",'
          '"running_summary":"x"} -->']
    mp.write_text(m._mem_compose("# Idx", [], wm), encoding="utf-8")
    # Terminal write removes this session's wm marker (clean teardown).
    m._commit_managed_entry(
        p, mem_entry="- [2026-06-10] **done** — finished",
        wm_remove_sid="sidGONE")
    _c, ents, gotwm = m._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == ["- [2026-06-10] **done** — finished"]
    assert gotwm == []  # marker dropped on teardown
