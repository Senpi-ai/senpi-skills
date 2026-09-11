#!/usr/bin/env python3
"""senpi-quant — paste any Hyperliquid address, get the desk.

  python3 desk.py 0x<address>                      # the full desk as Markdown
  python3 desk.py 0x<address> --section protection # one section, from the cached run if fresh
  python3 desk.py 0x<address> --json               # the analysis document (JSON on stdout)
  python3 desk.py 0x<address> --fixture F --dry    # offline, from recorded responses

Reads 90 days of fills, funding, fees, the equity curve, the live book and its resting orders from
Hyperliquid's public Info API (no auth), hourly candles for every coin touched, the public leaderboard for
the weekly rank, and — when a Senpi token is present — the smart-money cohort from Senpi discovery
(public-leaderboard cohort otherwise). Fails open: every optional layer degrades to a warning in `meta`.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hl_api  # noqa: E402
import market as market_mod  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402
import senpi_history  # noqa: E402
import smart_money  # noqa: E402
import timing as timing_mod  # noqa: E402
from roundtrips import episodes_from_fills  # noqa: E402

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BENCH_PATH = os.path.join(HERE, "..", "references", "benchmark.json")
DEFAULT_STATE_DIR = os.path.join(tempfile.gettempdir(), "senpi-quant")
FRESH_S = 600
PUBLIC_COHORT_N = 80          # live books read for the public smart-money cohort (parallel, cached 2 min)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _mcp_client(meta):
    if not os.environ.get("SENPI_AUTH_TOKEN"):
        return None
    try:
        from mcp_client import MCPClient
        return MCPClient()
    except Exception as e:  # noqa: BLE001
        meta.setdefault("warnings", []).append(f"senpi client unavailable: {e}")
        return None


class _MCPFixture:
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        addrs = kw.get("trader_addresses") or kw.get("addresses") or ([kw["trader_address"]] if kw.get("trader_address") else None)
        if addrs:
            k = f"{tool}::{str(addrs[0]).lower()}"
            if k in self._r:
                return self._r[k]
        if "offset" in kw:
            k = f"{tool}::{kw['offset']}"
            if k in self._r:
                return self._r[k]
        if tool in self._r:
            return self._r[tool]
        raise RuntimeError(f"fixture has no {tool}")


def analyze(addr, hl, days=90, mcp=None, want_rank=True, want_cohort=True, bench=None, meta=None):
    meta = meta if meta is not None else {}
    meta.setdefault("warnings", []); meta["timings"] = {}; meta["sources"] = {}
    t0 = time.time()
    tr_raw = hl.trader(addr, days=days)
    meta["timings"]["trader"] = round(time.time() - t0, 1)
    fills, cs, oo = tr_raw["fills"], tr_raw["clearinghouseState"], tr_raw["frontendOpenOrders"]
    win_start, now = tr_raw["window_start_ms"], tr_raw["now_ms"]
    ctxs = hl.meta()
    closed, opened = episodes_from_fills(fills)
    source = "public fills"
    cov = metrics.coverage(closed, opened, fills, tr_raw["userFees"])
    if mcp is not None:
        t_h = time.time()
        rows = senpi_history.fetch(mcp, addr, win_start, meta)
        meta["timings"]["senpi_history"] = round(time.time() - t_h, 1)
        if rows:
            closed, source = rows, f"Senpi discovery ({len(rows)} closed positions)"
    meta["sources"]["trades"] = source
    if source == "public fills" and cov and cov.get("overall") is not None and cov["overall"] < 0.9:
        meta["warnings"].append(f"Hyperliquid's public API returned about {100 * cov['overall']:.0f}% of your executed volume "
                                f"(TWAP executions older than a day aren't kept) — ledger totals are complete, trade-level patterns are read from the "
                                f"fills it did return; connect a Senpi account for the complete trade history")
    track = metrics.track_record(closed, opened, tr_raw["userFunding"], tr_raw["userFees"], win_start)
    track["coverage"] = cov
    track["ledger_net"] = metrics.ledger_pnl(tr_raw["portfolio"], win_start)
    track["fill_taker_share"] = track["taker_share"]
    if source != "public fills":
        # senpi rows carry no maker/taker split — keep the fill-level execution read from the public stream
        fb_closed, fb_open = episodes_from_fills(fills)
        fb = metrics.track_record(fb_closed, fb_open, tr_raw["userFunding"], tr_raw["userFees"], win_start)
        track["taker_share"], track["fee_recoverable"], track["volume"] = fb["taker_share"], fb["fee_recoverable"], fb["volume"]
    book = metrics.open_book(cs, oo, ctxs)
    pnl_curve = metrics.pnl_series(tr_raw["portfolio"], win_start)
    fl = metrics.flows(tr_raw["ledger"], addr)
    eq = metrics.equity_curve(tr_raw["portfolio"], fl, win_start)
    dd = metrics.drawdown(eq)
    avg_eq = (sum(v for _, v in eq) / len(eq)) if eq else None
    equity = dict(points=len(eq), start=eq[0][1] if eq else None, end=eq[-1][1] if eq else None, avg=avg_eq,
                  return_on_avg_equity=((track["ledger_net"] if track.get("ledger_net") is not None else track["net"]) / avg_eq) if avg_eq else None,
                  net_flows=sum(a for _, a in fl))
    act = metrics.activity(fills, win_start)
    # candles for timing + market
    t1 = time.time()
    coins = {e["coin"] for e in closed + opened if metrics.in_window(e, win_start)} | {p["coin"] for p in book["positions"]} | {"BTC"}
    try:
        candles = timing_mod.load_candles(hl.candles(sorted(coins), days=days + 1))
    except Exception as e:  # noqa: BLE001
        candles = {}; meta["warnings"].append(f"candles unavailable: {e}")
    meta["timings"]["candles"] = round(time.time() - t1, 1)
    tm_rows = timing_mod.per_trade([e for e in closed if metrics.in_window(e, win_start)], candles)
    tm = timing_mod.summarize(tm_rows) if tm_rows else None
    mf = market_mod.book_fit(book, candles, ctxs)
    # rank + cohort (public) — one leaderboard download serves both
    rank, sm, labels = None, None, None
    lb = None
    if want_rank or (want_cohort and mcp is None):
        t2 = time.time()
        try:
            lb = hl.leaderboard()
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"leaderboard unavailable: {e}")
        meta["timings"]["leaderboard"] = round(time.time() - t2, 1)
    if want_rank and lb:
        rank = hl_api.weekly_rank(lb, addr)
        if rank is None:
            meta["warnings"].append("address is not on Hyperliquid's leaderboard this week (no rank)")
    if want_cohort:
        t3 = time.time()
        per, source = None, None
        if mcp is not None:
            try:
                addrs = smart_money.senpi_cohort(mcp, meta)
                if addrs:
                    per = smart_money.senpi_positions(mcp, addrs, meta); source = f"Senpi discovery — {len(addrs)} wallets with ≥ $1M realized"
            except Exception as e:  # noqa: BLE001
                meta["warnings"].append(f"senpi cohort failed: {e}")
        if per is None and lb:
            addrs = hl_api.public_cohort(lb, n=PUBLIC_COHORT_N)
            states = hl.states(addrs)
            live = {a: s for a, s in states.items() if s and (s.get("assetPositions") or [])}
            per = smart_money.public_positions(live); source = f"public leaderboard — {len(live)} large profitable accounts with a live book"
        if per is not None:
            sm = smart_money.compare(per, book, opened); sm["source"] = source; sm["coins_covered"] = len(per)
        meta["timings"]["cohort"] = round(time.time() - t3, 1)
        meta["sources"]["cohort"] = source
    if mcp is not None:
        try:
            resp = mcp.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME", addresses=[addr], limit=1, timeout=15)
            rows = smart_money._traders_of(smart_money._ok(resp))
            if rows:
                t = rows[0]
                labels = dict(consistency=smart_money._field(t, "tcsLabel", "consistency", "consistencyLabel"), risk=smart_money._field(t, "risk", "riskLabel"),
                              activity=smart_money._field(t, "activity", "activityLabel"))
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"senpi labels unavailable: {e}")
    dims, quant = score.dimensions(track, book, dd, tm, mf, sm, closed, pnl_curve)
    lk = score.leaks(track, book, tm, tr_raw["userFunding"], [e for e in closed if metrics.in_window(e, win_start)], win_start, days)
    r = dict(address=addr, days=days, now_ms=now, window_start_ms=win_start, activity=act, track=track, book=book, equity=equity, drawdown=dd,
             pnl_curve=pnl_curve[-120:], timing=tm, market=mf, rank=rank, smart=sm, labels=labels, dimensions=dims, quant_score=quant,
             archetype=score.archetype(track, book, tm, act), flags=score.flags(track, book, dd, tm, mf, labels), leaks=lk,
             setups=score.best_setups([e for e in closed if metrics.in_window(e, win_start)], tm_rows), families=score.families(closed, tm, track),
             benchmark=bench, benchmark_table=smart_money.benchmark_table(track, bench) if bench else None,
             episodes=[{k: v for k, v in e.items()} for e in closed if metrics.in_window(e, win_start)][-200:], meta=meta)
    r["verdict"] = score.verdict(track, book, dims, lk)
    meta["timings"]["total"] = round(time.time() - t0, 1); meta["hl_calls"] = hl.calls
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi-quant: the desk for any Hyperliquid address")
    ap.add_argument("address")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--json", action="store_true", help="print the analysis document instead of Markdown")
    ap.add_argument("--section", choices=render.SECTIONS, action="append", help="render only these sections (repeatable)")
    ap.add_argument("--fixture", help="recorded responses (JSON) — offline run")
    ap.add_argument("--dry", action="store_true", help="never touch the network (requires --fixture)")
    ap.add_argument("--no-rank", action="store_true"); ap.add_argument("--no-cohort", action="store_true")
    ap.add_argument("--cache", default=hl_api.DEFAULT_CACHE, help="HTTP cache dir ('' to disable)")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--fresh", action="store_true", help="ignore a cached analysis")
    a = ap.parse_args(argv)
    addr = a.address.strip()
    if not ADDR_RE.match(addr):
        print(json.dumps({"error": "not a Hyperliquid address — expected 0x followed by 40 hex characters"})); return 2
    addr = addr.lower()
    os.makedirs(a.state_dir, exist_ok=True)
    state_path = os.path.join(a.state_dir, f"desk-{addr}.json")
    meta = {}
    bench = None
    if os.path.exists(BENCH_PATH):
        with open(BENCH_PATH) as fh:
            bench = json.load(fh).get("benchmark")
    r = None
    if a.section and not a.fresh and os.path.exists(state_path) and time.time() - os.path.getmtime(state_path) < FRESH_S:
        with open(state_path) as fh:
            r = json.load(fh)
    if r is None:
        if a.fixture:
            with open(a.fixture) as fh:
                rec = json.load(fh)
            hl = hl_api.HLFixture(rec); mcp = _MCPFixture(rec) if any(k.startswith("discovery_") for k in rec) else None
        else:
            if a.dry:
                print(json.dumps({"error": "--dry needs --fixture"})); return 2
            hl = hl_api.HL(cache_dir=a.cache or None); mcp = _mcp_client(meta)
        log(f"[senpi-quant] reading {addr[:6]}…{addr[-4:]}: fills, funding, fees, book, orders, equity …")
        try:
            r = analyze(addr, hl, days=a.days, mcp=mcp, want_rank=not a.no_rank, want_cohort=not a.no_cohort, bench=bench, meta=meta)
        except hl_api.HLError as e:
            print(json.dumps({"error": f"Hyperliquid read failed: {e}", "address": addr})); return 1
        if not r["activity"]["fills"] and not r["book"]["positions"]:
            print(json.dumps({"error": "no perp activity in the window and no open positions — nothing to read", "address": addr, "days": a.days})); return 3
        log(f"[senpi-quant] done in {meta['timings']['total']}s ({meta.get('hl_calls')} public reads)")
        with open(state_path, "w") as fh:
            json.dump(r, fh, default=float)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "episodes"}, default=float))
    else:
        print(render.render(r, a.section))
    return 0


if __name__ == "__main__":
    sys.exit(main())
