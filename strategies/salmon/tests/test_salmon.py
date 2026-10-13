"""Salmon 1.3.0: 4 slots x 18% at up to 3x; a 12% ROE stop (4.0% of price at 3x); no clock; the
breaker does not re-arm at midnight; the card describes the exit the file actually runs.

12 Oct template audit, numbers approved: 1.2.1 ran five 6-12% slots at 2-4x behind a 6% ROE stop
(1.5% of price at 4x), with the daily entry cap AT the slot count (every re-entry after a stop-out
waited for 00:00 UTC), the drawdown breaker re-arming every day, and a card promising a "tight
trailing stop" while phase-1 trailing was off and only the gains-only ratchet ran. These tests pin
the shipped numbers, one tick against a fake desk, the degrade paths, and the three code fixes
(the scoring fallbacks, the fraction guard, the slot default).

Run: python3 -B -m pytest strategies/salmon/tests -q
"""
import os
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SCANNER = next(s for s in RUNTIME["scanners"] if s["name"] == "salmon_scanner")
INPUTS = dict(SCANNER["inputs"])
SCHEMA = SCANNER["signal_data_schema"]
NOW = time.time()


# ── the shipped numbers ───────────────────────────────────────────────────────────────────

def test_the_stop_is_twelve_roe_four_percent_of_price_and_there_is_no_clock():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["enabled"] is False                              # no trailing: the ratchet only locks a gain
    assert pre["phase1"]["max_loss_pct"] == 12.0                          # ROE
    assert pre["phase1"]["max_loss_pct"] / INPUTS["maxLeverage"] == 4.0   # 4.0% of price at the 3x cap
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0] == {"trigger_pct": 12, "lock_hw_pct": 40}
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_sizing_agrees_everywhere_and_the_rails_do_not_re_arm():
    strat = RUNTIME["strategy"]
    assert strat["slots"] == INPUTS["maxSlots"] == MANIFEST["catalog"]["max_slots"] == 4
    assert strat["margin_pct"] == INPUTS["maxMarginPct"] == 18
    assert set(INPUTS["marginPctTiers"].values()) == {18}                 # a flat slot; conviction moves leverage only
    assert strat["slots"] * strat["margin_pct"] <= 100
    assert INPUTS["maxLeverage"] == MANIFEST["catalog"]["leverage_max"] == strat["default_leverage"] == 3
    assert max(INPUTS["leverageTiers"].values()) == 3 and min(INPUTS["leverageTiers"].values()) >= 1
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > strat["slots"]                  # a stop-out can be re-entered before midnight
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert SCANNER["timeout_seconds"] < SCANNER["interval_seconds"]
    assert MANIFEST["version"] == "1.3.0"


def test_the_card_and_the_header_describe_the_file():
    card = " ".join(str(MANIFEST["catalog"][f]) for f in ("tagline", "belief_plain", "thesis")).lower()
    assert "trailing" not in card and "tight" not in card                 # phase-1 trailing is off; the ratchet runs
    desc = RUNTIME["description"]
    for stale in ("leverage <= 4", "margin <= 20", "5 positions", "8% Phase 1", "12h weak-peak", "tight"):
        assert stale not in desc, stale
    assert "12% ROE" in desc and "4.0% of price at 3x" in desc


# ── a fake desk ───────────────────────────────────────────────────────────────────────────

def _candles(closes, now=NOW):
    """1h candles in the feed's shape (o/h/l/c/v strings, t/T epoch ms), oldest first."""
    out = []
    for k, c in enumerate(closes):
        t = int((now - (len(closes) - k) * 3600) * 1000)
        out.append({"t": t, "T": t + 3600000, "s": "X", "i": "1h", "o": f"{c}", "h": f"{c + 1}",
                    "l": f"{c - 1}", "c": f"{c}", "v": "1000", "n": 1})
    return out


def _bounce():
    """A 21-bar slide (RSI -> 0) then three up bars: RSI crosses back up through 30, rising, close up."""
    return [100 - 2 * i for i in range(21)] + [64, 68, 72]


def _flat():
    """Alternating +1/-1: RSI sits at 50, never near a line — no cross."""
    return [100 + (i % 2) for i in range(26)]


_BOARD = (("BTC", 5e8), ("ETH", 4e8), ("SOL", 3e8), ("HYPE", 2e8), ("XRP", 1e8), ("xyz:CL", 6e8), ("THIN", 1e6))


class _State:
    def __init__(self):
        self._log = []

    def __len__(self):
        return len(self._log)

    def last(self):
        return self._log[-1] if self._log else None

    def append(self, d):
        self._log.append(d)


class _MCP:
    """`candles` maps a name to its 1h candles (anything else is flat); `held` is what the wallet
    holds; a tool name or an asset name in `fail` makes that read raise."""
    def __init__(self, candles, held=(), fail=()):
        self.candles, self.held, self.fail, self.calls = candles, held, set(fail), []

    def call_tool(self, tool, args):
        self.calls.append((tool, (args or {}).get("asset")))
        if tool in self.fail:
            raise RuntimeError(f"{tool} down")
        if tool == "market_list_instruments":
            return {"success": True, "data": {"instruments": [
                {"name": n, "max_leverage": 10, "is_delisted": False, "context": {"dayNtlVlm": str(v)}}
                for n, v in _BOARD]}}
        if tool == "market_get_asset_data":
            if args["asset"] in self.fail:
                raise RuntimeError("asset data down")
            return {"success": True, "data": {"candles": {"1h": self.candles.get(args["asset"], _candles(_flat()))}}}
        if tool == "strategy_get_clearinghouse_state":
            # two views of one cross-margined wallet; a held name lives under `main`
            return {"data": {"main": {"marginSummary": {"accountValue": "1000.0"},
                                      "assetPositions": [{"position": {"coin": c, "szi": "1"}} for c in self.held]},
                             "xyz": {"assetPositions": []}}}
        return {}


