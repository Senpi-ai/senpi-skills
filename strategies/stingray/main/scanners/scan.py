"""STINGRAY — supervised scanner (NET-NEW Runtime 3.0 cross-asset SM-rotation strategy).

THESIS — cross-asset smart-money rotation. Existing SM agents follow smart money
*per asset*. Stingray follows the rotation *across the whole board*: it reads the
smart-money board, ranks every asset by net SM conviction (tilt x notional/breadth
weight), and goes LONG the assets SM is crowding INTO + SHORT the ones SM is FLEEING
— capturing days like "long semis/indices, short the crypto block".

Per tick it:
  1. reads account state + held positions (dual-DEX equity via max(), read-sanity
     guard — bison pattern),
  2. fetches `leaderboard_get_markets` (READ-GUARDED) and normalizes every asset on
     the board into per-asset long%/short% (bison `_get_sm_direction` accumulation)
     + notional/breadth (cheetah `_fetch_sm_markets` field extraction),
  3. ranks the board by conviction (|net_tilt| x weight) and splits LONG / SHORT
     sides, requiring a minimum tilt (58/42 like bison),
  4. applies a price-confirmation gate per shortlisted asset (READ-GUARDED, dex-aware
     via `_dex_for`): only LONG if the asset's 4h trend isn't a hard downtrend, only
     SHORT if not a hard uptrend — don't fight price; skip the asset on read fail,
  5. emits up to `maxLong` longs + `maxShort` shorts, scored by conviction; held +
     recent-signal dedup via ctx.state (240s TTL).

Read-only + single-pass — emits `marginPct` intents (PERCENT, conviction-tiered)
plus `leverage`; the runtime sizes the dollars, owns slots/cooldowns/risk gates, and
trails the DSL exit. No daemon, no push_signal, no create_position. EVERY MCP call is
read-guarded so a transient/permission error degrades that read instead of rolling
back the whole tick.
"""

import sys
import time

import scoring

