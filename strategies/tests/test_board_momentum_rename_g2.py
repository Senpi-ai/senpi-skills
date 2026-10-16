"""Board readers (G2): thirteen templates that take a side from the 4h board call it 4h leader
momentum — not smart money — and refuse a lean whose side has fewer than `minTraderCount` of the
top traders behind it.

`leaderboard_get_markets` is the 4h board: one row per (token, dex, direction);
`pct_of_top_traders_gain` is that row's share of the top traders' unrealized PnL over the last four
hours and `trader_count` is the headcount on that side. A side taken from it is 4h leader momentum.
Smart money in this catalog means the proven cohort (discovery_get_top_traders +
discovery_get_trader_state), which none of these packages read.

Needle (a): the strategy.yaml catalog says "4h leader", and nothing the author validator reads
(strategy.yaml `catalog` + `description`, every runtime.yaml `description`, README.md) says smart
money. Needle (b): the board helper refuses a single-row lean whose trader_count is one under the
instance's declared floor and accepts it at the floor — the floor is on the LEADING side only, so
the ratio math (and a thin minority row's share of it) is unchanged. polar's floor lives in
scoring.build_thesis on the summed headcount rather than in the helper, so its needle drives the
refusal through build_thesis and pins the input and the guard line.

Run:
  python3 -m pytest strategies/tests/test_board_momentum_rename_g2.py -q
"""
import os
import re
import sys

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SMART_MONEY = re.compile(r"smart[ _-]money", re.I)

IDS = ["lynx", "magpie", "marlin", "meerkat", "piranha", "polar", "python", "salamander",
       "sheep", "stag", "wolverine", "chameleon", "spider"]


