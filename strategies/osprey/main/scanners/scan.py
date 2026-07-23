"""OSPREY — supervised scanner (Runtime 3.0 port of the v2 Osprey cross-venue lag).

Cross-VENUE lag catcher: when a crypto leader (BTC) makes a strong move,
crypto-correlated XYZ equities (COIN/MSTR) tend to FOLLOW but on a different
venue, with a lag. Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - measures the LEADER's move over `moveLookbackBars` 1h candles; if |move| is
    below `minLeaderMovePct`, no lag to trade -> WAITING,
  - for each non-held, non-recently-signaled proxy: fetches its 1h candles,
    self-computes the catch-up gap (leader_move x beta - proxy_move) and scores
    it via the pure `scoring.build_thesis`,
  - emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main()
    emitted only `best`), sized by `marginPct` (PERCENT) + `leverage`.

Read-only + single-pass. No daemon, no push_signal, no create_position. The
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit.

FIDELITY NOTES vs the v2 producer (osprey-producer.py v1.0.1 + osprey_config.py):
  - Thesis math is BYTE-FOR-BYTE the v2 thesis: self-computed leader move +
    per-proxy catch-up gap from 1h candles (scoring.move_pct / catchup_gap /
    lag_direction / volume_trend, all verbatim). Smart-money on the proxy
    (leaderboard_get_markets) is a SCORE BONUS, not a gate (verbatim).
  - ARCHETYPE SIGNATURE READ — market_get_cross_asset_flows: the cross-asset-lag
    archetype's signature read. v2 Osprey did NOT call it (it self-computes the
    gap from candles, because cross_asset_flows only surfaces CRYPTO laggards and
    only BTC has lag data — XYZ equity proxies cannot be surfaced by it). This
    port reads it ONCE per tick, FULLY READ-GUARDED, purely as an INDEPENDENT
    CONFIRMATION of the leader move (RULE 1). It NEVER alters score or gating —
    so the v2 scoring/emit is preserved EXACTLY. On warmup / empty laggards /
    error it degrades to neutral and the tick proceeds on the self-computed
    leader move alone (the v2 path). The confirmation is surfaced in
    observability + data{leaderConfirmed,leaderFlowDir}. FLAGGED: this is an
    ADDED read vs v2 (a no-op on the decision), present to honor the archetype's
    documented signature read with the required warmup/BTC-only read-guard.
  - v2 conviction-scaled margin used marginPct=0.15 (a FRACTION) * account_value
    -> marginUsd. This port emits `marginPct` (PERCENT) and the runtime sizes
    (marginPct/100)*withdrawable. Defensive guard: a value <= 1.0 is treated as a
    pasted v2 fraction and converted x100 (dire/koala pattern).
  - DROPPED v2 order-lifecycle: none. v2 Osprey had NO cancel_order /
    has_resting_orders / stale-order purge — the producer NEVER closed (DSL owns
    exits), so there is no mutation to drop. The runtime's reconciliation owns
    order lifecycle as usual.
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1/tick.
  - v2 recent-signals JSON cache -> ctx.state dedup map (same TTL semantics).
"""

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



