#!/usr/bin/env python3
"""senpi-signals sweep — the whole gather in ONE process, then score.py. No agent orchestration.

    python3 sweep.py --print-feed          # what an agent runs for a user: prints only the feed
    python3 sweep.py --brief 3             # the short version another skill closes with: top 3 reads, one line each
    python3 sweep.py                       # debugging: run JSON, coverage lines, reads=<n>
    (SENPI_AUTH_TOKEN / SENPI_MCP_URL from env, like senpi-smart-money)

2.0 is ONE READING with no compare: it reads the market now, ranks what that reading shows, and keeps
no history, so back-to-back runs give the same feed. Compare over periods is v2.

What one sweep reads (the read budget, printed as `reads=<n>` at the end):
    1   market_list_instruments        universe + OI + price + 24h move + funding + volume, both dexes
    1   discovery_get_top_traders      the proven cohort (>= $1M lifetime realized), one page of 1000
    3   discovery_get_trader_state     the cohort's books, 50 wallets per read (SAMPLE_CAP 150)
    1   leaderboard_get_markets        the 4h board: crowd side, hot_4h_share, 4h price move
    1   leaderboard_get_momentum_events  the platform's own tiered whale/momentum events
    1   market_get_cross_asset_flows   BTC-led laggards
  = 8 reads. Every read fails soft: a failed source is recorded in `coverage`, the rest of the sweep
  still lands.

The cohort engine (smartmoney.py) and the MCP transport (mcp_client.py) are verbatim copies of
senpi-smart-money's, kept next to this file: skills install standalone, so this one never reaches into
another skill's folder. tests/test_vendored_parity.py fails when the copies drift.

Outputs (in --out-dir; default $SENPI_STATE_DIR/signals or ~/.openclaw/senpi-state/signals), overwritten
each run: current.json and signals.md. Nothing else is written. Stdlib only; Python 3.9+.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import contextlib
import datetime
import inspect
import io
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import score  # noqa: E402 — the ranker, called in-process

UNIVERSE_TOP_N = 120          # top-N by day notional volume (SKILL golden rule 6: floor + top-N)

# ── whale opens ───────────────────────────────────────────────────────────────
# A proven wallet OPENING size is the most concrete read this feed can carry, and it needs no
# history of our own: `discovery_get_trader_state` returns each position's own start time, so the
# age is a fact about the position rather than a diff against an earlier sweep. Adds and flips DO
# need a before-and-after and stay in v2 — a bigger position tells you nothing without the old size.
WHALE_OPEN_MIN_USD = 1_000_000     # below this it is a position, not a statement
WHALE_OPEN_MAX_AGE_S = 4 * 3600    # opened within the 4h horizon the rest of the feed reasons over
# every MCP read here is an idempotent GET, so one transient failure is retried rather than written
# off as a dark source (see _read)
READ_ATTEMPTS = 2
RETRY_SLEEP_S = 0.75
# Per-read SOCKET budget. The MCP server's own upstream budget is 20s, so a client budget at or under
# 20s abandons the read BEFORE the server can answer — and the server goes on working a query it can
# no longer deliver. The market/leaderboard reads are small and fast and keep the short budget; the
# cohort reads (the proven-trader lens, ~1000 ranking rows and 50 books per call) get a budget above
# the server's, with room for the handshake and the transfer.
READ_TIMEOUT_S = 15
COHORT_TIMEOUT_S = 30
COHORT_TOOLS = ("discovery_get_top_traders", "discovery_get_trader_state")
# Wall-clock budget for the WHOLE gather. The budgets above bound one read; nothing bounded the run,
# and at 15s/30s a degraded upstream can keep ~8 reads (each retried once) going for minutes. The
# agent that calls this script does have a bound — the brief path is given ~60s, the feed path ~120s
# — so an unbounded sweep does not return a thin feed on a bad day, it gets killed mid-flight and
# returns nothing. A read is not STARTED unless its own budget fits in what is left, which caps a run
# AT the deadline rather than at the deadline plus one hung read; whatever was read still renders,
# and the not-measured line names what was not.
BRIEF_DEADLINE_S = 45         # --brief closes someone else's answer, on that skill's ~60s budget
FEED_DEADLINE_S = 100         # --print-feed is the whole answer, on ~120s
UNIVERSE_MIN_VOL = score.CRED_FLOOR_VOL   # below this the ranker drops the name anyway
COHORT_PAGES = 1              # one page of 1000 (ALL_TIME, realized desc) fills the 150-wallet smart sample;
                              # the crowd band smartmoney pages for is not read here (crowd = 4h board)
BOARD_LIMIT = 500             # traders aggregated by leaderboard_get_markets (max 500)
MOMENTUM_LIMIT = 50
LAGGARD_MIN_FOLLOW = 0.8      # follow_rate below this is not a laggard, it is an uncorrelated name
HOURS_PER_YEAR = 24 * 365     # HL `funding` (metaAndAssetCtxs) is the HOURLY rate → ×8760 ×100 = %/yr


# ── the cohort engine: senpi-smart-money's, vendored verbatim next to this file ──
def _smartmoney():
    import smartmoney  # noqa: E402 — scripts/smartmoney.py, byte-identical to senpi-smart-money's
    return smartmoney


def _accepts_timeout(call_tool):
    """Whether this `call_tool` can carry a per-read budget as a `timeout=` keyword.

    It is asked, never assumed, because the budget must NOT ride in the arguments dict: that dict is
    the tool-arguments payload the MCP server receives, and a `timeout` key in it would be sent to
    the server as a tool argument. A `call_tool(name, args)` that knows nothing about budgets — the
    runtime's, and the tests' — is called with two arguments as before, on the callee's own budget.
    Uninspectable callables (builtins, some partials) take the same two-argument path.
    """
    try:
        params = inspect.signature(call_tool).parameters
    except (TypeError, ValueError):
        return False
    p = params.get("timeout")
    if p is not None:
        return p.kind is not inspect.Parameter.POSITIONAL_ONLY
    return any(q.kind is inspect.Parameter.VAR_KEYWORD for q in params.values())


class SweepDeadline(Exception):
    """A read that was never STARTED because the sweep's wall-clock deadline could not pay for it.

    It is not a failed read and must never be reported as one: nothing was asked of the server, so
    there is no upstream fault to chase. It is raised instead of returned so it travels the same path
    a transport failure does — including out through the vendored engine, which records it as a
    warning and leaves the cohort empty, exactly as it does for a read that failed.
    """


# ── the client adapter: smartmoney wants .mcp_call(tool, **kw); the runtime gives call_tool(name, args)
class Client:
    def __init__(self, call_tool, deadline_s=None, clock=time.monotonic):
        self.call_tool = call_tool
        self._call_takes_timeout = _accepts_timeout(call_tool)
        self.deadline_s = deadline_s      # None = unbounded (a caller with its own bound, and the tests)
        self._clock = clock
        self._started = clock()
        self.reads = 0
        self.failed = 0
        self.skipped = 0                  # reads the deadline never let start
        self.trader_states = []      # every discovery_get_trader_state row seen (for base-unit positions)
        self.top_traders = []        # every discovery_get_top_traders row seen (for lifetime realized PnL)
        self.wallet_pnl = {}         # proven wallet -> lifetime realized PnL, USD

    def elapsed(self):
        return self._clock() - self._started

    def can_start(self, budget):
        """Whether a read needing up to `budget` seconds can still finish inside the deadline.

        The projection is what makes the deadline a real cap: a check on elapsed time alone would let
        a 30s cohort read start at 44s of a 45s deadline and land at 74s, which is the overrun the
        deadline exists to prevent. Callers use it to skip a whole lens before paying for its first
        read (see `cohort`).
        """
        return self.deadline_s is None or self.elapsed() + budget <= self.deadline_s

    def mcp_call(self, tool, timeout=None, **kw):
        """Every read in this sweep is an idempotent GET, so a transport failure is retried before
        it is allowed to take a lens out of the run.

        The retry lives HERE and not in `_read()`. `_read` only wraps the four market/leaderboard
        calls; the cohort is gathered by the vendored engine, which calls this method directly — so
        a retry a layer up covered 4 of 8 reads and left the smart-money lens, the one this skill is
        named after, as exposed as before. A `success: false` envelope is deliberately NOT retried
        here: it is returned as-is so `_read` degrades that source exactly as it always has, and the
        vendored engine keeps seeing the response shape it expects rather than an exception.

        `timeout` is the budget the CALLER asks for — the vendored engine asks for one on every
        cohort read. It used to bind here and go no further, because only `kw` was forwarded, so
        every read ran on the transport's own 12s default; the cohort reads were cut off mid-flight
        while the MCP server (20s upstream budget) was still working them. It is forwarded now, and
        a cohort read is floored at COHORT_TIMEOUT_S so it outlives the server it is waiting on.

        A read is only STARTED while the sweep's wall-clock deadline can still pay for it; past that
        it raises SweepDeadline, untried. Patient reads without a run-level bound are how a slow
        upstream turns a thin feed into no feed — the sweep keeps waiting and the agent above it
        kills the process mid-flight.
        """
        budget = timeout or READ_TIMEOUT_S
        if tool in COHORT_TOOLS:
            budget = max(budget, COHORT_TIMEOUT_S)
        last = None
        for attempt in range(READ_ATTEMPTS):
            if not self.can_start(budget):
                # out of time for the RETRY of a read that genuinely failed: the failure is the fact
                # worth reporting, so it is re-raised unwrapped and the coverage line stays accurate
                if last is not None:
                    raise last
                self.skipped += 1
                raise SweepDeadline(
                    f"not started: {self.elapsed():.0f}s of the sweep's {self.deadline_s}s deadline "
                    f"is spent and this read needs up to {budget}s")
            self.reads += 1
            try:
                resp = (self.call_tool(tool, kw, timeout=budget) if self._call_takes_timeout
                        else self.call_tool(tool, kw))
            except Exception as e:  # noqa: BLE001 — transport; retried, then re-raised as before
                self.failed += 1
                last = e
                if attempt + 1 < READ_ATTEMPTS:
                    time.sleep(RETRY_SLEEP_S * (attempt + 1))
                continue
            if tool == "discovery_get_trader_state":
                self.trader_states.extend(_traders_of(_ok(resp)))
            elif tool == "discovery_get_top_traders":
                self.top_traders.extend(_traders_of(_ok(resp)))
            return resp
        raise last


def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _traders_of(data):
    """The row list of a read. The leaderboard tools wrap their client's payload under the same key
    it already uses — `leaderboard_get_markets` returns data.markets.markets[] and
    `leaderboard_get_momentum_events` data.events.events[] — so one same-key level is unwrapped."""
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        for k in ("traders", "markets", "events", "instruments", "data", "results"):
            v = data.get(k)
            if isinstance(v, dict) and isinstance(v.get(k), list):
                v = v[k]
            if isinstance(v, list):
                return [t for t in v if isinstance(t, dict)]
    return []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _read(c, tool, args, cov, key):
    """One guarded read: a failure degrades `key` in coverage, never the sweep. The RETRY is in
    `Client.mcp_call`, so it covers the cohort reads the vendored engine makes directly too."""
    try:
        data = _ok(c.mcp_call(tool, **args))
    except SweepDeadline as e:
        cov[key] = f"NO DATA: {tool} {e}"
        return None
    except Exception as e:  # noqa: BLE001
        cov[key] = f"failed: {tool}: {str(e)[:160]}"
        return None
    if data is None:
        cov[key] = f"failed: {tool}: tool reported success=false"
    return data


def _whale_open(p, wallet, szi, lifetime_pnl):
    """One proven wallet's position, IF it was opened recently enough and is big enough to be news.

    Read from a single sweep. `durationInSeconds` (or `startTime`) is the position's own age as the
    exchange reports it — it is not a comparison against anything we stored, which is why this is not
    a history detector. A position we cannot date is skipped rather than assumed fresh: an undated
    whale position is almost always an old one, and "opened just now" is exactly the claim that must
    never be guessed.
    """
    notional = abs(_num(p.get("positionValue")) or 0.0)
    if notional < WHALE_OPEN_MIN_USD:
        return None
    age = _num(p.get("durationInSeconds")) or _num(p.get("duration_in_seconds"))
    if age is None:
        start = _num(p.get("startTime")) or _num(p.get("start_time"))
        if not start:
            return None                       # undated: say nothing
        start_ms = start * 1000.0 if start < 1e12 else start
        age = (time.time() * 1000.0 - start_ms) / 1000.0
    if age < 0 or age > WHALE_OPEN_MAX_AGE_S:
        return None
    return {"wallet": wallet, "direction": "long" if szi > 0 else "short",
            "notional_usd": round(notional, 2), "age_seconds": round(age),
            "lifetime_realized_usd": lifetime_pnl}


def _dex(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


# ── 1. universe + per-asset metrics (one read) ─────────────────────────────────
def universe(c, cov, top_n):
    rows = _traders_of(_read(c, "market_list_instruments", {}, cov, "universe"))
    keep = []
    for r in rows:
        name, ctx = r.get("name"), r.get("context") if isinstance(r.get("context"), dict) else {}
        vol = _num(ctx.get("dayNtlVlm"))
        if not name or r.get("is_delisted") or vol is None or vol < UNIVERSE_MIN_VOL:
            continue
        price, prev = _num(ctx.get("markPx")), _num(ctx.get("prevDayPx"))
        fund = _num(ctx.get("funding"))
        keep.append((vol, name, {
            "dex": _dex(name),
            "notional_vol": vol,
            "oi": _num(ctx.get("openInterest")),           # BASE units — immune to the price move
            "price": price,
            "price_change_pct_24h": (round((price - prev) / prev * 100, 3)
                                     if price is not None and prev else None),
            "funding_annualized_pct": (round(fund * HOURS_PER_YEAR * 100, 3) if fund is not None else None),
            # the funding-sign proxy (positive ⇒ longs pay ⇒ crowd LONG); the 4h board overrides it
            "crowd_dir": (None if fund in (None, 0) else ("long" if fund > 0 else "short")),
            "crowd_source": "funding_sign",
        }))
    keep.sort(key=lambda t: -t[0])
    metrics = {name: m for _, name, m in keep[:top_n]}
    # cross-sectional funding percentile: where |annualized| ranks inside THIS universe
    fa = sorted(abs(m["funding_annualized_pct"]) for m in metrics.values()
                if m["funding_annualized_pct"] is not None)
    for m in metrics.values():
        f = m["funding_annualized_pct"]
        if f is not None and len(fa) > 1:
            m["funding_pctile"] = round(100.0 * sum(1 for x in fa if x < abs(f)) / (len(fa) - 1), 1)
    if metrics:
        cov["universe"] = f"ok ({len(metrics)} of {len(rows)} instruments kept)"
    elif "universe" not in cov:
        cov["universe"] = "NO DATA: market_list_instruments returned no rows"
    return metrics


# ── 2. the proven cohort (senpi-smart-money's engine, vendored as-is) ─────────
def cohort(c, cov, metrics):
    if not c.can_start(COHORT_TIMEOUT_S):
        # said here rather than let through the vendored engine, which would record it as a failed
        # read: nothing was asked of the server, and a cause invented for an empty cohort is exactly
        # the dead end this coverage line already cost one investigation
        c.skipped += 1
        cov["cohort"] = (f"NO DATA: the proven-cohort read was not started — {c.elapsed():.0f}s of the "
                         f"sweep's {c.deadline_s}s deadline is spent and a cohort read needs up to "
                         f"{COHORT_TIMEOUT_S}s. Not a failed read and not a token problem.")
        return
    try:
        sm = _smartmoney()
    except ImportError as e:
        cov["cohort"] = f"unavailable: {e}"
        return
    sm.MAX_PAGES = COHORT_PAGES
    meta = {"warnings": []}
    smart, _crowd = sm.build_cohorts(c, meta)
    per = sm.cohort_bias(c, smart, meta, "smart") if smart else {}
    if not smart or not per:
        cov["cohort"] = "NO DATA: " + (meta.get("cohorts_unavailable")
                                       or "; ".join(meta["warnings"]) or "empty cohort read")
        return
    smart_set = set(smart)
    for t in c.top_traders:          # the ranking rows the cohort was cut from carry lifetime realized PnL
        w = str(t.get("address") or t.get("trader_address") or t.get("wallet") or "").lower()
        if w in smart_set and w not in c.wallet_pnl:
            c.wallet_pnl[w] = round(sm._realized(t), 2)
    positions = {}                    # coin → {wallet: signed BASE size}
    opens = {}                        # coin → [whale opens, freshest first]
    for t in c.trader_states:
        w = str(t.get("address") or t.get("traderAddress") or "").lower()
        if w not in smart_set:
            continue
        for p in t.get("openPositions") or t.get("open_positions") or []:
            szi = _num(p.get("szi")) if isinstance(p, dict) else None
            if p.get("coin") and szi:
                positions.setdefault(p["coin"], {})[w] = szi
                o = _whale_open(p, w, szi, c.wallet_pnl.get(w))
                if o:
                    opens.setdefault(p["coin"], []).append(o)
    for v in opens.values():
        v.sort(key=lambda o: o["age_seconds"])
    covered = 0
    for coin, d in per.items():
        m = metrics.get(coin)
        if m is None:
            continue
        covered += 1
        ln, sn = int(d["n_long"]), int(d["n_short"])
        m.update({
            "smart_source": "proven_cohort",
            "smart_share_kind": "cohort_pct",      # headcount share — price cannot move it
            "smart_dir": "long" if ln >= sn else "short",
            "smart_share": round(100.0 * max(ln, sn) / len(smart), 2),
            "smart_long_n": ln, "smart_short_n": sn, "cohort_n": len(smart),
            "smart_net_bias": d["bias"], "smart_net_usd": d["net"],   # the engine's notional lean, as colour
            "smart_positions": positions.get(coin, {}),
            "whale_opens": opens.get(coin, []),
        })
    cov["cohort"] = (f"ok ({len(smart)} proven wallets, {covered} universe names positioned, "
                     f"{len(per) - covered} outside the universe)"
                     + ("; " + "; ".join(meta["warnings"]) if meta["warnings"] else ""))


# ── 3. the 4h board: crowd side + hot_4h_share + 4h price move ─────────────────
def board(c, cov, metrics):
    data = _read(c, "leaderboard_get_markets", {"limit": BOARD_LIMIT}, cov, "board_4h")
    rows = _traders_of(data)
    if not rows:
        cov.setdefault("board_4h", "NO DATA: leaderboard_get_markets returned no markets")
        return None
    hit = 0
    for r in rows:
        tok = str(r.get("token") or "")
        name = tok if tok in metrics else (f"xyz:{tok}" if f"xyz:{tok}" in metrics else None)
        if name is None:
            continue
        m = metrics[name]
        pc4 = _num(r.get("token_price_change_pct_4h"))
        if pc4 is not None:
            m["price_change_pct"] = pc4
        if r.get("is_dominant_direction") and str(r.get("direction")).lower() in ("long", "short"):
            hit += 1
            m["crowd_dir"] = str(r["direction"]).lower()
            m["crowd_source"] = "board_4h"
            m["hot_4h_dir"] = m["crowd_dir"]
            m["hot_4h_share"] = _num(r.get("pct_of_top_traders_gain"))
            m["hot_4h_trader_count"] = _num(r.get("trader_count"))
    inner = data.get("markets") if isinstance(data, dict) and isinstance(data.get("markets"), dict) else data
    src = _num(inner.get("source_trader_count")) if isinstance(inner, dict) else None
    cov["board_4h"] = f"ok ({hit} universe names on the board, {src or '?'} traders aggregated)"
    return src


# ── 4. events: momentum (the platform's own whale feed) + cross-asset laggards ─
def _age_min(ts, now):
    t = score._parse_ts(ts)
    return round((now - t).total_seconds() / 60.0, 1) if t else None


def momentum_events(c, cov, metrics, now):
    rows = _traders_of(_read(c, "leaderboard_get_momentum_events", {"limit": MOMENTUM_LIMIT}, cov, "momentum"))
    out = []
    for e in rows:
        if e.get("decision") != "sent":            # the platform already decided what is worth notifying
            continue
        tops = [p for p in (e.get("top_positions") or []) if isinstance(p, dict)]
        if not tops:
            continue
        p = max(tops, key=lambda x: abs(_num(x.get("delta_pnl")) or 0))
        mk = str(p.get("market") or "")
        asset = mk if mk in metrics else (f"xyz:{mk}" if f"xyz:{mk}" in metrics else None)
        if asset is None:
            continue
        m = metrics[asset]
        dp, wallet = _num(e.get("delta_pnl")) or 0.0, str(e.get("trader_id") or "")
        out.append({
            "asset": asset, "dex": m["dex"], "detector": "momentum_event",
            "direction": str(p.get("direction") or "").lower() or None,
            "numbers": [f"tier {e.get('tier')} {e.get('tier_label') or ''}".strip()
                        + f": a top trader's 4h PnL moved ${dp:,.0f}",
                        f"largest leg {mk} {str(p.get('direction') or '').upper()} "
                        f"(Δ ${_num(p.get('delta_pnl')) or 0:,.0f})"],
            "notional_vol": m.get("notional_vol"), "price_change_pct": m.get("price_change_pct"),
            "concrete_entity": (wallet[:6] + "…" + wallet[-4:]) if len(wallet) > 12 else (wallet or None),
            "magnitude": min(1.0, abs(dp) / 10_000_000), "age_minutes": _age_min(e.get("detected_at"), now),
            "as_of": e.get("detected_at"),
        })
    if "momentum" not in cov:
        cov["momentum"] = f"ok ({len(out)} sent events on universe names of {len(rows)} returned)"
    return out


def cross_asset(c, cov, metrics):
    data = _read(c, "market_get_cross_asset_flows", {}, cov, "cross_asset")
    if not isinstance(data, dict):
        cov.setdefault("cross_asset", "NO DATA: market_get_cross_asset_flows returned no payload")
        return []
    lead = data.get("leader") if isinstance(data.get("leader"), dict) else {}
    move, ldir = _num(lead.get("move_pct")), str(lead.get("direction") or "").lower()
    out = []
    for lg in data.get("laggards") or []:
        if not isinstance(lg, dict) or (_num(lg.get("follow_rate")) or 0) < LAGGARD_MIN_FOLLOW:
            continue
        asset = str(lg.get("asset") or "")
        m = metrics.get(asset)
        if m is None:
            continue
        gap = _num(lg.get("gap_pct")) or 0.0
        out.append({
            "asset": asset, "dex": m["dex"], "detector": "cross_asset_laggard",
            "direction": ldir if ldir in ("long", "short") else None,
            "numbers": [f"{lead.get('asset')} {move:+.1f}% in 4h" if move is not None else "leader moved",
                        f"{asset} {_num(lg.get('actual_move_pct')) or 0:+.1f}% vs "
                        f"{_num(lg.get('expected_move_pct')) or 0:+.1f}% expected (gap {gap:+.1f}pp)",
                        f"follow-rate {(_num(lg.get('follow_rate')) or 0) * 100:.0f}%"],
            "notional_vol": m.get("notional_vol"), "price_change_pct": m.get("price_change_pct"),
            "magnitude": min(1.0, abs(gap) / 5.0),
        })
    cov["cross_asset"] = (f"ok ({len(out)} laggards; leader {lead.get('asset')} {move:+.1f}%)"
                          if move is not None else "ok (no qualifying leader move)")
    return out


# ── the sweep ──────────────────────────────────────────────────────────────────
def gather(call_tool, top_n=UNIVERSE_TOP_N, now=None, deadline_s=FEED_DEADLINE_S, clock=time.monotonic):
    """Everything score.py needs, from one client: {"asset_metrics", "events", "coverage", "reads"}.

    `deadline_s` is the whole run's wall-clock budget (None = unbounded); `clock` is the monotonic
    source it measures against, injected so the deadline is testable without waiting for it.

    The four cheap market/leaderboard reads run BEFORE the cohort. The cohort is one lens and it can
    spend the entire deadline on its own — one ranking read plus a batch per 50 wallets, each of them
    the slowest call in the sweep — so reading it first is what makes a degraded upstream cost four
    detectors to save one. Last, it is the lens that goes dark, and the not-measured line says so.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    c, cov = Client(call_tool, deadline_s=deadline_s, clock=clock), {}
    metrics = universe(c, cov, top_n)
    if metrics:
        src = board(c, cov, metrics)
        events = momentum_events(c, cov, metrics, now) + cross_asset(c, cov, metrics)
        cohort(c, cov, metrics)
    else:
        src, events = None, []
    return {"generated": now.isoformat(), "asset_metrics": metrics, "events": events,
            "wallets": {w: {"realized_pnl_usd": v} for w, v in c.wallet_pnl.items()},
            "coverage": cov, "source_trader_count": src, "reads": c.reads, "reads_failed": c.failed,
            "reads_skipped": c.skipped}


