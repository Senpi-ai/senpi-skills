"""MAGPIE · PRE-LISTING book — supervised scanner (Runtime 3.0 port of the v2
Magpie PRE-LISTING leg).

DYNAMIC UNIVERSE. Each tick it discovers IPOPs (pre-IPO perpetuals) off the LIVE
market_list_instruments read by the VENUE'S LEVERAGE CAP (scoring.ipop_passes_universe:
name startswith 'xyz:', not delisted, max_leverage <= ipopMaxLeverageCap, dayNtlVlm
over the signature floor and over the book's own floor; the v2 funding band is
opt-in and off — see scoring), then for each LIVE IPOP scores the pre-listing trend
on CLOSED 1h and 4h candles (the bar in progress is excluded: its low and high are
partial, so a structure counted over it could flip inside the bar) via the pure
scoring module and emits a marginPct
intent + a per-signal leverage clamped to that instrument's venue max. Read-only,
single-pass. NO name is ever emitted that is not present in the live universe read.

EVERY ctx.senpi_mcp.call_tool is read-guarded (degrade, never crash): a transient/
permission error on the instrument-discovery read, a candle read, or the
smart-money read returns the empty/None degrade path — the tick emits fewer (or
zero) signals rather than rolling back the whole tick.

The wallet is read too (strategy_get_clearinghouse_state), on every tick that has a
candidate, and a name it already holds is skipped. The 12-minute signal dedup is
shorter than the 15-minute tick, so before 1.2.0 a held IPOP was emitted again on
every tick and the runtime was asked to open it again. That read is the one
exception to degrade-and-continue: unreadable positions mean NO opens this tick
(logged) — a held name that cannot be seen would be re-emitted.

Today the live universe typically has ZERO IPOPs (the marquee names have already
converted to STANDARD equity perps); the book correctly emits nothing until
trade.xyz lists the next pre-IPO perpetual — that is the event it waits for."""

import sys
import time

import scoring

def _sm_row_matches(row, token, target):
    """True if leaderboard row `row` is the market for `target`.

    `leaderboard_get_markets` returns BARE tickers (`NVDA`) plus a separate `dex`
    field, while our universe carries the qualified name (`xyz:NVDA`). A raw
    `token != target` compare therefore NEVER matches an xyz name, so every xyz
    instrument reads as "no smart-money data" and a hard SM gate blocks it
    permanently. Compare bare tickers, and require the dex to agree so a main-DEX
    name cannot cross-match its xyz twin (e.g. main `GOLD` vs `xyz:GOLD`)."""
    tok = str(token or "").upper()
    want = str(target or "").upper()
    if tok.split(":", 1)[-1] != want.split(":", 1)[-1]:
        return False
    row_xyz = (str((row or {}).get("dex", "")).strip().lower() == "xyz"
               or tok.startswith("XYZ:"))
    return row_xyz == want.startswith("XYZ:")


_DEFAULT_TTL = 720   # 12m signal-dedup: don't re-fire an IPOP while a signal is in flight


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error must NOT roll back the tick.
    Returns None so the caller's degrade path applies."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[magpie.pre_listing.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _fetch_ipop_universe(ctx, config):
    """Discover the LIVE IPOP universe off market_list_instruments(dex='xyz').
    Validates every candidate against the live read via scoring.ipop_passes_universe
    — only instruments that actually exist (and carry the leverage-cap signature over
    the volume floors) are returned. Degrades to [] on a failed read."""
    raw = _read(ctx, "market_list_instruments", {"dex": "xyz"})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return []
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    instruments = data.get("instruments", data) if isinstance(data, dict) else data
    if not isinstance(instruments, list):
        return []
    universe = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        name = inst.get("name", "")
        ctx_block = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        funding_abs = abs(float(scoring._f(ctx_block, "funding")))
        vol_usd = float(scoring._f(ctx_block, "dayNtlVlm"))
        max_lev = inst.get("max_leverage", 999)
        if not scoring.ipop_passes_universe(
                name, inst.get("is_delisted", False), funding_abs, max_lev, vol_usd, config):
            continue
        universe.append({"name": name, "max_leverage": int(scoring._f({"v": max_lev}, "v", default=5) or 5),
                         "funding": funding_abs, "vol_usd": vol_usd})
    return universe


def _fetch_candles(ctx, asset):
    md = _read(ctx, "market_get_asset_data", {
        "asset": asset,
        "candle_intervals": ["1h", "4h"],
        "dex": "xyz",
        "include_funding": False,
        "include_order_book": False,
    })
    if not md or (isinstance(md, dict) and not md.get("success", True)):
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    return (d.get("candles", {}) or {}) if isinstance(d, dict) else {}


