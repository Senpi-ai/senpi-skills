"""Board readers (G3): eleven templates call the 4h board what it is, and floor the read on trader_count.

`leaderboard_get_markets` is the 4h board: one row per (token, dex, direction), `pct_of_top_traders_gain`
is that row's share of the top traders' unrealized PnL over the last four hours (board-wide, sums to
~100), `trader_count` the headcount on that side, `is_dominant_direction` the larger side. A side taken
from it is 4h leader momentum — whoever is winning right now — not smart money, which in this catalog
means the proven cohort (`discovery_get_top_traders` >= $1M lifetime realized + `discovery_get_trader_state`)
that none of these templates read. Two needles per template:

  (a) the strategy.yaml catalog says "4h leader", and nothing the validator reads — strategy.yaml
      `catalog` + `description`, every runtime.yaml `description`, README.md — says smart money;
  (b) the board read refuses a side whose `trader_count` is below the template's declared floor, so a
      thin side never sets the lean. Per-row readers (a dominant-side row, the best row per token, a
      rank order, per-row scoring) refuse the row at the read itself; condor, lemon and vulture keep
      their existing floor in scoring.py (the row is read, then refused one call later, before anything
      leans on it) and are tested at that gate. raccoon's helper turns the LONG and SHORT rows into a
      ratio, so there the floor sits on the side the ratio picks: both rows always count (a dropped
      minority row would read as a 100% tilt and trip the strong-tilt bonus) and a thin lean is
      refused, never inflated.

Run:
  python3 -m pytest strategies/tests/test_board_momentum_rename_g3.py -q
"""
import os
import re
import sys

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

IDS = ["bald-eagle", "condor", "dog", "kestrel", "lemon", "jaguar", "orca", "roach", "scorpion", "vulture", "raccoon"]

# (runtime.yaml input, floor) — the templates that already floored at or above 10 keep theirs.
FLOORS = {
    "bald-eagle": ("minTraderCount", 10),
    "condor": ("minTraderCount", 50),
    "dog": ("minTraderCount", 30),
    "kestrel": ("minTraderCount", 10),
    "lemon": ("minSmTraders", 20),
    "jaguar": ("minTraderCount", 10),
    "orca": ("minTraderCount", 10),
    "roach": ("minTraderCount", 10),
    "scorpion": ("minTraderCount", 10),
    "vulture": ("minSmTraders", 15),
    "raccoon": ("minTraderCount", 10),
}

_SMART_MONEY = re.compile(r"smart[ _-]money", re.I)
_LEADER = re.compile(r"4h[ -]leader", re.I)


# ── helpers ──

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


