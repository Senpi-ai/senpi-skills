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
    rungs = [(float(a), float(b)) for a, b in
             re.findall(r"Up ([\d.]+)% . your stop moves to \*\*\+([\d.]+)%\*\*", _GUIDE)]
    assert rungs, "no 'Up N% -> your stop moves to +M%' lines found — template gone or reworded"

    for up, promised in rungs:
        active = max((lock for trig, lock in _tiers(_WORKED) if up >= trig), default=None)
        assert active is not None, f"template claims a stop at +{up:g}% but no tier has fired there"
        assert up * active / 100 == pytest.approx(promised), (
            f"template says 'up {up:g}% -> stop at +{promised:g}%'; {_WORKED} gives "
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
def test_the_time_cut_table_names_every_cut_and_its_real_duration(name):
    """The durations are what the agent reads out to the user, so they are the payload.

    Shipped with two wrong rows — mean_reversion's 2h weak_peak_cut written as 6h (copied from
    balanced), and scalp's 45m dead_weight_cut written as 8h, longer than its own 90m
    hard_timeout — under a none-vs-some check that could not see either.
    """
    dsl = _PRESETS[name]["dsl_preset"]
    live = {k: dsl[k]["interval_in_minutes"]
            for k in ("hard_timeout", "weak_peak_cut", "dead_weight_cut")
            if isinstance(dsl.get(k), dict) and dsl[k].get("enabled")}
    row = re.search(rf"^\| `{re.escape(name)}` \| (.+?) \|$", _GUIDE, re.M)
    assert row, f"{name} has no row in the time-cut table"
    text = row.group(1)

    if not live:
        assert text.strip() == "none", f"table says {name} is '{text.strip()}' but it has no cuts"
        return

    for cut, minutes in live.items():
        m = re.search(rf"`{cut} (\d+(?:\.\d+)?)([mh])`", text)
        assert m, f"{name} row must name `{cut} <duration>`; got '{text}'"
        shown = float(m.group(1)) * (60 if m.group(2) == "h" else 1)
        assert shown == minutes, \
            f"{name} row says {cut} is {m.group(1)}{m.group(2)}; preset has {minutes}min"
    for cut in set(("hard_timeout", "weak_peak_cut", "dead_weight_cut")) - set(live):
        assert f"`{cut} " not in text, f"{name} row names {cut}, but the preset does not enable it"


@pytest.mark.parametrize("doc", sorted(_REFS.glob("*.md")), ids=lambda p: p.name)
def test_no_reference_teaches_a_dropped_default(doc):
    """Every YAML example an agent might copy. dsl-configuration.md drifted for weeks teaching
    `phase1.enabled: true` and breakeven rungs after the fleet dropped both; the presets file said
    so and nothing checked. One grep beats re-reading five files."""
    text = doc.read_text(encoding="utf-8")
    assert "lock_hw_pct: 0 " not in text and "lock_hw_pct: 0}" not in text, \
        f"{doc.name} shows a `lock_hw_pct: 0` rung — exits flat, still pays fees, dropped fleet-wide"
    assert "enabled: true, max_loss_pct" not in text and "phase1:\n  enabled: true" not in text, \
        f"{doc.name} shows `phase1.enabled: true` — trailing is off fleet-wide (ratchets into a loss)"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
