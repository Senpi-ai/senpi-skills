"""Regression guard: the demand-driven strategies must surface for the way users
actually prompt for them (from senpi-strategy-creation-prompts-2026-07-13.csv).
Each is expected at rank 1 among the theme-ranked survivors; the bar asserted here
is top-3 (leaves headroom for future catalog growth). Runs offline on the bundled
catalog — no MCP. See discover.py `_configurable` (gecko) + ASSET_SYN (gold/xauusd)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

_CATALOG = os.path.join(HERE, "..", "catalog.json")
_RECORDS = json.load(open(_CATALOG))["skills"]


def _intent(assets=None, direction=None, exclude=None):
    return {"direction": direction, "assets": assets or [], "exclude": exclude or [],
            "budget": None, "_broadened_classes": None}


def _rank(target, itn, theme):
    res = discover.match(itn, _RECORDS)
    if theme:
        discover.apply_theme(res, theme)
    ids = [c["id"] for c in res.get("candidates", [])]
    return (ids.index(target) + 1) if target in ids else None


# (target, intent, theme) mirroring the real user prompts
_CASES = [
    ("viper",     _intent(), "smc ict market structure"),
    ("salmon",    _intent(), "mean reversion rsi oversold buy the dip"),
    ("armadillo", _intent(), "low risk capital preservation conservative"),
    ("starling",  _intent(), "follow smart money rotation"),
    ("ant",       _intent(), "funding carry cash and carry harvest"),
    ("raven",     _intent(), "adaptive self tuning momentum learns"),
    ("ram",       _intent(assets=[("class", "commodities")]), "gold xauusd metals"),
    ("gecko",     _intent(assets=[("named", "VVV")]), "any coin"),
    # ── demand-driven builds from the 2026-08-04 verbatim-prompts doc ──
    ("shadow",   _intent(), "mirror multiple traders fresh entry copy the top traders"),
    ("rotator",  _intent(), "rebalance conviction every few hours concentrated smart money"),
    ("mandate",  _intent(), "capital preservation no leverage diversified portfolio rwa"),
    ("hare",     _intent(), "scalp btc eth session quick in and out leverage"),
    ("kite",     _intent(), "smc ict break of structure fibonacci divergence"),
]


def test_each_new_strategy_ranks_top3_for_its_prompt():
    for target, itn, theme in _CASES:
        rank = _rank(target, itn, theme)
        assert rank is not None, f"{target} was filtered OUT for its own query"
        assert rank <= 3, f"{target} ranked {rank} (>3) for its query — discoverability regressed"


def test_gecko_survives_any_named_asset():
    # gecko is the configurable catch-all: it must survive a named-asset filter for a coin
    # no fixed template covers, rather than being hard-rejected.
    for coin in ("VVV", "LIT", "WIF", "PEPE"):
        res = discover.match(_intent(assets=[("named", coin)]), _RECORDS)
        ids = [c["id"] for c in res.get("candidates", [])]
        assert "gecko" in ids, f"gecko filtered out for named {coin} — configurability broken"


def test_gold_resolves_from_gold_and_xauusd():
    w = []
    assert ("class", "commodities") in discover._norm_assets("gold", w)
    assert ("class", "commodities") in discover._norm_assets("xauusd", w)
    assert ("class", "commodities") in discover._norm_assets("XAU", w)


if __name__ == "__main__":
    test_each_new_strategy_ranks_top3_for_its_prompt()
    test_gecko_survives_any_named_asset()
    test_gold_resolves_from_gold_and_xauusd()
    print("ALL DISCOVERABILITY TESTS PASS")
