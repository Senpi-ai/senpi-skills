"""BALD EAGLE — supervised scanner (Runtime 3.0 port of the v2 XYZ Contrarian Fader).

UNIVERSE scanner over a FIXED 6-asset XYZ macro basket (CL, BRENTOIL, GOLD, SILVER,
SP500, XYZ100; all on the Hyperliquid HIP-3 `xyz` DEX). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - fetches the smart-money leaderboard (leaderboard_get_markets), filtered to the
    `xyz` DEX + the 6-asset universe, picking the best SM row per token,
  - for each non-held, non-recently-signaled candidate: fetches 1h/4h candles + funding,
    scores it IN THE SM DIRECTION via the pure `scoring.score_candidate`,
  - applies the v2 gate stack: minScore floor, then the spread HARD GATE (<0.1% book
    spread — execution-quality, not thesis),
  - FLIPS the direction (contrarian fade — emit OPPOSITE the SM consensus),
  - emits the SINGLE highest-scoring candidate (v2 main() emitted only `best`),
    sized by conviction-scaled leverage (5x/7x) + a fixed margin PERCENT.

Read-only + single-pass. Emits a top-level `marginPct` (PERCENT) + `leverage`; the
runtime sizes the dollars, owns cooldowns/daily caps/drawdown halt, and trails the DSL
exit. No daemon, no push_signal, no create_position.

FIDELITY NOTES vs eagle-producer.py v5.0.1 (thesis verbatim from v4.1):
  - SCORING (scoring.score_candidate): SM concentration, trader depth, contribution
    velocity (1h/4h), 4h price alignment, move-exhaustion bonus (XYZ 1-2% bands), 4h
    trend structure, 1h momentum, volume trend, funding alignment — ALL VERBATIM.
    MIN_SCORE 8 verbatim. Leverage tiers (5x/7x; cap 7) verbatim.
  - CONTRARIAN FLIP: scoring runs in the SM consensus direction; scan() emits the
    OPPOSITE direction (scoring.fade_direction) — verbatim v2 main() behaviour.
  - SPREAD HARD GATE (<0.1%, MAX_SPREAD_PCT 0.001): preserved as a producer-side
    pre-filter via market_get_asset_data(..., include_order_book=True, dex="xyz").
    SPREAD_UNREADABLE / SPREAD_WIDE both skip the candidate (verbatim).
  - MARGIN: v2 MARGIN_PCT=0.40 (FRACTION) * account_value -> marginUsd. Runtime 3.0
    sizes from a PERCENT in (0,100], so this emits marginPct=40 and the runtime sizes
    (marginPct/100)*withdrawable. The 40% value is preserved verbatim. (See the
    "<=1.0 -> pasted fraction, x100" guard in scan() for the input override.)
  - DROPPED (now owned by the runtime/scaffold, NOT thesis):
      * has_resting_orders()/cancel_order 600s stale-purge — that auto-CANCELS orders,
        a MUTATION; scan() is read-only (mutations raise PermissionError). The runtime's
        own order/slot reconciliation owns stale-order handling now. FLAGGED below.
      * is_asset_cooled_down() 360m soft pre-filter -> ctx.state recent-signal dedup +
        the runtime's per_asset_cooldown_seconds guard_rail (same intent).
      * push_signal / create_position -> scan() returns the candidate; the runtime
        executes (FEE_OPTIMIZED_LIMIT maker entry).
  - v2 emitted exactly one signal (best). Preserved: scan() emits <= 1 signal/tick.
  - fundingAnnualizedPct: v2 emitted funding*100*24*365 (a rough APR). Preserved.
"""

import sys
import time

import scoring

