"""Board readers (G4): a side read from the 4h board is "4h leader momentum", not smart money — and the
read floors on `trader_count`.

`leaderboard_get_markets` is the 4h board: one row per (token, dex, direction); `pct_of_top_traders_gain`
is that row's share of the top traders' total unrealized PnL over the last four hours (board-wide, sums to
~100); `trader_count` is the headcount on that side. A side taken from it is where the last four hours'
winners sit — 4h leader momentum. Smart money in this catalog means the proven cohort
(`discovery_get_top_traders` ≥ $1M lifetime realized + `discovery_get_trader_state`), which none of these
eight templates read. Two needles per template:

  (a) every field the validator reads — strategy.yaml `catalog.*` and `description`, each runtime.yaml
      `description`, README.md — says "4h leader" and never "smart money";
  (b) the board read floors on `trader_count` — the floor declared in runtime.yaml `inputs`
      (`minTraderCount`, or the pre-existing floor cheetah / raptor already carried) — and where it floors
      matters. A helper that folds the LONG row and the SHORT row of one asset into a ratio (barnacle,
      osprey, dragonfly, owl) keeps BOTH rows in the ratio and refuses only when the LEADING side's headcount
      is under the floor: dropping a thin minority row would turn a two-row asset into a one-row asset, the
      tilt would read 100, and the "strong tilt" bonuses would fire on exactly the thin reads the floor was
      meant to refuse. A helper that keeps one row per token in a map (otter, pangolin) refuses the
      thin row itself. cheetah / raptor gate in scoring on the row's own headcount.

Run:
  python3 -m pytest strategies/tests/test_board_momentum_rename_g4.py -q
"""
import glob
import os
import re
import sys

import pytest
import yaml

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IDS = ["barnacle", "cheetah", "dragonfly", "osprey", "otter", "owl", "pangolin", "raptor"]
_SMART_MONEY = re.compile(r"smart[ _-]money", re.I)
# floors that already existed under another name (kept, not duplicated)
_FLOOR_INPUT = {"cheetah": "smMinTraders", "raptor": "minSmTraders"}
_NEW_FLOOR = 10


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


def _strings(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, path)


def _inputs(pkg, instance="main"):
    ry = yaml.safe_load(open(os.path.join(_ROOT, pkg, instance, "runtime.yaml"), encoding="utf-8"))
    return dict(next(s for s in ry["scanners"] if s.get("type") == "external_scanner")["inputs"])


def _row(token, direction, pct, traders, dex=""):
    """One board row in the documented shape (bare token + dex)."""
    return {"token": token, "dex": dex, "direction": direction, "max_leverage": 25,
            "pct_of_top_traders_gain": pct, "contribution_pct_change_15m": 0.9,
            "contribution_pct_change_1h": 0.4, "contribution_pct_change_4h": 1.2,
            "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5,
            "token_price_change_pct_4h": 1.4, "day_notional_volume": 6.0e8,
            "trader_count": traders, "is_dominant_direction": True}


def _board(*rows):
    return {"success": True, "data": {"markets": list(rows), "source_trader_count": 100,
                                       "window": "4h", "timestamp": 1789000000}}


class _Mcp:
    def __init__(self, board):
        self.board = board

    def call_tool(self, name, args):
        assert name == "leaderboard_get_markets", f"unexpected tool call {name!r}"
        return self.board


class _Ctx:
    def __init__(self, board):
        self.senpi_mcp = _Mcp(board)
        self.wallet = "0x" + "ab" * 20
        self.state = None


