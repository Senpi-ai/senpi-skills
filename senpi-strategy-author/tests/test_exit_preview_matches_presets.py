"""Every number in references/explaining-the-exit.md must be derivable from dsl-presets.yaml.

references/dsl-configuration.md drifted from dsl-presets.yaml in the same directory and spent weeks
teaching ladders the presets had already banned. Prose has no compiler; this is one.

Engine (senpi-trading-runtime src/dsl/engine/floors.ts):
  active rung = highest tier whose trigger_pct <= ROE   (tierIndexFromPrice)
  floor ROE   = high-water ROE x lock_hw_pct / 100      (computeTierFloor)
"""
import pathlib
import re

import pytest
import yaml

_REFS = pathlib.Path(__file__).resolve().parents[1] / "references"
_GUIDE = (_REFS / "explaining-the-exit.md").read_text(encoding="utf-8")
_PRESETS = yaml.safe_load((_REFS / "dsl-presets.yaml").read_text(encoding="utf-8"))["presets"]
_WORKED = "let_winners_run"  # the preset the template is worked in


def _tiers(name):
    return [(t["trigger_pct"], t["lock_hw_pct"])
            for t in _PRESETS[name]["dsl_preset"]["phase2"]["tiers"]]


def test_every_number_in_the_template_comes_from_the_preset():
    """The rungs, the 'nothing locked below N%' line, and the max-loss floor."""
    rungs = [(int(a), int(b)) for a, b in
             re.findall(r"Up (\d+)% . your stop moves to \*\*\+(\d+)%\*\*", _GUIDE)]
    assert rungs, "no 'Up N% -> your stop moves to +M%' lines found — template gone or reworded"

    for up, promised in rungs:
        active = max((lock for trig, lock in _tiers(_WORKED) if up >= trig), default=None)
        assert active is not None, f"template claims a stop at +{up}% but no tier has fired there"
        assert up * active / 100 == pytest.approx(promised), (
            f"template says 'up {up}% -> stop at +{promised}%'; {_WORKED} gives "
            f"+{up * active / 100:g}%. Re-derive from dsl-presets.yaml."
        )

    first = _tiers(_WORKED)[0][0]
    assert re.search(rf"Below \+{first}% . nothing is locked in", _GUIDE), \
        f"{_WORKED}'s first tier is +{first}%; the 'Below +N%' line disagrees"

    max_loss = _PRESETS[_WORKED]["dsl_preset"]["phase1"]["max_loss_pct"]
    shown = re.search(r"only floor is the \*\*.(\d+)% max loss\*\*", _GUIDE)
    assert shown and int(shown.group(1)) == int(max_loss), \
        f"template shows {shown and shown.group(1)}% max loss; {_WORKED} carries {max_loss}%"


@pytest.mark.parametrize("name", sorted(_PRESETS))
def test_the_time_cut_table_agrees_on_whether_a_preset_has_cuts(name):
    """Row says "none" iff the preset has no cuts. Deliberately none-vs-some, not which ones:
    the row is prose and the value that matters is let_winners_run (the worked template) staying
    cut-free. ponytail: widen to per-cut matching only if a row is ever wrong in detail."""
    dsl = _PRESETS[name]["dsl_preset"]
    live = {k for k in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")
            if isinstance(dsl.get(k), dict) and dsl[k].get("enabled")}
    row = re.search(rf"^\| `{re.escape(name)}` \| (.+?) \|$", _GUIDE, re.M)
    assert row, f"{name} has no row in the time-cut table"
    assert (row.group(1).strip() == "none") == (not live), \
        f"table says {name} is '{row.group(1).strip()}' but it carries {sorted(live) or 'nothing'}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
