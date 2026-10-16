"""Tortoise 2.0.0: one position per asset; the re-buy clock restarts at the EXIT; a wide stop; no clock cut.

1.x keyed the schedule to the BUY time, so once a position had been held longer than the interval
the tick after a stop-out saw the name flat and overdue and bought it straight back — about 30
minutes after the exit, into the same move. It also carried a 10% ROE stop (5% of price at 2x)
that sold exactly the dips a buyer like this exists to hold through, a 30-day hard_timeout, and a
card that promised accumulation the runtime cannot do (there is no supported way to add to an
open position). These tests pin the exit-keyed clock, the fraction guard, the 2x cap, the shipped
numbers and the honest card.

Run: python3 -B -m pytest strategies/tortoise/tests -q
"""
import os
import re
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402

RUNTIME = yaml.safe_load(open(os.path.join(HERE, "..", "main", "runtime.yaml"), encoding="utf-8"))
MANIFEST = yaml.safe_load(open(os.path.join(HERE, "..", "strategy.yaml"), encoding="utf-8"))
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "tortoise_main_signals")
INPUTS = dict(SIGNALS["inputs"])
SCHEMA = SIGNALS["signal_data_schema"]
DAY = 24 * 3600.0


class _State:
    def __init__(self, last=None):
        self._l = [last] if last else []

    def __len__(self):
        return len(self._l)

    def last(self):
        return self._l[-1] if self._l else None

    def append(self, d):
        self._l.append(d)


class _MCP:
    """One clearinghouse view: `held` is the list of coins held; `fail` makes the read raise."""
    def __init__(self, held=(), fail=False):
        self.held, self.fail, self.calls = list(held), fail, []

    def call_tool(self, tool, args):
        self.calls.append(tool)
        assert tool == "strategy_get_clearinghouse_state", tool
        if self.fail:
            raise RuntimeError("clearinghouse down")
        positions = [{"position": {"coin": c, "szi": "0.01", "entryPx": "100"}} for c in self.held]
        return {"data": {"main": {"marginSummary": {"accountValue": "1000.0", "totalMarginUsed": "0"},
                                  "assetPositions": positions}}}


class _Ctx:
    def __init__(self, held=(), last=None, fail=False):
        self.wallet = "0x" + "c" * 40
        self.senpi_mcp = _MCP(held, fail)
        self.state = _State(last)
        self.scanner_name, self.interval_seconds, self.dry_run = "tortoise_main_signals", 1800, False


def _tick(inputs=None, held=(), last=None, fail=False):
    ctx = _Ctx(held, last, fail)
    out = scan.scan(dict(inputs or INPUTS), ctx)
    return out, ctx.state.last() or {}


def _carry(rec, **edits):
    """The next tick reads this tick's record back. Ticks are 30 minutes apart in production, so
    the race-window dedup map (TTL 240s) is aged accordingly; `edits` overwrite top-level keys."""
    r = dict(rec)
    r["signaled"] = {k: v - 1800 for k, v in (rec.get("signaled") or {}).items()}
    r.update(edits)
    return r


def _record(buys=None, exits=None, held=()):
    return {"dca_history": buys or {}, "exits": exits or {}, "held": list(held), "signaled": {}, "result": {}}


# ── the shipped numbers ───────────────────────────────────────────────────────────────────