# ── (a) the text ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sid", IDS)
def test_catalog_says_4h_leader_never_smart_money(sid):
    pkg = os.path.join(_ROOT, sid)
    sy = yaml.safe_load(open(os.path.join(pkg, "strategy.yaml"), encoding="utf-8"))
    docs = [("strategy.yaml", {"catalog": sy.get("catalog"), "description": sy.get("description")})]
    for rt in sorted(glob.glob(os.path.join(pkg, "**", "runtime.yaml"), recursive=True)):
        docs.append((os.path.relpath(rt, pkg), {"description": yaml.safe_load(open(rt, encoding="utf-8")).get("description")}))
    readme = os.path.join(pkg, "README.md")
    if os.path.isfile(readme):
        docs.append(("README.md", {"text": open(readme, encoding="utf-8", errors="ignore").read()}))

    hits = [(f, field, m.group(0)) for f, d in docs for field, s in _strings(d)
            for m in [_SMART_MONEY.search(s)] if m]
    assert hits == [], f"{sid} still describes the 4h board as smart money: {hits}"

    catalog_text = " ".join(s for _, s in _strings(sy["catalog"])).lower()
    assert "4h leader" in catalog_text, f"{sid}: the catalog never names the signal it reads (4h leader)"
    tags = [str(t).lower() for t in sy["catalog"].get("tags") or []]
    assert not any(_SMART_MONEY.search(t) for t in tags)
    if sid != "osprey":   # osprey's tags never carried the claim; the others swapped it for leader-momentum
        assert "leader-momentum" in tags


# ── (b) the floor ─────────────────────────────────────────────────────────────

def _floor(sid, instance="main"):
    inputs = _inputs(sid, instance)
    key = _FLOOR_INPUT.get(sid, "minTraderCount")
    assert key in inputs, f"{sid}/{instance}: runtime.yaml declares no trader_count floor ({key})"
    floor = int(inputs[key])
    assert floor >= _NEW_FLOOR
    if key == "minTraderCount":
        assert floor == _NEW_FLOOR
    return floor, inputs


def _probe_ratio(sid, instance, read, token, dex=""):
    """The three helpers that fold LONG + SHORT rows into one ratio. `read(scan, ctx, floor)` returns
    (direction, tilt) or None. The ratio math is untouched; the only new outcome is a refusal."""
    floor, _ = _floor(sid, instance)
    scan, _ = _load(sid, instance)
    row = lambda side, pct, n: _row(token, side, pct, n, dex=dex)      # noqa: E731
    lean = lambda board, f=floor: read(scan, _Ctx(board), f)             # noqa: E731

    # (a) a 3-trader LONG row owning 83% of the gain beside a 50-trader SHORT row: the lean would be
    #     LONG, but 3 leaders do not make a lean — and dropping that row must NOT flip it to SHORT
    assert lean(_board(row("long", 83.0, 3), row("short", 17.0, 50))) is None, f"{sid}/{instance} (a)"
    # (b) a 40-trader LONG row at 60% beside a 5-trader SHORT row at 40%: LONG at the tilt the ratio
    #     gives (60) — the minority side's thinness never changes the number (never 100)
    d, tilt = lean(_board(row("long", 60.0, 40), row("short", 40.0, 5)))
    assert d == "LONG" and tilt == pytest.approx(60.0), f"{sid}/{instance} (b): {d} {tilt}"
    # (c) only thin rows for the asset: no lean, one-sided or two-sided
    assert lean(_board(row("long", 4.1, floor - 1))) is None, f"{sid}/{instance} (c)"
    assert lean(_board(row("long", 4.1, floor - 1), row("short", 9.0, floor - 1))) is None, f"{sid}/{instance} (c)"
    # (d) min_traders=0: the floor is off and the lean returns
    d, tilt = lean(_board(row("long", 83.0, 3), row("short", 17.0, 50)), 0)
    assert d == "LONG" and tilt == pytest.approx(83.0), f"{sid}/{instance} (d): {d} {tilt}"
    # a single thick row is still a full tilt: the floor refuses, it never rescales
    assert lean(_board(row("long", 4.1, floor))) == ("LONG", 100.0), f"{sid}/{instance}"


def _read_pair(scan, ctx, floor, asset):
    d, tilt = scan._get_sm_direction(ctx, asset, floor)
    return None if d is None else (d, tilt)


