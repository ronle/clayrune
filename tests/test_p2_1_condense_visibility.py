"""P2-1: memory-condensation visibility (IMPROVEMENT_PLAN_V2.md).

Backend deliverable only — the frontend surface lives in the frozen
static/index.html. Verifies the status helpers round-trip and that
/agent/status always exposes a `condense` block.
"""
import importlib


def _server(tmp_data_dir):
    s = importlib.import_module("server")
    importlib.reload(s)
    return s


def test_condense_status_defaults_idle(tmp_data_dir):
    s = _server(tmp_data_dir)
    assert s._get_condense_status("nope") == {"state": "idle"}


def test_condense_status_roundtrip(tmp_data_dir):
    s = _server(tmp_data_dir)
    s._set_condense_status("p1", state="running", bytes_before=1234)
    st = s._get_condense_status("p1")
    assert st["state"] == "running"
    assert st["bytes_before"] == 1234
    # Returned dict is a copy — mutating it must not corrupt internal state.
    st["state"] = "tampered"
    assert s._get_condense_status("p1")["state"] == "running"


def test_agent_status_includes_condense_block(tmp_data_dir):
    s = _server(tmp_data_dir)
    client = s.app.test_client()
    r = client.get("/api/project/unknownproj/agent/status")
    assert r.status_code == 200
    body = r.get_json()
    assert "sessions" in body
    assert body["condense"] == {"state": "idle"}


def test_condense_combined_bytes_zero_when_absent(tmp_data_dir):
    s = _server(tmp_data_dir)
    # A project whose memory/archive paths don't exist → 0, no exception.
    assert s._condense_combined_bytes({"id": "x", "project_path": ""}) == 0