def rank(current_path, feed_path, now=None, top=None, lens="both"):
    """score.py in-process (its CLI is its API), with no --state: one reading, nothing written to history.
    Returns its stdout JSON."""
    argv = [current_path, "--out", feed_path, "--lens", lens]
    if now:
        argv += ["--now", now]
    if top:
        argv += ["--top", str(top)]
    saved, buf = sys.argv, io.StringIO()
    try:
        sys.argv = ["score.py"] + argv
        with contextlib.redirect_stdout(buf):
            score.main()
    finally:
        sys.argv = saved
    return json.loads(buf.getvalue())


def default_out_dir():
    """Where current.json + signals.md land, overwritten each run: $SENPI_STATE_DIR/signals when set, else
    ~/.openclaw/senpi-state/signals. They are this run's outputs, not history."""
    base = os.environ.get("SENPI_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".openclaw", "senpi-state")
    return os.path.join(base, "signals")


def run(call_tool, out_dir=None, now=None, top_n=UNIVERSE_TOP_N, top=None, lens="both",
        deadline_s=FEED_DEADLINE_S, clock=time.monotonic):
    """One sweep: one reading, no compare. Writes current.json + signals.md and returns
    {"summary", "reads", "coverage", "current", "result", "out_dir"}."""
    out_dir = out_dir or default_out_dir()
    os.makedirs(out_dir, exist_ok=True)
    cur = gather(call_tool, top_n=top_n, now=score._parse_ts(now) if now else None,
                 deadline_s=deadline_s, clock=clock)
    # Hand the reading to score.py through files only THIS run can see, then publish them under the
    # stable names with os.replace (atomic). Two sweeps sharing an out dir — the chip and the sweep
    # inside a market pulse — used to write and re-read the same current.json, so an interleave let
    # one run rank the other's universe and present assets it never read.
    tag = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    run_current = os.path.join(out_dir, f".current.{tag}.json")
    run_feed = os.path.join(out_dir, f".signals.{tag}.md")
    try:
        with open(run_current, "w") as f:
            json.dump(cur, f)
        res = rank(run_current, run_feed, now=now, top=top, lens=lens)
        os.replace(run_current, os.path.join(out_dir, "current.json"))
        os.replace(run_feed, os.path.join(out_dir, "signals.md"))
    finally:
        for stale in (run_current, run_feed):
            if os.path.exists(stale):
                os.unlink(stale)
    cov = res.get("coverage") or {}
    summary = (f"assets {len(cur['asset_metrics'])} · events {len(cur['events'])} · "
               f"trade {len(res.get('trade') or [])} · social {len(res.get('social') or [])} · "
               f"smart-money lens {cov.get('smart_money_lens', 'n/a')} · "
               f"reads {cur['reads']} ({cur['reads_failed']} failed"
               + (f", {cur['reads_skipped']} past the {deadline_s}s deadline" if cur["reads_skipped"] else "")
               + ") · "
               f"out {out_dir}")
    return {"summary": summary, "reads": cur["reads"], "coverage": cur["coverage"], "current": cur,
            "result": res, "out_dir": out_dir}


# what a failed source is called in the feed's one "not measured" line — reader words, not tool names
PLAIN_SOURCE = {"universe": "market data", "cohort": "proven-trader positions", "board_4h": "the 4h leaderboard",
                "momentum": "momentum events", "cross_asset": "cross-asset flows"}


def universe_is_dark(coverage):
    """True when the ONE read everything else hangs off — market_list_instruments — gave us nothing.

    Every other lens is optional: it degrades and the feed says so. This one is not. With no
    universe there are no assets to score, so `score.py` renders its quiet-market line — and the
    skill teaches the agent that a quiet read is a correct answer to present as-is. A total outage
    would therefore be reported to the user as a calm market. It is an error, not a feed.
    """
    return str((coverage or {}).get("universe", "")).startswith(("failed", "NO DATA", "unavailable"))



def not_measured(rep):
    """ONE plain line naming any source that failed, or "" — so a dark lens is never read as "nothing
    happened", without coverage lines, counts or tool names."""
    dark = [PLAIN_SOURCE[k] for k, v in (rep.get("coverage") or {}).items()
            if k in PLAIN_SOURCE and str(v).startswith(("failed", "NO DATA", "unavailable"))]
    return "_Not measured this run: " + ", ".join(dark) + "._" if dark else ""


OUTAGE_LINE = ("_Senpi Signals could not read the market this run — the universe read failed, so "
               "nothing was measured. This is an outage, not a quiet market. Worth trying again in "
               "a minute._")


def feed_text(rep):
    """The rendered feed an agent presents, verbatim, plus the not-measured line when a source failed."""
    if universe_is_dark(rep.get("coverage")):
        return OUTAGE_LINE
    feed = (rep.get("result") or {}).get("feed_md")
    if feed is None:      # a caller that built `rep` by hand
        with open(os.path.join(rep["out_dir"], "signals.md")) as f:
            feed = f.read()
    feed = feed.rstrip("\n")
    dark = not_measured(rep)
    return feed + ("\n\n" + dark if dark else "")


def brief_text(rep, n):
    """The short version another skill closes with (senpi-market-pulse): the top n trade reads, one line
    each, no legend — plus the not-measured line when a source failed."""
    if universe_is_dark(rep.get("coverage")):
        # the brief closes EVERY market pulse: a dark universe must not print there as a market that
        # had nothing to say — that is the quiet-market read, inside someone else's answer
        return OUTAGE_LINE
    text = score.render_brief((rep.get("result") or {}).get("trade") or [], n)
    dark = not_measured(rep)
    return text + ("\n" + dark if dark else "")


def _adapter(client):
    """`call_tool(name, args)` over an MCPClient, with the per-read budget carried through as the
    SOCKET timeout — `args` stays exactly the tool-arguments payload the server receives."""
    def call_tool(name, args, timeout=READ_TIMEOUT_S):
        return client.mcp_call(name, timeout=timeout, **args)
    return call_tool


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi-signals: gather the universe and rank it, in one process")
    ap.add_argument("--out-dir", default=None,
                    help="where current.json + signals.md land (default: $SENPI_STATE_DIR/signals or ~/.openclaw/senpi-state/signals)")
    ap.add_argument("--now", default=None, help="ISO timestamp (tests / backfills)")
    ap.add_argument("--top-n", type=int, default=UNIVERSE_TOP_N, help="universe size (top-N by volume)")
    ap.add_argument("--top", type=int, default=None, help="feed length (score.py --top)")
    ap.add_argument("--lens", choices=["both", "trade", "social"], default="both")
    ap.add_argument("--print-feed", action="store_true",
                    help="print only the feed to present (signals.md, plus one line naming any failed source); "
                         "diagnostics are shown only if the sweep itself fails")
    ap.add_argument("--brief", type=int, default=None, metavar="N",
                    help="print only the top N trade reads, one line each (the short version another skill "
                         "closes with); same sweep, same outputs written")
    a = ap.parse_args(argv)
    from mcp_client import MCPClient  # noqa: E402 — scripts/mcp_client.py, byte-identical to senpi-smart-money's
    client = MCPClient()
    # the brief runs inside another skill's answer and is given less time than the full feed is
    kwargs = dict(out_dir=a.out_dir, now=a.now, top_n=a.top_n, top=a.top, lens=a.lens,
                  deadline_s=BRIEF_DEADLINE_S if a.brief is not None else FEED_DEADLINE_S)
    if a.print_feed or a.brief is not None:
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rep = run(_adapter(client), **kwargs)
        except Exception:
            sys.stderr.write(err.getvalue())
            raise
        print(brief_text(rep, a.brief) if a.brief is not None else feed_text(rep))
        # a dark universe is an outage, not a quiet market: exit non-zero so a caller that only
        # checks the status code cannot present it as a successful, empty read
        return 3 if universe_is_dark(rep["coverage"]) else 0
    rep = run(_adapter(client), **kwargs)
    print(json.dumps(rep["result"], indent=2))
    for k, v in rep["coverage"].items():
        print(f"[coverage] {k}: {v}", file=sys.stderr)
    print(f"[sweep] {rep['summary']}", file=sys.stderr)
    print(f"reads={rep['reads']}")
    if universe_is_dark(rep["coverage"]):
        print("[sweep] universe read failed — this run measured nothing; it is an outage, not a "
              "quiet market.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
