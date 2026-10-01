"""The runtime latches the daily loss and drawdown halts (runtime 3.0.137+, PR #456): once a check sees a
genuine breach the gate stays CLOSED after PnL recovers, the latch survives restarts, and with
`drawdown_reset_on_day_rollover: false` a drawdown halt never auto-resets. The references must not tell
an agent the halt clears on its own when it does not, and must leave clearing it to the user. Durations
are seconds: the runtime removed the `*_minutes` forms, so a recipe copied from the envelope must use them.
"""
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_risk_gates_documents_the_halt_latch():
    text = _read("senpi-strategy-author", "references", "risk-gates.md")
    assert "## Halt latch (gates 1 and 2)" in text
    assert "with `false` it never auto-resets" in text
    assert "Editing a threshold does not release a latch." in text
    assert "survives gateway restarts and updates" in text
    assert "without their explicit approval" in text
    assert "(default `false` = ~24h carry)" not in text


def test_risk_gates_uses_seconds_not_removed_minutes_fields():
    text = _read("senpi-strategy-author", "references", "risk-gates.md")
    assert "_minutes" not in text
    assert "cooldown_seconds: 3600" in text


def test_surfaces_that_say_when_a_halt_lets_go_mention_the_latch():
    assert "never auto-resets" in _read("senpi-trading-runtime", "references", "runtime-yaml.md")
    assert "otherwise never on its own" in _read("senpi-strategy-ops", "references", "lifecycle.md")
    portfolio = _read("senpi-portfolio", "SKILL.md")
    assert portfolio.count("never clear it") + portfolio.count("you never clear it") >= 2
