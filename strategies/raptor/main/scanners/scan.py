"""RAPTOR — supervised scanner (Runtime 3.0 port of the v2 Raptor v4.0.0 producer).

Quality-first hot-streak follower. Per tick: pull ELITE/RELIABLE traders winning the
WEEKLY window (discovery_get_top_traders), fetch each one's open positions
(leaderboard_get_trader_positions), pick the strongest position, confirm a smart-money
lean (leaderboard_get_markets) + whale entry-discipline (market_get_prices), score via
the pure `scoring`, dedup per-(trader,asset) in ctx.state, and emit the SINGLE best
candidate as a conviction-scaled marginPct + per-signal leverage (7/8/10) INTENT. The
runtime sizes the dollars, owns slots/cooldowns/risk gates, and trails the DSL exit.

Read-only + single-pass — no daemon, no push_signal. Derived universe (coins come from
the hot trader's positions; xyz banned). EVERY ctx.senpi_mcp.call_tool is read-guarded
(per-trader in the positions loop) so one transient/permission read error degrades that
asset rather than rolling back the whole tick."""

import sys
import time

import scoring


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[raptor.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


def _extract_list(raw, *candidate_paths):
    """v2 _extract_list — walk candidate key-paths, fall back to a top-level list or a
    'data' list. Preserved verbatim."""
    if raw is None:
        return []
    for path in candidate_paths:
        cur = raw
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                cur = None
                break
        if isinstance(cur, list):
            return cur
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return []


def fetch_quality_hot_traders(ctx, inputs):
    """Port of v2 fetch_quality_hot_traders (verbatim filter chain): ELITE/RELIABLE
    weekly winners with unrealized >= minDeltaPnl, sorted by unrealized desc."""
    limit = int(inputs.get("qualityPoolSize", 20))
    min_delta_usd = float(inputs.get("minDeltaPnl", 500_000))
    raw = _read(ctx, "discovery_get_top_traders", {
        "time_frame": "WEEKLY",
        "sort_by": "PROFIT_AND_LOSS_UNREALIZED",
        "consistency": ["ELITE", "RELIABLE"],
        "open_position_filter": True,
        "limit": limit,
    })
    if not raw:
        return []
    raw_list = _extract_list(raw, ("data", "traders"), ("traders",))
    quality = []
    for t in raw_list:
        if not isinstance(t, dict):
            continue
        addr = str(t.get("address", "")).lower()
        if not addr:
            continue
        unrealized = scoring.safe_float(t.get("unRealizedProfitAndLoss", 0))
        realized = scoring.safe_float(t.get("realizedProfitAndLoss", 0))
        total_pnl = scoring.safe_float(t.get("profitAndLoss", unrealized + realized))
        tcs_label = str(t.get("tcsLabel", "")).upper()
        if tcs_label not in ("ELITE", "RELIABLE"):
            continue
        if unrealized < min_delta_usd:
            continue
        quality.append({
            "address": addr,
            "unrealized_pnl": unrealized,
            "realized_pnl": realized,
            "total_pnl": total_pnl,
            "tcs_label": tcs_label,
            "tcs_value": scoring.safe_float(t.get("tcsValue", 0)),
            "roi": scoring.safe_float(t.get("returnOnInvestment", 0)),
        })
    quality.sort(key=lambda x: x["unrealized_pnl"], reverse=True)
    return quality


def fetch_trader_positions(ctx, trader_address):
    """v3.4 nested-dict parser fix preserved verbatim."""
    raw = _read(ctx, "leaderboard_get_trader_positions", {"trader_id": trader_address})
    if not raw:
        return []
    return _extract_list(
        raw,
        ("data", "positions", "positions"),   # nested-dict (actual prod shape)
        ("data", "top_positions", "positions"),
        ("data", "positions"),
        ("data", "top_positions"),
        ("positions",),
        ("top_positions",),
    )


def fetch_sm_map(ctx, inputs):
    """Port of v2 fetch_sm_map (verbatim): per-asset smart-money lean from
    leaderboard_get_markets, xyz dex dropped."""
    xyz_banned = bool(inputs.get("xyzBanned", True))
    raw = _read(ctx, "leaderboard_get_markets", {"limit": 100})
    if not raw:
        return {}
    markets = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            markets = data.get("markets", [])
            if isinstance(markets, dict):
                markets = markets.get("markets", [])
        elif isinstance(data, list):
            markets = data
    elif isinstance(raw, list):
        markets = raw
    sm_map = {}
    for m in markets:
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", "")).upper()
        dex = str(m.get("dex", "")).lower()
        if xyz_banned and dex == "xyz":
            continue
        if not token:
            continue
        sm_map[token] = {
            "direction": str(m.get("direction", "")).upper(),
            "pct": scoring.safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0) or 0),
            "price_chg_4h": scoring.safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring.safe_float(m.get("token_price_change_pct_1h",
                                                     m.get("price_change_1h", 0))),
            "contrib_15m": scoring.safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": scoring.safe_float(m.get("contribution_pct_change_1h", 0)),
        }
    return sm_map


def fetch_current_px(ctx, asset):
    """Port of v2's inline price fetch for the entry-discipline gate. 0.0 if unknown."""
    raw = _read(ctx, "market_get_prices", {"assets": [asset]})
    if not raw:
        return 0.0
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, dict):
        return scoring.safe_float(data.get(asset, 0))
    return 0.0


def held_assets_from_clearinghouse(ctx):
    """Read-only held-asset list from the clearinghouse — used to skip re-entering a coin
    we already hold (defence-in-depth alongside the runtime's slot/dedup ownership). One
    wallet, two sub-DEX views; collect coins from both. Returns set of upper-cased coins."""
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": ctx.wallet})
    held = set()
    if not ch:
        return held
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return held
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if not isinstance(pos, dict):
                continue
            if scoring.safe_float(pos.get("szi", 0)) == 0:
                continue
            coin = str(pos.get("coin", "")).upper()
            if coin:
                held.add(coin)
    return held


