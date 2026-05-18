"""Leg C structured condense (docs/CONDENSE_STRUCTURED_DESIGN.md).

Pure-function coverage of the executor swap: JSON parse, pre-write payload
validation (every reject branch), and the rebased transactional apply —
including the invariants the design promises (wm markers byte-preserved,
archive append-only, decisions for vanished entries silently skipped, no fact
ever erased).
"""
import importlib


def _server(tmp_data_dir):
    s = importlib.import_module("server")
    importlib.reload(s)
    return s


def _proj():
    # No project_path → _get_memory_path falls back to MEMORY_DIR/<id>.md,
    # which lives under the isolated tmp_data_dir.
    return {"id": "tproj"}


def _seed(s, curated, entries, wm=None):
    p = _proj()
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(s._mem_compose(curated, entries, wm or []), encoding="utf-8")
    return p, mp


# ── _condense_parse_json ─────────────────────────────────────────────────────

def test_parse_json_plain_and_fenced(tmp_data_dir):
    s = _server(tmp_data_dir)
    assert s._condense_parse_json('{"a":1}') == {"a": 1}
    assert s._condense_parse_json('```json\n{"a":1}\n```') == {"a": 1}
    assert s._condense_parse_json('sure:\n{"a":1}\nthanks') == {"a": 1}
    assert s._condense_parse_json("not json") is None
    assert s._condense_parse_json("[1,2]") is None  # top-level must be object


# ── _validate_condense_payload ───────────────────────────────────────────────

def test_validate_reject_branches(tmp_data_dir):
    s = _server(tmp_data_dir)
    ids, heads = {"a", "b"}, {"## Topic"}
    V = s._validate_condense_payload
    assert V({"entry_decisions": [], "curated_rewrite": None}, ids, heads)[0]
    assert V("x", ids, heads) == (False, "not_object")
    assert V({"entry_decisions": [], "curated_rewrite": "hi"}, ids, heads) == (
        False, "curated_rewrite_forbidden_v1")
    assert V({"entry_decisions": {}}, ids, heads) == (
        False, "entry_decisions_not_list")
    assert V({"entry_decisions": [{"id": "z", "action": "keep"}],
              "curated_rewrite": None}, ids, heads) == (False, "unknown_id")
    assert V({"entry_decisions": [{"id": "a", "action": "keep"},
                                  {"id": "a", "action": "keep"}],
              "curated_rewrite": None}, ids, heads) == (False, "duplicate_id")
    assert V({"entry_decisions": [{"id": "a", "action": "nuke"}],
              "curated_rewrite": None}, ids, heads) == (False, "bad_action")
    assert V({"entry_decisions": [{"id": "a", "action": "fold",
                                   "fold_into": "## Nope",
                                   "pointer_line": "- x"}],
              "curated_rewrite": None}, ids, heads) == (
        False, "fold_into_not_a_heading")
    assert V({"entry_decisions": [{"id": "a", "action": "fold",
                                   "fold_into": "## Topic",
                                   "pointer_line": "  "}],
              "curated_rewrite": None}, ids, heads) == (
        False, "empty_pointer_line")
    assert V({"entry_decisions": [{"id": "a", "action": "fold",
                                   "fold_into": "## Topic",
                                   "pointer_line": "- a\n- b"}],
              "curated_rewrite": None}, ids, heads) == (
        False, "multiline_pointer_line")
    assert V({"entry_decisions": [{"id": "a", "action": "fold",
                                   "fold_into": "## Topic",
                                   "pointer_line": "- <!-- clayrune:wm:x -->"}],
              "curated_rewrite": None}, ids, heads) == (
        False, "pointer_line_synthesizes_machinery")


# ── _condense_apply ──────────────────────────────────────────────────────────

