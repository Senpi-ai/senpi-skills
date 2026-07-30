"""MARLIN — supervised scanner (Runtime 3.0 port of the v2 Marlin order-book-imbalance
momentum strategy).

Multi-asset basket (BTC/ETH/SOL/HYPE by default). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - for each non-held, non-recently-signaled candidate fetches market_get_asset_data
    (5m/15m candles + L2 order_book) and the smart-money lean (leaderboard_get_markets),
  - scores via the pure `scoring.build_thesis` (3 hard gates: book imbalance picks the
    side, 15m momentum confirms it, smart-money agrees with tilt >= floor),
  - emits the SINGLE highest-scoring candidate at/above `minScore` (v2 main() emitted
    only `best`), sized at a FIXED margin percent + clamped leverage.

Read-only + single-pass — emits a `marginPct` intent (PERCENT) plus a `leverage`; the
runtime sizes the dollars, owns cooldowns/risk gates, and trails the DSL exit. No
daemon, no push_signal, no create_position.

FIDELITY NOTES vs the v2 producer (marlin-producer.py v1.0.1 / SKILL.md v1.0.0):
  - v2 fixed margin: marginPct=0.15 (a FRACTION) * account_value -> marginUsd. This
    port uses marginPct=15 (a PERCENT) and emits `marginPct`; the runtime sizes
    (marginPct/100)*withdrawable. The <=1.0 guard converts a pasted fraction defensively.
  - v2 leverage: min(int(leverage), MAX_LEVERAGE=5). Preserved: clamp to [1, 5].
  - v2 emitted exactly one signal (best, highest score). Preserved: scan() emits <=1/tick.
  - v2 recent-signals JSON cache (RECENT_SIGNAL_TTL_SEC=240, 4x-TTL prune) -> ctx.state
    dedup map (same TTL semantics).
  - v2 score wire-normalization (min(score/9, 1.0)) is dropped — the scaffold owns the
    [0,1] wire score; we emit the raw integer score on data{} (per the scan contract).
  - v2 fetch_sm_direction used >=50 long_ratio for the LONG/SHORT split; the gate only
    accepts sm_dir == imbalance direction with tilt >= smTiltMinPct, so the split rule
    is reproduced verbatim in _get_sm_direction.
  - ARCHETYPE NOTE: the orchestrator hint labeled marlin "smart-money / leaderboard
    momentum". The PRIMARY edge is the L2 ORDER-BOOK IMBALANCE (market_get_asset_data
    order_book); smart-money (leaderboard_get_markets) is the THIRD confirming gate, not
    the driver. Catalog declared sub_style=orderbook_pressure to reflect this.
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



# v2 defaults (marlin-producer.py / marlin-config.json)
_UNIVERSE_DEFAULT = ["BTC", "ETH", "SOL", "HYPE"]
_DEFAULT_MIN_SCORE = 5            # v2 minScore / DEFAULT_MIN_SCORE
_DEFAULT_MARGIN_PCT = 15.0       # v2 marginPct 0.15 fraction -> 15 PERCENT
_DEFAULT_LEVERAGE = 4            # v2 DEFAULT_LEVERAGE
_MAX_LEVERAGE = 5               # v2 MAX_LEVERAGE (hardcoded, not configurable)
_DEFAULT_RECENT_TTL = 240        # v2 RECENT_SIGNAL_TTL_SEC — race-window dedup


def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''. Marlin's
    universe is all main-DEX majors, so this only ever returns '' in practice."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts the shared free
    balance -> 2x sizing). assetPositions are enumerated across both sections. Ported
    verbatim from v2 cfg.get_positions, including the read-sanity guard (margin in use
    + empty positions -> skip tick)."""
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — a read error must not roll back the whole tick
        print(f"[marlin.scan] clearinghouse read failed: {exc!r}", file=sys.stderr)
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

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a corrupt
    # clearinghouse read can report margin/notional IN USE while returning an EMPTY
    # positions list; sizing or running the held-asset dedup off that re-enters held
    # names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not positions:
        print("[marlin.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, positions


def _asset_data(ctx, coin):
    """Raw market_get_asset_data document (5m/15m candles + L2 order_book) for `coin`,
    or None. READ-GUARDED. The raw doc is returned (not unwrapped) so
    scoring.book_imbalance can read doc["data"]["order_book"] verbatim like v2."""
    try:
        md = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": coin,
            "candle_intervals": ["5m", "15m"],
            "include_funding": False,
            "include_order_book": True,
            "dex": _dex_for(coin),
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[marlin.scan] market_get_asset_data({coin}) read failed: {exc!r}", file=sys.stderr)
        return None
    if not md:
        return None
    if isinstance(md, dict) and md.get("success") is False:
        return None
    return md if isinstance(md, dict) else None


def _get_sm_direction(ctx, coin):
    """Port of v2 fetch_sm_direction: net smart-money lean for `coin` from
    leaderboard_get_markets. Returns (direction, tilt_pct) or (None, 0). READ-GUARDED.

    Verbatim split: long_ratio >= 50 -> ("LONG", long_ratio); else ("SHORT", 100-long_ratio);
    total<=0 -> ("NEUTRAL", 50); coin not found -> (None, 0)."""
    try:
        raw = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception as exc:  # noqa: BLE001 — smart-money is a hard gate; a read error -> neutral -> skip asset
        print(f"[marlin.scan] leaderboard_get_markets read failed (smart-money -> none): {exc!r}",
              file=sys.stderr)
        return None, 0.0
    if not raw or (isinstance(raw, dict) and raw.get("success") is False):
        return None, 0.0
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw
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
        if not _sm_row_matches(m, token, coin):
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
    universe = [str(a) for a in inputs.get("universe", _UNIVERSE_DEFAULT)]
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))

    # margin PERCENT in (0,100]. Defensive fraction->percent guard (v2 stored 0.15).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:   # a value <=1.0 is a pasted FRACTION (e.g. 0.15) -> x100
        margin_pct *= 100

    leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    leverage = max(1, min(leverage, _MAX_LEVERAGE))

    account_value, positions = _get_account(ctx)
    if account_value <= 0:
        return []
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}

    signaled = _prune_signaled(_load_signaled(ctx), ttl, now)

    # ── score every eligible candidate (held + recently-signaled filtered BEFORE the
    #    per-asset MCP fetch, as in v2 main()) ──
    candidates = []
    scanned = 0
    for coin in universe:
        if not coin:
            continue
        if coin.upper() in held_set:
            continue
        if _was_recently_signaled(signaled, coin, ttl, now):
            continue
        scanned += 1
        md = _asset_data(ctx, coin)
        if not md:
            continue
        data = md.get("data", {}) if isinstance(md, dict) else {}
        candles = data.get("candles", {}) if isinstance(data, dict) else {}
        candles_5m = candles.get("5m", []) or []
        candles_15m = candles.get("15m", []) or []
        sm = _get_sm_direction(ctx, coin)
        th = scoring.build_thesis(coin, md, candles_5m, candles_15m, sm, inputs)
        if th and th["score"] >= min_score:
            candidates.append(th)

    out = []
    if not candidates:
        result = {"ts": now, "scanned": scanned, "emitted": False,
                  "held": held_assets, "note": f"WAITING (min score {min_score:.0f})"}
        print(f"[marlin.scan] WAITING — no book-imbalance + momentum + SM alignment "
              f"(min score {min_score:.0f}); scanned={scanned} held={held_assets}",
              file=sys.stderr)
    else:
        # v2 emitted exactly the single best (highest score).
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        signaled[best["coin"].upper()] = now
        result = {"ts": now, "scanned": scanned, "emitted": True,
                  "coin": best["coin"], "direction": best["direction"],
                  "score": best["score"], "leverage": leverage,
                  "marginPct": round(margin_pct, 4), "held": held_assets,
                  "reasons": best["reasons"]}
        print(f"[marlin.scan] EMIT {best['coin']} {best['direction']} score={best['score']} "
              f"{leverage}x marginPct={margin_pct:.2f}% | {best['reasons'][:5]}", file=sys.stderr)
        out = [{
            "asset": best["coin"],
            "direction": best["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 1..5; runtime applies it
            "data": {
                "score": best["score"],
                "leverage": leverage,
                "direction": best["direction"],
                "reasons": best["reasons"],
                "imbalanceRatio": best["imbalance_ratio"],
                "bidDepth": best["bid_depth"],
                "askDepth": best["ask_depth"],
                "mom15mPct": best["mom_15m_pct"],
                "mom5mPct": best["mom_5m_pct"],
                "smDirection": best["sm_direction"] or "NEUTRAL",
                "smTiltPct": best["sm_tilt_pct"],
                "volumeTrendPct": best["volume_trend_pct"],
                "heldAssets": held_assets,
            },
        }]

    # ── persist dedup map + this tick's result every tick; bounded by
    #    state_history_max_count. Read back via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[marlin.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