# v2 defaults (osprey-producer.py / osprey-config.json)
_DEFAULT_LEADER = "BTC"
_DEFAULT_PROXIES = [
    {"proxy": "xyz:COIN", "beta": 1.8},
    {"proxy": "xyz:MSTR", "beta": 2.5},
]
_DEFAULT_MOVE_LOOKBACK = 4            # 1h bars — the "recent move" window for both legs
_DEFAULT_MIN_LEADER_MOVE = 2.0       # leader must move at least this % to matter
_DEFAULT_MIN_SCORE = 4               # v2 DEFAULT_MIN_SCORE
_DEFAULT_MARGIN_PCT = 15.0           # PERCENT of withdrawable (v2 fraction 0.15 -> 15%)
_DEFAULT_LEVERAGE = 4                # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 10                   # v2 MAX_LEVERAGE (hardcoded cap)
_DEFAULT_RECENT_TTL = 240            # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across
    main/xyz (two views of ONE cross-margined wallet — summing double-counts the
    shared free balance -> 2x sizing). Ported verbatim from v2 cfg.get_positions,
    including the read-sanity guard (margin in use + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[osprey.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
        return 0.0, []
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
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": scoring._f(pos.get("marginUsed", 0))})

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[osprey.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _fetch_candles(ctx, asset):
    """1h candles for `asset` or [] (READ-GUARDED). Ported from v2 fetch_candles
    (1h only, no funding, no order book)."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h"],
            "include_funding": False,
            "include_order_book": False,
            "dex": _dex_for(asset),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[osprey.scan] market_get_asset_data({asset}) read failed: {exc!r}", file=sys.stderr)
        return []
    if not md:
        return []
    if isinstance(md, dict) and md.get("success") is False:
        return []
    d = md.get("data", md) if isinstance(md, dict) else md
    if not isinstance(d, dict):
        return []
    candles = d.get("candles", {}) or {}
    return candles.get("1h", []) if isinstance(candles, dict) else []


def _get_sm_direction(ctx, asset):
    """Net smart-money lean for `asset` from leaderboard_get_markets.
    Returns (direction, pct) or (None, 0.0). READ-GUARDED. Ported verbatim from
    v2 fetch_sm_direction: long_ratio >= 50 -> LONG else SHORT (NEUTRAL/50 when
    found but flat). SM is a SCORE BONUS on XYZ equities, never a gate."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a bonus; never crash the tick
        print(f"[osprey.scan] leaderboard_get_markets read failed (smart-money -> neutral): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw:
        return None, 0.0
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", markets))
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None, 0.0

    long_pct, short_pct, found = 0.0, 0.0, False
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("coin", m.get("asset", "")))).upper()
        if not _sm_row_matches(m, token, asset):
            continue
        found = True
        d = str(m.get("direction", "")).upper()
        pct = scoring._f(m.get("pct_of_top_traders_gain", m.get("longPct", 0)))
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
    return ("LONG", long_ratio) if long_ratio >= 50 else ("SHORT", 100 - long_ratio)


def _get_leader_flow(ctx, leader, min_leader_move):
    """ARCHETYPE SIGNATURE READ — market_get_cross_asset_flows.

    READ-GUARDED + DEGRADE-TO-NEUTRAL. This is the cross-asset-lag archetype's
    signature read; here it serves ONLY as an INDEPENDENT confirmation of the
    leader move (RULE 1). It NEVER alters score or gating (the v2 thesis self-
    computes the leader move + gap from candles), so the v2 decision is exact.

    The tool has a daily-cron WARMUP and only BTC has lag data; on warmup / empty
    / non-BTC / any error it returns (False, 'NONE') and the tick proceeds on the
    self-computed leader move alone. Returns (confirmed: bool, flow_dir: str)."""
    try:
        raw = ctx.senpi_mcp.call_tool("market_get_cross_asset_flows", {
            "leader_asset": leader,
            "min_move_pct": min_leader_move,
            "window": "4h",
        })
    except Exception as exc:  # noqa: BLE001 — warmup/BTC-only/transient — degrade to neutral
        print(f"[osprey.scan] cross_asset_flows read failed (leader confirm -> neutral): {exc!r}",
              file=sys.stderr)
        return False, "NONE"
    if not raw:
        return False, "NONE"
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return False, "NONE"
    # Leader-move block shape varies; probe the documented "leader" summary.
    leader_blk = data.get("leader", data.get("leader_move", {}))
    if not isinstance(leader_blk, dict):
        return False, "NONE"
    mag = abs(scoring._f(leader_blk.get("move_pct", leader_blk.get("magnitude", 0))))
    flow_dir = str(leader_blk.get("direction", "")).upper() or "NONE"
    confirmed = mag >= float(min_leader_move) and flow_dir in ("LONG", "SHORT", "UP", "DOWN")
    return confirmed, flow_dir


# ── ctx.state: recent-signal dedup (port of v2 recent-signals.json) ──

def _load_signaled(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    sig = last.get("signaled", {})
    return dict(sig) if isinstance(sig, dict) else {}


def _prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def scan(inputs, ctx):
    now = time.time()
    leader = str(inputs.get("leader", _DEFAULT_LEADER) or _DEFAULT_LEADER)
    proxies = inputs.get("proxies", _DEFAULT_PROXIES)
    lookback = int(inputs.get("moveLookbackBars", _DEFAULT_MOVE_LOOKBACK))
    min_leader = float(inputs.get("minLeaderMovePct", _DEFAULT_MIN_LEADER_MOVE))
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    lev_default = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1
    # (an operator who pasted the v2 FRACTION 0.15) into a PERCENT so it never
    # silently sizes ~100x small (the runtime sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[osprey.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # leverage clamp: min(default, MAX_LEVERAGE) — verbatim from v2 main()
    leverage = min(lev_default, _MAX_LEVERAGE)

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── RULE 1: the leader must move first (self-computed from candles) ──
    leader_candles = _fetch_candles(ctx, leader)
    leader_closes = [scoring._close(c) for c in leader_candles]
    leader_move = scoring.move_pct(leader_closes, lookback)

    if leader_move is None or abs(leader_move) < min_leader:
        result = {"ts": now, "emitted": False, "held": held_assets,
                  "leaderMovePct": round(leader_move, 2) if leader_move is not None else None,
                  "note": f"WAITING — leader {leader} move below {min_leader}% threshold"}
        print(f"[osprey.scan] WAITING — leader {leader} move "
              f"{leader_move if leader_move is not None else 'n/a'} below {min_leader}%; "
              f"held={held_assets}", file=sys.stderr)
        _persist(ctx, signaled, result)
        return []

    # ARCHETYPE SIGNATURE READ (confirmation-only; never gates — see _get_leader_flow)
    leader_confirmed, leader_flow_dir = _get_leader_flow(ctx, leader, min_leader)

    # ── score each proxy's catch-up gap (held + recently-signaled filtered
    #    BEFORE the per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for proxy_cfg in proxies:
        proxy = str(proxy_cfg.get("proxy", "")).strip()
        if not proxy:
            continue
        pu = proxy.upper()
        if pu in held_set:
            continue
        if _was_recently_signaled(signaled, proxy, ttl, now):
            continue
        scanned += 1
        proxy_candles = _fetch_candles(ctx, proxy)
        if len(proxy_candles) <= lookback:
            continue
        proxy_closes = [scoring._close(c) for c in proxy_candles]
        sm = _get_sm_direction(ctx, proxy)
        th = scoring.build_thesis(proxy_cfg, leader_move, proxy_closes, proxy_candles, sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "emitted": False, "scanned": scanned, "held": held_assets,
                  "leaderMovePct": round(leader_move, 2), "leaderConfirmed": leader_confirmed,
                  "note": f"WAITING — {leader} moved {leader_move:+.1f}% but no proxy owes a catch-up gap"}
        print(f"[osprey.scan] WAITING — {leader} moved {leader_move:+.1f}% but no proxy gap "
              f">= floor; scanned={scanned} held={held_assets}", file=sys.stderr)
    else:
        # v2 sort: (score, |gap|) descending; emit exactly the single best.
        candidates.sort(key=lambda c: (c["score"], abs(c["gap_pct"])), reverse=True)
        best = candidates[0]
        signaled[best["coin"].upper()] = now
        result = {"ts": now, "emitted": True, "scanned": scanned, "held": held_assets,
                  "coin": best["coin"], "direction": best["direction"], "score": best["score"],
                  "leverage": leverage, "marginPct": round(margin_pct, 4),
                  "leaderMovePct": round(leader_move, 2), "gapPct": best["gap_pct"],
                  "leaderConfirmed": leader_confirmed, "reasons": best["reasons"]}
        print(f"[osprey.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% gap={best['gap_pct']:+.1f}% "
              f"leader={leader_move:+.1f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..10; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "leader": leader,
                "leaderMovePct": best.get("leader_move_pct") or 0.0,
                "proxyMovePct": best.get("proxy_move_pct") or 0.0,
                "gapPct": best.get("gap_pct") or 0.0,
                "beta": best.get("beta") or 0.0,
                "smDirection": best.get("sm_direction") or "NONE",
                "smTiltPct": best.get("sm_tilt_pct") or 0.0,
                "volumeTrendPct": best.get("volume_trend_pct") or 0.0,
                "leaderConfirmed": leader_confirmed,
                "leaderFlowDir": leader_flow_dir,
                "heldAssets": held_assets,
            },
        }]

    _persist(ctx, signaled, result)
    return out


def _persist(ctx, signaled, result):
    """Append the dedup map + this tick's result every tick; bounded by
    state_history_max_count. Read back via ctx.state.recent(n)."""
    if ctx.state is None:
        return
    try:
        ctx.state.append({"signaled": signaled, "result": result})
    except Exception as exc:  # noqa: BLE001
        print(f"[osprey.scan] WARNING: state append failed; next tick may re-emit "
              f"a suppressed signal: {exc!r}", file=sys.stderr)