def _probe_barnacle():
    _probe_ratio("barnacle", "main", lambda s, c, f: _read_pair(s, c, f, "xyz:NVDA"), "NVDA", dex="xyz")


def _probe_cheetah():
    floor, inputs = _floor("cheetah")            # smMinTraders: the hard gate in score_confluence
    _, scoring = _load("cheetah")
    market = {"token": "ETH", "dex": "", "rank": 1, "direction": "LONG", "pct": 12.0, "traders": floor - 1,
              "price_chg_4h": 3.0, "price_chg_1h": 0.5, "contrib_15m": 1.5, "contrib_1h": 4.0,
              "volume": 0.0, "avg_volume_6h": 0.0}
    assert scoring.score_confluence(market, {}, 0, inputs) == (0, [])
    score, reasons = scoring.score_confluence(dict(market, traders=floor), {}, 0, inputs)
    assert score > 0 and reasons


def _read_dragonfly(scan, ctx, floor):
    r = scan._sm_for_asset(ctx, "BTC", floor)
    return None if r is None else (r["direction"], r["pct"])


def _probe_dragonfly():
    for instance in ("btc", "hype"):
        _probe_ratio("dragonfly", instance, _read_dragonfly, "BTC")
        # the aggregation is exactly as it was: `traders` still counts BOTH sides (40 + 5)
        floor, _ = _floor("dragonfly", instance)
        scan, _ = _load("dragonfly", instance)
        r = scan._sm_for_asset(_Ctx(_board(_row("BTC", "long", 60.0, 40), _row("BTC", "short", 40.0, 5))), "BTC", floor)
        assert r["traders"] == 45, instance


def _probe_osprey():
    _probe_ratio("osprey", "main", lambda s, c, f: _read_pair(s, c, f, "xyz:COIN"), "COIN", dex="xyz")


def _probe_otter():
    floor, inputs = _floor("otter")
    scan, _ = _load("otter")
    sm = scan.fetch_sm_map(_Ctx(_board(_row("HYPE", "short", 25.63, floor - 1),
                                       _row("SOL", "long", 3.6, floor))), inputs)
    assert "HYPE" not in sm and sm["SOL"]["direction"] == "LONG"
    assert sm["SOL"]["pct"] == 3.6, "pct_of_top_traders_gain is already a percent — no ×100"


def _read_owl(scan, ctx, floor):
    sm, _btc = scan.fetch_sm_positioning_map(ctx, {"minTraderCount": floor})
    if "BTC" not in sm:
        return None
    long_share = sm["BTC"][0]
    return ("LONG", long_share) if long_share >= 50 else ("SHORT", 100 - long_share)


def _probe_owl():
    _probe_ratio("owl", "main", _read_owl, "BTC")


def _probe_pangolin():
    floor, _ = _floor("pangolin")
    scan, _ = _load("pangolin")
    sm = scan._get_sm_map(_Ctx(_board(_row("HYPE", "short", 25.63, floor - 1),
                                      _row("SOL", "long", 3.6, floor))), floor)
    assert "HYPE" not in sm and sm["SOL"]["direction"] == "long"


def _probe_raptor():
    floor, inputs = _floor("raptor")                # minSmTraders: the gate in scoring.sm_gate
    _, scoring = _load("raptor")
    pos = {"direction": "LONG"}
    assert scoring.sm_gate({"direction": "LONG", "pct": 5.0, "traders": floor - 1}, pos, inputs) is False
    assert scoring.sm_gate({"direction": "LONG", "pct": 5.0, "traders": floor}, pos, inputs) is True


_PROBES = {"barnacle": _probe_barnacle, "cheetah": _probe_cheetah, "dragonfly": _probe_dragonfly,
           "osprey": _probe_osprey, "otter": _probe_otter, "owl": _probe_owl,
           "pangolin": _probe_pangolin, "raptor": _probe_raptor}


@pytest.mark.parametrize("sid", IDS)
def test_board_read_refuses_a_thin_side(sid):
    _PROBES[sid]()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
