"""P2-2: per-project upload quota (IMPROVEMENT_PLAN_V2.md).

Limits default to 0 (unlimited) → no behavior change unless configured.
Verifies precedence, helper math, and 413 enforcement on both the
project attachment endpoint and the agent image endpoint.
"""
import importlib
import io


def _server(tmp_data_dir):
    s = importlib.import_module("server")
    importlib.reload(s)
    return s


def test_upload_limit_precedence(tmp_data_dir):
    s = _server(tmp_data_dir)
    s.CONFIG["upload_max_file_bytes"] = 100
    assert s._upload_limit(None, "upload_max_file_bytes") == 100
    # Project override wins.
    assert s._upload_limit({"upload_max_file_bytes": 50},
                           "upload_max_file_bytes") == 50
    # 0 / invalid → unlimited (0).
    assert s._upload_limit({"upload_max_file_bytes": 0},
                           "upload_max_file_bytes") == 0
    assert s._upload_limit({"upload_max_file_bytes": "x"},
                           "upload_max_file_bytes") == 0


def test_project_attachment_usage_sums(tmp_data_dir):
    s = _server(tmp_data_dir)
    proj = {"backlog": [
        {"attachments": [{"size": 100}, {"size": 50}]},
        {"attachments": [{"size": 25}]},
        {"attachments": [{"size": "bad"}]},
    ]}
    assert s._project_attachment_usage(proj) == 175


def test_incoming_file_size_nonconsuming(tmp_data_dir):
    s = _server(tmp_data_dir)
    from werkzeug.datastructures import FileStorage
    fs = FileStorage(stream=io.BytesIO(b"x" * 42), filename="a.bin")
    assert s._incoming_file_size(fs) == 42
    assert fs.stream.read() == b"x" * 42  # not consumed by the measure


def test_per_file_cap_blocks_oversize_attachment(tmp_data_dir):
    s = _server(tmp_data_dir)
    s.save_project("proj1", {"id": "proj1", "name": "P1",
                             "backlog": [{"id": "it1", "text": "t"}]})
    s.CONFIG["upload_max_file_bytes"] = 10
    c = s.app.test_client()
    r = c.post("/api/project/proj1/backlog/it1/attachments",
               data={"file": (io.BytesIO(b"y" * 50), "big.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 413
    assert r.get_json()["limit_bytes"] == 10


def test_under_limit_attachment_succeeds(tmp_data_dir):
    s = _server(tmp_data_dir)
    s.save_project("proj2", {"id": "proj2", "name": "P2",
                             "backlog": [{"id": "it1", "text": "t"}]})
    s.CONFIG["upload_max_file_bytes"] = 0          # unlimited
    s.CONFIG["upload_quota_bytes"] = 0
    c = s.app.test_client()
    r = c.post("/api/project/proj2/backlog/it1/attachments",
               data={"file": (io.BytesIO(b"hello"), "ok.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_project_quota_blocks_when_cumulative_exceeded(tmp_data_dir):
    s = _server(tmp_data_dir)
    # Existing usage 90 B; quota 100 B (per-project override); new 50 B → over.
    s.save_project("proj3", {
        "id": "proj3", "name": "P3", "upload_quota_bytes": 100,
        "backlog": [{"id": "it1", "text": "t",
                     "attachments": [{"id": "a", "size": 90}]}]})
    s.CONFIG["upload_max_file_bytes"] = 0
    c = s.app.test_client()
    r = c.post("/api/project/proj3/backlog/it1/attachments",
               data={"file": (io.BytesIO(b"z" * 50), "more.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 413
    assert r.get_json()["error"] == "project upload quota exceeded"


def test_agent_image_per_file_cap(tmp_data_dir):
    s = _server(tmp_data_dir)
    s.CONFIG["upload_max_file_bytes"] = 5
    c = s.app.test_client()
    r = c.post("/api/agent/upload-image",
               data={"file": (io.BytesIO(b"P" * 99), "p.png")},
               content_type="multipart/form-data")
    assert r.status_code == 413