def test_apply_keep_demote_fold_and_wm_preserved(tmp_data_dir):
    s = _server(tmp_data_dir)
    curated = "# Index\n\n## Topic\n- [old](o.md) — hook"
    e_keep = "- [2026-05-18] **keep me** — recent"
    e_dem = "- [2026-05-01] **junk** — no lasting value"
    e_fold = "- [2026-05-10] **insight** — server.py:4902 matters"
    wm = ['<!-- clayrune:wm:sid9 {"session_id":"sid9","running_summary":"x"} -->']
    p, mp = _seed(s, curated, [e_keep, e_dem, e_fold], wm)

    payload = {"entry_decisions": [
        {"id": s._sha8(e_keep), "action": "keep"},
        {"id": s._sha8(e_dem), "action": "demote"},
        {"id": s._sha8(e_fold), "action": "fold",
         "fold_into": "## Topic",
         "pointer_line": "- [insight](insight.md) — server.py:4902 matters"},
    ], "curated_rewrite": None}

    st = s._condense_apply(p, payload)
    assert (st["kept"], st["demoted"], st["folded"]) == (1, 1, 1)

    final = mp.read_text(encoding="utf-8")
    cur, ents, gotwm = s._mem_split_full(final)
    # keep stays, demote+fold removed from managed
    assert ents == [e_keep]
    # wm marker byte-preserved
    assert gotwm == wm
    # fold pointer inserted under its heading; original curated line intact
    assert "- [insight](insight.md) — server.py:4902 matters" in cur
    assert "- [old](o.md) — hook" in cur
    # archive got BOTH the demoted and folded raw lines, nothing erased
    arch = s._get_archive_path(p).read_text(encoding="utf-8")
    assert e_dem in arch and e_fold in arch
    assert "## Archived Session Log" in arch


def test_apply_rebase_skips_vanished_and_keeps_unmentioned(tmp_data_dir):
    s = _server(tmp_data_dir)
    e1 = "- [2026-05-18] **present** — here"
    e2 = "- [2026-05-18] **also present, no decision** — here"
    p, mp = _seed(s, "# Index", [e1, e2])
    # Decision references an id that is NOT in the live file (Step-6 / teardown
    # removed it meanwhile) + e1 demote + e2 has no decision (→ default keep).
    payload = {"entry_decisions": [
        {"id": "deadbeef", "action": "demote"},
        {"id": s._sha8(e1), "action": "demote"},
    ], "curated_rewrite": None}
    st = s._condense_apply(p, payload)
    assert st["skipped_rebased"] == 1          # the ghost decision
    assert st["demoted"] == 1 and st["kept"] == 1
    _c, ents, _w = s._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == [e2]                         # unmentioned entry survives


def test_apply_fold_downgrades_when_heading_gone(tmp_data_dir):
    s = _server(tmp_data_dir)
    e = "- [2026-05-18] **fact** — keep the path data/x.json"
    p, mp = _seed(s, "# Index\n\n## RealHeading", [e])
    payload = {"entry_decisions": [
        {"id": s._sha8(e), "action": "fold",
         "fold_into": "## RealHeading", "pointer_line": "- [f](f.md) — x"},
    ], "curated_rewrite": None}
    # Mutate the file out from under the plan: heading removed after plan time.
    mp.write_text(s._mem_compose("# Index", [e], []), encoding="utf-8")
    st = s._condense_apply(p, payload)
    # Heading gone → never lose the fact: raw entry demoted to archive instead.
    assert st["fold_downgraded"] == 1 and st["folded"] == 0
    assert e in s._get_archive_path(p).read_text(encoding="utf-8")
    _c, ents, _w = s._mem_split_full(mp.read_text(encoding="utf-8"))
    assert ents == []