# defaults (overridable via runtime.yaml inputs)
_DEFAULT_MIN_TILT_LONG = 58.0     # long_ratio >= 58 -> LONG candidate (bison threshold)
_DEFAULT_MIN_TILT_SHORT = 42.0    # long_ratio <= 42 -> SHORT candidate (bison threshold)
_DEFAULT_MARGIN_PCT = 12.0        # PERCENT of withdrawable (0,100]; tiered x1.25/x1.5 by |net_tilt|
_DEFAULT_LEVERAGE = 4             # clamped to [1,5] (+ venue max) in scan()
_MIN_LEVERAGE = 1
_MAX_LEVERAGE = 5                 # leverage_max (catalog); runtime also clamps to venue max
_DEFAULT_MAX_LONG = 2
_DEFAULT_MAX_SHORT = 2
_DEFAULT_RECENT_TTL = 240         # signal-dedup TTL (s)
_DEFAULT_LEADERBOARD_LIMIT = 100  # board breadth to scan
_DEFAULT_VOL_FLOOR = 1.0          # conviction_weight notional floor


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply (board
    empty -> skip tick; per-asset trend read fail -> skip that asset, don't fight an
    unknown chart)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[stingray.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass '' (bison pattern).
    Stingray is cross-asset (crypto + xyz equities/commodities/indices), so this DOES
    return 'xyz' for xyz: markets — the trend-confirmation read must be dex-aware."""
    return "xyz" if str(asset).lower().startswith("xyz:") else ""


# ── ACCOUNT + HELD ASSETS (bison _get_account, verbatim shape incl. read-sanity guard) ──

def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts the shared free
    balance -> 2x sizing). assetPositions are per-sub-DEX so they are enumerated across
    both sections. Ported from bison, including the read-sanity guard (margin in use +
    empty positions -> skip tick)."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    positions, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        account_value = max(account_value, scoring.safe_float(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring.safe_float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "direction": "LONG" if szi > 0 else "SHORT",
                "margin": scoring.safe_float(pos.get("marginUsed", 0)),
            })

    # read-sanity guard (funding/$0 glitch 2026-06, ported from bison): a corrupt read
    # can report margin/notional IN USE while returning an EMPTY positions list; running
    # the held dedup off that re-enters held names. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring.safe_float(_ms.get("totalMarginUsed", 0)),
                   abs(scoring.safe_float(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[stingray.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


# ── SM BOARD (bison _get_sm_direction per-asset long%/short% accumulation +
#    cheetah _fetch_sm_markets field extraction, COMBINED — do not invent the shape) ──

def _board(ctx, limit, xyz_banned):
    """Read leaderboard_get_markets (READ-GUARDED) and fold the per-direction rows
    into ONE normalized dict per asset:
      { token, dex, long_pct, short_pct, volume, traders }

    The leaderboard returns one ROW per (asset, direction) carrying
    `pct_of_top_traders_gain` (bison's `_get_sm_direction` reads exactly these long /
    short rows). We accumulate long_pct + short_pct per (token, dex) so each asset gets
    a single net-tilt input, and we carry the max volume / trader_count seen across its
    rows (cheetah's `_fetch_sm_markets` field names: token, dex, direction,
    pct_of_top_traders_gain, trader_count, volume). Returns [] on read failure."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return []

    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", markets.get("leaderboard", []))
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw
    if not isinstance(markets, list):
        return []

    by_asset = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", ""))))
        if not token:
            continue
        dex = str(m.get("dex", "")).lower()
        if xyz_banned and dex == "xyz":
            continue
        direction = str(m.get("direction", "")).lower()
        # bison reads pct_of_top_traders_gain (fallback longPct), same field cheetah uses
        pct = scoring.safe_float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
        vol = scoring.safe_float(m.get("volume", m.get("avg_volume_6h", m.get("avgVolume", 0))))
        traders = scoring.safe_float(m.get("trader_count", m.get("traders", 0)))

        key = (token, dex)
        row = by_asset.setdefault(key, {
            "token": token, "dex": dex,
            "long_pct": 0.0, "short_pct": 0.0,
            "volume": 0.0, "traders": 0.0,
        })
        if direction == "long":
            row["long_pct"] += pct
        elif direction == "short":
            row["short_pct"] += pct
        # carry the strongest notional/breadth seen across the asset's rows
        row["volume"] = max(row["volume"], vol)
        row["traders"] = max(row["traders"], traders)

    return list(by_asset.values())


# ── PRICE-CONFIRMATION GATE (don't fight price). bison _asset_data + trend_structure ──

def _hard_trend(ctx, coin):
    """Return the 4h trend label ('BULLISH' / 'BEARISH' / 'NEUTRAL') for `coin`, or
    None on read failure. READ-GUARDED, dex-aware via _dex_for (bison _asset_data +
    scoring.trend_structure). Used to refuse longs into a hard downtrend and shorts
    into a hard uptrend — we don't fight price."""
    md = _read(ctx, "market_get_asset_data", {
        "asset": coin,
        "candle_intervals": ["4h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": _dex_for(coin),
    })
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return None
    candles = (d.get("candles", {}) or {}).get("4h", [])
    if not candles:
        return None
    label, _strength = _trend_structure(candles)
    return label


def _trend_structure(candles, lookback=6):
    """Higher lows = BULLISH, lower highs = BEARISH (bison scoring.trend_structure,
    same strict-> counting + 0.6 gate). Kept local to keep the gate self-contained."""
    if len(candles) < lookback:
        return "NEUTRAL", 0.0

    def _low(c):
        if isinstance(c, dict):
            return scoring.safe_float(c.get("low", c.get("l", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return scoring.safe_float(c[3])
        return 0.0

    def _high(c):
        if isinstance(c, dict):
            return scoring.safe_float(c.get("high", c.get("h", 0)))
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            return scoring.safe_float(c[2])
        return 0.0

    lows = [_low(c) for c in candles[-lookback:]]
    highs = [_high(c) for c in candles[-lookback:]]
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    total = lookback - 1
    if higher_lows >= total * 0.6:
        return "BULLISH", higher_lows / total
    if lower_highs >= total * 0.6:
        return "BEARISH", lower_highs / total
    return "NEUTRAL", 0.0


def _price_ok(ctx, coin, direction):
    """Price-confirmation gate. LONG is blocked only by a HARD downtrend (4h BEARISH);
    SHORT only by a HARD uptrend (4h BULLISH). NEUTRAL passes (rotation can lead price).
    On read failure -> None: the caller treats an unknown chart as a SKIP (don't size
    blind into a chart we couldn't read)."""
    label = _hard_trend(ctx, coin)
    if label is None:
        return None
    if direction == "LONG" and label == "BEARISH":
        return False
    if direction == "SHORT" and label == "BULLISH":
        return False
    return True


# ── ctx.state: recent-signal dedup (240s TTL) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (bison _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(str(coin).upper())
    if last is None:
        return False
    return (now - last) < ttl


def _emit_side(ctx, ranked, direction, cap, held_set, signaled, ttl, now,
               base_margin, leverage, max_margin):
    """Walk the conviction-ranked side, apply held + recent-signal + price gates, and
    return up to `cap` emit dicts (highest conviction first). Mutates `signaled` for
    each emitted asset (dedup). Each emit is sized by conviction (margin_pct_for) and
    carries the clamped leverage."""
    emits = []
    for c in ranked:
        if len(emits) >= cap:
            break
        coin = c["token"]
        cu = coin.upper()
        if cu in held_set:
            continue
        if _recently_signaled(signaled, coin, ttl, now):
            continue
        ok = _price_ok(ctx, coin, direction)
        if ok is None:
            print(f"[stingray.scan] SKIP {coin} {direction}: trend read failed (don't size blind)",
                  file=sys.stderr)
            continue
        if not ok:
            print(f"[stingray.scan] SKIP {coin} {direction}: price gate (hard "
                  f"{'down' if direction == 'LONG' else 'up'}trend, don't fight price)",
                  file=sys.stderr)
            continue
        margin_pct = scoring.margin_pct_for(abs(c["net_tilt"]), base_margin, max_margin)
        signaled[cu] = now
        emits.append({
            "asset": coin,
            "direction": direction,
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # clamped [1,5] + venue max
            "data": {
                "direction": direction,
                "longRatio": c["long_ratio"],
                "netTilt": c["net_tilt"],
                "conviction": c["conviction"],
                "weight": c["weight"],
                "smVolume": c["volume"],
                "smTraders": c["traders"],
                "leverage": leverage,
            },
        })
    return emits


def scan(inputs, ctx):
    now = time.time()
    min_tilt_long = float(inputs.get("minTiltLong", _DEFAULT_MIN_TILT_LONG))
    min_tilt_short = float(inputs.get("minTiltShort", _DEFAULT_MIN_TILT_SHORT))
    base_margin = float(inputs.get("marginPctBase", _DEFAULT_MARGIN_PCT))   # PERCENT (0,100]
    # marginPct <=1.0 -> x100 guard: a fraction (0.12) slipped in via config means PERCENT
    if base_margin <= 1.0:
        print(f"[stingray.scan] marginPctBase={base_margin} looks like a FRACTION; "
              f"x100 -> {base_margin * 100} PERCENT", file=sys.stderr)
        base_margin *= 100.0
    max_margin = float(inputs.get("marginPctMax", 0)) or None
    leverage_in = int(inputs.get("leverageDefault", _DEFAULT_LEVERAGE))
    leverage = max(_MIN_LEVERAGE, min(leverage_in, _MAX_LEVERAGE))   # clamp [1,5]; runtime clamps venue max too
    max_long = int(inputs.get("maxLong", _DEFAULT_MAX_LONG))
    max_short = int(inputs.get("maxShort", _DEFAULT_MAX_SHORT))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))
    xyz_banned = bool(inputs.get("xyzBanned", False))   # cross-asset: xyz INCLUDED by default
    vol_floor = float(inputs.get("convictionVolFloor", _DEFAULT_VOL_FLOOR))

    # ── account + held ──
    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        print("[stingray.scan] cannot read account value; skip tick", file=sys.stderr)
        if ctx.state is not None:
            try:
                ctx.state.append({"signaled": _prune_signaled(_load_signaled(ctx), ttl, now),
                                  "result": {"ts": now, "emitted": False, "gate": "no_account"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[stingray.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── SM board -> conviction-ranked LONG / SHORT sides ──
    rows = _board(ctx, limit, xyz_banned)
    if not rows:
        print("[stingray.scan] empty SM board (leaderboard_get_markets); skip tick", file=sys.stderr)
        result = {"ts": now, "emitted": False, "gate": "no_board", "held": held_assets}
    else:
        longs, shorts = scoring.rank_board(rows, min_tilt_long, min_tilt_short, vol_floor)
        # weight fallback: if the board carried no notional/breadth, conviction collapses
        # to a constant -> re-rank both sides on |net_tilt| alone (still a valid rotation rank)
        if not scoring.board_has_weight(rows):
            longs.sort(key=lambda c: abs(c["net_tilt"]), reverse=True)
            shorts.sort(key=lambda c: abs(c["net_tilt"]), reverse=True)
            print("[stingray.scan] board carries no notional/breadth — ranking on |net_tilt| only",
                  file=sys.stderr)
        else:
            longs.sort(key=lambda c: c["conviction"], reverse=True)
            shorts.sort(key=lambda c: c["conviction"], reverse=True)

        long_emits = _emit_side(ctx, longs, "LONG", max_long, held_set, signaled, ttl, now,
                                base_margin, leverage, max_margin)
        short_emits = _emit_side(ctx, shorts, "SHORT", max_short, held_set, signaled, ttl, now,
                                 base_margin, leverage, max_margin)
        out = long_emits + short_emits

        if out:
            print(f"[stingray.scan] EMIT {len(long_emits)}L + {len(short_emits)}S "
                  f"| board={len(rows)} longs={len(longs)} shorts={len(shorts)} held={held_assets} "
                  f"| {[ (e['asset'], e['direction']) for e in out ]}", file=sys.stderr)
            result = {"ts": now, "emitted": True, "board": len(rows),
                      "longSide": len(longs), "shortSide": len(shorts),
                      "emittedAssets": [(e["asset"], e["direction"]) for e in out],
                      "held": held_assets}
            if ctx.state is not None:
                try:
                    ctx.state.append({"signaled": signaled, "result": result})
                except Exception as exc:  # noqa: BLE001
                    print(f"[stingray.scan] WARNING: state append failed; next tick may re-emit "
                          f"a suppressed signal: {exc!r}", file=sys.stderr)
            return out

        print(f"[stingray.scan] WAITING — no rotation emit | board={len(rows)} "
              f"longs={len(longs)} shorts={len(shorts)} held={held_assets} "
              f"(tilt floors {min_tilt_long:.0f}/{min_tilt_short:.0f})", file=sys.stderr)
        result = {"ts": now, "emitted": False, "gate": "no_rotation", "board": len(rows),
                  "longSide": len(longs), "shortSide": len(shorts), "held": held_assets}

    # persist dedup map + this tick's result (bounded by state_history_max_count)
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[stingray.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
    return []
