"""P2-3: _log() level gate (IMPROVEMENT_PLAN_V2.md).

Default level keeps output identical to the old bare print()s; raising
log_level quiets lower-severity lines. print()-signature-compatible so
the sweep was mechanical.
"""
import importlib


def _server(tmp_data_dir):
    s = importlib.import_module("server")
    importlib.reload(s)
    return s


def test_default_info_emits(tmp_data_dir, capsys):
    s = _server(tmp_data_dir)
    s.CONFIG["log_level"] = "info"
    s._log("hello", "world")
    assert "hello world" in capsys.readouterr().out


def test_warn_threshold_suppresses_info(tmp_data_dir, capsys):
    s = _server(tmp_data_dir)
    s.CONFIG["log_level"] = "warn"
    s._log("chatter")                 # default level=info → gated out
    s._log("danger", level="error")   # error ≥ warn → shown
    out = capsys.readouterr().out
    assert "chatter" not in out
    assert "danger" in out


def test_print_signature_compat(tmp_data_dir, capsys):
    s = _server(tmp_data_dir)
    s.CONFIG["log_level"] = "info"
    # Multi-arg, sep/end kwargs, explicit flush — must pass straight through.
    s._log("a", "b", sep="-", end="!", flush=True)
    assert capsys.readouterr().out == "a-b!"


def test_unknown_level_treated_as_info(tmp_data_dir, capsys):
    s = _server(tmp_data_dir)
    s.CONFIG["log_level"] = "info"
    s._log("x", level="bogus")
    assert "x" in capsys.readouterr().out


def test_log_level_is_editable_config_key(tmp_data_dir):
    s = _server(tmp_data_dir)
    assert "log_level" in s._CONFIG_EDITABLE_KEYS
