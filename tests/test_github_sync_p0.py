"""One regression test per github_sync P0 fix (IMPROVEMENT_PLAN_V2.md
Sprint 2 / P0-1..P0-7).

Each test asserts the FIXED behavior and is designed to fail against the
pre-fix github_sync.py (verified by running this file with
github_sync.py.orig restored — see IMPROVEMENT_PLAN_V2_FLAWS.md log).
"""
import json

import pytest


def _proj(backlog):
    return {"id": "p", "github_repo": "o/r",
            "github_sync_enabled": True, "backlog": backlog}


def _issue(num, title="T", state="OPEN", priority=None):
    labels = [{"name": f"priority:{priority}"}] if priority else []
    return {"number": num, "title": title, "state": state,
            "labels": labels, "author": {"login": "u"}, "updatedAt": "x"}


# ── P0-1: pagination / no 100-issue cap ──────────────────────────────────────

def test_p0_1_no_hundred_issue_cap(gs, fake_gh, project_store):
    issues = [_issue(n, f"Issue {n}") for n in range(1, 151)]  # 150 > 100
    fake_gh.on(["issue", "list"], stdout=json.dumps(issues))
    project_store["p"] = _proj([])

    ok, summary = gs.sync_project("p")

    assert ok, summary
    assert len(project_store["p"]["backlog"]) == 150
    # The list call must request well past 100.
    listed = fake_gh.calls_matching(["issue", "list"])[0]
    limit = listed[listed.index("--limit") + 1]
    assert int(limit) >= 1000


# ── P0-2: last_synced_state 3-way merge (no silent clobber) ──────────────────

def test_p0_2_local_edit_preserved_when_github_unchanged(gs, fake_gh, project_store):
    # Item already synced; base == GitHub's current title.
    item = {"id": "a", "text": "local edit", "priority": "normal",
            "status": "open", "github_issue_number": 5,
            "last_synced_state": {"text": "orig", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"],
               stdout=json.dumps([_issue(5, "orig")]))  # GitHub unchanged

    ok, _ = gs.sync_project("p")

    assert ok
    # Old code did GitHub-wins → would overwrite to "orig". Fixed: kept.
    assert project_store["p"]["backlog"][0]["text"] == "local edit"


def test_p0_2_github_adopted_when_local_untouched(gs, fake_gh, project_store):
    item = {"id": "a", "text": "orig", "priority": "normal",
            "status": "open", "github_issue_number": 5,
            "last_synced_state": {"text": "orig", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"],
               stdout=json.dumps([_issue(5, "changed on github")]))

    gs.sync_project("p")
    assert project_store["p"]["backlog"][0]["text"] == "changed on github"


def test_p0_2_conflict_keeps_local_and_logs(gs, fake_gh, project_store):
    item = {"id": "a", "text": "local changed", "priority": "normal",
            "status": "open", "github_issue_number": 5,
            "last_synced_state": {"text": "orig", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"],
               stdout=json.dumps([_issue(5, "github changed")]))

    gs.sync_project("p")
    assert project_store["p"]["backlog"][0]["text"] == "local changed"
    assert any("conflict" in m.lower() for _, m in gs._activity_log)


# ── P0-3: no redundant close/reopen every cycle ──────────────────────────────

def test_p0_3_no_redundant_close_reopen(gs, fake_gh, project_store):
    item = {"id": "a", "text": "t", "priority": "normal", "status": "open",
            "github_issue_number": 5,
            "last_synced_state": {"text": "t", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"], stdout=json.dumps([_issue(5, "t")]))

    gs.sync_project("p")

    # Status matches base → zero close/reopen API calls (old code: 1/cycle).
    assert fake_gh.count(["issue", "close"]) == 0
    assert fake_gh.count(["issue", "reopen"]) == 0


def test_p0_3_close_still_pushed_on_real_local_change(gs, fake_gh, project_store):
    item = {"id": "a", "text": "t", "priority": "normal", "status": "done",
            "github_issue_number": 5,
            "last_synced_state": {"text": "t", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"], stdout=json.dumps([_issue(5, "t")]))

    gs.sync_project("p")
    assert fake_gh.count(["issue", "close"]) == 1


# ── P0-4: deleted-on-GitHub handling ─────────────────────────────────────────

def test_p0_4_deleted_issue_unlinked_not_zombie(gs, fake_gh, project_store):
    item = {"id": "a", "text": "t", "priority": "normal", "status": "open",
            "github_issue_number": 99, "github_synced_at": "old",
            "last_synced_state": {"text": "t", "priority": "normal",
                                  "status": "open"}}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"], stdout=json.dumps([]))  # #99 gone

    gs.sync_project("p")

    out = project_store["p"]["backlog"][0]
    assert out["github_issue_number"] is None
    assert out.get("github_deleted") is True
    assert out["text"] == "t"  # local task preserved
    # Never tries to close/reopen a dead issue.
    assert fake_gh.count(["issue", "close"]) == 0
    assert fake_gh.count(["issue", "reopen"]) == 0


# ── P0-5: symmetric sanitization ─────────────────────────────────────────────

def test_p0_5_local_text_sanitized_on_push(gs, fake_gh, project_store):
    item = {"id": "a", "text": "fix <script>x</script> bug",
            "priority": "normal", "status": "open"}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"], stdout=json.dumps([]))
    fake_gh.on(["issue", "create"],
               stdout="https://github.com/o/r/issues/12")

    gs.sync_project("p")

    create = fake_gh.calls_matching(["issue", "create"])[0]
    title = create[create.index("--title") + 1]
    assert "<script>" not in title
    # Stored sanitized → no spurious "updated" next cycle.
    assert "<script>" not in project_store["p"]["backlog"][0]["text"]


# ── P0-6: push issue bodies ──────────────────────────────────────────────────

def test_p0_6_body_is_pushed(gs, fake_gh, project_store):
    item = {"id": "a", "text": "title", "priority": "normal",
            "status": "open", "notes": "the detailed body"}
    project_store["p"] = _proj([item])
    fake_gh.on(["issue", "list"], stdout=json.dumps([]))
    fake_gh.on(["issue", "create"],
               stdout="https://github.com/o/r/issues/3")

    gs.sync_project("p")

    create = fake_gh.calls_matching(["issue", "create"])[0]
    body = create[create.index("--body") + 1]
    assert body == "the detailed body"  # old code always pushed ''


# ── P0-7: throttle bulk pushes ───────────────────────────────────────────────

def test_p0_7_create_cap_per_cycle(gs, fake_gh, project_store):
    backlog = [{"id": f"i{n}", "text": f"task {n}", "priority": "normal",
                "status": "open"} for n in range(50)]
    project_store["p"] = _proj(backlog)
    fake_gh.on(["issue", "list"], stdout=json.dumps([]))
    counter = {"n": 0}

    def _create(argv):
        counter["n"] += 1
        return 0, f"https://github.com/o/r/issues/{counter['n']}", ""

    fake_gh.on(["issue", "create"], callback=_create)

    gs.sync_project("p")

    creates = fake_gh.count(["issue", "create"])
    assert creates <= 25, f"expected <=25 creates/cycle, got {creates}"
    assert any("deferred" in m.lower() for _, m in gs._activity_log)
