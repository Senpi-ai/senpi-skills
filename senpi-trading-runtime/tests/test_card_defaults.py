"""Every catalog card carries `defaults`: how each wallet is set out of the box, read from the package.

17 Nov 2026, blind re-test through the Senpi connector: the agent picked Mustang S&P for "long the S&P
500 with some leverage, above its 200-day average, ~$500" and said margin is "~4% of account ... the card
doesn't specify a fixed marginPct". The package opens 30% of free margin at 3x with one slot: $150 of
margin, a $450 position. 99 of 145 cards never stated a size. These tests pin the field the agent now
quotes (card_defaults.py, via gen_catalog.py) to the packages, case by case where a size differs by signal.

    python3 -m pytest senpi-trading-runtime/tests/test_card_defaults.py -q
"""
import glob
import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-trading-runtime", "scripts"))
import card_defaults as CD  # noqa: E402

CATALOG = {r["id"]: r for r in json.load(open(os.path.join(ROOT, "strategies", "catalog.json")))["skills"]}


def _package(sid):
    pkg = os.path.join(ROOT, "strategies", sid)
    man = yaml.safe_load(open(os.path.join(pkg, "strategy.yaml")))
    rts = {i["name"]: yaml.safe_load(open(os.path.join(pkg, i["runtime"]))) for i in man["instances"]}
    return man, rts, pkg


def _wallets(sid):
    return {w["wallet"]: w for w in CATALOG[sid]["defaults"]["wallets"]}


def _sizes(sid, wallet="main"):
    return [(c["margin_pct"], c["of"], c["leverage"]) for c in _wallets(sid)[wallet]["size"]]


@pytest.mark.parametrize("sid", sorted(CATALOG))
def test_every_record_states_its_defaults_and_matches_the_package(sid):
    """One entry per wallet, in manifest order, each with a size, max positions, the stop, the profit
    locks and the cadence; and the committed catalog equals a fresh read of the package, so a retune
    that skips gen_catalog cannot leave a card quoting the old numbers."""
    man, rts, pkg = _package(sid)
    got = CATALOG[sid].get("defaults")
    assert got == CD.record_defaults(sid, man, rts, pkg), f"{sid}: catalog `defaults` is stale — run gen_catalog.py"
    assert [w["wallet"] for w in got["wallets"]] == [i["name"] for i in man["instances"]]
    for w in got["wallets"]:
        assert w["size"] and all({"when", "margin_pct", "of", "leverage"} <= set(c) for c in w["size"]), w
        assert w["max_positions"] and w["stop_loss_roe_pct"] and w["profit_locks"] and w["cadence_seconds"], w
        for c in w["size"]:
            assert c["margin_pct"] is not None or c.get("set_by"), f"{sid}/{w['wallet']}: a null size says what sets it"


def test_a_scanner_sizing_key_needs_a_sizing_entry():
    """The default reading is one flat marginPct at one leverage. A scanner whose inputs carry a tier, a
    weight, a per-market table or a second leg would be flattened by it, so it must be described in
    card_defaults.SIZING, written from the scanner's own code."""
    missing = []
    for sid in CATALOG:
        man, rts, _ = _package(sid)
        for name, rt in rts.items():
            keys = sorted(set(CD._scanner_inputs(rt)) & set(CD.SIZING_KEYS))
            if keys and sid not in CD.SIZING:
                missing.append(f"{sid}/{name}: {keys}")
    assert not missing, "add these to card_defaults.SIZING (read the scanner first):\n" + "\n".join(missing)


def test_mustang_sp500_is_30pct_of_free_margin_at_3x_one_position():
    w = _wallets("mustang-sp500")["main"]
    assert _sizes("mustang-sp500") == [(30, "free margin", 3)]
    assert w["max_positions"] == 1 and w["stop_loss_roe_pct"] == 30 and w["universe"] == ["xyz:SP500"]
    assert w["profit_locks"][0] == {"at_roe_pct": 15, "keeps_pct_of_peak_gain": 30}


def test_mustang_semis_is_20pct_at_1x():
    assert _sizes("mustang-semis") == [(20, "free margin", 1)]
    assert _wallets("mustang-semis")["main"]["max_positions"] == 1


def test_kudu_is_4pct_of_the_account_at_2x_per_name_plus_the_12pct_3x_washout_leg():
    """Kudu converts a share of the ACCOUNT into the free-margin percent it emits (scan.py: clip =
    account_value * pct / 100), so every basket name is the same size; the washout leg is its own case."""
    assert _sizes("kudu") == [(4, "account", 2), (12, "account", 3)]
    assert _wallets("kudu")["main"]["max_positions"] == 11


def test_yak_is_15pct_of_the_account_per_sleeve_at_each_markets_leverage():
    sizes = _sizes("yak")
    assert {m for m, _, _ in sizes} == {15} and {o for _, o, _ in sizes} == {"account"}
    lev = {a: c["leverage"] for c in _wallets("yak")["main"]["size"] for a in c["markets"]}
    assert lev == {"xyz:XYZ100": 2, "xyz:SP500": 2, "xyz:GOLD": 2, "xyz:CL": 1, "xyz:SILVER": 1}


def test_a_multi_wallet_fund_states_each_wallets_share():
    shares = [w["funding_share"] for w in CATALOG["squirrel"]["defaults"]["wallets"]]
    assert shares == [0.17, 0.33, 0.5]


def test_only_enabled_clock_exits_are_stated():
    rt = {"exit": {"dsl_preset": {"phase1": {"max_loss_pct": 20}, "phase2": {"tiers": []},
                                  "hard_timeout": {"enabled": True, "interval_in_minutes": 1440},
                                  "dead_weight_cut": {"enabled": False, "interval_in_minutes": 60}}}}
    assert CD.exits(rt)["time_exits"] == {"closes_after_min": 1440}
    rt["exit"]["dsl_preset"]["hard_timeout"]["enabled"] = False
    assert "time_exits" not in CD.exits(rt)


def test_tables_kept_in_scanner_code_are_read_from_it():
    """Some engines keep their sizing in code, not config: bison steps marginPctBase by score in
    scoring.margin_tier_pct (x1.25 from 11, x1.5 from 12, and its minScore 11 makes the base unreachable),
    and athena-x's phalanx sleeve is configured at 5x but scan.py holds leverage to _MAX_LEVERAGE = 4."""
    assert [(c["when"], c["margin_pct"], c["leverage"]) for c in _wallets("bison")["main"]["size"]] == [
        ("score 12+", 37.5, 5), ("score 11 to under 12", 31.25, 5)]
    assert {c["leverage"] for c in _wallets("athena-x")["phalanx"]["size"]} == {4}


def test_a_sizing_function_of_an_unknown_shape_stops_the_build(tmp_path):
    scanners = tmp_path / "main" / "scanners"
    scanners.mkdir(parents=True)
    (scanners / "scoring.py").write_text("def margin_tier_pct(score, base):\n"
                                         "    if score > 9:\n        return base * 2\n    return base\n")
    with pytest.raises(ValueError, match="not a ladder"):
        CD.code_steps({"pkg_dir": str(tmp_path), "inst_dir": "main"}, "margin_tier_pct")


def test_max_positions_never_exceeds_the_markets_it_can_hold():
    """The runtime holds one position per market per wallet: ox core has 10 slots and 9 sleeves."""
    assert _wallets("ox")["core"]["max_positions"] == 9
