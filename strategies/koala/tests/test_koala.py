"""Koala 2.0.0: an entry counts when it fills; the stop is the card's; no clock; re-entry by default.

Measured on 2+ years of BTC/ETH/SOL 4h candles (112 weekly starts each): the 10% ROE stop the file
carried (5% of price at 2x) ended half of all holds at a loss inside about eight days; the card's
30% ROE stop turns the median hold positive (+2.5% over 23 days on BTC, 71% positive). 1.x also
recorded the entry when the signal was EMITTED, so a refused first order spent the lifetime one-shot
and the wallet sat idle until redeployed. These tests pin the pending-then-filled state machine and
the shipped numbers.

Run: python3 -B -m pytest strategies/koala/tests -q
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
SIGNALS = next(s for s in RUNTIME["scanners"] if s["name"] == "koala_main_signals")
INPUTS = dict(SIGNALS["inputs"])


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
    """One clearinghouse view: `held` is the coin held (or None for a flat wallet)."""
    def __init__(self, held=None):
        self.held = held

    def call_tool(self, tool, args):
        assert tool == "strategy_get_clearinghouse_state"
        positions = [{"position": {"coin": self.held, "szi": "0.01", "entryPx": "100000"}}] if self.held else []
        return {"data": {"main": {"marginSummary": {"accountValue": "1000.0"}, "assetPositions": positions}}}


class _Ctx:
    def __init__(self, held=None, last=None):
        self.wallet = "0x" + "b" * 40
        self.senpi_mcp = _MCP(held)
        self.state = _State(last)
        self.scanner_name, self.interval_seconds, self.dry_run = "koala_main_signals", 1800, False


def _tick(inputs, held=None, last=None):
    ctx = _Ctx(held, last)
    out = scan.scan(dict(inputs), ctx)
    rec = ctx.state.last() or {}
    return out, rec.get("koala") or {}, (rec.get("result") or {}).get("gate"), rec


def _carry(rec, koala=None):
    """The next tick reads this tick's record back (optionally with the koala state edited). Ticks are
    30 minutes apart in production, so the race-window dedup map (TTL 240s) is aged accordingly."""
    r = dict(rec)
    if koala is not None:
        r["koala"] = koala
    r["signaled"] = {k: v - 1800 for k, v in (rec.get("signaled") or {}).items()}
    return r


# ── the shipped numbers ───────────────────────────────────────────────────────────────────

def test_the_stop_is_the_cards_thirty_percent_and_there_is_no_clock():
    pre = RUNTIME["exit"]["dsl_preset"]
    assert pre["phase1"]["max_loss_pct"] == 30.0                       # ROE: 15% of price at 2x
    assert pre["phase1"]["max_loss_pct"] / INPUTS["leverage"] == 15.0
    for cut in ("hard_timeout", "weak_peak_cut", "dead_weight_cut"):
        assert pre[cut]["enabled"] is False, cut
    tiers = pre["phase2"]["tiers"]
    assert tiers[0]["trigger_pct"] == 12 and [t["trigger_pct"] for t in tiers] == sorted(t["trigger_pct"] for t in tiers)
    assert all(0 < t["lock_hw_pct"] < 100 for t in tiers)


def test_sizing_re_entry_and_version():
    assert INPUTS["marginPct"] == RUNTIME["strategy"]["margin_pct"] == 40
    assert INPUTS["leverage"] == 2 and INPUTS["maxLeverage"] == MANIFEST["catalog"]["leverage_max"] == 3
    assert INPUTS["fireOnceMode"] is False and INPUTS["reEntryCooldownHours"] == 168
    assert INPUTS["pendingEntryExpirySeconds"] == 5400 and SIGNALS["interval_seconds"] * 3 == 5400
    rails = RUNTIME["risk"]["guard_rails"]
    assert "max_entries_per_day" not in rails          # one position, one pending marker, a 7-day cooldown: no calendar cap
    assert rails["drawdown_reset_on_day_rollover"] is False
    assert MANIFEST["version"] == "2.0.0"
    assert "90" not in MANIFEST["catalog"]["tagline"] and "three months" not in MANIFEST["catalog"]["belief_plain"]


# ── the pure state machine ────────────────────────────────────────────────────────────────

def test_pending_marker_lifecycle_is_pure():
    now = 1_000_000.0
    s = scoring.record_pending(scoring.empty_state(), now)
    assert scoring.pending_fill_state(s, 5400, now + 100) == "waiting"
    assert scoring.pending_fill_state(s, 5400, now + 5400) == "expired"
    assert scoring.pending_fill_state(scoring.empty_state(), 5400, now) == "none"
    filled = scoring.confirm_fill(s, now + 1800)
    assert filled["first_entry_at"] == now and filled["total_entries"] == 1 and filled["pending_since"] is None
    assert filled["last_exit_at"] is None
    dropped = scoring.drop_pending(s)
    assert dropped["pending_since"] is None and dropped["unfilled_entries"] == 1 and dropped.get("first_entry_at") is None
    assert scoring.should_enter(dropped, True, 168, now + 9999)         # the one-shot was never spent


# ── scan(): the entry becomes real on the fill ────────────────────────────────────────────

def test_an_entry_is_recorded_when_the_asset_shows_up_held_not_when_emitted():
    inputs = dict(INPUTS, fireOnceMode=True)
    out, koala, gate, rec = _tick(inputs)                                 # flat wallet, fresh state
    assert len(out) == 1 and out[0]["asset"] == "BTC" and out[0]["direction"] == "LONG"
    assert out[0]["marginPct"] == 40 and out[0]["leverage"] == 2
    assert gate == "pass" and koala["pending_since"] and koala.get("first_entry_at") is None and koala.get("total_entries", 0) == 0
    out2, koala2, gate2, rec2 = _tick(inputs, held="BTC", last=_carry(rec))   # the order filled
    assert out2 == [] and gate2 == "holding"
    assert koala2["total_entries"] == 1 and koala2["first_entry_at"] == koala["pending_since"] and koala2["pending_since"] is None
    out3, koala3, gate3, _ = _tick(inputs, held="BTC", last=_carry(rec2))
    assert out3 == [] and gate3 == "holding" and koala3["total_entries"] == 1


def test_a_refused_first_order_does_not_spend_the_one_shot():
    inputs = dict(INPUTS, fireOnceMode=True)
    out, koala, gate, rec = _tick(inputs)
    assert len(out) == 1
    out2, koala2, gate2, rec2 = _tick(inputs, held=None, last=_carry(rec))      # not held yet: wait, no second emit
    assert out2 == [] and gate2 == "pending_fill" and koala2["pending_since"] == koala["pending_since"]
    stale = dict(koala2, pending_since=time.time() - 6000)                       # three ticks have passed, still flat
    out3, koala3, gate3, _ = _tick(inputs, held=None, last=_carry(rec2, stale))
    assert len(out3) == 1 and gate3 == "pass", "1.x stayed silent for life here"
    assert koala3["unfilled_entries"] == 1 and koala3.get("first_entry_at") is None and koala3["pending_since"]


def test_fire_once_is_spent_by_a_fill_and_re_entry_waits_for_its_cooldown():
    once = dict(INPUTS, fireOnceMode=True)
    _, _, _, rec = _tick(once)
    _, _, _, rec = _tick(once, held="BTC", last=_carry(rec))                      # filled
    out, koala, gate, rec = _tick(once, held=None, last=_carry(rec))              # the DSL closed it
    assert out == [] and gate == "not_eligible" and koala["last_exit_at"] and koala["total_entries"] == 1
    cycle = dict(INPUTS, fireOnceMode=False, reEntryCooldownHours=168)
    _, _, _, rec = _tick(cycle)
    _, _, _, rec = _tick(cycle, held="BTC", last=_carry(rec))
    out, koala, gate, rec = _tick(cycle, held=None, last=_carry(rec))
    assert out == [] and gate == "not_eligible" and "cooldown" in (rec["result"].get("note") or "")
    aged = dict(koala, last_exit_at=time.time() - 169 * 3600)
    out, koala, gate, _ = _tick(cycle, held=None, last=_carry(rec, aged))
    assert len(out) == 1 and out[0]["data"]["reasons"] == ["hodl_re_entry"] and koala["pending_since"]


def test_a_pasted_fraction_margin_becomes_a_percent():
    out, _, _, _ = _tick(dict(INPUTS, marginPct=0.4))
    assert out and out[0]["marginPct"] == 40.0