class _Ctx:
    def __init__(self, mcp, state=None):
        self.senpi_mcp, self.wallet, self.state, self.dry_run = mcp, "0x" + "c" * 40, state, False
        self.scanner_name, self.interval_seconds = "salmon_scanner", SCANNER["interval_seconds"]


# ── scan(): one tick, then the degrade paths ──────────────────────────────────────────────

def test_a_tick_emits_the_shipped_size_inside_the_schema_and_skips_held_and_thin_names():
    mcp = _MCP({"BTC": _candles(_bounce()), "SOL": _candles(_bounce()), "THIN": _candles(_bounce())}, held=("ETH",))
    st = _State()
    out = scan.scan(dict(INPUTS), _Ctx(mcp, st))
    by = {s["asset"]: s for s in out}
    assert set(by) == {"BTC", "SOL"}             # ETH held; HYPE/XRP flat; THIN under the floor; xyz: excluded
    for s in out:
        assert s["marginPct"] == 18 and s["leverage"] == 3 and s["direction"] == "LONG"
        assert s["data"]["band"] == "apex" and s["data"]["leverage"] == s["leverage"]
        assert set(s["data"]) <= set(SCHEMA) and all(v is not None for v in s["data"].values())
    reads = {a for t, a in mcp.calls if t == "market_get_asset_data"}
    assert "ETH" not in reads and "THIN" not in reads and "xyz:CL" not in reads   # none of them cost a candle read
    assert len(st) == 1 and st.last()["result"] == {"ts": st.last()["result"]["ts"], "opened": 2, "held": 1, "free": 1}
    assert set(st.last()["recent"]) == {"BTC", "SOL"}


def test_a_failed_read_degrades_the_tick_instead_of_crashing_it(capsys):
    # one name's candles unreadable: that name is skipped, the rest still emit
    mcp = _MCP({"BTC": _candles(_bounce()), "SOL": _candles(_bounce())}, fail=("BTC",))
    out = scan.scan(dict(INPUTS), _Ctx(mcp, _State()))
    assert [s["asset"] for s in out] == ["SOL"]
    assert "market_get_asset_data(BTC) read failed" in capsys.readouterr().err
    # the instrument list unreadable: no universe, no opens, and the tick still records its state
    st = _State()
    out = scan.scan(dict(INPUTS), _Ctx(_MCP({"BTC": _candles(_bounce())}, fail=("market_list_instruments",)), st))
    assert out == [] and len(st) == 1 and st.last()["result"]["opened"] == 0
    assert "instruments unreadable" in capsys.readouterr().err
    # the clearinghouse unreadable: we do not know what is held, so nothing is opened
    out = scan.scan(dict(INPUTS), _Ctx(_MCP({"BTC": _candles(_bounce())}, fail=("strategy_get_clearinghouse_state",)), _State()))
    assert out == []


def test_a_recent_signal_is_not_re_fired_inside_its_ttl():
    st = _State()
    st.append({"recent": {"BTC": NOW - 60}, "result": {}})
    out = scan.scan(dict(INPUTS), _Ctx(_MCP({"BTC": _candles(_bounce()), "SOL": _candles(_bounce())}), st))
    assert [s["asset"] for s in out] == ["SOL"]


# ── the code fixes: each fails on 1.2.1 ──────────────────────────────────────────────────

def test_the_scoring_fallbacks_match_the_file_not_the_old_four_x():
    # 1.2.1 fell back to {apex: 4} / 12% / cap 4 / 20% when an input was missing — bigger than the file ships.
    assert scoring.sizing_for("apex", {}) == (3, 18.0)
    assert scoring.sizing_for("base", {}) == (2, 18.0)
    assert scoring.sizing_for("apex", {"leverageTiers": {"apex": 9}}) == (3, 18.0)       # the fallback cap holds
    assert scoring.sizing_for("apex", {"marginPctTiers": {"apex": 50}})[1] == 18.0


def test_a_pasted_fraction_margin_is_a_percent_and_one_is_one_percent():
    assert scoring.sizing_for("base", dict(INPUTS, marginPctTiers={"base": 0.18}))[1] == 18.0   # 1.2.1 floored it to 1%
    assert scoring.sizing_for("base", dict(INPUTS, marginPctTiers={"base": 1}))[1] == 1.0       # 1 is a legal 1%


def test_the_slot_default_is_four():
    # five bouncing names and no maxSlots input: 1.2.1 defaulted to five slots and opened all five
    inputs = {k: v for k, v in INPUTS.items() if k != "maxSlots"}
    names = ("BTC", "ETH", "SOL", "HYPE", "XRP")
    out = scan.scan(inputs, _Ctx(_MCP({n: _candles(_bounce()) for n in names}), _State()))
    assert len(out) == 4
