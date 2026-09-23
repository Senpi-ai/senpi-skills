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
import addresses as addr_book
import book as book_mod  # noqa: E402
import dsl as dsl_mod  # noqa: E402
import senpi_history  # noqa: E402
import smart_money  # noqa: E402
import strategy_read  # noqa: E402
import taxonomy  # noqa: E402
import timing as timing_mod  # noqa: E402
from roundtrips import episodes_from_fills  # noqa: E402

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
BENCH_PATH = os.path.join(HERE, "..", "references", "benchmark.json")
# desk.py does the work and carried no version of its own, so an install with a fresh SKILL.md and
# render.py but a stale desk.py passed every gate — which is exactly what happened on 2026-09-21: the
# step-4 progress line still read "senpi-smart-money" where the shipped source says "senpi-market-pulse".
# Pinned to render.VERSION by a test, and printed by --version so a stale copy is one command away.
VERSION = "1.29.0"

DEFAULT_STATE_DIR = os.path.join(tempfile.gettempdir(), "quant-desk")
FRESH_S = 600
PUBLIC_COHORT_N = 80          # live books read for the public smart-money cohort (parallel, cached 2 min)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# OpenClaw renders a RUNNING exec's stderr to the chat as it is produced, so these lines are the
# user's only company during a 40s sweep. They were nine gerund clauses with no number and no
# position — "scanning …, auditing …, running …" — which a host concatenates into one run-on
# sentence, and which is exactly what a reader saw. A step marker, the elapsed clock and a number
# the run has just learned make each line a beat that has to stand on its own.
_STEPS = 8


def step(n, msg, t0=None, found=None):
    where = f"[{n}/{_STEPS}]"
    when = f" {time.time() - t0:.0f}s" if t0 else ""
    what = f" — {found}" if found else ""
    print(f"[quant-desk] {where}{when} {msg}{what}", file=sys.stderr, flush=True)


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


# Hourly candles over 91 days, one request per coin, six at a time. It is the single most expensive
# step in the desk and it scales with how many names a book touches — which is unbounded. A wallet
# trading 174 coins asked for 187 candle pulls and the run was SIGTERM'd by the agent's exec timeout
# at "reading the tape: 120 of 187", having done all the work and produced nothing. Breadth is not a
# defect (a systematic book legitimately runs 80 names), so this caps the READ rather than refusing
# the book, and says in `meta` what it covered. (a live desk, 2026-09-23.)
MAX_TAPE_COINS = 60


def _tape(coins, book, track, meta):
    """The coins to pull hourly candles for, in priority order, capped at MAX_TAPE_COINS.

    Priority is where the reader's money is, not alphabetical: every OPEN position first (the
    protection audit and the stop ladder are about those, and dropping one would silently omit a
    live position), then BTC (every beta and correlation figure is against it), then traded coins by
    volume. Cohort and attention coins fill whatever is left — they colour the read, they are not
    the read.
    """
    held = {p["coin"] for p in book["positions"]}
    by_vol = sorted((track.get("coins") or {}).items(), key=lambda kv: -(kv[1].get("volume") or 0))
    order, seen = [], set()
    for c in list(held) + ["BTC"] + [c for c, _ in by_vol] + sorted(coins):
        if c in coins and c not in seen:
            seen.add(c); order.append(c)
    if len(order) <= MAX_TAPE_COINS:
        return set(order)
    kept, dropped = order[:MAX_TAPE_COINS], order[MAX_TAPE_COINS:]
    meta["tape"] = {"coins_touched": len(order), "coins_read": len(kept), "dropped": len(dropped),
                    "rule": f"every open position and BTC, then the {MAX_TAPE_COINS} largest by volume"}
    meta.setdefault("warnings", []).append(
        f"wide book: {len(order)} coins touched, hourly tape read for the {len(kept)} that carry the "
        f"position risk and the volume")
    return set(kept)


def _ratio_or_none(num, base, cap=10.0):
    """None when the base is too small for the ratio to mean anything (|ratio| > cap)."""
    if not base:
        return None
    r = num / base
    return None if abs(r) > cap else r