def _load(pkg, instance="main"):
    """Import a package's scanner modules with its own scanners/ dir first on sys.path."""
    d = os.path.join(_ROOT, pkg, instance, "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring
        import scan
        return scan, scoring
    finally:
        sys.path.remove(d)


def _inputs(pkg, instance):
    """The instance's real runtime.yaml inputs."""
    ry = yaml.safe_load(open(os.path.join(_ROOT, pkg, instance, "runtime.yaml")))
    return dict(next(s for s in ry["scanners"] if s.get("type") == "external_scanner")["inputs"])


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _user_text(pkg):
    """[(file, text)] — everything the validator's smart-money rule reads; strategy.yaml first."""
    root = os.path.join(_ROOT, pkg)
    sy = yaml.safe_load(open(os.path.join(root, "strategy.yaml")))
    docs = [("strategy.yaml", " ".join(_strings({k: sy.get(k) for k in ("catalog", "description")})))]
    for dp, _, fs in sorted(os.walk(root)):
        if "runtime.yaml" in fs:
            ry = yaml.safe_load(open(os.path.join(dp, "runtime.yaml")))
            docs.append((os.path.relpath(os.path.join(dp, "runtime.yaml"), root), str(ry.get("description") or "")))
    if os.path.isfile(os.path.join(root, "README.md")):
        docs.append(("README.md", open(os.path.join(root, "README.md"), errors="ignore").read()))
    return docs


# ── needle (a): the text calls the board what it is ──

@pytest.mark.parametrize("sid", IDS)
def test_catalog_says_4h_leader_and_never_smart_money(sid):
    docs = _user_text(sid)
    assert "4h leader" in docs[0][1].lower(), f"{sid}: strategy.yaml catalog does not say '4h leader'"
    hits = [(f, m.group(0)) for f, t in docs for m in [_SMART_MONEY.search(t)] if m]
    assert not hits, f"{sid}: still described as smart money in {hits}"


# ── needle (b): the board helper refuses a thin leading side ──

class _FakeMcp:
    def __init__(self, board):
        self.board = board

    def call_tool(self, name, args):
        assert name == "leaderboard_get_markets", f"unexpected tool call {name!r}"
        return self.board


class _FakeCtx:
    def __init__(self, board):
        self.senpi_mcp = _FakeMcp(board)
        self.wallet = "0x" + "ab" * 20
        self.state = None


def _board(token, dex, traders):
    """The live envelope with ONE row for `token`: a LONG side (the leading side by construction)
    with `traders` of the 4h leaders behind it."""
    return {"success": True, "data": {"markets": {"markets": [
        {"token": token, "dex": dex, "direction": "long", "max_leverage": 20,
         "pct_of_top_traders_gain": 6.0, "contribution_pct_change_15m": 0.9,
         "contribution_pct_change_1h": 1.2, "contribution_pct_change_4h": 2.0,
         "token_price_change_pct_15m": 0.2, "token_price_change_pct_1h": 0.9,
         "token_price_change_pct_4h": 3.1, "day_notional_volume": 4.2e7,
         "trader_count": traders, "is_dominant_direction": True}],
        "source_trader_count": 100, "window": "4h", "timestamp": 1789503722}}}


def _tuple_helper(name):
    return lambda scan, ctx, floor, asset: getattr(scan, name)(ctx, asset, floor)[0]


# id -> (instance, asset, read(scan, ctx, floor, asset) -> the lean or a falsy value)
CASES = {
    "lynx":               ("main", "BTC", _tuple_helper("_get_sm_direction")),
    "magpie/pre_listing": ("pre_listing", "xyz:SPCX", _tuple_helper("_fetch_sm_direction")),
    "magpie/graduation":  ("graduation", "xyz:SPCX", _tuple_helper("_fetch_sm_direction")),
    "marlin":             ("main", "BTC", _tuple_helper("_get_sm_direction")),
    "meerkat":            ("main", "ETH", _tuple_helper("_get_sm_direction")),
    "piranha":            ("main", "BTC", _tuple_helper("_get_sm_direction")),
    "python":             ("main", "BTC", lambda scan, ctx, floor, asset:
                           scan.get_sm_map(ctx, {"minTraderCount": floor}).get(asset)),
    "salamander":         ("main", "BTC", _tuple_helper("_get_sm_direction")),
    "sheep":              ("main", "BTC", _tuple_helper("_get_sm_direction")),
    "stag":               ("main", "BTC", lambda scan, ctx, floor, asset:
                           scan._sm_direction(scan._sm_markets(ctx), asset, floor)[0]),
    "wolverine":          ("main", "HYPE", lambda scan, ctx, floor, asset:
                           (scan._sm_for_asset(ctx, asset, floor) or {}).get("direction")),
    "chameleon":          ("main", "ETH", _tuple_helper("_fetch_sm_direction")),
    "spider":             ("swing", "NVDA", lambda scan, ctx, floor, asset:
                           scan._get_sm_map(ctx, floor).get(asset)),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_board_helper_refuses_a_lean_with_too_few_traders_behind_it(case):
    sid = case.split("/")[0]
    instance, asset, read = CASES[case]
    scan, _ = _load(sid, instance)
    floor = int(_inputs(sid, instance)["minTraderCount"])   # declared in the instance's runtime.yaml
    assert floor >= 10, f"{case}: minTraderCount {floor} is below the fleet floor of 10"
    token, dex = asset.split(":")[-1], ("xyz" if asset.lower().startswith("xyz:") else "")

    thin = read(scan, _FakeCtx(_board(token, dex, floor - 1)), floor, asset)
    assert not thin, f"{case}: a lean with only {floor - 1} of the 4h leaders behind it was accepted: {thin!r}"
    lean = read(scan, _FakeCtx(_board(token, dex, floor)), floor, asset)
    assert lean in ("LONG", 100.0) or (isinstance(lean, tuple) and lean[0] == "LONG"), \
        f"{case}: a lean with {floor} of the 4h leaders behind it was refused: {lean!r}"


def test_polar_floors_the_summed_headcount_in_scoring():
    scan, scoring = _load("polar", "main")
    inputs = _inputs("polar", "main")
    floor = int(inputs["minSmTraderCount"])
    assert floor >= 10
    sm = scan._sm_for_asset(_FakeCtx(_board("ETH", "", floor - 1)), "ETH")
    assert (sm["direction"], sm["traders"]) == ("LONG", floor - 1)       # the helper reports the headcount…
    assert scoring.build_thesis(None, None, None, None, 0.0, 0.0, 0.0, 0.0, sm, inputs) is None   # …the thesis refuses it
    src = open(os.path.join(_ROOT, "polar", "main", "scanners", "scoring.py")).read()
    assert 'inputs.get("minSmTraderCount", 30)' in src and 'sm.get("traders", 0)) < min_sm_traders' in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