def _fetch_sm_direction(ctx, asset, min_traders=10):
    """Net smart-money lean for `asset` from leaderboard_get_markets (USER-SCOPE
    auth). Returns (direction|None, tilt_pct). None direction triggers the
    sparse-pre-listing trend-only fallback in scoring."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw or (isinstance(raw, dict) and not raw.get("success", True)):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0
    long_pct, short_pct, found = 0.0, 0.0, False
    side_n = {}                                    # per-side headcount of the 4h leaders
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, asset):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        side_n[d] = int(m.get("trader_count", 0) or 0)
        pct = scoring._f(m, "pct_of_top_traders_gain", "longPct")
        if d == "LONG":
            long_pct = pct
        elif d == "SHORT":
            short_pct = pct
    if not found:
        return None, 0.0
    total = long_pct + short_pct
    if total <= 0:
        return "NEUTRAL", 50.0
    long_ratio = (long_pct / total) * 100
    if side_n.get("LONG" if long_ratio >= 50 else "SHORT", 0) < min_traders:
        return None, 0.0   # the leading side is too thin (< minTraderCount of the 4h leaders) to call a lean
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def _held_names(ctx):
    """Names the wallet already holds, read off strategy_get_clearinghouse_state.

    Returns (names, ok). `names` is a set of UPPER-cased, xyz-qualified coin names from BOTH
    sections of the one wallet (`main` and `xyz` are two views of the same account; positions
    live INSIDE each section, never at the top level). A coin in the `xyz` section may arrive
    bare (`SPCX`) or qualified (`xyz:SPCX`); both become `XYZ:SPCX`, so a held xyz name always
    matches the universe's spelling and a main-DEX twin (`GOLD` vs `xyz:GOLD`) never does.
    `ok` is False when the read failed or came back in no readable shape: the caller then opens
    nothing this tick, because a held name it cannot see would be re-emitted and pyramided."""
    if not ctx.wallet:
        print("[magpie.pre_listing.scan] no wallet on ctx — positions unreadable", file=sys.stderr)
        return set(), False
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not isinstance(ch, dict) or not ch.get("success", True):
        return set(), False
    data = ch.get("data", ch)
    held, readable = set(), False
    for section_name in ("main", "xyz"):
        section = data.get(section_name) if isinstance(data, dict) else None
        if not isinstance(section, dict):
            continue
        readable = True
        for ap in section.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else None
            if not isinstance(pos, dict) or scoring._f(pos, "szi") == 0:
                continue
            coin = str(pos.get("coin", "") or "").upper()
            if not coin:
                continue
            if section_name == "xyz" and not coin.startswith("XYZ:"):
                coin = "XYZ:" + coin
            held.add(coin)
    if not readable:
        print("[magpie.pre_listing.scan] clearinghouse read has no main/xyz section — positions unreadable",
              file=sys.stderr)
    return held, readable


def scan(inputs, ctx):
    min_score = int(inputs.get("minScore", 5))
    min_traders = int(inputs.get("minTraderCount", 10))   # 4h-board headcount floor
    margin_pct = float(inputs.get("marginPct", 25))   # PERCENT of withdrawable (0,100], not a fraction
    max_lev = int(inputs.get("maxLeverage", 3))       # IPOP discovery-bounds regime
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    now = time.time()

    recent = (ctx.state.last() or {}).get("recent", {}) if ctx.state else {}

    universe = _fetch_ipop_universe(ctx, inputs)
    ipop_names = [u["name"] for u in universe]

    def _persist():
        if ctx.state is None:
            return
        try:
            ctx.state.append({"recent": recent, "result": {
                "ts": now, "ipop_universe": ipop_names, "emitted": [c["asset"] for c in out]}})
        except Exception as exc:  # noqa: BLE001
            print(f"[magpie.pre_listing.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    out = []
    if not universe:
        # No IPOPs in the live universe — no name at or under the IPOP cap over the volume floors.
        # This is the steady state; the book waits for the next pre-IPO listing.
        print("[magpie.pre_listing.scan] WAITING — no IPOPs in live xyz universe "
              "(no instrument at or under the IPOP leverage cap over the volume floors)", file=sys.stderr)
        _persist()
        return out

    held, held_ok = _held_names(ctx)
    if not held_ok:
        print("[magpie.pre_listing.scan] HOLD — wallet positions unreadable; no opens this tick "
              "(a held name cannot be told from a new one; the next tick retries)", file=sys.stderr)
        _persist()
        return out

    for inst in universe:
        coin = inst["name"]
        cu = coin.upper()
        if cu in held:                                       # already holding — never re-emit a held name
            continue
        last = recent.get(cu)
        if last is not None and (now - last) < ttl:        # signal-dedup
            continue
        candles = _fetch_candles(ctx, coin)
        if not candles:
            continue
        c1 = scoring.closed_candles(candles.get("1h", []) or [], scoring.HOUR_SECONDS, now)        # closed bars only
        c4 = scoring.closed_candles(candles.get("4h", []) or [], scoring.FOUR_HOUR_SECONDS, now)
        sm_dir, sm_tilt = _fetch_sm_direction(ctx, coin, min_traders)
        th = scoring.build_thesis_pre_listing(coin, c1, c4, sm_dir, sm_tilt, inputs)
        if not th or th["score"] < min_score:
            continue
        leverage = scoring.clamp_leverage(max_lev, inst["max_leverage"])
        if leverage <= 0:
            continue
        out.append({
            "asset": coin,                    # an xyz: name straight from the live universe read
            "direction": th["direction"],
            "marginPct": margin_pct,          # SIZING INTENT — runtime sizes the dollars
            "leverage": leverage,             # per-signal, clamped to this instrument's venue max
            "data": {
                "score": th["score"], "direction": th["direction"], "leverage": leverage,
                "ipopFlag": True, "trend4h": th.get("trend4h"), "smTilt": round(th.get("sm_tilt", 0.0), 1),
                "reasons": th["reasons"],
            },
        })
        recent[cu] = now
        print(f"[magpie.pre_listing.scan] EMIT {coin} {th['direction']} {leverage}x "
              f"score={th['score']} | {th['reasons']}", file=sys.stderr)

    _persist()
    return out
