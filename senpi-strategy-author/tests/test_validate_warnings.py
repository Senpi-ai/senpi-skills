#!/usr/bin/env python3
"""The validator's ADVISORY channel — `warnings()` — and the wall between it and `validate()`.

Three findings a green package does not surface and a user learns from their fills instead:

* a hard stop whose PRICE distance (max_loss_pct ÷ leverage) sits inside intraday noise — a run of
  wick stop-outs that reads as "the strategy is broken";
* multi-slot sizing the runtime cannot fund: slots × margin over 100%, or a scanner that emits
  several signals per tick with no free-margin gate (every open after the first lands
  `position_open_failed`);
* a daily entry cap, which pauses the runtime by design — unsaid, the quiet reads as a dead strategy.

None of them may ever fail validation: the exit code belongs to `validate()` alone.

    python3 -m pytest senpi-strategy-author/tests/test_validate_warnings.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import contextlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_RUNTIME = """\
name: {sid}-main
group: {sid}
description: >
  A fixture recipe whose only variables are the sizing, leverage, exit and risk knobs, so each
  advisory warning can be provoked on its own and nothing else colours the result.
strategy:
  wallet: "${{FIX_WALLET}}"
  slots: {slots}
  margin_pct: {margin}
  default_leverage: {leverage}
exit:
{exit}
scanners:
  - type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 900
    inputs: {{}}
    signal_data_schema:
      score:
        type: float
{risk}"""

_GATED_SCAN = ("def scan(inputs, ctx):\n"
               "    free = float(ctx.clearinghouse().get('withdrawable', 0))\n"
               "    return [] if free < 10 else []\n")
_BLIND_SCAN = "def scan(inputs, ctx):\n    return []\n"


def _package(tmp_path, sid="fix", slots=1, margin=20, leverage=3, exit_block="  dsl_preset: balanced",
             risk="", scan=_BLIND_SCAN):
    d = tmp_path / sid
    (d / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(f'id: {sid}\nversion: "1.0.0"\n')   # FLAT: main is synthesized
    (d / "runtime.yaml").write_text(_RUNTIME.format(sid=sid, slots=slots, margin=margin,
                                                    leverage=leverage, exit=exit_block, risk=risk))
    (d / "scanners" / "scan.py").write_text(scan)
    (d / "scanners" / "scoring.py").write_text("def score(x):\n    return x\n")
    return d


def _kinds(warns):
    return [w.split("[", 1)[1].split("]", 1)[0] for w in warns if "[" in w]


# ---- [stop] ----

def test_tight_stop_at_high_leverage_warns_with_the_arithmetic(tmp_path):
    # balanced = 8% ROE; at 10x that is 0.8% of price — inside a wick.
    w = vs.warnings(_package(tmp_path, leverage=10))
    stop = [x for x in w if "[stop]" in x]
    assert len(stop) == 1
    assert "0.80% of price" in stop[0] and "8% ROE at 10x" in stop[0]


def test_roomy_stop_does_not_warn(tmp_path):
    # balanced 8% at 2x = 4% of price.
    assert not [x for x in vs.warnings(_package(tmp_path, leverage=2)) if "[stop]" in x]


def test_inline_preset_and_scanner_leverage_are_read(tmp_path):
    # Inline max_loss_pct 10 with scanner-input leverage tiers up to 20 → 0.5%.
    d = _package(tmp_path, leverage=2, exit_block="  dsl_preset:\n    max_loss_pct: 10\n    phase1:\n      enabled: false")
    rt = d / "runtime.yaml"
    rt.write_text(rt.read_text().replace("inputs: {}", "inputs:\n      leverageTiers: [5, 20]"))
    assert vs.max_leverage(__import__("yaml").safe_load(rt.read_text())) == 20
    assert [x for x in vs.warnings(d) if "[stop]" in x]


def test_unknown_leverage_or_preset_is_silent(tmp_path):
    d = _package(tmp_path, leverage=10, exit_block="  dsl_preset: no_such_preset")
    assert not [x for x in vs.warnings(d) if "[stop]" in x]


# ---- [sizing] ----

def test_multi_slot_without_free_margin_gate_warns(tmp_path):
    w = vs.warnings(_package(tmp_path, slots=3, margin=20, scan=_BLIND_SCAN))
    assert any("no free-margin gate" in x for x in w)
    assert not any("can never fund" in x for x in w)        # 3 × 20 = 60% — fundable


def test_free_margin_idiom_clears_the_gate_warning(tmp_path):
    w = vs.warnings(_package(tmp_path, slots=3, margin=20, scan=_GATED_SCAN))
    assert not any("no free-margin gate" in x for x in w)


def test_overcommitted_slots_warn_with_the_count_that_cannot_fund(tmp_path):
    w = vs.warnings(_package(tmp_path, slots=3, margin=40, scan=_GATED_SCAN))
    over = [x for x in w if "can never fund" in x]
    assert len(over) == 1 and "3 slots × 40% margin = 120%" in over[0] and "last 1 slot" in over[0]


def test_single_slot_never_raises_sizing_warnings(tmp_path):
    assert "sizing" not in _kinds(vs.warnings(_package(tmp_path, slots=1, margin=90, scan=_BLIND_SCAN)))


# ---- [cap] ----

def test_daily_cap_is_surfaced_and_a_cap_at_or_below_slots_says_so(tmp_path):
    risk = "risk:\n  guard_rails:\n    max_entries_per_day: 4\n"
    w = vs.warnings(_package(tmp_path, slots=4, margin=20, scan=_GATED_SCAN, risk=risk))
    cap = [x for x in w if "[cap]" in x]
    assert len(cap) == 1
    assert "Runtime paused: Max Entries/Day" in cap[0] and "fills the book once" in cap[0]


def test_daily_cap_above_slots_is_a_plain_note(tmp_path):
    risk = "risk:\n  guard_rails:\n    max_entries_per_day: 12\n"
    w = vs.warnings(_package(tmp_path, slots=2, margin=20, scan=_GATED_SCAN, risk=risk))
    cap = [x for x in w if "[cap]" in x]
    assert len(cap) == 1 and "fills the book once" not in cap[0]


def test_no_cap_no_note(tmp_path):
    assert "cap" not in _kinds(vs.warnings(_package(tmp_path)))


# ---- the wall between the channels ----

def test_warnings_never_become_errors(tmp_path):
    risk = "risk:\n  guard_rails:\n    max_entries_per_day: 2\n"
    d = _package(tmp_path, slots=3, margin=40, leverage=20, scan=_BLIND_SCAN, risk=risk)
    assert vs.validate(d) == []                       # green
    assert len(vs.warnings(d)) >= 4                   # and loud


def test_main_prints_warnings_under_the_verdict_and_exits_zero(tmp_path):
    d = _package(tmp_path, leverage=10)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            vs.main(["validate_strategy.py", str(d)])
        except SystemExit as e:
            code = e.code
    out = buf.getvalue()
    assert code == 0
    assert out.startswith("✓ fix") and "advisory warning" in out and "[stop]" in out
