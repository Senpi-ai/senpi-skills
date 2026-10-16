"""[fees] — the validator's turnover-cost advisory (an estimate, worded to be relayed)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import validate_strategy as vs  # noqa: E402


def _package(tmp_path, interval, cap, slots, margin, leverage):
    pkg = Path(tmp_path) / "churn"
    (pkg / "scanners").mkdir(parents=True)
    (pkg / "strategy.yaml").write_text('schema_version: 1\nid: churn\nversion: "1.0.0"\ncatalog:\n  name: "Churn"\n')
    cap_line = "    max_entries_per_day: %d\n" % cap if cap else ""
    (pkg / "runtime.yaml").write_text(
        "name: churn-main\ngroup: churn\nversion: 1.0.0\ndescription: churn\n"
        "strategy:\n  wallet: \"${CHURN_WALLET}\"\n  slots: %d\n  margin_pct: %g\n  default_leverage: %d\n"
        "scanners:\n  - name: s\n    type: external_scanner\n    path: ./scanners\n    entrypoint: scan.py\n"
        "    interval_seconds: %d\n    inputs: {}\n"
        "actions:\n  - name: open\n    action_type: OPEN_POSITION\n    decision_mode: rule\n    scanners: [s]\n"
        "exit:\n  engine: dsl\n  dsl_preset:\n    phase1:\n      enabled: false\n      max_loss_pct: 20\n"
        "risk:\n  guard_rails:\n%s" % (slots, margin, leverage, interval, cap_line or "    drawdown_halt_pct: 20\n"))
    (pkg / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    withdrawable = 0\n    return []\n")
    return pkg


def test_a_ten_minute_scanner_with_a_daily_cap_warns_with_the_arithmetic(tmp_path):
    # 12 entries/day × 20% × 3x × 2 sides × 0.05% = 0.72% of the budget per day
    w = [x for x in vs.warnings(_package(tmp_path, 600, 12, 4, 20, 3)) if "[fees]" in x]
    assert len(w) == 1 and "0.72%" in w[0] and "12 entries/day" in w[0] and "the daily cap" in w[0]


def test_a_slow_low_leverage_book_does_not_warn(tmp_path):
    # 2 slots × 1 turn/day at 6h × 10% × 2x × 2 × 0.05% = 0.04%/day
    assert not [x for x in vs.warnings(_package(tmp_path, 21600, None, 2, 10, 2)) if "[fees]" in x]


def test_no_cap_uses_the_cadence_turnover_assumption(tmp_path):
    fd = vs.fee_drag_pct_per_day({"strategy": {"slots": 3, "margin_pct": 15, "default_leverage": 5},
                                  "scanners": [{"type": "external_scanner", "interval_seconds": 300}],
                                  "risk": {"guard_rails": {}}})
    assert fd and abs(fd[0] - 3 * 3 * 0.15 * 5 * 2 * 0.0005 * 100) < 1e-9 and "3 slot(s) turning 3" in fd[2]


def test_nothing_to_size_returns_none():
    assert vs.fee_drag_pct_per_day({"strategy": {"slots": 2}}) is None
