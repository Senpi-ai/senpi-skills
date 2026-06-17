"""Adaptive risk governor — outcome-aware + equity-aware entry gating.

A reusable drop-in for fund producers. Replaces hardcoded `max_entries_per_day`,
bypass-on-profit, fixed per-asset cooldowns, and fixed daily-loss limits with
gates driven by the strategy's OWN realized results — no hardwired counts,
flags, minutes, or dollar floors:

  • Green/red day budget: the per-day entry budget SCALES UP with intraday gain
    (more shots when winning) and STOPS when the day reddens past an ADAPTIVE
    band — tighter on a losing streak, wider when winning. Replaces
    max_entries + bypass-on-profit + daily_loss_limit in one rule.

  • Trailing (multi-day) drawdown backstop with hysteresis: halts NEW entries
    when equity is > trailingDdStop below the rolling (non-resetting) peak, and
    resumes only once equity recovers to within trailingDdResume of that peak.
    Kills the multi-day compounding the daily-reset trap allowed — and it only
    stops entries, never force-flattens (the DSL still owns exits).

  • Per-asset cooldown BY OUTCOME: a name that closed green re-enters on the next
    qualifying signal (no cooldown); a name that closed red backs off, the
    cooldown doubling per consecutive loss on that name (capped). This subsumes
    the "exclude volatile names" bandaid — a name that keeps losing backs itself
    off; a name that works stays available.

State is producer-local JSON (no extra MCP calls). Close outcomes are inferred
from the last-seen unrealized-PnL sign before a position leaves the held set — a
proxy (~95% accurate, off only on a final-tick zero-cross). Instantiation =
enabled; a producer that does not construct a governor keeps its prior behavior.

Usage:
    gov = AdaptiveGovernor(state_path, config_dict)
    snap = gov.observe(account_value, positions, now)   # call ONCE per tick
    cap  = gov.max_entries()                             # dynamic entry budget (0 = stop)
    if gov.asset_blocked(name): ...                      # outcome cooldown
    # snap = {day_pnl_pct, trailing_dd_pct, win_rate, band, halted}
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under Apache-2.0
# Source: https://github.com/Senpi-ai/senpi-skills

import json
import os
import tempfile
import time
from datetime import datetime, timezone


DEFAULTS = {
    "baseEntries": 3,            # entries/day on a flat day (3-slot let-winners-run leg)
    "entryStepPct": 0.05,        # +1 entry per +5% green
    "maxEntriesCap": 8,          # hard cap on the green-scaled budget
    "bandWinRateLo": 0.35,       # win-rate at/below which the red-stop band is tightest
    "bandWinRateHi": 0.55,       # win-rate at/above which it is widest
    "bandMin": 0.05,             # red-stop band on a losing streak (stop the day at -5%)
    "bandMax": 0.12,             # red-stop band when winning (let it run to -12%)
    "trailingDdStop": 0.20,      # halt new entries past 20% off the rolling peak
    "trailingDdResume": 0.10,    # resume once recovered to within 10% of the peak (hysteresis)
    "assetCooldownBaseSec": 7200,    # 2h base cooldown after a LOSS
    "assetCooldownCapSec": 86400,    # 24h max
    "winRateWindow": 20,         # rolling closed-trade window for win-rate
    "neutralWinRate": 0.5,       # assumed win-rate before any closes are recorded
}


def _utc_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


class AdaptiveGovernor:
    def __init__(self, state_path, config=None):
        self.path = str(state_path)
        c = dict(DEFAULTS)
        for k, v in (config or {}).items():
            if k in DEFAULTS and v is not None:
                c[k] = v
        self.c = c
        self.state = self._read()

    # ─── persistence ─────────────────────────────────────────
    def _read(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self):
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.state, f, default=str)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ─── the single mutation point: call once per tick ───────
    def observe(self, account_value, positions, now=None):
        now = now if now is not None else time.time()
        try:
            av = float(account_value or 0)
        except (TypeError, ValueError):
            av = 0.0
        st = self.state

        # equity curve (day-open / day-peak reset on UTC rollover; rolling peak persists)
        eq = st.setdefault("equity", {})
        day = _utc_day(now)
        if eq.get("day_key") != day or "day_open" not in eq:
            eq["day_key"] = day
            eq["day_open"] = av
            eq["day_peak"] = av
        if av > 0:
            eq["day_peak"] = max(float(eq.get("day_peak", av) or av), av)
            eq["rolling_peak"] = max(float(eq.get("rolling_peak", av) or av), av)

        # close detection → per-asset outcome cache + rolling win/loss list
        held_prev = st.get("held", {})
        held_now = {}
        for p in positions or []:
            coin = str(p.get("coin") or "").upper()
            if coin:
                try:
                    held_now[coin] = float(p.get("upnl", 0) or 0)
                except (TypeError, ValueError):
                    held_now[coin] = 0.0
        outcomes = st.setdefault("outcomes", {})
        recent = st.setdefault("recent_closes", [])
        for coin, last_upnl in held_prev.items():
            if coin not in held_now:  # position closed since last tick
                win = 1 if float(last_upnl) >= 0 else 0
                o = outcomes.setdefault(coin, {})
                o["last_close_ts"] = now
                o["last_close_win"] = win
                o["consec_losses"] = 0 if win else int(o.get("consec_losses", 0)) + 1
                recent.append(win)
        k = int(self.c["winRateWindow"])
        if len(recent) > k:
            st["recent_closes"] = recent[-k:]
        st["held"] = held_now

        # trailing-DD halt with hysteresis
        rp = float(eq.get("rolling_peak", av) or av)
        trailing_dd = (rp - av) / rp if rp > 0 else 0.0
        halted = bool(eq.get("halted", False))
        if not halted and trailing_dd > self.c["trailingDdStop"]:
            halted = True
        elif halted and trailing_dd <= self.c["trailingDdResume"]:
            halted = False
        eq["halted"] = halted
        st["_last_av"] = av

        self._write()
        return self.snapshot()

    # ─── pure reads (no mutation) ────────────────────────────
    def _win_rate(self):
        recent = self.state.get("recent_closes", [])
        return (sum(recent) / len(recent)) if recent else float(self.c["neutralWinRate"])

    def _band(self):
        wr = self._win_rate()
        lo, hi = self.c["bandWinRateLo"], self.c["bandWinRateHi"]
        t = 0.0 if hi <= lo else _clamp((wr - lo) / (hi - lo), 0.0, 1.0)
        return self.c["bandMin"] + (self.c["bandMax"] - self.c["bandMin"]) * t

    def snapshot(self):
        eq = self.state.get("equity", {})
        av = float(self.state.get("_last_av", eq.get("day_open", 0)) or 0)
        day_open = float(eq.get("day_open", av) or av)
        rolling_peak = float(eq.get("rolling_peak", av) or av)
        day_pnl = (av - day_open) / day_open if day_open > 0 else 0.0
        trailing_dd = (rolling_peak - av) / rolling_peak if rolling_peak > 0 else 0.0
        return {
            "day_pnl_pct": round(day_pnl, 4),
            "trailing_dd_pct": round(trailing_dd, 4),
            "win_rate": round(self._win_rate(), 3),
            "band": round(self._band(), 4),
            "halted": bool(eq.get("halted", False)),
        }

    def max_entries(self):
        """Dynamic per-day entry budget. 0 = stop (red past band, or halted)."""
        snap = self.snapshot()
        if snap["halted"]:
            return 0
        dp, band = snap["day_pnl_pct"], snap["band"]
        base = int(self.c["baseEntries"])
        cap = int(self.c["maxEntriesCap"])
        if dp >= 0:                       # green → scale up, capped
            return min(cap, base + int(dp / self.c["entryStepPct"]))
        if dp > -band:                    # small red within the noise band → base
            return base
        return 0                          # turned red past the adaptive band → stop the day

    def asset_blocked(self, asset, now=None):
        """Outcome-based cooldown: gains re-enter freely; losses back off, the
        cooldown doubling per consecutive loss on that name (capped)."""
        now = now if now is not None else time.time()
        o = self.state.get("outcomes", {}).get(str(asset or "").upper())
        if not o:
            return False
        if int(o.get("last_close_win", 1)):     # last close was a gain → no cooldown
            return False
        cl = int(o.get("consec_losses", 1))
        cool = min(self.c["assetCooldownBaseSec"] * (2 ** max(0, cl - 1)),
                   self.c["assetCooldownCapSec"])
        return (now - float(o.get("last_close_ts", 0))) < cool
