"""PYTHON — supervised scanner (Runtime 3.0 port of the v2 Python "Patience Hunter").

Wide-universe, multi-day-hold. Each tick it:
  1. reads the account (dual-DEX equity collapsed via max(), NOT sum()),
  2. builds the universe (top `universeSize` HL crypto perps by 24h volume;
     excludes xyz:/HIP-3 + stablecoins; OI>$1M, vol>$1M floors — port of v2
     get_universe),
  3. reads smart-money lean (leaderboard_get_markets) + per-coin candles,
  4. scores via the pure `scoring.build_thesis` (incl. the MACRO/REGIME GATE),
  5. emits ALL candidates >= minScore (held-set + per-coin cooldown filtered),
     conviction-sized (marginUsd + 3/5/7x leverage clamped to venue max).

Read-only + single-pass. Every ctx.senpi_mcp.call_tool is READ-GUARDED: a read
error degrades that factor (or returns []), never crashes the tick. No daemon,
no push_signal — the runtime sizes the dollars, owns slots/cooldowns/risk gates,
and trails the DSL exit.

Faithful to the v2 producer thesis; ONE deliberate change is flagged: the v2
producer emitted exactly ONE best signal per tick (slots managed in-process).
Here `scan()` emits ALL qualifying candidates sorted by score and the runtime's
`slots` (=2) + per-asset cooldown own the per-tick ceiling. Every signal-GATING
threshold (universe filters, minScore, held-set, per-coin cooldown) is preserved
exactly; only the emission CAP moved to the runtime."""

import sys
import time

import scoring

