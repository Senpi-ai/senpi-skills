"""Board readers (G1): the 4h board is leader momentum, not smart money — and the lean is floored.

`leaderboard_get_markets` is the 4h leaderboard: one row per (token, dex, direction) whose
`pct_of_top_traders_gain` is that side's share of the top traders' unrealized PnL over the last
four hours. Whoever was on the winning side of the last four hours is at the top, so a side
taken from it is 4h leader momentum. Smart money in this catalog means the proven cohort
(`discovery_get_top_traders` / `discovery_get_trader_state`), which none of these fourteen
templates read.

Needles per template:
  (a) everything the validator reads — strategy.yaml `catalog.*` and `description`, every
      runtime.yaml `description`, README.md — says "4h leader" and never "smart money";
  (b) the floor sits on the LEADING side: the ratio math is untouched (a thin minority row
      still dilutes the tilt — it is never dropped, so a two-row name can't collapse into a
      100% single-row read), and a lean whose leading side has fewer than `minTraderCount`
      (default 10) traders is refused outright;
  (c) `minTraderCount` is declared in runtime.yaml `inputs` and read by scan().
Every helper is importable (the same sys.path idiom as test_board_last_row_overwrite.py), so
(b) drives the real function for all fourteen — no grep-only fallbacks.

Run:
  python3 -m pytest strategies/tests/test_board_momentum_rename_g1.py -q
"""
import os
import re
import sys

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SMART_MONEY = re.compile(r"smart[ _-]money", re.I)
_LEADER = re.compile(r"4h[ -]leader", re.I)

# id -> (helper name, takes ctx or a markets list)
_HELPERS = {
    "badger": ("_get_sm_direction", "ctx"),
    "beaver": ("_sm_for_asset", "ctx"),
    "bison": ("_get_sm_direction", "ctx"),
    "bobcat": ("_get_sm_direction", "ctx"),
    "egret": ("_get_sm_direction", "ctx"),
    "grizzly": ("_sm_for_asset", "ctx"),
    "hawk": ("_get_sm_direction", "ctx"),
    "hedgehog": ("_get_sm_direction", "ctx"),
    "heron": ("_sm_for_asset", "ctx"),
    "hornet": ("_get_sm_direction", "ctx"),
    "hummingbird": ("_sm_for_asset", "ctx"),
    "hyena": ("_sm_for", "markets"),
    "kodiak": ("_sm_for_asset", "ctx"),
    "lemur": ("_get_sm_direction", "ctx"),
}
IDS = sorted(_HELPERS)
# grizzly's dict carries the leading side's RAW gain share as `pct` (6.0 here), not the ratio
_EXPECT_TILT = {sid: 60.0 for sid in IDS}
_EXPECT_TILT["grizzly"] = 6.0


