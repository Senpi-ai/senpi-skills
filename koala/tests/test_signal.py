#!/usr/bin/env python3
"""Unit tests for Koala's pure functions (should_enter, record_entry,
record_exit). Stubs koala_config + senpi_runtime_helpers.
Run: python3 koala/tests/test_signal.py
"""
import importlib.util
import sys
import types
from pathlib import Path

_cfg = types.ModuleType("koala_config")
_cfg.load_config = lambda: {}
_cfg.mcp_call = lambda *a, **k: None
_cfg.get_positions = lambda w: (0, [])
_cfg.was_recently_signaled = lambda c: False
_cfg.record_signal = lambda c: None
_cfg.read_koala_state = lambda: {}
_cfg.write_koala_state = lambda s: None
_cfg.output = lambda d: None
_cfg._wrapper_client = types.SimpleNamespace(push_signal=lambda **k: None)
sys.modules["koala_config"] = _cfg

_helpers = types.ModuleType("senpi_runtime_helpers")
class SenpiClientError(Exception):
    pass
_helpers.SenpiClientError = SenpiClientError
_helpers.producer_daemon = lambda **k: None
sys.modules["senpi_runtime_helpers"] = _helpers

_path = Path(__file__).resolve().parent.parent / "scripts" / "koala-producer.py"
_spec = importlib.util.spec_from_file_location("koala_producer", _path)
kp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kp)

NOW = 1_700_000_000.0
HOUR = 3600.0


def test_should_enter_fresh_state_fire_once():
    # Brand-new deploy, fire-once mode → enter
    state = {}
    assert kp.should_enter(state, fire_once=True, re_entry_cooldown_hours=168, now_ts=NOW) is True


def test_should_enter_fresh_state_re_entry_mode():
    # Brand-new deploy, re-entry mode → also enter
    state = {}
    assert kp.should_enter(state, fire_once=False, re_entry_cooldown_hours=168, now_ts=NOW) is True


def test_should_enter_blocks_after_entry_in_fire_once():
    # Fire-once + already entered → block forever
    state = {"first_entry_at": NOW - 30 * 24 * HOUR}   # 30 days ago
    assert kp.should_enter(state, fire_once=True, re_entry_cooldown_hours=168, now_ts=NOW) is False


def test_should_enter_blocks_re_entry_when_no_exit_recorded():
    # Re-entry mode, entered before, but no exit recorded → position is still
    # held (or close went undetected) → don't double-enter
    state = {"first_entry_at": NOW - 10 * 24 * HOUR}
    assert kp.should_enter(state, fire_once=False, re_entry_cooldown_hours=168, now_ts=NOW) is False


def test_should_enter_re_entry_during_cooldown():
    # Re-entry mode, exited 3 days ago, cooldown is 7 days → still blocked
    state = {"first_entry_at": NOW - 30 * 24 * HOUR, "last_exit_at": NOW - 3 * 24 * HOUR}
    assert kp.should_enter(state, fire_once=False, re_entry_cooldown_hours=168, now_ts=NOW) is False


def test_should_enter_re_entry_after_cooldown():
    # Re-entry mode, exited 8 days ago, cooldown is 7 days → can re-enter
    state = {"first_entry_at": NOW - 30 * 24 * HOUR, "last_exit_at": NOW - 8 * 24 * HOUR}
    assert kp.should_enter(state, fire_once=False, re_entry_cooldown_hours=168, now_ts=NOW) is True


def test_record_entry_sets_first_entry_only_once():
    # First entry → first_entry_at is set
    s1 = kp.record_entry({}, NOW)
    assert s1["first_entry_at"] == NOW
    assert s1["last_entry_at"] == NOW
    assert s1["total_entries"] == 1
    # Second entry (a re-entry) → first_entry_at stays at original, last_entry_at advances
    s2 = kp.record_entry(s1, NOW + 86400)
    assert s2["first_entry_at"] == NOW       # unchanged
    assert s2["last_entry_at"] == NOW + 86400
    assert s2["total_entries"] == 2


def test_record_exit_sets_last_exit():
    state = {"first_entry_at": NOW - 86400, "last_entry_at": NOW - 86400, "total_entries": 1}
    s2 = kp.record_exit(state, NOW)
    assert s2["last_exit_at"] == NOW
    # Doesn't disturb other fields
    assert s2["first_entry_at"] == NOW - 86400
    assert s2["total_entries"] == 1


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