# v2 ASSET_COOLDOWN_MINUTES = 720 (12h). Defence-in-depth alongside the runtime's
# per_asset_cooldown_seconds gate. Overridable via inputs.
_DEFAULT_RECENT_TTL = 43200       # 12h in seconds
_DEFAULT_UNIVERSE_SIZE = 50
_DEFAULT_MIN_OI_USD = 1_000_000
_DEFAULT_MIN_VOL_USD = 1_000_000
_DEFAULT_MIN_TRADER_COUNT = 30
_STABLES = {"USDC", "USDT", "USDE", "FDUSD", "DAI"}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── MCP data fetchers (route the producer's calls through ctx.senpi_mcp, READ-GUARDED) ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    Dual-DEX equity collapse: account_value via max() across main/xyz — TWO VIEWS
    of ONE cross-margined wallet, never summed (summing double-counts free balance
    -> 2x sizing). assetPositions ARE per-sub-DEX, so enumerate both sections.

    Port of v2's read-sanity guard: if margin/notional reads IN USE while the
    positions list is EMPTY (a corrupt clearinghouse read, funding/$0 glitch),
    skip the tick — running held-asset dedup off that re-enters held names."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must skip the tick, not crash it
        print(f"[python.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []
    positions, account_value = [], 0.0
    in_use = 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) if isinstance(s.get("marginSummary"), dict) else {}
        account_value = max(account_value, _f(ms.get("accountValue", 0)))
        in_use = max(in_use, _f(ms.get("totalMarginUsed", 0)), abs(_f(ms.get("totalNtlPos", 0))))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = _f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": _f(pos.get("marginUsed", 0)),
            })
    if in_use > 1.0 and not positions:
        print("[python.scan] read-sanity guard: margin in use but empty positions list — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _get_universe(ctx, inputs):
    """Port of v2 get_universe: top `universeSize` main-DEX crypto perps by 24h
    volume. Excludes xyz:/HIP-3 names and stablecoins; OI>min_oi_usd and
    vol>min_vol_usd floors. Returns [{"coin", "volume", "oi_usd", "markPx",
    "maxLeverage"}, ...]. READ-GUARDED -> [] on failure (the tick then no-ops)."""
    universe_size = int(inputs.get("universeSize", _DEFAULT_UNIVERSE_SIZE))
    min_oi_usd = _f(inputs.get("minOiUsd", _DEFAULT_MIN_OI_USD))
    min_vol_usd = _f(inputs.get("minVolUsd", _DEFAULT_MIN_VOL_USD))
    try:
        data = ctx.senpi_mcp.call_tool("market_list_instruments", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[python.scan] market_list_instruments read failed: {exc!r}", file=sys.stderr)
        return []
    if not data:
        return []
    instruments = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(instruments, dict):
        instruments = instruments.get("instruments", [])
    if not isinstance(instruments, list):
        return []

    filtered = []
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name", "")
        if not name or name.startswith("xyz:"):
            continue
        if name.upper() in _STABLES:
            continue
        ctx_meta = inst.get("context", {}) if isinstance(inst.get("context"), dict) else {}
        day_ntl_vlm = _f(ctx_meta.get("dayNtlVlm", inst.get("dayNtlVlm", 0)))
        oi = _f(ctx_meta.get("openInterest", inst.get("openInterest", 0)))
        mark_px = _f(ctx_meta.get("markPx", inst.get("markPx", 0)))
        oi_usd = oi * mark_px
        if oi_usd < min_oi_usd:
            continue
        if day_ntl_vlm < min_vol_usd:
            continue
        filtered.append({
            "coin": name,
            "volume": day_ntl_vlm,
            "oi_usd": oi_usd,
            "markPx": mark_px,
            "maxLeverage": int(inst.get("max_leverage", inst.get("maxLeverage", 10)) or 10),
        })
    filtered.sort(key=lambda x: -x["volume"])
    return filtered[:universe_size]


def _get_sm_map(ctx, inputs):
    """Port of v2 get_sm_map: {COIN: (direction, pct, traders, cc_15m)} net
    smart-money lean from leaderboard_get_markets, filtered to traders >=
    min_trader_count. READ-GUARDED -> {} on failure (smart-money is optional)."""
    min_traders = int(inputs.get("minTraderCount", _DEFAULT_MIN_TRADER_COUNT))
    try:
        data = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money optional; never crash the tick on it
        print(f"[python.scan] leaderboard_get_markets read failed (smart-money -> empty): {exc!r}",
              file=sys.stderr)
        return {}
    if not data:
        return {}
    markets = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return {}

    by_coin = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not token:
            continue
        direction = str(m.get("direction", "")).lower()
        pct = _f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        traders = int(m.get("trader_count", m.get("traderCount", 0)) or 0)
        cc_15m = _f(m.get("contribution_pct_change_15m", 0))
        entry = by_coin.setdefault(token, {"long_pct": 0.0, "short_pct": 0.0, "traders": 0, "cc_15m": 0.0})
        if direction == "long":
            entry["long_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m
        elif direction == "short":
            entry["short_pct"] = pct
            entry["traders"] = max(entry["traders"], traders)
            entry["cc_15m"] = cc_15m

    result = {}
    for token, d in by_coin.items():
        total = d["long_pct"] + d["short_pct"]
        if total == 0 or d["traders"] < min_traders:
            continue
        long_ratio = (d["long_pct"] / total) * 100
        if long_ratio > 58:
            result[token] = ("LONG", long_ratio, d["traders"], d["cc_15m"])
        elif long_ratio < 42:
            result[token] = ("SHORT", 100 - long_ratio, d["traders"], d["cc_15m"])
        else:
            result[token] = ("NEUTRAL", 50, d["traders"], d["cc_15m"])
    return result


def _fetch_candles(ctx, coin):
    """Per-coin candles (15m/1h/4h/1d) + funding. READ-GUARDED -> None on failure."""
    try:
        data = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["15m", "1h", "4h", "1d"],
            "include_funding": True,
            "include_order_book": False,
            "dex": "",
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[python.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not data:
        return None
    if isinstance(data, dict) and data.get("success") is False:
        return None
    asset_data = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(asset_data, dict):
        return None
    candles = asset_data.get("candles", {}) or {}
    asset_ctx = asset_data.get("asset_context", asset_data.get("assetContext", {})) or {}
    funding = _f(asset_ctx.get("funding", 0))
    return {"candles": candles, "funding": funding}


# ── ctx.state: per-coin signaled-cooldown map + per-tick result history ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 2)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


# ── Entry point ──

def scan(inputs, ctx):
    now = time.time()
    min_score = float(inputs.get("minScore", scoring.MIN_SCORE))
    family_max_lev = int(inputs.get("maxLeverage", scoring.MAX_LEVERAGE))
    ttl = _f(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        return []

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    universe = _get_universe(ctx, inputs)
    if not universe:
        # persist pruned dedup so it doesn't grow unbounded on no-universe ticks
        _persist(ctx, signaled, {"ts": now, "emitted": False, "gate": "no_universe"})
        return []

    sm_map = _get_sm_map(ctx, inputs)

    candidates = []
    for asset in universe:
        coin = asset["coin"]
        cu = coin.upper()
        if cu in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        md = _fetch_candles(ctx, coin)
        if not md:
            continue
        candles = md["candles"]
        thesis = scoring.build_thesis(
            coin,
            candles.get("15m", []), candles.get("1h", []),
            candles.get("4h", []), candles.get("1d", []),
            md["funding"], sm_map.get(cu),
        )
        if not thesis or thesis["score"] < min_score:
            continue
        thesis["_max_lev"] = asset.get("maxLeverage")
        candidates.append(thesis)

    candidates.sort(key=lambda c: -c["score"])

    out = []
    for th in candidates:
        leverage = scoring.clamp_leverage(
            min(scoring.get_leverage_for_score(th["score"]), family_max_lev),
            th.get("_max_lev"),
        )
        if leverage <= 0:
            continue
        margin_pct = scoring.get_margin_pct(th["score"])          # FRACTION (0.25/0.30/0.40)
        margin_usd = round(account_value * margin_pct, 2)
        if margin_usd <= 0:
            continue
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginUsd": margin_usd,            # top-level USD margin (runtime sizes from this)
            "leverage": leverage,               # top-level, conviction-tiered (3/5/7), venue-clamped
            "data": {
                "score": th["score"],
                "leverage": leverage,
                "marginUsd": margin_usd,
                "direction": th["direction"],
                "reasons": th["reasons"],
                "trend4h": th["trend_4h"],
                "trend1h": th["trend_1h"],
                "mom1h": th["mom_1h"],
                "mom4h": th["mom_4h"],
                "mom1d": th["mom_1d"],
                "funding": th["funding"],
                "rsi": th["rsi"],
                "volRatio": th["vol_ratio"],
                "heldAssets": held_assets,
            },
        })
        signaled[th["coin"].upper()] = now

    best = out[0]["asset"] if out else None
    result = {"ts": now, "emitted": bool(out), "gate": "pass" if out else "no_candidates",
              "scanned": len(universe), "candidates": len(candidates),
              "signals": len(out), "best": best, "held": held_assets}
    if out:
        print(f"[python.scan] EMIT {len(out)} | scanned={len(universe)} "
              f"top={best} score={candidates[0]['score']} held={held_assets}", file=sys.stderr)
    else:
        print(f"[python.scan] HOLD | scanned={len(universe)} candidates={len(candidates)} "
              f"held={held_assets}", file=sys.stderr)

    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    """Persist the per-coin signaled-cooldown map + this tick's result record
    (bounded by state_history_max_count; read back via ctx.state.recent(n))."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[python.scan] WARNING: state append failed; next tick may re-emit "
              f"suppressed signals: {exc!r}", file=sys.stderr)