def _load(pkg, instance="main"):
    """Import a package's scanner modules with its own scanners/ dir first on sys.path."""
    d = os.path.join(_ROOT, pkg, instance, "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _user_facing_text(pkg):
    """The text the validator reads, as [(where, text)]."""
    root = os.path.join(_ROOT, pkg)
    out = []
    man = yaml.safe_load(open(os.path.join(root, "strategy.yaml"), encoding="utf-8")) or {}
    out += [("strategy.yaml catalog/description", s)
            for s in _strings({"catalog": man.get("catalog"), "description": man.get("description")})]
    for dirpath, _, files in os.walk(root):
        if "runtime.yaml" in files:
            doc = yaml.safe_load(open(os.path.join(dirpath, "runtime.yaml"), encoding="utf-8")) or {}
            out.append((os.path.relpath(os.path.join(dirpath, "runtime.yaml"), root) + " description",
                        str(doc.get("description") or "")))
    readme = os.path.join(root, "README.md")
    if os.path.isfile(readme):
        out.append(("README.md", open(readme, encoding="utf-8").read()))
    return out


def _row(direction, pct, traders):
    return {"token": "BTC", "dex": "", "direction": direction, "max_leverage": 40,
            "pct_of_top_traders_gain": pct, "contribution_pct_change_15m": 0.5,
            "contribution_pct_change_1h": 0.2, "contribution_pct_change_4h": None,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.3,
            "token_price_change_pct_4h": 1.0, "day_notional_volume": 5.0e8,
            "trader_count": traders, "is_dominant_direction": direction == "long"}


# a 3-trader LONG side that owns 83% of the gain, beside a 50-trader SHORT side: the leading side is thin
_WHALE_LEADS = [_row("long", 5.0, 3), _row("short", 1.0, 50)]
# a 40-trader LONG side at 60% beside a 5-trader SHORT side at 40%: the leading side clears the floor
_BROAD_LEADS = [_row("long", 6.0, 40), _row("short", 4.0, 5)]
_THIN_ONLY = [_row("long", 5.0, 3)]


class _FakeMcp:
    def __init__(self, board):
        self.board = board

    def call_tool(self, name, args):
        assert name == "leaderboard_get_markets", name
        return {"success": True, "data": {"markets": list(self.board), "source_trader_count": 100}}


class _FakeCtx:
    def __init__(self, board):
        self.senpi_mcp = _FakeMcp(board)
        self.wallet = "0x" + "ab" * 20


def _direction(result):
    """Normalise the family's return shapes: (dir, tilt) tuples, {direction: …} dicts, None."""
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get("direction")
    return result[0]


def _tilt(result):
    if isinstance(result, dict):
        return result.get("tilt", result.get("pct"))
    return result[1]


def _call(pkg, board, **kw):
    scan = _load(pkg)
    helper, kind = _HELPERS[pkg]
    fn = getattr(scan, helper)
    return fn(board, "BTC", **kw) if kind == "markets" else fn(_FakeCtx(board), "BTC", **kw)


# ── (a) the text says 4h leader momentum, never smart money ──

@pytest.mark.parametrize("pkg", IDS)
def test_user_facing_text_says_4h_leader_not_smart_money(pkg):
    docs = _user_facing_text(pkg)
    hits = [(where, m.group(0)) for where, s in docs for m in [_SMART_MONEY.search(s)] if m]
    assert not hits, f"{pkg} still says smart money in {hits}"
    catalog_text = " ".join(s for where, s in docs if where.startswith("strategy.yaml"))
    assert _LEADER.search(catalog_text), f"{pkg}: the catalog text must name the 4h leaders"


# ── (b) the floor is on the leading side ──

@pytest.mark.parametrize("pkg", IDS)
def test_a_thin_leading_side_is_refused_not_the_minority_row(pkg):
    # the 3-trader whale side leads at 83%: no lean at all — not SHORT (the minority is never promoted)
    assert _direction(_call(pkg, _WHALE_LEADS)) is None, \
        f"{pkg}: a lean was called on a 3-trader leading side"
    # a board where every row for the name is thin reads as no row at all
    assert _direction(_call(pkg, _THIN_ONLY)) is None, \
        f"{pkg}: a thin-only board produced a lean"
    # the floor is the knob: at 0 the same whale side is the lean again
    assert _direction(_call(pkg, _WHALE_LEADS, min_traders=0)) == "LONG", \
        f"{pkg}: min_traders is not the floor the helper applies"


@pytest.mark.parametrize("pkg", IDS)
def test_a_thin_minority_row_still_dilutes_the_tilt(pkg):
    """40-trader LONG at 60% beside a 5-trader SHORT at 40%: LONG with the tilt the ratio gives —
    60, not 100. The minority row is never dropped, so the ≥55/≥70 tilt gates see the real split."""
    res = _call(pkg, _BROAD_LEADS)
    assert _direction(res) == "LONG", f"{pkg}: a 40-trader leading side must call the lean"
    assert _tilt(res) == pytest.approx(_EXPECT_TILT[pkg]), \
        f"{pkg}: the 5-trader minority row was dropped from the ratio (tilt {_tilt(res)})"
    if isinstance(res, dict) and "traders" in res:
        assert res["traders"] == 40, f"{pkg}: headcount must be the leading side's own"


# ── (c) declared and wired ──

@pytest.mark.parametrize("pkg", IDS)
def test_floor_is_declared_and_wired(pkg):
    doc = yaml.safe_load(open(os.path.join(_ROOT, pkg, "main", "runtime.yaml"), encoding="utf-8"))
    inputs = [s for s in doc["scanners"] if s.get("type") == "external_scanner"][0]["inputs"]
    assert inputs.get("minTraderCount") == 10, f"{pkg}: minTraderCount not declared at 10"
    src = open(os.path.join(_ROOT, pkg, "main", "scanners", "scan.py"), encoding="utf-8").read()
    assert 'inputs.get("minTraderCount"' in src, f"{pkg}: scan() does not read minTraderCount"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
