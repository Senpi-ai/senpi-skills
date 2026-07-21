#!/usr/bin/env python3
"""cli_json robustness — the JSON payload may land on STDOUT or STDERR.

`openclaw senpi state -r <id> --json` was observed emitting its payload to stderr; the old
stdout-only `cli_json` returned None, so a healthy runtime read as 'no state' and verify reported a
FALSE `scanner=broken`. These lock the stdout-first / stderr-fallback behaviour.

    python3 -m pytest senpi-strategy-ops/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _cli  # noqa: E402

_STATE = '{"scanners":[{"name":"sc1","runCount":11,"enabled":true}]}'
_PARSED = {"scanners": [{"name": "sc1", "runCount": 11, "enabled": True}]}


def test_cli_json_parses_stdout(monkeypatch):
    monkeypatch.setattr(_cli, "run_cli", lambda *a, **k: (0, _STATE, ""))
    assert _cli.cli_json(["x"]) == _PARSED


def test_cli_json_falls_back_to_stderr(monkeypatch):
    """The observed failure: payload on stderr, stdout empty — must still be recovered."""
    monkeypatch.setattr(_cli, "run_cli", lambda *a, **k: (0, "", _STATE))
    assert _cli.cli_json(["x"]) == _PARSED


def test_cli_json_stdout_wins_over_stderr(monkeypatch):
    """Normal path is unchanged: when stdout has JSON it is used, stderr is ignored."""
    monkeypatch.setattr(_cli, "run_cli", lambda *a, **k: (0, '{"a":1}', '{"b":2}'))
    assert _cli.cli_json(["x"]) == {"a": 1}


def test_cli_json_none_on_nonzero_rc(monkeypatch):
    """A genuine command failure (rc!=0) still returns None even if stderr carries an error object."""
    monkeypatch.setattr(_cli, "run_cli", lambda *a, **k: (1, "", '{"error":"nope"}'))
    assert _cli.cli_json(["x"]) is None


def test_cli_json_none_when_no_json_anywhere(monkeypatch):
    monkeypatch.setattr(_cli, "run_cli", lambda *a, **k: (0, "just logs", "more logs"))
    assert _cli.cli_json(["x"]) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