def analyze(addr, hl, days=90, mcp=None, want_rank=True, want_cohort=True, bench=None, meta=None, whose="mine",
            wallets=None):
    """One desk. `wallets`, when given, is every wallet of a senpi user's book: each is read on its
    own and the reads are unioned into a single `tr_raw` (see book.py). `addr` stays the label."""
    meta = meta if meta is not None else {}
    meta.setdefault("warnings", []); meta["timings"] = {}; meta["sources"] = {}
    t0 = time.time()
    pre_closed = pre_opened = None
    if wallets:
        step(1, f"scanning every fill across {len(wallets)} wallets …")
        tr_raw, pre_closed, pre_opened, per_wallet = book_mod.read(hl, wallets, days=days, progress=log)
        meta["wallets"] = per_wallet
    else:
        step(1, "scanning every fill, funding payment, transfer and resting order …")
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
    # Episodes for a book are built PER WALLET upstream: `episodes_from_fills` follows position per
    # coin through `startPosition`, and two wallets both trading BTC interleave into one broken track.
    pub_closed, pub_opened = (pre_closed, pre_opened) if pre_closed is not None else episodes_from_fills(fills)
    closed, opened = pub_closed, pub_opened
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
    indexed = None
    if mcp is not None:
        public_closed = len(closed)
        t_h = time.time()
        rows = []
        for _w in (wallets or [addr]):
            _r = senpi_history.fetch(mcp, _w, win_start, meta)
            for _e in _r:
                _e["wallet"] = _w
            rows.extend(_r)
        rows.sort(key=lambda e: e.get("close_time") or e.get("open_time") or 0)
        meta["timings"]["senpi_history"] = round(time.time() - t_h, 1)
        if rows:
            _partial = " — PARTIAL, a page failed to read and the totals below are short" if meta.get("senpi_history_partial") else ""
            closed, source = rows, f"senpi discovery ({len(rows)} closed position{'s' if len(rows) != 1 else ''}){_partial}"
            indexed = True
        elif public_closed and not meta.get("senpi_history_failed"):
            # The public endpoints show closed round trips in this window and senpi's index returned
            # none for the same window. That is a CONTRADICTION between two sources, not a quiet
            # wallet: this address is not in the index yet. Without the distinction the desk drops
            # silently to public fills — which miss TWAP slices — and a whale gets a confident desk
            # built on a fraction of their volume, with nothing in the output saying so.
            indexed = False
    # A read senpi could not ANSWER is not a wallet senpi does not HAVE. Leaving `indexed` at None
    # on a failed read is the difference between "we don't know" and a confident wrong claim.
    meta["sources"]["trades"] = source
    meta["indexed"] = indexed
    track = metrics.track_record(closed, opened, tr_raw["userFunding"], tr_raw["userFees"], win_start, tr_raw.get("userFees_xyz"))
    track["coverage"] = cov
    track["ledger_net"] = metrics.ledger_pnl(tr_raw["portfolio"], win_start)
    track["fill_taker_share"] = track["taker_share"]
    if source != "public fills":
        # senpi rows carry no maker/taker split — keep the fill-level execution read from the public stream
        fb_closed, fb_open = pub_closed, pub_opened      # already built above, over the same fills
        fb = metrics.track_record(fb_closed, fb_open, tr_raw["userFunding"], tr_raw["userFees"], win_start, tr_raw.get("userFees_xyz"))
        track["taker_share"], track["fee_recoverable"], track["volume"] = fb["taker_share"], fb["fee_recoverable"], fb["volume"]
        # …and the RATES with them. Item 12 measures them from the fills, and discovery episodes carry
        # no `taker_fees`, so on the indexed path `_tk_fees` sums to 0 and both rates fall back to the
        # schedule — leaving schedule-derived basis points sitting beside fills-derived volume, in a
        # sentence that still does not multiply out. (@0xsarvesh, #718, on 1.24.0.)
        track["fee_rate_taker"], track["fee_rate_maker"] = fb["fee_rate_taker"], fb["fee_rate_maker"]
        # …and the fee TOTAL those rates and that volume belong to. `track["fees"]` stays discovery's,
        # because the P&L breakdown above it is closed-trade accounting and that is the right number
        # there — but the execution sentence quotes fills-derived volume and fills-derived rates, and
        # was pairing them with discovery's fee total: "$218,602 in fees on $1.39B of volume at 2.8 bp
        # taker" multiplies out to $339k, off by 1.55x. Found on the first live 1.24.1 run.
        track["fee_total_exec"] = fb["fees"]
    step(2, "auditing the live book — every position's stop, liquidation distance and funding …", t0,
         f"{len(fills):,} fills across {len({e.get('coin') for e in fills})} coins")
    book = metrics.open_book(cs, oo, ctxs, ages, tr_raw.get("clearinghouseState_xyz"), tr_raw.get("frontendOpenOrders_xyz"), ctx_xyz,
                             metrics.whole_account_value(tr_raw.get("portfolio"), tr_raw.get("spotClearinghouseState")),
                             metrics.spot_free_usdc(tr_raw.get("spotClearinghouseState")))
    # B5 (@0xsarvesh, #718). The startPosition-jump heuristic can only see gaps it can infer from the
    # fills it DID get — a whole TWAP series older than the retained window leaves no jump behind.
    # Hyperliquid's own P&L series is an independent witness: what we rebuilt from fills, plus what
    # the open book is carrying, should land on the ledger's own delta. It never lands exactly (a
    # position already open when the window opened carries unrealized P&L that predates it), so this
    # is not a gate — it LOWERS the coverage figure the desk already prints when the gap is bigger
    # than the open book can explain, and stays out of the reader's page otherwise.
    cov["reconstructed_net"] = track["net"] + book["unrealized"]
    cov["ledger_net"] = track["ledger_net"]
    if track["ledger_net"]:
        _gap = abs(track["ledger_net"] - cov["reconstructed_net"])
        cov["ledger_gap"] = _gap
        cov["effective"] = min(cov["overall"] if cov["overall"] is not None else 1.0,
                               max(0.0, 1.0 - _gap / abs(track["ledger_net"])))
    # What senpi's own runtime is doing to these positions. The desk already reads the resting stop
    # off the exchange, so a DSL position is correctly PROTECTED today — but a price with no context
    # reads as a static stop when it is a floor that ratchets. Only ever annotates a position whose
    # backend row the exchange corroborates (see dsl.corroborated). Silent no-op without a token, on
    # an external wallet, or on any failure.
    _t_dsl = time.time()
    try:
        _n_dsl = sum(dsl_mod.attach(mcp, w, book, meta) for w in (wallets or [addr]))
        if _n_dsl:
            # was measured from `t1`, an earlier mark, so this read as the cumulative time to here
            # rather than what the DSL calls cost. (@0xsarvesh, #753.)
            meta["timings"]["dsl"] = round(time.time() - _t_dsl, 1)
    except Exception as e:  # noqa: BLE001
        meta["warnings"].append(f"runtime DSL state unavailable: {e}")
    fl = metrics.flows(tr_raw["ledger"], wallets or addr)
    pnl_curve = metrics.pnl_series(tr_raw["portfolio"], win_start)
    # transfer-adjusted, as equity_curve's docstring, methodology.md and SKILL rule 3 all promise.
    # `fl` is computed on the line above; passing [] meant a trader who withdrew their profit read as
    # a blown account — identical trades, +$55k, scored dd 90%/risk 18 withdrawn vs dd 4%/risk 82 left
    # on the exchange.
    eq = metrics.equity_curve(tr_raw["portfolio"], fl, win_start)
    # drawdown needs the RAW curve, not this one. Its numerator (cumulative P&L) is already
    # transfer-immune; its denominator is "the equity the fall came out of", and feeding it the
    # transfer-ADJUSTED curve made `av_at` go negative on an account funded mid-window — base
    # collapsed to ~0, dd_pct read 0%, and a real drawdown lost its risk penalty. The mirror case
    # saturated to 100% and printed "the account went to zero" on a live funded book. My B3 fix in
    # #733 introduced this. (@danielmbirochi, #718.)
    eq_raw = metrics.equity_curve(tr_raw["portfolio"], [], win_start)
    pnl_pts = pnl_curve            # same call, same args — computed once
    dd = metrics.drawdown(pnl_pts, eq_raw)
    funded = [v for _, v in eq if v > 0]
    avg_eq = (sum(funded) / len(funded)) if funded else None
    equity = dict(points=len(eq), start=eq[0][1] if eq else None, end=eq[-1][1] if eq else None, avg=avg_eq,
                  # a return is only a return against an equity base that means something. On a book
                  # that decayed to $0 the average equity is a rounding error and this read -3191.5%,
                  # which tells a reader nothing except that the denominator collapsed.
                  return_on_avg_equity=_ratio_or_none((track["ledger_net"] if track.get("ledger_net") is not None else track["net"]), avg_eq),
                  net_flows=sum(a for _, a in fl))
    act = metrics.activity(fills, win_start)
    majors, large = taxonomy.crypto_tiers(ctxs)
    breadth = market_mod.breadth(ctxs, ctx_xyz)
    # ---- cohorts + attention first (they name coins the candle pull must cover)
    rank, labels, lb = None, None, None
    cohorts, attention, fregime = [], None, None
    if (want_rank and not wallets) or (want_cohort and mcp is None):
        t2 = time.time()
        try:
            lb = hl.leaderboard()
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"leaderboard unavailable: {e}")
        meta["timings"]["leaderboard"] = round(time.time() - t2, 1)
    if want_rank and lb and not wallets:
        rank = hl_api.weekly_rank(lb, addr)
        if rank is None:
            meta["warnings"].append("address is not on Hyperliquid's leaderboard this week (no rank)")
    if want_cohort:
        t3 = time.time()
        step(3, "running senpi-smart-money — the proven cohort and the hot 30-day cohort against this book …", t0,
             f"{len(book['positions'])} open position(s), {len(book['naked'])} unprotected")
        if mcp is not None:
            for name, fetch in (("proven", smart_money.proven_cohort), ("hot", smart_money.hot_cohort)):
                try:
                    addrs = fetch(mcp, meta)
                    bks = smart_money.books(mcp, addrs, meta, progress=log, label={"proven": "the proven cohort, ", "hot": "the hot 30-day cohort, "}.get(name, "")) if addrs else []
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
        step(4, "running senpi-market-pulse — funding regime, where the top traders' gains sit, momentum …", t0)
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
    step(5, "reading the tape — 90 days of candles for every coin touched, regime by regime …", t0)
    # `closed` is discovery's rows on the indexed path, but the lever grid reads the FILLS-derived
    # episodes (B6's discovery half below) — so the tape has to cover both, or the grid silently
    # finds no candles for the coins it was just pointed at and abstains on the whole book.
    coins = {e["coin"] for e in closed + opened + pub_closed + pub_opened if metrics.in_window(e, win_start)} \
        | {p["coin"] for p in book["positions"]} | {"BTC"}
    for cv in cohorts:
        coins |= {h["coin"] for h in cv.get("they_hold") or []}
    if attention:
        coins |= {m["coin"] for m in attention["markets"] if m.get("coin")}
    coins = _tape(coins, book, track, meta)
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
    # B6, the discovery half (@0xsarvesh, #718). A discovery row is NOT a position held at one size:
    # the largest xyz:SKHX row on 0x615f9484… is 13,651 fills over 20 days carrying `size` 7,999
    # against a peak concurrent exposure near 880 — `size` accumulates over the position's life. The
    # degenerate one-step path priced an exit counterfactual on $9.9M of notional that never existed
    # at once: the same constant-size bug B6 removed on the fills path, on a LARGER base. Measured on
    # one wallet, same window, same minute: fills $35,032 (0.188 of losses) vs discovery $1,043,709
    # (1.032) — 30x apart, and the indexed number is the one every token-holding user gets.
    #
    # The fills are fetched on every run whatever the source, so the split is: TOTALS from discovery,
    # where it is genuinely more complete, and the LEVER GRID from episodes that carry a real size
    # path. An incomplete real exposure beats a complete fictional one.
    lev_win = in_win
    if source != "public fills":
        lev_win = [e for e in pub_closed if metrics.in_window(e, win_start)]
        meta["sources"]["levers"] = (f"public fills ({len(lev_win)} rebuildable round trips) — the "
                                     f"counterfactual grid needs the size path discovery rows do not carry")
    tm_rows = timing_mod.per_trade(lev_win, candles)
    tm = timing_mod.summarize(tm_rows) if tm_rows else None
    mf = market_mod.book_fit(book, candles, ctxs)
    regimes_days = market_mod.daily_regimes(daily, basket)
    rperf = market_mod.regime_performance(in_win, regimes_days)
    sm = cohorts[0] if cohorts else None
    ctx_by = {u["name"]: c for u, c in zip(ctxs[0]["universe"], ctxs[1])}
    if ctx_xyz:
        ctx_by.update({u["name"]: c for u, c in zip(ctx_xyz[0]["universe"], ctx_xyz[1])})
    coin_regimes = {c: market_mod.coin_regime(c, candles, ctx_by.get(c)) for c in coins}
    step(6, "finding the leaks, pricing the fixes, running senpi-signals for live matches …", t0,
         f"{len(coins)} coins of tape")
    # funding is a lever too, so it is priced once here and read by both
    _fl = min(score._funding_after(tr_raw["userFunding"], in_win, win_start, 24.0),
              -float(track.get("funding") or 0.0))
    # `closed` runs days+60 so episodes opening before the window can still be completed; every
    # figure the reader sees is 90-day. Handing the raw set to the levers put the size lever's
    # median-winner threshold on up to 150 days inside a 90-day desk. (@danielmbirochi, #718.)
    lv = score.levers(tm_rows, lev_win, tm, funding_late=max(0.0, _fl))
    lk = score.leaks(track, book, tm, tr_raw["userFunding"], in_win, win_start, days, lv)
    # the ONE quotable number: a union over trades, never the sum of the leaks above
    rec = score.recoverable(tm_rows, lev_win, track, tm, lv)
    setups = score.best_setups(lev_win, tm_rows)
    step(7, "reading the playbook — what the book actually does, by class, side and size …", t0,
         f"{len(lk)} leak(s) priced")
    fp = strategy_read.fingerprint(in_win, opened, book, track, act, tm, candles, ctxs, pnl_curve, win_start, now, equity=eq)
    strategy = dict(fingerprint=fp, statements=strategy_read.statements(fp, track, book), critique=strategy_read.critique(fp, track, book, mf, sm, cohorts))
    context = dict(breadth=breadth, funding_regime=fregime, attention=attention, regime_days=regimes_days, regime_performance=rperf)
    opps = opportunities.scout(in_win, setups, book, breadth, coin_regimes, cohorts, attention, majors, large)
    step(8, "scoring the book on six dimensions, comparing to the top traders, scouting today's matches …", t0)
    dims, quant = score.dimensions(track, book, dd, tm, mf, sm, in_win, pnl_curve)   # 90-day window, as everything else
    r = dict(address=addr, days=days, now_ms=now, window_start_ms=win_start, activity=act, track=track, book=book, equity=equity, drawdown=dd,
             pnl_curve=pnl_curve[-120:], timing=tm, market=mf, rank=rank, smart=sm, cohorts=cohorts, labels=labels, dimensions=dims, quant_score=quant,
             archetype=score.archetype(track, book, tm, act, opened), flags=score.flags(track, book, dd, tm, mf, labels), leaks=lk, recoverable=rec,
             setups=setups, families=score.families(closed, tm, track), strategy=strategy, context=context, opportunities=opps,
             benchmark=bench, benchmark_table=smart_money.benchmark_table(track, bench) if bench else None,
             episodes=[{k: v for k, v in e.items() if k != "size_path"} for e in in_win][-300:], meta=meta)
    r["whose"] = whose
    r["indexed"] = indexed
    if wallets:
        r["wallets"] = list(wallets)
        # Which wallet did what. A book answers "how am I trading"; this answers "and which of my
        # strategies is carrying it" — the question a reader asks next, and the only one the union
        # destroys by construction.
        _bw = {w: dict(wallet=w, trades=0, wins=0, realized=0.0, fees=0.0, volume=0.0, unrealized=0.0, open=0) for w in wallets}
        for e in in_win:
            row = _bw.get(e.get("wallet"))
            if row is None:
                continue
            row["trades"] += 1; row["wins"] += 1 if e["win"] else 0
            row["realized"] += e["realized"]; row["fees"] += e["fees"]; row["volume"] += e["volume"]
        # track_record charges fees on STILL-OPEN episodes to the window too (the entry was paid for
        # inside it). Leaving them out here made the per-wallet nets sum to $71,221 under a headline
        # net of $67,207 — a table that does not add up to the number above it.
        for e in opened:
            row = _bw.get(e.get("wallet"))
            if row is not None:
                row["fees"] += e["fees"]
        for pos in book["positions"]:
            row = _bw.get(pos.get("wallet"))
            if row is not None:
                row["unrealized"] += pos["unrealized"]; row["open"] += 1
        for x in (tr_raw["userFunding"] or []):
            row = _bw.get(x.get("wallet"))
            if row is not None and x["time"] >= win_start:
                row["funding"] = row.get("funding", 0.0) + float(x["delta"]["usdc"])
        # `realized` on an episode is GROSS. The desk's own headline number is net of fees and
        # funding, and a column headed "Realized" sitting under it had better mean the same thing —
        # the two wallets here summed to +$123,624 gross beside a net of +$67,207 on the same page.
        for row in _bw.values():
            row["funding"] = row.get("funding", 0.0)
            row["net"] = row["realized"] - row["fees"] + row["funding"]
        r["by_wallet"] = sorted(_bw.values(), key=lambda x: -(x["net"] + x["unrealized"]))
    r["verdict"] = score.verdict(track, book, dims, lk)
    r["followups"] = followups.offer(r, whose=whose)
    meta["timings"]["total"] = round(time.time() - t0, 1); meta["hl_calls"] = hl.calls
    return r