def test_the_stop_is_wide_and_there_is_no_clock_cut():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["enabled"] is False
    assert pre["phase1"]["max_loss_pct"] == 30.0                       # ROE
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverage"] == 15.0  # 15% of price at 2x
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0]["lock_hw_pct"] >= 40
    assert [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_sizing_cap_rails_cadence_and_version():
    strat = RUNTIME["strategy"]
    assert INPUTS["marginPct"] == strat["margin_pct"] == 25
    assert strat["slots"] == 3 == len(INPUTS["assets"]) == MANIFEST["catalog"]["max_slots"]
    assert strat["slots"] * INPUTS["marginPct"] <= 100
    assert INPUTS["leverage"] == strat["default_leverage"] == MANIFEST["catalog"]["leverage_max"] == scan._MAX_LEVERAGE == 2
    rails = RUNTIME["risk"]["guard_rails"]
    assert rails["max_entries_per_day"] > strat["slots"]
    assert rails["drawdown_reset_on_day_rollover"] is False
    # One crash stops all three correlated names; that must not latch the no-reset halt for good.
    worst = strat["slots"] * INPUTS["marginPct"] * RUNTIME["exit"]["dsl_preset"]["phase1"]["max_loss_pct"] / 100
    assert rails["drawdown_halt_pct"] > worst, (rails["drawdown_halt_pct"], worst)
    assert SIGNALS["timeout_seconds"] < SIGNALS["interval_seconds"]
    assert INPUTS["intervalHours"] == 24
    assert MANIFEST["version"] == "2.0.0"


def test_the_card_and_the_runtime_text_no_longer_promise_what_the_code_does_not_do():
    card = " ".join(str(MANIFEST["catalog"].get(f) or "") for f in ("name", "tagline", "belief_plain", "thesis"))
    text = card + " " + RUNTIME["description"]
    low = text.lower()
    for stale in ("accumulat", "30d", "30-day", "30 day", "hard_timeout so"):
        assert stale not in low, stale
    for old_number in (r"\b5\s*%", r"\b8\s*%", r"\b10\s*%\s*roe"):               # the 1.x stop, margin and ROE stop
        assert not re.search(old_number, low), old_number
    assert "one position" in low and "15%" in text and "30%" in text
    assert "dca" not in MANIFEST["catalog"]["tags"] and "dollar-cost-averaging" not in MANIFEST["catalog"]["tags"]


# ── the pure clock ────────────────────────────────────────────────────────────────────────

def test_the_anchor_is_the_latest_of_buy_and_exit_and_exits_are_detected_purely():
    now = 1_000_000.0
    exits, gone = scoring.detect_exits(["BTC", "eth"], {"ETH"}, {}, now)
    assert gone == ["BTC"] and exits == {"BTC": now}
    assert scoring.detect_exits([], ["BTC"], {"SOL": 5.0}, now) == ({"SOL": 5.0}, [])
    anchors = scoring.schedule_anchor({"BTC": now - 3 * DAY, "ETH": now - 100}, {"BTC": now - 3600})
    assert anchors == {"BTC": now - 3600, "ETH": now - 100}          # the exit outranks the older buy
    assert scoring.pick_next_dca_asset(["BTC", "ETH"], anchors, DAY, now) is None
    assert scoring.pick_next_dca_asset(["BTC", "ETH", "SOL"], anchors, DAY, now) == "SOL"   # never bought wins
    assert scoring.pick_next_dca_asset(["BTC"], anchors, DAY, now + DAY) == "BTC"


# ── scan(): one tick against a fake desk ─────────────────────────────────────────────────

def test_a_flat_wallet_buys_the_first_name_on_the_first_tick_with_the_shipped_sizing():
    out, rec = _tick()
    assert len(out) == 1 and out[0]["asset"] == "BTC" and out[0]["direction"] == "LONG"
    assert out[0]["marginPct"] == 25 and out[0]["leverage"] == 2
    data = out[0]["data"]
    assert set(data) <= set(SCHEMA) and all(v is not None for v in data.values())
    assert data["anchor"] == "never_bought" and data["score"] == 5
    assert rec["dca_history"]["BTC"] and rec["held"] == [] and rec["exits"] == {}


def test_held_names_are_never_added_to_and_the_basket_fills_one_name_per_tick():
    out, rec = _tick()
    assert [s["asset"] for s in out] == ["BTC"]
    out, rec = _tick(held=["BTC"], last=_carry(rec))
    assert [s["asset"] for s in out] == ["ETH"]
    out, rec = _tick(held=["BTC", "ETH"], last=_carry(rec))
    assert [s["asset"] for s in out] == ["SOL"]
    out, rec = _tick(held=["BTC", "ETH", "SOL"], last=_carry(rec))
    assert out == [] and rec["result"]["emitted"] is False and rec["held"] == ["BTC", "ETH", "SOL"]


def test_a_stop_out_restarts_the_clock_at_the_exit_not_the_buy():
    """Fails on 1.x: the buy was three days ago, so the flat name was 'overdue' and re-bought on the
    very next tick after the stop fired."""
    one = dict(INPUTS, assets=["BTC"])
    now = time.time()
    held_rec = _record(buys={"BTC": now - 3 * DAY}, held=["BTC"])
    out, rec = _tick(one, held=[], last=held_rec)                      # the DSL closed it since the last tick
    assert out == [], "1.x bought BTC straight back here"
    assert abs(rec["exits"]["BTC"] - now) < 60 and rec["held"] == []
    assert "no asset past its interval" in rec["result"]["note"]
    out, rec = _tick(one, held=[], last=_carry(rec))                   # half an hour later: still waiting
    assert out == []
    aged = dict(rec["exits"], BTC=now - DAY - 60)                      # a full interval after the exit
    out, rec = _tick(one, held=[], last=_carry(rec, exits=aged))
    assert len(out) == 1 and out[0]["asset"] == "BTC"
    assert out[0]["data"]["anchor"] == "last_exit" and "clock_last_exit" in out[0]["data"]["reasons"]


def test_an_emit_that_never_filled_is_retried_once_per_interval_not_every_tick():
    one = dict(INPUTS, assets=["BTC"])
    out, rec = _tick(one)
    assert len(out) == 1
    out, rec = _tick(one, held=[], last=_carry(rec))                   # not held yet: no second emit
    assert out == []
    stale = dict(rec["dca_history"], BTC=time.time() - DAY - 60)
    out, rec = _tick(one, held=[], last=_carry(rec, dca_history=stale))
    assert len(out) == 1 and out[0]["data"]["anchor"] == "last_buy"


def test_a_failed_read_degrades_carries_the_held_set_forward_and_misses_no_exit(capsys):
    one = dict(INPUTS, assets=["BTC"])
    now = time.time()
    held_rec = _record(buys={"BTC": now - 3 * DAY}, held=["BTC"])
    out, rec = _tick(one, held=[], last=held_rec, fail=True)           # the read raises during the outage
    assert out == [] and rec["exits"] == {} and rec["held"] == ["BTC"]  # nothing invented, nothing forgotten
    assert "degrade" in capsys.readouterr().err
    out, rec = _tick(one, held=[], last=_carry(rec))                   # the first good read sees it flat
    assert out == [] and abs(rec["exits"]["BTC"] - now) < 60


def test_a_pasted_fraction_margin_becomes_a_percent_and_one_percent_stays_one():
    out, _ = _tick(dict(INPUTS, marginPct=0.25))
    assert out and out[0]["marginPct"] == 25.0                          # 1.x emitted 0.25
    out, _ = _tick(dict(INPUTS, marginPct=1))
    assert out and out[0]["marginPct"] == 1.0


def test_leverage_is_clamped_to_the_package_cap():
    out, _ = _tick(dict(INPUTS, leverage=5))
    assert out and out[0]["leverage"] == 2 and out[0]["data"]["leverage"] == 2   # 1.x let 3x through


def test_a_one_x_record_still_ends_a_hold_after_the_upgrade():
    """A 1.x record carried the held list only inside `result`; the exit must still be seen."""
    now = time.time()
    old = {"dca_history": {"BTC": now - 3 * DAY}, "signaled": {}, "result": {"emitted": False, "held": ["BTC"]}}
    out, rec = _tick(dict(INPUTS, assets=["BTC"]), held=[], last=old)
    assert out == [] and abs(rec["exits"]["BTC"] - now) < 60
