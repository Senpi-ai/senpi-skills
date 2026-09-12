#!/usr/bin/env python3
"""quant-desk — paste any Hyperliquid address, get the desk.

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

import deep as deep_mod  # noqa: E402
import followups  # noqa: E402
import hl_api  # noqa: E402
import market as market_mod  # noqa: E402
import metrics  # noqa: E402
import opportunities  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402
import senpi_history  # noqa: E402
import smart_money  # noqa: E402
import strategy_read  # noqa: E402
import taxonomy  # noqa: E402
import timing as timing_mod  # noqa: E402
from roundtrips import episodes_from_fills  # noqa: E402

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BENCH_PATH = os.path.join(HERE, "..", "references", "benchmark.json")
DEFAULT_STATE_DIR = os.path.join(tempfile.gettempdir(), "quant-desk")
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
        if "time_frame" in kw and "offset" in kw:
            k = f"{tool}::{kw['time_frame']}::{kw['offset']}"
            if k in self._r:
                return self._r[k]
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


def analyze(addr, hl, days=90, mcp=None, want_rank=True, want_cohort=True, bench=None, meta=None, whose="mine"):
    meta = meta if meta is not None else {}
    meta.setdefault("warnings", []); meta["timings"] = {}; meta["sources"] = {}
    t0 = time.time()
    log("[quant-desk] scanning every fill, funding payment, transfer and resting order …")
    tr_raw = hl.trader(addr, days=days)
    meta["timings"]["trader"] = round(time.time() - t0, 1)
    fills, cs, oo = tr_raw["fills"], tr_raw["clearinghouseState"], tr_raw["frontendOpenOrders"]
    win_start, now = tr_raw["window_start_ms"], tr_raw["now_ms"]
    ctxs = hl.meta()
    ctx_xyz = None
    try:
        ctx_xyz = hl.meta("xyz")
    except Exception as e:  # noqa: BLE001
        meta["warnings"].append(f"xyz contexts unavailable: {e}")
    closed, opened = episodes_from_fills(fills)
    ages = {}
    if mcp is not None:
        try:
            resp = mcp.mcp_call("discovery_get_trader_state", trader_addresses=[addr], include_position_age=True, timeout=20)
            for t in smart_money._traders_of(smart_money._ok(resp)):
                for p in (t.get("openPositions") or t.get("open_positions") or []):
                    st = smart_money._f(p, "startTime", "start_time", default=0.0)
                    if isinstance(p, dict) and p.get("coin") and st:
                        ages[p["coin"]] = st * 1000.0 if st < 1e12 else st
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"own position ages unavailable: {e}")
    source = "public fills"
    cov = metrics.coverage(closed, opened, fills, tr_raw["userFees"])
    if mcp is not None:
        t_h = time.time()
        rows = senpi_history.fetch(mcp, addr, win_start, meta)
        meta["timings"]["senpi_history"] = round(time.time() - t_h, 1)
        if rows:
            closed, source = rows, f"senpi discovery ({len(rows)} closed position{'s' if len(rows) != 1 else ''})"
    meta["sources"]["trades"] = source
    track = metrics.track_record(closed, opened, tr_raw["userFunding"], tr_raw["userFees"], win_start)
    track["coverage"] = cov
    track["ledger_net"] = metrics.ledger_pnl(tr_raw["portfolio"], win_start)
    track["fill_taker_share"] = track["taker_share"]
    if source != "public fills":
        # senpi rows carry no maker/taker split — keep the fill-level execution read from the public stream
        fb_closed, fb_open = episodes_from_fills(fills)
        fb = metrics.track_record(fb_closed, fb_open, tr_raw["userFunding"], tr_raw["userFees"], win_start)
        track["taker_share"], track["fee_recoverable"], track["volume"] = fb["taker_share"], fb["fee_recoverable"], fb["volume"]
    log("[quant-desk] auditing the live book: every position's stop, liquidation distance and funding …")
    book = metrics.open_book(cs, oo, ctxs, ages, tr_raw.get("clearinghouseState_xyz"), tr_raw.get("frontendOpenOrders_xyz"), ctx_xyz,
                             metrics.whole_account_value(tr_raw.get("portfolio"), tr_raw.get("spotClearinghouseState")),
                             metrics.spot_free_usdc(tr_raw.get("spotClearinghouseState")))
    pnl_curve = metrics.pnl_series(tr_raw["portfolio"], win_start)
    fl = metrics.flows(tr_raw["ledger"], addr)
    eq = metrics.equity_curve(tr_raw["portfolio"], [], win_start)          # raw account value over the window
    pnl_pts = metrics.pnl_series(tr_raw["portfolio"], win_start)
    dd = metrics.drawdown(pnl_pts, eq)
    funded = [v for _, v in eq if v > 0]
    avg_eq = (sum(funded) / len(funded)) if funded else None
    equity = dict(points=len(eq), start=eq[0][1] if eq else None, end=eq[-1][1] if eq else None, avg=avg_eq,
                  return_on_avg_equity=((track["ledger_net"] if track.get("ledger_net") is not None else track["net"]) / avg_eq) if avg_eq else None,
                  net_flows=sum(a for _, a in fl))
    act = metrics.activity(fills, win_start)
    majors, large = taxonomy.crypto_tiers(ctxs)
    breadth = market_mod.breadth(ctxs, ctx_xyz)
    # ---- cohorts + attention first (they name coins the candle pull must cover)
    rank, labels, lb = None, None, None
    cohorts, attention, fregime = [], None, None
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
        log("[quant-desk] running senpi-smart-money: the proven cohort and the hot 30-day cohort against this book …")
        if mcp is not None:
            for name, fetch in (("proven", smart_money.proven_cohort), ("hot", smart_money.hot_cohort)):
                try:
                    addrs = fetch(mcp, meta)
                    bks = smart_money.books(mcp, addrs, meta, progress=log) if addrs else []
                    if bks:
                        cv = smart_money.cohort_view(name, bks, book, opened, majors, large, ages, now)
                        cv["source"] = ("senpi discovery — top traders by all-time realized P&L, ≥ $1M realized" if name == "proven"
                                        else "senpi discovery — the most profitable traders of the last 30 days, holding positions now")
                        cohorts.append(cv)
                except Exception as e:  # noqa: BLE001
                    meta["warnings"].append(f"{name} cohort failed: {e}")
        if not cohorts and lb:
            addrs = hl_api.public_cohort(lb, n=PUBLIC_COHORT_N)
            states = hl.states(addrs)
            bks = smart_money.public_books(states)
            if bks:
                cv = smart_money.cohort_view("proven", bks, book, opened, majors, large, ages, now)
                cv["source"] = f"Hyperliquid leaderboard — {len(bks)} large profitable accounts with a live book"
                cohorts.append(cv)
        meta["timings"]["cohort"] = round(time.time() - t3, 1)
        meta["sources"]["cohort"] = [c["source"] for c in cohorts]
    if mcp is not None:
        log("[quant-desk] running senpi-market-pulse: funding regime, where the top traders' gains sit, momentum …")
        try:
            fregime = market_mod.funding_regime(mcp.mcp_call("market_get_funding_regime", timeout=10))
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"funding regime unavailable: {e}")
        try:
            mk = mcp.mcp_call("leaderboard_get_markets", limit=100, timeout=12)
            try:
                mo = mcp.mcp_call("leaderboard_get_momentum_events", limit=50, timeout=12)
            except Exception as e:  # noqa: BLE001
                mo = None; meta["warnings"].append(f"momentum events unavailable: {e}")
            attention = market_mod.attention(mk, mo, book)
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"top-trader markets unavailable: {e}")
        try:
            resp = mcp.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME", addresses=[addr], limit=1, timeout=15)
            rows = smart_money._traders_of(smart_money._ok(resp))
            if rows:
                t = rows[0]
                labels = dict(consistency=smart_money._field(t, "tcsLabel", "consistency", "consistencyLabel"), risk=smart_money._field(t, "risk", "riskLabel"),
                              activity=smart_money._field(t, "activity", "activityLabel"), tcs=smart_money._field(t, "tcsValue", "tcs_score", "tcsScore"),
                              roi_all_time=smart_money._field(t, "returnOnInvestment", "roi"), pnl_all_time=smart_money._field(t, "profitAndLoss", "pnl"),
                              win_rate=smart_money._field(t, "winRate", "win_rate"), max_drawdown=smart_money._field(t, "maxDrawdown", "max_drawdown"))
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"senpi labels unavailable: {e}")
    # ---- candles: every coin the trader touched or holds, BTC, and what the cohorts and top traders are in
    t1 = time.time()
    log("[quant-desk] reading the tape: 90 days of candles for every coin touched, regime by regime …")
    coins = {e["coin"] for e in closed + opened if metrics.in_window(e, win_start)} | {p["coin"] for p in book["positions"]} | {"BTC"}
    for cv in cohorts:
        coins |= {h["coin"] for h in cv.get("they_hold") or []}
    if attention:
        coins |= {m["coin"] for m in attention["markets"] if m.get("coin")}
    try:
        candles = timing_mod.load_candles(hl.candles(sorted(coins), days=days + 1))
    except Exception as e:  # noqa: BLE001
        candles = {}; meta["warnings"].append(f"candles unavailable: {e}")
    basket = ["BTC", "ETH"] + [c for c in (breadth.get("majors") or []) + (breadth.get("large") or []) if c not in ("BTC", "ETH")][:10]
    try:
        daily = hl.candles(basket, days=days + 2, interval="1d")
    except Exception as e:  # noqa: BLE001
        daily = {}; meta["warnings"].append(f"daily candles unavailable: {e}")
    meta["timings"]["candles"] = round(time.time() - t1, 1)
    in_win = [e for e in closed if metrics.in_window(e, win_start)]
    tm_rows = timing_mod.per_trade(in_win, candles)
    tm = timing_mod.summarize(tm_rows) if tm_rows else None
    mf = market_mod.book_fit(book, candles, ctxs)
    regimes_days = market_mod.daily_regimes(daily, basket)
    rperf = market_mod.regime_performance(in_win, regimes_days)
    sm = cohorts[0] if cohorts else None
    ctx_by = {u["name"]: c for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    if ctx_xyz:
        ctx_by.update({u["name"]: c for u, c in zip(ctx_xyz[0]["universe"], ctx_xyz[1])})
    coin_regimes = {c: market_mod.coin_regime(c, candles, ctx_by.get(c)) for c in coins}
    log("[quant-desk] finding the leaks, pricing the fixes, running senpi-signals for live matches …")
    lk = score.leaks(track, book, tm, tr_raw["userFunding"], in_win, win_start, days)
    setups = score.best_setups(in_win, tm_rows)
    log("[quant-desk] reading the playbook: what the book actually does, by class, side and size — decoding the setups that actually pay …")
    fp = strategy_read.fingerprint(in_win, opened, book, track, act, tm, candles, ctxs, pnl_curve, win_start, now)
    strategy = dict(fingerprint=fp, statements=strategy_read.statements(fp, track, book), critique=strategy_read.critique(fp, track, book, mf, sm, cohorts))
    context = dict(breadth=breadth, funding_regime=fregime, attention=attention, regime_days=regimes_days, regime_performance=rperf)
    opps = opportunities.scout(in_win, setups, book, breadth, coin_regimes, cohorts, attention, majors, large)
    log("[quant-desk] running quant: scoring the book on six dimensions, comparing it to the top traders, scouting today's matches, developing the recommendations …")
    dims, quant = score.dimensions(track, book, dd, tm, mf, sm, closed, pnl_curve)
    r = dict(address=addr, days=days, now_ms=now, window_start_ms=win_start, activity=act, track=track, book=book, equity=equity, drawdown=dd,
             pnl_curve=pnl_curve[-120:], timing=tm, market=mf, rank=rank, smart=sm, cohorts=cohorts, labels=labels, dimensions=dims, quant_score=quant,
             archetype=score.archetype(track, book, tm, act, opened), flags=score.flags(track, book, dd, tm, mf, labels), leaks=lk,
             setups=setups, families=score.families(closed, tm, track), strategy=strategy, context=context, opportunities=opps,
             benchmark=bench, benchmark_table=smart_money.benchmark_table(track, bench) if bench else None,
             episodes=[{k: v for k, v in e.items()} for e in in_win][-300:], meta=meta)
    r["whose"] = whose
    r["verdict"] = score.verdict(track, book, dims, lk)
    r["followups"] = followups.offer(r, whose=whose)
    meta["timings"]["total"] = round(time.time() - t0, 1); meta["hl_calls"] = hl.calls
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description="quant-desk: the desk for any Hyperliquid address")
    ap.add_argument("address", nargs="?", help="the wallet; omit with --compare")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mine", action="store_true", help="the reader's own book (second person) — the default")
    g.add_argument("--other", "--analyst", dest="other", action="store_true", help="someone else's book (analyst mode): third person, learn-from-them follow-ups")
    ap.add_argument("--compare", nargs="+", metavar="0x", help="two or more addresses side by side (cached runs are reused)")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--json", action="store_true", help="print the analysis document instead of Markdown")
    ap.add_argument("--section", choices=render.SECTIONS, action="append", help="render only these sections (repeatable)")
    ap.add_argument("--deep", choices=sorted(deep_mod.MODES), help="a follow-up deep dive from the cached run (protect, smart, scout, replay, funding, regime, compare, rules, strategy, watch)")
    ap.add_argument("--fixture", help="recorded responses (JSON) — offline run")
    ap.add_argument("--dry", action="store_true", help="never touch the network (requires --fixture)")
    ap.add_argument("--no-rank", action="store_true"); ap.add_argument("--no-cohort", action="store_true")
    ap.add_argument("--cache", default=hl_api.DEFAULT_CACHE, help="HTTP cache dir ('' to disable)")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--fresh", action="store_true", help="ignore a cached analysis")
    a = ap.parse_args(argv)
    whose = "other" if a.other else "mine"
    os.makedirs(a.state_dir, exist_ok=True)
    if a.compare:
        rs = []
        for x in a.compare:
            x = x.strip().lower()
            if not ADDR_RE.match(x):
                print(json.dumps({"error": f"not a Hyperliquid address: {x}"})); return 2
            sp = os.path.join(a.state_dir, f"desk-{x}.json")
            if os.path.exists(sp) and time.time() - os.path.getmtime(sp) < 6 * FRESH_S and not a.fresh:
                with open(sp) as fh:
                    rs.append(json.load(fh)); continue
            sub = [x, "--json", "--state-dir", a.state_dir, "--cache", a.cache, "--other"] + (["--fixture", a.fixture] if a.fixture else []) + (["--dry"] if a.dry else []) + (["--days", str(a.days)] if a.days != 90 else [])
            rc = main(sub if not a.no_cohort else sub + ["--no-cohort"])
            if rc != 0:
                return rc
            with open(sp) as fh:
                rs.append(json.load(fh))
        print(render.render_compare(rs)); return 0
    if not a.address:
        print(json.dumps({"error": "an address is required (or --compare 0x… 0x…)"})); return 2
    addr = a.address.strip()
    if not ADDR_RE.match(addr):
        print(json.dumps({"error": "not a Hyperliquid address — expected 0x followed by 40 hex characters"})); return 2
    addr = addr.lower()
    state_path = os.path.join(a.state_dir, f"desk-{addr}.json")
    meta = {}
    bench = None
    if os.path.exists(BENCH_PATH):
        with open(BENCH_PATH) as fh:
            bench = json.load(fh).get("benchmark")
    r = None
    if (a.section or a.deep) and not a.fresh and os.path.exists(state_path) and time.time() - os.path.getmtime(state_path) < FRESH_S:
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
            hl = hl_api.HL(cache_dir=a.cache or None); hl.progress = log; mcp = _mcp_client(meta)
        log(f"[quant-desk] running senpi quant desk on {addr[:6]}…{addr[-4:]}")
        try:
            r = analyze(addr, hl, days=a.days, mcp=mcp, want_rank=not a.no_rank, want_cohort=not a.no_cohort, bench=bench, meta=meta, whose=whose)
        except hl_api.HLError as e:
            print(json.dumps({"error": f"Hyperliquid read failed: {e}", "address": addr})); return 1
        if not r["activity"]["fills"] and not r["book"]["positions"]:
            print(json.dumps({"error": "no perp activity in the window and no open positions — nothing to read", "address": addr, "days": a.days})); return 3
        log(f"[quant-desk] done in {meta['timings']['total']}s ({meta.get('hl_calls')} reads)")
        with open(state_path, "w") as fh:
            json.dump(r, fh, default=float)
    if a.deep:
        candles = {}
        if a.deep in ("protect", "replay"):
            if a.fixture:
                with open(a.fixture) as fh:
                    hl = hl_api.HLFixture(json.load(fh))
            else:
                hl = hl_api.HL(cache_dir=a.cache or None); hl.progress = log
            coins = sorted({p["coin"] for p in r["book"]["positions"]} | {e["coin"] for e in r["episodes"]})
            try:
                candles = timing_mod.load_candles(hl.candles(coins, days=a.days + 1))
            except Exception as e:  # noqa: BLE001
                log(f"[quant-desk] candles unavailable for the deep dive: {e}")
        data = {"protect": lambda: deep_mod.protect(r, candles), "replay": lambda: deep_mod.replay(r, candles), "funding": lambda: deep_mod.funding_forecast(r),
                "compare": lambda: deep_mod.compare_windows(r), "rules": lambda: deep_mod.rules(r), "regime": lambda: deep_mod.regime(r), "watch": lambda: deep_mod.watch(r),
                "smart": lambda: dict(cohorts=r.get("cohorts") or []), "scout": lambda: dict(opportunities=r.get("opportunities") or [], setups=r.get("setups")),
                "strategy": lambda: r.get("strategy") or {}}[a.deep]()
        if a.json:
            print(json.dumps(data, default=float))
        else:
            md = render.render_deep(a.deep, data, r)
            if r.get("whose") == "other":
                import voice
                md = voice.third_person(md, f"{addr[:6]}…{addr[-4:]}")
            print(md)
        return 0
    if (a.other or a.mine) and r.get("whose") != whose:
        r["whose"] = whose; r["followups"] = followups.offer(r, whose=whose)     # a cached run re-voiced
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "episodes"}, default=float))
    else:
        print(render.render(r, a.section))
    return 0


if __name__ == "__main__":
    sys.exit(main())