class _Flight:
    """One desk per address per box, via a PID file in the state dir.

    Deliberately not a hard mutex: the only job is to stop an agent that cannot see a result from
    launching a second, third and fifth sweep of the same wallet into the same rate bucket. A stale
    file (the process died, or was SIGTERM'd by an exec timeout — which is exactly how this starts)
    must never wedge the address, so a lock whose PID is gone, or which is older than STALE_S, is
    taken over rather than respected.
    """
    STALE_S = 15 * 60

    def __init__(self, state_dir, addr, force=False):
        self.path = os.path.join(state_dir, f"flight-{addr}.pid")
        self.force = force
        self.held = False
        self._age = 0

    def _alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError, TypeError):
            return False
        except PermissionError:
            return True          # someone else's process, but it exists

    def age_s(self):
        return self._age

    def acquire(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path) and not self.force:
                age = int(time.time() - os.path.getmtime(self.path))
                try:
                    with open(self.path) as fh:
                        pid = int((fh.read() or "0").strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and pid != os.getpid() and self._alive(pid) and age < self.STALE_S:
                    self._age = age
                    return False
            with open(self.path, "w") as fh:
                fh.write(str(os.getpid()))
            self.held = True
        except OSError:
            self.held = False    # a state dir we cannot write is not a reason to refuse the desk
        return True

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            os.remove(self.path)
        except OSError:
            pass


def resolve_whose(book, addr, other=False, mine=False, claim=False):
    """Whose book this is. **An address is the reader's own book unless we know otherwise.**

    The flagship path is a Hyperliquid trader pasting their own address to see their own desk, so
    that is the default: asking them to claim it first would put a question in front of the one
    moment the product exists for.

    What the address book adds is memory, not suspicion. An address already read as someone else's
    stays someone else's — the reader looked at a whale last week, and a bare re-run should not
    start giving them the whale's leaks to fix. Anything the book has not seen is theirs.

    Order: an explicit flag on this run, then what the book already knows, then the default.
    """
    if other:
        return "other"
    if mine or claim:
        return "mine"
    if addr_book.relationship(book, addr) == addr_book.ANALYZED:
        return "other"                 # we have already established this one is not theirs
    return "mine"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Senpi Quant Desk: the desk for any Hyperliquid address")
    ap.add_argument("address", nargs="?", help="the wallet; omit with --compare")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mine", action="store_true", help="the reader's own book (second person)")
    g.add_argument("--claim", action="store_true",
                   help="the reader says this address is theirs: read it as their book AND remember it "
                        "(a claim, not proof — we cannot verify ownership of an address from a message)")
    g.add_argument("--other", "--analyst", dest="other", action="store_true", help="someone else's book (analyst mode): third person, learn-from-them follow-ups")
    ap.add_argument("--compare", nargs="+", metavar="0x", help="two or more addresses side by side (cached runs are reused)")
    ap.add_argument("--book", nargs="+", metavar="0x",
                    help="ONE desk over several wallets — a senpi user's whole book. Every wallet is read "
                         "and the reads are unioned: one score, one set of leaks, one P&L. Use this for a "
                         "senpi user (every strategy wallet, closed ones included), not --compare, which "
                         "scores each wallet separately and answers a different question.")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--version", action="version", version=f"quant-desk desk.py {VERSION}",
                    help="print this script's version — the one gate that catches a stale desk.py")
    ap.add_argument("--json", action="store_true", help="print the analysis document instead of Markdown")
    ap.add_argument("--section", choices=render.SECTIONS, action="append", help="render only these sections (repeatable)")
    ap.add_argument("--deep", choices=sorted(deep_mod.MODES), help="a follow-up deep dive from the cached run (protect, smart, scout, replay, funding, regime, compare, rules, strategy, watch)")
    ap.add_argument("--fixture", help="recorded responses (JSON) — offline run")
    ap.add_argument("--dry", action="store_true", help="never touch the network (requires --fixture)")
    ap.add_argument("--no-rank", action="store_true"); ap.add_argument("--no-cohort", action="store_true")
    ap.add_argument("--cache", default=hl_api.DEFAULT_CACHE, help="HTTP cache dir ('' to disable)")
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--force", action="store_true",
                    help="read a vault as if it were a trader's wallet, and ignore a desk already in "
                         "flight on this address (both are refusals that are usually right)")
    ap.add_argument("--fresh", action="store_true", help="ignore a cached analysis")
    ap.add_argument("--find", metavar="BAND", choices=sorted(hl_api.FIND_BANDS),
                    help="candidate wallets to run the desk on, by account size: "
                         + ", ".join(sorted(hl_api.FIND_BANDS)))
    ap.add_argument("--find-window", default="week", choices=("week", "month", "allTime"),
                    help="who is hot right now (week) or who has held up (month/allTime)")
    ap.add_argument("--find-losers", action="store_true",
                    help="the worst in the band instead of the best — the desk reads a losing book just as well")
    ap.add_argument("--addresses", action="store_true",
                    help="print this box's address book as JSON and exit — which wallets are the reader's, "
                         "which they have read, and which are not in senpi's index yet")
    a = ap.parse_args(argv)
    os.makedirs(a.state_dir, exist_ok=True)
    book = addr_book.load(a.state_dir)
    if a.find:
        hl = hl_api.HL(cache_dir=a.cache or None)
        # A ~40 MB public fetch with no auth and no SLA. Everywhere else the desk degrades on it
        # (`leaderboard unavailable` as a warning); here it was the whole answer and outside any try,
        # so a slow venue printed a traceback for the agent to read back to the reader.
        try:
            lb = hl.leaderboard()
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"error": f"Hyperliquid's leaderboard did not answer: {e}",
                              "retry": "it is a large public file — worth one more try in a minute"}))
            return 2
        rows = hl_api.find_traders(lb, band=a.find, window=a.find_window,
                                   losers=a.find_losers)
        print(json.dumps({"band": a.find, "window": a.find_window,
                          "worst_first": bool(a.find_losers), "candidates": rows}, indent=2))
        return 0
    if a.addresses:
        print(json.dumps(book, indent=2, sort_keys=True)); return 0
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
    # A book runs the SAME path as one address — analyze, the empty check, the state file, --deep,
    # --section, the render all work unchanged on a merged read. The only things that differ are
    # which wallets get read, what an empty result means, and that a book is not an address to
    # remember. Everything else is shared, deliberately: a parallel branch here drifts.
    wallets = None
    if a.book:
        wallets = []
        for x in a.book:
            x = x.strip().lower()
            if not ADDR_RE.match(x):
                print(json.dumps({"error": f"not a Hyperliquid address: {x}"})); return 2
            if x not in wallets:
                wallets.append(x)
        # The label is the first wallet: the state file and the header key on it, and a book is not
        # itself an address. `wallets` carries the truth, and the header reads "across N wallets".
        addr = wallets[0]
    elif not a.address:
        print(json.dumps({"error": "an address is required (or --book 0x… 0x… for a whole senpi book, "
                                   "or --compare 0x… 0x… to score wallets separately)"})); return 2
    else:
        addr = a.address.strip()
        if not ADDR_RE.match(addr):
            print(json.dumps({"error": "not a Hyperliquid address — expected 0x followed by 40 hex characters"})); return 2
        addr = addr.lower()
    # Whose book this is comes from the address book, not from how the request was phrased. An
    # UNKNOWN address is someone else's: the desk gives advice in the second person, and delivering
    # that about a stranger's trading is the failure worth defaulting against. Owner voice needs a
    # wallet senpi issued, a claim the reader already made, or an explicit flag on this run.
    # A BOOK is the reader's own by construction — they resolved these wallets from their own
    # `strategy_list`. Running it through the address book let one stale `analyzed` mark on one of N
    # wallets flip the voice of the whole book to the third person: "Their desk — across 2 wallets",
    # to the person who owns them. Only an explicit --other overrides that.
    whose = ("other" if a.other else "mine") if wallets else \
        resolve_whose(book, addr, other=a.other, mine=a.mine, claim=a.claim)
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
        # ---- is this even a trader? One call, before ~200. See hl_api.subject().
        subj = hl.subject(addr) if not a.fixture else {"role": "user"}
        if subj.get("role") == "vault" and not a.force:
            nm = subj.get("name") or "a vault"
            desc = (subj.get("description") or "").strip()
            print(json.dumps({
                "not_a_trader": "vault",
                "name": subj.get("name"), "description": desc or None, "address": addr,
                "error": f"{nm} is a Hyperliquid VAULT, not a trader's wallet — the desk reads how "
                         f"someone trades, and a vault is a pooled book run by its leader.",
                "say_to_the_reader": (
                    f"That address is **{nm}**"
                    + (f" — {desc[0].lower() + desc[1:]}" if desc else "")
                    + " A trader scorecard does not describe it: there is no entry thesis to time, "
                      "no stop to place, and the P&L belongs to its depositors rather than to one "
                      "trader. Want me to run the desk on your own wallet instead?"),
                "if_you_meant_it": "re-run with --force to read it as a trader anyway",
                "leader": subj.get("leader")}, indent=2))
            return 4
        # ---- one desk per address per box. The desk takes 20-60s (longer on a wide book), so an
        # agent that does not see a result re-runs it — and on 2026-09-23 one agent had FIVE
        # concurrent runs on the same wallet, each making ~200 requests into the same per-IP rate
        # bucket. The 429s that killed three of them were entirely self-inflicted: siblings
        # competing for a bucket that refills on a minute. A second run adds nothing a first is not
        # already computing, so refuse it and say where the real one is.
        lock = _Flight(a.state_dir, addr, force=a.force)
        if not lock.acquire():
            print(json.dumps({
                "already_running": True, "address": addr, "since_s": lock.age_s(),
                "error": f"a desk on {addr[:6]}…{addr[-4:]} has been running on this box for "
                         f"{lock.age_s()}s. Starting a second one does not make the first finish — "
                         f"they compete for the same rate limit, which is how runs die.",
                "what_to_do": "wait for the run in flight; its result lands in the same state file. "
                              "Poll the exec session you already started rather than launching another."},
                indent=2))
            return 5
        log(f"[quant-desk] running senpi quant desk on {addr[:6]}…{addr[-4:]}")
        try:
            r = analyze(addr, hl, days=a.days, mcp=mcp, want_rank=not a.no_rank and not wallets,
                        want_cohort=not a.no_cohort, bench=bench, meta=meta, whose=whose, wallets=wallets)
        except hl_api.HLError as e:
            # A 429 here is the venue's rate bucket, not a broken wallet, and it is the one failure
            # a reader can act on — so say which it was rather than printing the raw exception.
            rate = "429" in str(e)
            print(json.dumps({
                "error": f"Hyperliquid read failed: {e}",
                **({"wallets": wallets} if wallets else {"address": addr}),
                "rate_limited": rate,
                "what_to_do": ("Hyperliquid rate-limited this box. Wait about a minute and run it "
                               "ONCE more — do not launch a second run while one is in flight, that "
                               "is what exhausts the budget.") if rate else
                              "a transient read failure — one retry is worth it"})); return 1
        finally:
            lock.release()
        if not r["activity"]["fills"] and not r["book"]["positions"] and wallets:
            # A book that reads empty is a different dead end: this reader already resolved their
            # wallets, so pointing them back at strategy_list is noise.
            print(json.dumps({
                "error": f"no PERP activity in the last {a.days} days across any of these "
                         f"{len(wallets)} wallets, and no open perp positions. "
                         f"Spot trades and transfers are not perp activity and are not read here.",
                "what_this_usually_means": "a strategy that was funded but never filled has no history "
                                           "to read, and a wallet that only ever held spot or moved "
                                           "funds has none either. If some of these were closed "
                                           "strategies, they may simply predate the window.",
                "wallets": wallets, "days": a.days, "indexed": r.get("indexed"),
                "perp_fills_in_window": 0, "open_perp_positions": 0})); return 3
        if not r["activity"]["fills"] and not r["book"]["positions"]:
            # Carry `indexed` out even here. Without it a caller cannot tell "senpi has never seen this
            # wallet" from "senpi has it and there is simply nothing in the window" — and those two need
            # opposite things said to the reader. Spot fills do not count as perp activity, so a wallet
            # the owner knows is busy can land here; say which it is rather than "nothing to read".
            print(json.dumps({
                # A senpi user's embedded wallet is a FUNDING wallet — deposits land there and move
                # out to the strategy subwallets that actually trade. Two of four users on launch
                # night were pointed here by their own agent and told their book was empty, on books
                # that trade daily. The guidance is fixed in SKILL.md; this says it too, so the
                # dead end corrects itself even when the wrong wallet is picked.
                "error": f"no PERP activity in the last {a.days} days and no open perp positions. "
                         f"Spot trades and transfers are not perp activity and are not read here.",
                "if_this_is_your_own_wallet": "If you trade through senpi, your perp history is in your "
                                              "STRATEGY wallets, not this one — an embedded wallet is the "
                                              "funding wallet. Resolve them with strategy_list and run the "
                                              "desk on those.",
                "address": addr, "days": a.days, "indexed": r.get("indexed"),
                "perp_fills_in_window": 0, "open_perp_positions": 0})); return 3
        log(f"[quant-desk] done in {meta['timings']['total']}s ({meta.get('hl_calls')} reads)")
        # atomic: stage 1 writes this and stages 2-4 read it, so a half-written relay file breaks
        # the whole staged run — and JSONDecodeError is not an HLError, so the handler above misses it
        hl_api._atomic_json(state_path, json.loads(json.dumps(r, default=float)))
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
    if (a.other or a.mine or a.claim) and r.get("whose") != whose:
        r["whose"] = whose; r["followups"] = followups.offer(r, whose=whose)     # a cached run re-voiced
    rel = addr_book.CLAIMED if a.claim else (addr_book.ANALYZED if whose == "other" else None)
    tr = r.get("track") or {}
    if wallets:
        # Recording a book under its first wallet would file the whole book's verdict against one
        # subwallet, and a later single-wallet run on that address would read back the wrong digest.
        addr_book.save(a.state_dir, book)
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "episodes"}, default=float))
        else:
            print(render.render(r, a.section))
        return 0
    addr_book.record(book, addr, relationship=rel, indexed=r.get("indexed"),
                     # `score` and `generated` are not keys on the record — both were silently None
                     digest={"at": r.get("now_ms"), "score": r.get("quant_score"),
                             "verdict": r.get("verdict"), "net": tr.get("ledger_net")})
    addr_book.save(a.state_dir, book)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "episodes"}, default=float))
    else:
        print(render.render(r, a.section))
    return 0


if __name__ == "__main__":
    sys.exit(main())