def _strings(obj, path=""):
    """(dotted_path, text) for every string in a parsed YAML value — the validator's walk."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, path)


def _user_text(sid):
    """[(file, field, text)] — everything a user reads: strategy.yaml `catalog` + `description`, each
    runtime.yaml `description`, README.md."""
    pkg = os.path.join(_ROOT, sid)
    with open(os.path.join(pkg, "strategy.yaml"), encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    docs = [("strategy.yaml", {k: d.get(k) for k in ("catalog", "description")})]
    for dirpath, _dirs, files in sorted(os.walk(pkg)):
        if "runtime.yaml" in files:
            p = os.path.join(dirpath, "runtime.yaml")
            with open(p, encoding="utf-8") as f:
                rt = yaml.safe_load(f) or {}
            docs.append((os.path.relpath(p, pkg), {"description": rt.get("description")}))
    readme = os.path.join(pkg, "README.md")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8", errors="ignore") as f:
            docs.append(("README.md", {"text": f.read()}))
    return [(fname, field, s) for fname, doc in docs for field, s in _strings(doc)]


def _declared_input(sid, name):
    with open(os.path.join(_ROOT, sid, "main", "runtime.yaml"), encoding="utf-8") as f:
        rt = yaml.safe_load(f)
    for sc in rt["scanners"]:
        if sc.get("type") == "external_scanner":
            return sc["inputs"][name]
    raise AssertionError(f"{sid}: no external_scanner in runtime.yaml")


def _row(token, direction, traders, dex="", pct=6.0, dominant=True, **kw):
    """One board row in the documented shape (fixtures/leaderboard_get_markets.json)."""
    r = {"token": token, "dex": dex, "direction": direction, "max_leverage": 20,
         "pct_of_top_traders_gain": pct, "contribution_pct_change_15m": 1.0,
         "contribution_pct_change_1h": 0.5, "contribution_pct_change_4h": 2.0,
         "token_price_change_pct_15m": 0.2, "token_price_change_pct_1h": 0.6,
         "token_price_change_pct_4h": 2.5, "day_notional_volume": 25_000_000.0,
         "trader_count": traders, "is_dominant_direction": dominant}
    r.update(kw)
    return r


def _board(rows):
    return {"success": True, "data": {"markets": {"markets": rows, "source_trader_count": 100,
                                                  "window": "4h", "timestamp": 1789503749}}}


class _FakeState:
    def __init__(self):
        self.rows = []

    def __len__(self):
        return len(self.rows)

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def recent(self, n):
        return self.rows[-n:]


class _FakeMcp:
    def __init__(self, responses):
        self.responses = responses

    def call_tool(self, name, args):
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        return self.responses[name]


class _FakeCtx:
    def __init__(self, responses, wallet="0x" + "ab" * 20):
        self.senpi_mcp = _FakeMcp(responses)
        self.wallet = wallet
        self.state = _FakeState()


# ── (a) the text ──

@pytest.mark.parametrize("sid", IDS)
def test_catalog_says_4h_leader_and_nothing_says_smart_money(sid):
    texts = _user_text(sid)
    catalog = " ".join(s for fname, field, s in texts if fname == "strategy.yaml" and field.startswith("catalog."))
    assert _LEADER.search(catalog), f"{sid}: the catalog never says '4h leader'"
    hits = [(fname, field, m.group(0)) for fname, field, s in texts for m in [_SMART_MONEY.search(s)] if m]
    assert not hits, f"{sid}: still described as smart money: {hits}"


# ── (b) the read refuses a thin side ──

def _bald_eagle(scan, floor):
    # the thin LONG row holds the bigger share and would win the best-row-per-token pass without the gate
    board = _board([_row("GOLD", "long", floor - 1, dex="xyz", pct=9.0),
                    _row("GOLD", "short", floor, dex="xyz", pct=4.0, dominant=False),
                    _row("CL", "long", floor - 1, dex="xyz", pct=8.0)])
    cands = scan._scan_xyz_sm(_FakeCtx({"leaderboard_get_markets": board}), ["GOLD", "CL"], floor)
    assert [(c["token"], c["direction"], c["traders"]) for c in cands] == [("GOLD", "SHORT", floor)]


def _condor(scan, floor):
    sm = {"direction": "LONG", "gain_share_pct": 15.0, "is_dominant": True, "traders": floor,
          "p4h": 6.0, "p1h": 1.0, "c15m": 2.0, "c1h": 1.0}
    asset = {"coin": "ETH", "oi_usd": 5e6, "volume_24h": 1e8, "price": 100.0, "funding": 0.0}
    deep = scan.scoring.evaluate_trend_continuation(asset, dict(sm), None, 14, {"minTraderCount": floor})
    thin = scan.scoring.evaluate_trend_continuation(asset, dict(sm, traders=floor - 1), None, 14, {"minTraderCount": floor})
    assert deep and deep["direction"] == "LONG" and thin is None


def _dog(scan, floor):
    def run(rows):
        ctx = _FakeCtx({
            "strategy_get_clearinghouse_state": {"data": {"main": {
                "marginSummary": {"accountValue": "1000"}, "assetPositions": []}}},
            "leaderboard_get_markets": _board(rows),
            "market_get_funding_regime": {"data": {"regime": "SHORT_CROWDED"}},
            "market_get_funding_history": {"data": {"data": []}},
            "market_get_asset_data": {"data": {"asset_context": {"funding": -0.0005}}},
        })
        return scan.scan({}, ctx)

    # the thin LONG row holds the bigger share and would win the highest-share pass without the gate
    thin = _row("BTC", "long", floor - 1, pct=20.0, token_price_change_pct_4h=3.5)
    deep = _row("BTC", "short", floor, pct=10.0, dominant=False, token_price_change_pct_4h=-3.5,
                token_price_change_pct_1h=-0.5, contribution_pct_change_1h=1.5)
    out = run([thin, deep])
    assert [(o["asset"], o["direction"], o["data"]["smDirection"]) for o in out] == [("BTC", "LONG", "SHORT")]
    assert run([thin]) == []


def _kestrel(scan, floor):
    board = _board([_row("NVDA", "long", floor - 1, dex="xyz"), _row("GOLD", "short", floor, dex="xyz")])
    sm = scan._fetch_sm_xyz_map(_FakeCtx({"leaderboard_get_markets": board}), floor)
    assert set(sm) == {"GOLD"} and sm["GOLD"]["trader_count"] == floor


def _lemon(scan, floor):
    sm = {"asset": "SOL", "is_xyz": False, "direction": "LONG", "pct": 20.0, "traders": floor,
          "price_chg_4h": 3.5, "price_chg_1h": -0.2, "contrib_15m": -2.5, "contrib_1h": -1.0, "contrib_4h": -2.0}
    deep = scan.scoring.evaluate_fade(dict(sm), 0.0, None, False, {"minSmTraders": floor})
    thin = scan.scoring.evaluate_fade(dict(sm, traders=floor - 1), 0.0, None, False, {"minSmTraders": floor})
    assert deep and deep["direction"] == "SHORT" and thin is None


def _jaguar(scan, floor):
    ctx = _FakeCtx({"leaderboard_get_markets": _board([_row("ZEC", "long", floor - 1), _row("ETH", "long", floor)])})
    assert scan.scan({}, ctx) == []                                   # first tick seeds the history
    snap = ctx.state.rows[-1]["scan"]["markets"]
    assert [(m["token"], m["rank"], m["traders"]) for m in snap] == [("ETH", 1, floor)]


def _orca(scan, floor):
    board = _board([_row("ZEC", "long", floor - 1), _row("ETH", "long", floor)])
    markets = scan._fetch_markets(_FakeCtx({"leaderboard_get_markets": board}), 100, 50, True, floor)
    assert [(m["token"], m["rank"], m["traders"]) for m in markets] == [("ETH", 2, floor)]   # rank = board index


def _roach(scan, floor):
    ctx = _FakeCtx({"leaderboard_get_markets": _board([_row("ZEC", "long", floor - 1), _row("ETH", "long", floor)])})
    assert scan.scan({}, ctx) == []                                   # first tick seeds the history
    snap = ctx.state.rows[-1]["scans"][-1]["markets"]
    assert [(m["token"], m["rank"], m["traders"]) for m in snap] == [("ETH", 2, floor)]      # rank = board index


def _scorpion(scan, floor):
    board = _board([_row("BTC", "long", floor - 1), _row("ETH", "long", floor)])
    rows = scan._get_markets(_FakeCtx({"leaderboard_get_markets": board}), floor)
    assert [(m["token"], m["trader_count"]) for m in rows] == [("ETH", floor)]


def _vulture(scan, floor):
    row = _row("HYPE", "long", floor, pct=18.0, token_price_change_pct_4h=8.0, token_price_change_pct_1h=1.0,
               contribution_pct_change_15m=3.0, contribution_pct_change_1h=1.0)
    tiers = [{"min_score": 11, "leverage": 7, "label": "apex"}, {"min_score": 10, "leverage": 5, "label": "conviction"}]
    wl = scan.scoring.DEFAULT_WHITELIST
    deep = scan.scoring.score_market(dict(row), None, {}, wl, tiers, {"minSmTraders": floor})
    thin = scan.scoring.score_market(dict(row, trader_count=floor - 1), None, {}, wl, tiers, {"minSmTraders": floor})
    assert deep and deep["asset"] == "HYPE" and thin is None


def _raccoon(scan, floor):
    # a ratio helper: the floor sits on the side the ratio picks, never on the rows — the minority row
    # still counts, so a thin lean is refused, never inflated to 100
    rows = [_row("NVDA", "long", 3, dex="xyz", pct=5.0), _row("NVDA", "short", 50, dex="xyz", pct=1.0, dominant=False),
            _row("GOLD", "long", 40, dex="xyz", pct=6.0), _row("GOLD", "short", 5, dex="xyz", pct=4.0, dominant=False),
            _row("CL", "long", floor - 1, dex="xyz", pct=6.0)]
    sm = scan._fetch_sm_map(_FakeCtx({"leaderboard_get_markets": _board(rows)}))
    assert sm["XYZ:NVDA"] == (5.0, 1.0, 3, 50) and sm["XYZ:GOLD"] == (6.0, 4.0, 40, 5)   # both rows kept, headcount per side
    assert scan._sm_direction(sm, "xyz:NVDA", floor) == (None, 0.0)      # a 3-trader LONG leads at 83% -> no lean
    assert scan._sm_direction(sm, "xyz:GOLD", floor) == ("LONG", 60.0)   # a 40-trader LONG leads at 60 -> 60, not 100
    assert scan._sm_direction(sm, "xyz:CL", floor) == (None, 0.0)        # a thin-only row -> no lean
    lean = scan._sm_direction(sm, "xyz:NVDA", 0)                          # floor 0 -> the lean returns, ratio untouched
    assert lean[0] == "LONG" and round(lean[1], 1) == 83.3


_READS = {"bald-eagle": _bald_eagle, "condor": _condor, "dog": _dog, "kestrel": _kestrel, "lemon": _lemon,
          "jaguar": _jaguar, "orca": _orca, "roach": _roach, "scorpion": _scorpion, "vulture": _vulture,
          "raccoon": _raccoon}


@pytest.mark.parametrize("sid", IDS)
def test_board_read_refuses_a_side_below_the_trader_count_floor(sid):
    name, floor = FLOORS[sid]
    assert _declared_input(sid, name) == floor, f"{sid}: runtime.yaml does not declare {name}: {floor}"
    scanners = os.path.join(_ROOT, sid, "main", "scanners")
    src = "".join(open(os.path.join(scanners, f), encoding="utf-8").read() for f in ("scan.py", "scoring.py"))
    assert f'.get("{name}"' in src, f"{sid}: the scanner never reads {name}"
    _READS[sid](_load(sid), floor)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