def build_candidate(ctx, trader, positions, sm_map, inputs):
    """v2 build_signal, decomposed: pure selection/SM/score in scoring, the one MCP read
    (current price for entry-discipline) here under a read-guard."""
    best_pos, concentration = scoring.select_best_position(positions, inputs)
    if not best_pos:
        return None

    sm = sm_map.get(best_pos["asset"])
    if not scoring.sm_gate(sm, best_pos, inputs):
        return None

    # v3.3 ENTRY DISCIPLINE — fetch current price only when the whale's entry is known
    current_px = 0.0
    if best_pos.get("whale_entry_px", 0) > 0:
        current_px = fetch_current_px(ctx, best_pos["asset"])
    if scoring.price_run_blocks(best_pos, current_px, inputs):
        return None

    return scoring.score_signal(trader, best_pos, concentration, sm, current_px, inputs)


def scan(inputs, ctx):
    now = time.time()
    dedupe_hours = float(inputs.get("eventDedupeHours", 4))
    min_score = float(inputs.get("minScore", 6))
    positions_fetch_limit = int(inputs.get("positionsFetchLimit", 10))
    tiers = inputs.get("leverageTiers", [[10, 10], [8, 8], [6, 7]])
    default_leverage = int(inputs.get("defaultLeverage", 7))

    # ── state: per-(trader,asset) event-dedup map (v2 seen-events, 4h window) ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    cutoff = now - (dedupe_hours * 3600)
    seen = {k: v for k, v in (last.get("seen") or {}).items() if v > cutoff}

    def _seen_key(trader_id, asset):
        return f"{trader_id[:10].lower()}:{asset}"

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({"seen": seen, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[raptor.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    quality_traders = fetch_quality_hot_traders(ctx, inputs)
    if not quality_traders:
        print("[raptor.scan] no ELITE/RELIABLE traders above threshold", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_quality_traders", "candidates": 0})
        return []

    sm_map = fetch_sm_map(ctx, inputs)
    if not sm_map:
        print("[raptor.scan] no smart-money data", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_sm_data", "candidates": 0})
        return []

    held_upper = held_assets_from_clearinghouse(ctx)
    scan_limit = min(len(quality_traders), positions_fetch_limit)

    candidates = []
    for t in quality_traders[:scan_limit]:
        positions = fetch_trader_positions(ctx, t["address"])
        if not positions:
            continue
        sig = build_candidate(ctx, t, positions, sm_map, inputs)
        if not sig:
            continue
        if seen.get(_seen_key(t["address"], sig["asset"])) is not None:   # event-dedup
            continue
        if sig["asset"] in held_upper:                                    # already holding
            continue
        candidates.append(sig)

    if not candidates:
        print(f"[raptor.scan] no candidates passed filters ({len(quality_traders)} quality traders)",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_candidates",
                  "quality_traders": len(quality_traders), "candidates": 0})
        return []

    candidates.sort(key=lambda s: s["score"], reverse=True)
    best = candidates[0]

    if best["score"] < min_score:
        print(f"[raptor.scan] HOLD: best score {best['score']} < {min_score:.0f}", file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "below_min_score",
                  "quality_traders": len(quality_traders), "candidates": len(candidates),
                  "score": best["score"], "asset": best["asset"], "direction": best["direction"]})
        return []

    leverage = scoring.get_leverage_for_score(best["score"], tiers, default_leverage)
    margin_pct = scoring.margin_pct_for(best["score"], inputs)

    # mark the (trader,asset) event seen so we don't re-fire it within the dedupe window
    seen[_seen_key(best["fullTraderId"], best["asset"])] = now

    print(f"[raptor.scan] EMIT: {best['asset']} {best['direction']} score={best['score']} "
          f"{leverage}x margin={margin_pct}% tcs={best['tcs']} | {best['reasons']}", file=sys.stderr)
    _persist({"ts": now, "emitted": True, "gate": "pass",
              "quality_traders": len(quality_traders), "candidates": len(candidates),
              "asset": best["asset"], "direction": best["direction"], "score": best["score"],
              "leverage": leverage, "marginPct": margin_pct, "tcs": best["tcs"],
              "concentration": best["concentration"], "reasons": best["reasons"]})

    # whaleEntryPx/currentPx are number-typed in the schema; v2 emitted them as null when
    # unknown. Omit (rather than emit null) when unknown — an absent optional key is always
    # schema-valid, a present null on a number-typed key is not guaranteed to be.
    data = {
        "score": best["score"], "leverage": leverage, "direction": best["direction"],
        "tcs": best["tcs"], "traderId": best["traderId"],
        "traderDeltaPnl": best["traderDeltaPnl"], "positionDeltaPnl": best["positionDeltaPnl"],
        "concentration": best["concentration"], "smPct": best["smPct"],
        "smTraders": best["smTraders"], "priceChg4h": best["priceChg4h"],
        "priceChg1h": best["priceChg1h"], "reasons": best["reasons"],
    }
    if best["whaleEntryPx"] is not None:
        data["whaleEntryPx"] = best["whaleEntryPx"]
    if best["currentPx"] is not None:
        data["currentPx"] = best["currentPx"]

    return [{
        "asset": best["asset"],
        "direction": best["direction"],
        "marginPct": margin_pct,          # PERCENT of withdrawable (0,100] — runtime sizes the dollars
        "leverage": leverage,             # conviction-tiered (7/8/10); runtime applies it
        "data": data,
    }]