# v2 constants (eagle-producer.py v5.0.1) — defaults; overridable via inputs
_DEFAULT_TRACKED_XYZ = ["CL", "BRENTOIL", "GOLD", "SILVER", "SP500", "XYZ100"]
_DEFAULT_MIN_SCORE = 8                 # v4.0 contrarian threshold (verbatim)
_DEFAULT_MARGIN_PCT = 40.0            # v2 MARGIN_PCT=0.40 -> 40 PERCENT
_DEFAULT_MAX_SPREAD_PCT = 0.001       # 0.1% max book spread (execution-quality hard gate)
_DEFAULT_MAX_LEVERAGE = 7            # v2 MAX_LEVERAGE
_DEFAULT_TTL = 21600                 # 360m — mirror v2 is_asset_cooled_down(360) anti re-fire
_DEFAULT_MIN_SM_PCT = 3.0           # informational (concentration already scored 0 below 3%)
_DEFAULT_MIN_SM_TRADERS = 5         # informational


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick (the contract rolls ANY exception back to []). Returns None on failure
    so the existing degrade paths apply (markets empty -> skip; candle read None ->
    technicals just don't contribute; spread unreadable -> skip that candidate)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[bald-eagle.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (port of v2 cfg.get_positions, verbatim shape) ──

def _get_account(ctx, wallet):
    """(account_value, held_tokens_set) from strategy_get_clearinghouse_state.

    READ-GUARDED. Dual-DEX equity collapse: account_value via max() across main/xyz
    (two views of ONE cross-margined wallet — summing double-counts the shared free
    balance -> 2x sizing). held_tokens are uppercase coins with the XYZ: prefix
    stripped (v2 held_tokens normalization). Includes the v2 read-sanity guard
    (margin in use + empty positions -> skip tick)."""
    if not wallet:
        return 0.0, set()
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if not ch:
        return 0.0, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, set()

    account_value = 0.0
    held = set()
    any_position = False
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            any_position = True
            coin = str(pos.get("coin", "")).upper().replace("XYZ:", "")
            if coin:
                held.add(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a corrupt
    # clearinghouse read can report margin/notional IN USE while returning an EMPTY
    # positions list; running the held-token dedup off that re-enters held names
    # (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)),
                   abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not any_position:
        print("[bald-eagle.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, set()
    return account_value, held


# ── SM SCANNING (port of v2 scan_xyz_sm, verbatim parse) ──

def _scan_xyz_sm(ctx, allowed):
    """leaderboard_get_markets filtered to dex='xyz' + the allowed universe. Keeps the
    best SM row per token (highest pct_of_top_traders_gain). Returns a list of
    normalized candidate dicts sorted by pct desc. READ-GUARDED (None -> [])."""
    raw = _read(ctx, "leaderboard_get_markets", {})
    if not raw:
        return []

    markets = raw
    if isinstance(markets, dict):
        markets = markets.get("data", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return []

    allowed_set = {a.upper() for a in allowed}
    token_best = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if dex != "xyz":
            continue
        if token not in allowed_set:
            continue

        pct = scoring._f(m.get("pct_of_top_traders_gain", 0))
        traders = int(scoring._f(m.get("trader_count", 0)))
        direction = str(m.get("direction", "")).upper()
        if direction not in ("LONG", "SHORT"):
            continue

        entry = {
            "token": token,
            "direction": direction,
            "pct": pct,
            "traders": traders,
            "price_chg_4h": scoring._f(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring._f(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_chg_1h": scoring._f(m.get("contribution_pct_change_1h", 0)),
            "contrib_chg_4h": scoring._f(m.get("contribution_pct_change_4h", 0)),
        }

        if token not in token_best or pct > token_best[token]["pct"]:
            token_best[token] = entry

    return sorted(token_best.values(), key=lambda x: x["pct"], reverse=True)


# ── CANDLE DATA FOR SCORING (port of v2 fetch_candle_data) ──

def _fetch_candle_data(ctx, token):
    """1h + 4h candles + funding for technical scoring. Requires dex='xyz'. READ-GUARDED.
    Returns the inner data dict ({candles{}, asset_context|funding}) or None (degrade —
    the technicals block in scoring just doesn't contribute)."""
    data = _read(ctx, "market_get_asset_data", {
        "asset": f"xyz:{token}",
        "candle_intervals": ["1h", "4h"],
        "include_funding": True,
        "include_order_book": False,
        "dex": "xyz",
    })
    if not data:
        return None
    if isinstance(data, dict) and "success" in data and not data.get("success"):
        return None
    return data.get("data", data) if isinstance(data, dict) else None


# ── SPREAD GATE (port of v2 check_spread) ──

def _check_spread(ctx, token):
    """Live order-book spread for an XYZ asset. Returns (spread_pct, bid, ask) or
    (None, 0, 0). Requires dex='xyz' — XYZ orderbook is silent without it. READ-GUARDED."""
    data = _read(ctx, "market_get_asset_data", {
        "asset": f"xyz:{token}",
        "candle_intervals": [],
        "include_funding": False,
        "include_order_book": True,
        "dex": "xyz",
    })
    if not data:
        return None, 0, 0

    ad = data.get("data", data) if isinstance(data, dict) else None
    if not isinstance(ad, dict):
        return None, 0, 0

    ob = ad.get("order_book", ad.get("orderBook", {}))
    if not isinstance(ob, dict):
        return None, 0, 0

    # API returns order_book.levels = [bids_array, asks_array]; each level is a dict
    # {px, sz, n}. Verified vs live xyz:BRENTOIL/GOLD/CL (v2 comment preserved).
    levels = ob.get("levels", [])
    if not isinstance(levels, list) or len(levels) < 2:
        return None, 0, 0
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        return None, 0, 0

    def _lvl_px(lvl):
        if isinstance(lvl, dict):
            return scoring._f(lvl.get("px", lvl.get("price", 0)))
        if isinstance(lvl, (list, tuple)) and lvl:
            return scoring._f(lvl[0])
        return 0

    best_bid = _lvl_px(bids[0])
    best_ask = _lvl_px(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        return None, 0, 0

    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid
    return spread_pct, best_bid, best_ask


# ── ctx.state: recent-signal dedup (port of v2 is_asset_cooled_down) ──

def _load_recent(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}
    last = ctx.state.last() or {}
    rec = last.get("recent", {})
    return dict(rec) if isinstance(rec, dict) else {}


def scan(inputs, ctx):
    now = time.time()
    allowed = inputs.get("trackedAssets", _DEFAULT_TRACKED_XYZ)
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    max_spread_pct = float(inputs.get("maxSpreadPct", _DEFAULT_MAX_SPREAD_PCT))
    max_leverage = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    tiers = inputs.get("leverageTiers", scoring.DEFAULT_LEVERAGE_TIERS)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # margin PERCENT in (0,100]; defensive guard: a <=1.0 input is a pasted FRACTION
    # (v2 stored 0.40) -> x100 (-> 40). (dire/koala pattern.)
    base_margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if base_margin_pct <= 1.0:
        base_margin_pct *= 100

    account_value, held_tokens = _get_account(ctx, ctx.wallet)
    if account_value <= 0:
        # state still advances so liveness (state.json mtime) is observable
        if ctx.state is not None:
            try:
                ctx.state.append({"recent": _load_recent(ctx),
                                  "result": {"ts": now, "emitted": False, "gate": "no_account"}})
            except Exception as exc:  # noqa: BLE001
                print(f"[bald-eagle.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)
        print("[bald-eagle.scan] WAITING — no account value", file=sys.stderr)
        return []

    recent = _load_recent(ctx)

    raw_candidates = _scan_xyz_sm(ctx, allowed)

    out = []
    result = None
    if not raw_candidates:
        result = {"ts": now, "emitted": False, "gate": "no_sm",
                  "scanned": len(allowed), "held": sorted(held_tokens)}
        print(f"[bald-eagle.scan] WAITING — no SM signals on {','.join(sorted(allowed))} "
              f"(scanned={len(allowed)} held={sorted(held_tokens)})", file=sys.stderr)
    else:
        scored = []
        for cand in raw_candidates:
            token = cand["token"]
            if token in held_tokens:                               # dedup layer 1 (held)
                continue
            last = recent.get(token)                               # dedup layer 2 (recent emit)
            if last is not None and (now - last) < ttl:
                continue

            candle_data = _fetch_candle_data(ctx, token)
            score, reasons = scoring.score_candidate(cand, candle_data)
            if score < min_score:                                  # gate: minScore floor
                continue

            # gate: spread HARD GATE (execution-quality, <0.1%)
            spread_pct, _bid, _ask = _check_spread(ctx, token)
            if spread_pct is None:
                reasons.append("order book spread unreadable")
                continue
            if spread_pct > max_spread_pct:
                reasons.append(f"spread too wide at {spread_pct*100:.3f}%")
                continue
            reasons.append(f"tight spread of {spread_pct*100:.3f}%")

            # CONTRARIAN FLIP — fade SM consensus (verbatim v2 main())
            sm_direction = cand["direction"]
            fade_dir = scoring.fade_direction(sm_direction)
            reasons.insert(0, f"fading the {sm_direction} smart-money crowd on xyz:{token}")

            funding = 0.0
            if candle_data:
                ac = candle_data.get("asset_context", candle_data)
                if isinstance(ac, dict):
                    funding = scoring._f(ac.get("funding", 0))

            scored.append({
                "asset": f"xyz:{token}",
                "token": token,
                "direction": fade_dir,
                "score": score,
                "reasons": reasons,
                "smDirection": sm_direction,
                "smPct": cand["pct"],
                "smTraders": cand["traders"],
                "priceChg4h": cand["price_chg_4h"],
                "priceChg1h": cand["price_chg_1h"],
                "contribChg1h": cand["contrib_chg_1h"],
                "contribChg4h": cand["contrib_chg_4h"],
                "fundingAnnualizedPct": funding * 100 * 24 * 365,   # rough APR (v2 verbatim)
                "spreadPct": spread_pct * 100,
            })

        if not scored:
            best_raw = raw_candidates[0]
            note = (f"{len(raw_candidates)} XYZ candidates, best raw: {best_raw['token']} "
                    f"{best_raw['direction']} {best_raw['pct']:.1f}% SM — "
                    f"none passed score {min_score:.0f} + spread gate")
            result = {"ts": now, "emitted": False, "gate": "no_candidate",
                      "scanned": len(allowed), "candidates": 0, "held": sorted(held_tokens)}
            print(f"[bald-eagle.scan] WAITING — {note}", file=sys.stderr)
        else:
            # v2 emitted exactly the single best (highest score).
            scored.sort(key=lambda x: x["score"], reverse=True)
            best = scored[0]
            leverage = scoring.get_leverage_for_score(best["score"], tiers, max_leverage)
            margin_pct = round(base_margin_pct, 4)

            recent[best["token"]] = now
            result = {"ts": now, "emitted": True, "gate": "pass",
                      "asset": best["asset"], "direction": best["direction"],
                      "score": best["score"], "leverage": leverage,
                      "marginPct": margin_pct, "smDirection": best["smDirection"],
                      "smPct": best["smPct"], "candidates": len(scored),
                      "held": sorted(held_tokens), "reasons": best["reasons"][:6]}
            print(f"[bald-eagle.scan] EMIT {best['asset']} {best['direction']} "
                  f"(fade SM {best['smDirection']}) score={best['score']} {leverage}x "
                  f"marginPct={margin_pct}% | {best['reasons'][:6]}", file=sys.stderr)
            out = [{
                "asset": best["asset"],                # "xyz:TOKEN" (HIP-3 DEX prefix)
                "direction": best["direction"],        # FADE direction (opposite SM)
                "marginPct": margin_pct,               # PERCENT of withdrawable — runtime sizes the dollars
                "leverage": leverage,                  # conviction-tiered (5/7); runtime applies + clamps
                "data": {
                    "score": float(best["score"]),
                    "leverage": float(leverage),
                    "marginPct": margin_pct,
                    "smDirection": best["smDirection"],
                    "fadeDirection": best["direction"],
                    "smPct": float(best["smPct"]),
                    "smTraders": int(best["smTraders"]),
                    "priceChg4hPct": float(best["priceChg4h"]),
                    "priceChg1hPct": float(best["priceChg1h"]),
                    "contribChg1hPct": float(best["contribChg1h"]),
                    "contribChg4hPct": float(best["contribChg4h"]),
                    "fundingAnnualizedPct": float(best["fundingAnnualizedPct"]),
                    "spreadPct": float(best["spreadPct"]),
                    "reasons": best["reasons"],
                    "heldAssets": sorted(held_tokens),
                },
            }]

    # ── persist dedup map + this tick's result EVERY tick; bounded by
    #    state_history_max_count. Read the history via ctx.state.recent(n). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"recent": recent, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[bald-eagle.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