def test_apply_fold_downgrades_when_heading_ambiguous(tmp_data_dir):
    """Committee Seat 1 F2: a heading that resolves to >1 curated line must
    NOT misplace the pointer — downgrade to demote, never lose the fact."""
    s = _server(tmp_data_dir)
    e = "- [2026-05-18] **fact** — server.py:4902 matters"
    # Same heading text appears twice in curated (e.g. under two parents).
    curated = "# Index\n\n## Notes\n- a\n\n### Sub\n\n## Notes\n- b"
    p, mp = _seed(s, curated, [e])
    payload = {"entry_decisions": [
        {"id": s._sha8(e), "action": "fold",
         "fold_into": "## Notes", "pointer_line": "- [f](f.md) — x"},
    ], "curated_rewrite": None}
    st = s._condense_apply(p, payload)
    assert st["fold_downgraded"] == 1 and st["folded"] == 0
    cur, ents, _w = s._mem_split_full(mp.read_text(encoding="utf-8"))
    assert "- [f](f.md) — x" not in cur          # pointer never placed
    assert ents == []                             # raw entry left managed
    assert e in s._get_archive_path(p).read_text(encoding="utf-8")  # → archive
    assert st["curated_lines"] == len(cur.splitlines())  # gauge populated


def test_apply_archive_is_append_only(tmp_data_dir):
    s = _server(tmp_data_dir)
    p = _proj()
    ap = s._get_archive_path(p)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text("## Archived Session Log\n- [old] preexisting\n",
                  encoding="utf-8")
    e = "- [2026-05-18] **drop** — bye"
    mp = s._get_memory_path(p)
    mp.write_text(s._mem_compose("# I", [e], []), encoding="utf-8")
    s._condense_apply(p, {"entry_decisions": [
        {"id": s._sha8(e), "action": "demote"}], "curated_rewrite": None})
    arch = ap.read_text(encoding="utf-8")
    assert "- [old] preexisting" in arch    # prior archive content untouched
    assert e in arch


# ── _should_condense: structured trigger is line-keyed (closes the gap) ───────

def _proj_with_big_claude_md(s, tmp_path, monkeypatch):
    """Project whose MEMORY.md stays isolated under tmp_data_dir (native path
    suppressed) but whose project_path holds a 200 KB CLAUDE.md."""
    monkeypatch.setattr(s, "_native_memory_path", lambda pp: None)
    ppdir = tmp_path / "proj"
    ppdir.mkdir()
    (ppdir / "CLAUDE.md").write_text("x" * 200_000, encoding="utf-8")
    return {"id": "tproj", "project_path": str(ppdir)}


def test_structured_trigger_ignores_huge_claude_md(tmp_data_dir, tmp_path,
                                                   monkeypatch):
    s = _server(tmp_data_dir)
    s.CONFIG["condense_mode"] = "structured"
    s.CONFIG["index_line_budget"] = 10
    p = _proj_with_big_claude_md(s, tmp_path, monkeypatch)
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)

    # Tiny MEMORY.md (< budget lines) — the 200 KB CLAUDE.md must NOT trip
    # structured condense (the recurring-no-op gap we are closing).
    mp.write_text(s._mem_compose("# Index", ["- [d] **a** — x"], []),
                  encoding="utf-8")
    assert s._should_condense(p, include_claude_md=True) is False

    # Over the LINE budget → fires, regardless of CLAUDE.md size.
    big = [f"- [d] **e{i}** — line {i}" for i in range(40)]
    mp.write_text(s._mem_compose("# Index", big, []), encoding="utf-8")
    assert s._should_condense(p, include_claude_md=True) is True


def test_agent_trigger_unchanged_byte_keyed(tmp_data_dir, tmp_path,
                                            monkeypatch):
    s = _server(tmp_data_dir)
    s.CONFIG["condense_mode"] = "agent"           # legacy path = default
    s.CONFIG["condense_threshold_kb"] = 30
    p = _proj_with_big_claude_md(s, tmp_path, monkeypatch)
    mp = s._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(s._mem_compose("# I", ["- [d] **a** — x"], []),
                  encoding="utf-8")
    # Agent path stays combined-byte keyed → the big CLAUDE.md still trips it
    # (proves we did not alter legacy behavior).
    assert s._should_condense(p, include_claude_md=True) is True
