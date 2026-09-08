"""The exit preview in explaining-the-exit.md must stay arithmetically true to dsl-presets.yaml.

WHY THIS EXISTS (2026-09-08). `references/dsl-configuration.md` drifted away from
`references/dsl-presets.yaml` in the same directory and spent weeks teaching ladders the presets
had already banned (`phase1.enabled: true`, `lock_hw_pct: 0` rungs). A worked example in prose has
no compiler, so it rots silently and the agent teaches the user numbers that are no longer real.

SKILL.md's confirm step now shows users a worked `let_winners_run` preview. These tests recompute
that example from the preset file using the engine's own formula, so the day someone retunes a
preset the doc fails instead of lying:

    floor ROE = high-water ROE x lock_hw_pct / 100        (dsl/engine/floors.ts computeTierFloor)
    active tier = highest tier whose trigger_pct is <= ROE (dsl/engine/floors.ts tierIndexFromPrice)

Run:
  python3 -m pytest senpi-strategy-author/tests/test_exit_preview_matches_presets.py -q
"""
import pathlib
import re

import pytest
import yaml

_REFS = pathlib.Path(__file__).resolve().parents[1] / "references"
_GUIDE = _REFS / "explaining-the-exit.md"
_PRESETS = _REFS / "dsl-presets.yaml"

_PRESET_IN_PREVIEW = "let_winners_run"

_GUIDE_TEXT = _GUIDE.read_text(encoding="utf-8")
_PRESETS_DOC = yaml.safe_load(_PRESETS.read_text(encoding="utf-8"))["presets"]


def _tiers(name):
    """[(trigger_pct, lock_hw_pct), ...] for one preset, in file order."""
    return [
        (t["trigger_pct"], t["lock_hw_pct"])
        for t in _PRESETS_DOC[name]["dsl_preset"]["phase2"]["tiers"]
    ]


def _floor_roe(name, roe):
    """The engine's floor at `roe`, or None when no tier has been reached yet."""
    active = None
    for trigger, lock in _tiers(name):          # tierIndexFromPrice: LAST tier at or below roe
        if roe >= trigger:
            active = lock
    if active is None:
        return None
    return roe * active / 100                   # computeTierFloor


def _preview_rungs():
    """[(up_pct, stop_pct), ...] as explaining-the-exit.md promises them to the user."""
    return [
        (int(a), int(b))
        for a, b in re.findall(r"Up (\d+)% . your stop moves to \*\*\+(\d+)%\*\*", _GUIDE_TEXT)
    ]


def test_the_preview_is_still_in_the_skill():
    """Guard the guard: a preview that got deleted or reworded must not pass vacuously."""
    assert _preview_rungs(), "no 'Up N% -> your stop moves to +M%' lines found in explaining-the-exit.md"


@pytest.mark.parametrize("up,promised", _preview_rungs())
def test_each_rung_matches_the_engine_arithmetic(up, promised):
    """Every number shown to a user must be what the DSL engine would actually do."""
    expected = _floor_roe(_PRESET_IN_PREVIEW, up)
    assert expected is not None, f"explaining-the-exit.md claims a stop at +{up}% ROE but no tier has fired there"
    assert expected == pytest.approx(promised), (
        f"explaining-the-exit.md tells the user 'up {up}% -> stop at +{promised}%', but {_PRESET_IN_PREVIEW} "
        f"gives +{expected:g}%. Re-derive the preview from dsl-presets.yaml."
    )


def test_the_no_lock_yet_threshold_is_the_first_trigger():
    """'Below +N% nothing is locked in' has to be the preset's own first rung."""
    first_trigger = _tiers(_PRESET_IN_PREVIEW)[0][0]
    assert re.search(rf"Below \+{first_trigger}% . nothing is locked in", _GUIDE_TEXT), (
        f"{_PRESET_IN_PREVIEW}'s first tier is +{first_trigger}%; explaining-the-exit.md's 'Below +N%' line disagrees"
    )


def test_the_downside_number_is_the_presets_max_loss():
    """The floor quoted under the ladder must be the preset's real max_loss_pct."""
    max_loss = _PRESETS_DOC[_PRESET_IN_PREVIEW]["dsl_preset"]["phase1"]["max_loss_pct"]
    shown = re.search(r"only floor is the \*\*.(\d+)% max loss\*\*", _GUIDE_TEXT)
    assert shown, "explaining-the-exit.md no longer states the max-loss floor under the ladder"
    assert int(shown.group(1)) == int(max_loss), (
        f"explaining-the-exit.md shows a {shown.group(1)}% max loss; {_PRESET_IN_PREVIEW} carries {max_loss}%"
    )


def test_the_preview_preset_still_has_no_time_cuts():
    """explaining-the-exit.md tells the agent let_winners_run has none — so it must not grow one silently."""
    dsl = _PRESETS_DOC[_PRESET_IN_PREVIEW]["dsl_preset"]
    live = [k for k in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")
            if isinstance(dsl.get(k), dict) and dsl[k].get("enabled")]
    assert not live, (
        f"{_PRESET_IN_PREVIEW} now carries {live}; explaining-the-exit.md says it has none. "
        "Update the preview's time-cut line."
    )


def test_no_shipped_preset_trips_the_unreachable_first_rung_rule():
    """The ~40% warning must fire on hand-rolled ladders only, never on our own presets."""
    offenders = {n: _tiers(n)[0][0] for n in _PRESETS_DOC if _tiers(n)[0][0] > 40}
    assert not offenders, (
        f"these presets would trigger explaining-the-exit.md's own 'first rung above ~40%' warning: {offenders}"
    )


def test_no_shipped_preset_carries_a_breakeven_rung():
    """`lock_hw_pct: 0` exits flat and still pays fees — banned fleet-wide, and explaining-the-exit.md says so."""
    offenders = {n: t for n in _PRESETS_DOC for t in _tiers(n) if t[1] == 0}
    assert not offenders, f"lock_hw_pct: 0 rungs found: {offenders}"


def test_the_time_cut_table_matches_the_presets():
    """The table tells the agent which cuts to mention per preset; a retune must not silently
    make it wrong — that is exactly how dsl-configuration.md rotted."""
    for name, preset in _PRESETS_DOC.items():
        dsl = preset["dsl_preset"]
        live = {k for k in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")
                if isinstance(dsl.get(k), dict) and dsl[k].get("enabled")}
        row = re.search(rf"^\| `{re.escape(name)}` \| (.+?) \|$", _GUIDE_TEXT, re.M)
        assert row, f"{name} has no row in the time-cut table"
        claims_none = row.group(1).strip() == "none"
        assert claims_none == (not live), (
            f"time-cut table says {name} is '{row.group(1).strip()}' but the preset carries "
            f"{sorted(live) or 'nothing'}"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
