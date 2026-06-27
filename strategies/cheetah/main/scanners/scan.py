"""CHEETAH — supervised scanner (Runtime 3.0 port of the v2 Cheetah confluence sniper).

UNIVERSE scanner. Per tick: read the account + held set (clearinghouse), fetch the
top-100 smart-money leaderboard markets, update a 20-scan rank-climb history in
ctx.state, fetch/refresh the quality-trader overlap map (cached 15m in ctx.state),
score every eligible market via the pure `scoring.score_confluence`, apply the held /
post-close / emit cooldown filters, pick the SINGLE strongest candidate that clears
`minScore`, and emit ONE conviction-tiered signal (3/5/7/8x by score). The runtime
sizes the dollars (marginPct intent), owns slots/dedup/risk gates, and trails the DSL
exit. Read-only + single-pass — no daemon, no push_signal.

Faithful port of the v2 producer `main()` flow. v2-quirks preserved and flagged. The
v2 producer's wallet-isolated JSON state files (cooldowns / last-closed / previously-held /
scan-history / quality-cache) collapse into ctx.state records here (the runtime owns the
per-wallet isolation + transactional rollback)."""

import sys
import time

import scoring

# v2 producer constants (defaults; overridable via inputs)
_DEFAULT_MIN_SCORE = 10               # v2 MIN_SCORE_DEFAULT
_DEFAULT_MARGIN_PCT = 30.0            # v2 MARGIN_PCT=0.30 -> 30 PERCENT
_DEFAULT_MAX_POSITIONS = 1            # v2 MAX_POSITIONS
_DEFAULT_EMIT_COOLDOWN = 14400        # v2 ASSET_COOLDOWN_MINUTES=240
_DEFAULT_POST_CLOSE_COOLDOWN = 14400  # v2 POST_CLOSE_COOLDOWN_MINUTES=240
_DEFAULT_TTL = 14400                  # signal-dedup TTL (mirror emit cooldown)
_DEFAULT_LEADERBOARD_LIMIT = 100      # v2 fetch_sm_markets limit
_SCAN_HISTORY_MAX = 20                # v2 scan-history keeps last 20 scans
_DEFAULT_QT_CACHE = 900               # v2 QT_CACHE_MINUTES=15
_DEFAULT_QT_POOL = 25                 # v2 QT_POOL_SIZE
_DEFAULT_QT_TIMEFRAME = "MONTHLY"     # v2 QT_TIMEFRAME
_DEFAULT_QT_CONSISTENCY = ["ELITE", "RELIABLE", "STREAKY"]  # v2 QT_CONSISTENCY


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back the
    whole tick. Returns None on failure so the existing degrade paths apply (markets
    empty -> skip tick; quality fetch empty -> +3 bonus just doesn't fire)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[cheetah.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── ACCOUNT + HELD ASSETS (v2 get_account_state, verbatim shape) ──

def _account_state(ctx, wallet):
    """Returns (account_value, position_count, held_assets_set). held_assets is
    uppercase coin symbols of any open position — dedup defense layer 1."""
    if not wallet:
        return None, None, set()
    ch = _read(ctx, "strategy_get_clearinghouse_state", {"strategy_wallet": wallet})
    if not ch:
        return None, None, set()
    data = ch.get("data", ch) if isinstance(ch, dict) else {}
    total_value = 0.0
    pos_count = 0
    held = set()
    for section in ("main", "xyz"):
        s = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {})
        total_value += scoring.safe_float(ms.get("accountValue", 0))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            if scoring.safe_float(pos.get("szi", 0)) != 0:
                pos_count += 1
                coin = pos.get("coin")
                if coin:
                    held.add(str(coin).upper())
    return total_value, pos_count, held


# ── MARKET FETCHING (v2 fetch_sm_markets, verbatim) ──

def _fetch_sm_markets(ctx, limit, xyz_banned):
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return []
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

    normalized = []
    for i, m in enumerate(markets):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = str(m.get("dex", "")).lower()
        if xyz_banned and dex == "xyz":          # v2 XYZ_BANNED — drop xyz markets
            continue
        if not token:
            continue
        normalized.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "pct": scoring.safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": scoring.safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring.safe_float(m.get("token_price_change_pct_1h",
                                                     m.get("price_change_1h", 0))),
            "contrib_15m": scoring.safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": scoring.safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": scoring.safe_float(m.get("volume", 0)),
            "avg_volume_6h": scoring.safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
        })
    return normalized


def _rank_climb(prev_snapshot, current_snapshot, token, dex):
    """v2 get_rank_climb — ranks gained for (token,dex) between the last two scans."""
    if not prev_snapshot or not current_snapshot:
        return 0
    prev_rank = current_rank = None
    for m in prev_snapshot:
        if m.get("token") == token and m.get("dex", "") == dex:
            prev_rank = m.get("rank", 999)
            break
    for m in current_snapshot:
        if m.get("token") == token and m.get("dex", "") == dex:
            current_rank = m.get("rank", 999)
            break
    if prev_rank is None or current_rank is None:
        return 0
    return max(0, prev_rank - current_rank)


# ── QUALITY-TRADER POSITIONS (v2 fetch_quality_trader_positions, verbatim parse) ──

def _fetch_quality_positions(ctx, inputs):
    """Returns { "ASSET:DIRECTION": [addr, ...] }. v7.1.0 widened filter +
    v7.1.1 dual-shape parser preserved verbatim. Per-trader reads are individually
    read-guarded so one bad trader never rolls back the tick."""
    pool_size = int(inputs.get("qtPoolSize", _DEFAULT_QT_POOL))
    timeframe = inputs.get("qtTimeframe", _DEFAULT_QT_TIMEFRAME)
    consistency = inputs.get("qtConsistency", _DEFAULT_QT_CONSISTENCY)

    raw = _read(ctx, "discovery_get_top_traders", {
        "time_frame": timeframe,
        "sort_by": "PROFIT_AND_LOSS_UNREALIZED",
        "consistency": consistency,
        # v7.1.0: open_position_filter removed — pool stayed empty when WEEKLY +
        # currently-flat elites returned 0; per-trader positions fetch filters flat
        # traders implicitly (empty positions array contributes nothing).
        "limit": pool_size,
    })
    if not raw:
        return {}

    traders = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            traders = data.get("traders", [])
        elif isinstance(data, list):
            traders = data

    addresses = []
    for t in traders:
        if isinstance(t, dict):
            addr = str(t.get("address", "")).lower()
            if addr:
                addresses.append(addr)

    positions_map = {}
    shape_warned = False
    for addr in addresses:
        data = _read(ctx, "leaderboard_get_trader_positions", {"trader_id": addr})
        if not data:
            continue
        # v7.1.1: leaderboard_get_trader_positions returns
        #   { data: { positions: { trader_id, rank, positions: [...] } } }
        # — nested one level deeper than the schema guide. Handle BOTH shapes.
        positions = []
        if isinstance(data, dict):
            d = data.get("data", data)
            if isinstance(d, dict):
                raw_positions = d.get("positions", d.get("top_positions", []))
                if isinstance(raw_positions, list):
                    positions = raw_positions
                elif isinstance(raw_positions, dict):
                    nested = raw_positions.get("positions", [])
                    if isinstance(nested, list):
                        positions = nested
                    elif not shape_warned:
                        print(f"[cheetah.scan] POSITIONS_SHAPE_WARN nested dict for {addr[:10]}: "
                              f"keys={list(raw_positions.keys())[:8]}; treating as empty.",
                              file=sys.stderr)
                        shape_warned = True
                elif not shape_warned:
                    print(f"[cheetah.scan] POSITIONS_SHAPE_WARN type "
                          f"{type(raw_positions).__name__} for {addr[:10]}: treating as empty.",
                          file=sys.stderr)
                    shape_warned = True
            elif isinstance(d, list):
                positions = d
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            asset = str(
                pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", ""))))
            ).upper()
            if not asset:
                continue
            direction = str(pos.get("direction", pos.get("side", ""))).upper()
            if direction not in ("LONG", "SHORT"):
                szi = scoring.safe_float(pos.get("szi", 0))
                if szi != 0:
                    direction = "LONG" if szi > 0 else "SHORT"
                else:
                    direction = "LONG"          # v2-quirk: szi==0 defaults LONG
            key = f"{asset}:{direction}"
            positions_map.setdefault(key, []).append(addr)

    if len(addresses) < max(2, pool_size // 2) or not positions_map:
        print(f"[cheetah.scan] QT_POOL_WARN traders={len(addresses)} (configured {pool_size}), "
              f"positions_map_size={len(positions_map)}; +3 QUALITY_TRADER bonus may not fire.",
              file=sys.stderr)
    return positions_map


def scan(inputs, ctx):
    wallet = ctx.wallet
    min_score = float(inputs.get("minScore", _DEFAULT_MIN_SCORE))
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))   # PERCENT (0,100]
    max_positions = int(inputs.get("maxPositions", _DEFAULT_MAX_POSITIONS))
    xyz_banned = bool(inputs.get("xyzBanned", True))
    leaderboard_limit = int(inputs.get("leaderboardLimit", _DEFAULT_LEADERBOARD_LIMIT))
    tiers = inputs.get("leverageTiers", scoring.DEFAULT_LEVERAGE_TIERS)
    emit_cooldown = float(inputs.get("emitCooldownSeconds", _DEFAULT_EMIT_COOLDOWN))
    post_close_cooldown = float(inputs.get("postCloseCooldownSeconds", _DEFAULT_POST_CLOSE_COOLDOWN))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))
    qt_cache = float(inputs.get("qtCacheSeconds", _DEFAULT_QT_CACHE))
    now = time.time()

    # ── prior state (cooldown maps, held set, scan-rank history, quality cache) ──
    last = (ctx.state.last() or {}) if ctx.state else {}
    emit_cooldowns = dict(last.get("emit_cooldowns") or {})       # {ASSET: ts}
    last_closed = dict(last.get("last_closed") or {})             # {ASSET: ts}
    previously_held = set(last.get("previously_held") or [])
    scan_history = list(last.get("scan_history") or [])           # [ [ {token,dex,rank,direction}... ] ... ]
    recent = dict(last.get("recent") or {})                       # {ASSET: ts} signal-dedup
    quality_cache = last.get("quality_cache") or {}               # {ts, positions_map}

    def _persist(extra=None):
        if ctx.state is None:
            return
        rec = {
            "ts": now,
            "emit_cooldowns": emit_cooldowns,
            "last_closed": last_closed,
            "previously_held": sorted(previously_held),
            "scan_history": scan_history[-_SCAN_HISTORY_MAX:],
            "recent": recent,
            "quality_cache": quality_cache,
        }
        if extra:
            rec.update(extra)
        try:
            ctx.state.append(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[cheetah.scan] WARNING: state append failed: {exc!r}", file=sys.stderr)

    # ── account state + held assets ──
    account_value, pos_count, held_assets = _account_state(ctx, wallet)
    if account_value is None or account_value <= 0:
        print("[cheetah.scan] cannot read account value; skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_account"}})
        return []

    # ── close detection: anything in previously_held but no longer held just closed ──
    closed_this_tick = set(previously_held) - set(held_assets)
    for asset in closed_this_tick:
        last_closed[asset] = now
    previously_held = set(held_assets)

    # ── max-positions guard (1-slot sniper) ──
    if pos_count >= max_positions:
        print(f"[cheetah.scan] riding open position(s): {sorted(held_assets)}", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "max_positions", "held": sorted(held_assets)}})
        return []

    # ── fetch SM markets ──
    markets = _fetch_sm_markets(ctx, leaderboard_limit, xyz_banned)
    if not markets:
        print("[cheetah.scan] failed to fetch leaderboard_get_markets; skip tick", file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_markets"}})
        return []

    # ── update scan-rank history (for rank-climb scoring) ──
    prev_snapshot = scan_history[-1] if scan_history else None
    current_snapshot = [
        {"token": m["token"], "dex": m["dex"], "rank": m["rank"], "direction": m["direction"]}
        for m in markets
    ]
    scan_history.append(current_snapshot)
    scan_history = scan_history[-_SCAN_HISTORY_MAX:]

    # ── quality-trader positions (cached qt_cache seconds in ctx.state) ──
    if quality_cache and (now - quality_cache.get("ts", 0)) < qt_cache:
        quality_positions = quality_cache.get("positions_map", {})
    else:
        quality_positions = _fetch_quality_positions(ctx, inputs)
        quality_cache = {"ts": now, "positions_map": quality_positions}

    # ── score every eligible market ──
    # Filter order (v2): held > post-close-cooldown > emit-cooldown > score.
    thresholds = inputs
    candidates = []
    all_scored = []
    for market in markets:
        token = market["token"]
        if token in held_assets:                                  # dedup layer 1
            continue
        lc = last_closed.get(token, 0)
        if lc and (now - lc) < post_close_cooldown:               # post-close cooldown
            continue
        ec = emit_cooldowns.get(token, 0)
        if ec and (now - ec) < emit_cooldown:                     # emit (anti-spam) cooldown
            continue

        rc = _rank_climb(prev_snapshot, current_snapshot, token, market["dex"])
        score, reasons = scoring.score_confluence(market, quality_positions, rc, thresholds)
        if score == 0:
            continue
        all_scored.append({"token": token, "direction": market["direction"], "score": score})
        if score >= min_score:
            key = f"{token}:{market['direction']}"
            cand = dict(market)
            cand["score"] = score
            cand["reasons"] = reasons
            cand["quality_align"] = len(quality_positions.get(key, []))
            cand["rank_climb"] = rc
            candidates.append(cand)

    if not candidates:
        top3 = sorted(all_scored, key=lambda s: s["score"], reverse=True)[:3]
        print(f"[cheetah.scan] 0 candidates >= {min_score:.0f} ({len(all_scored)} scored); top={top3}",
              file=sys.stderr)
        _persist({"result": {"emitted": False, "gate": "no_candidate", "scored": len(all_scored),
                             "top": top3, "closed": sorted(closed_this_tick)}})
        return []

    # ── pick the single strongest candidate (v2 emits only top candidate per tick) ──
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    leverage = scoring.get_leverage_for_score(best["score"], tiers)   # runtime clamps to venue max

    vol_ratio = round(best["volume"] / best["avg_volume_6h"], 2) if best["avg_volume_6h"] > 0 else 0
    out = [{
        "asset": best["token"],
        "direction": best["direction"],
        "marginPct": margin_pct,                  # SIZING INTENT (PERCENT) — runtime sizes the dollars
        "leverage": leverage,                     # conviction-tiered 3/5/7/8; runtime applies + clamps
        "data": {
            "score": best["score"],
            "leverage": leverage,
            "direction": best["direction"],
            "reasons": " | ".join(best["reasons"]),
            "smPct": float(best["pct"]),
            "smTraders": int(best["traders"]),
            "rank": int(best["rank"]),
            "contrib15m": float(best["contrib_15m"]),
            "contrib1h": float(best["contrib_1h"]),
            "priceChg4hPct": float(best["price_chg_4h"]),
            "priceChg1hPct": float(best["price_chg_1h"]),
            "volRatio": vol_ratio,
            "qualityAlign": int(best.get("quality_align", 0)),
            "rankClimb": int(best.get("rank_climb", 0)),
            "heldAssets": sorted(held_assets),
        },
    }]

    # ── mark emit cooldown + signal-dedup for the emitted asset ──
    emit_cooldowns[best["token"]] = now
    recent[best["token"]] = now
    print(f"[cheetah.scan] EMIT {best['token']} {best['direction']} score={best['score']} "
          f"{leverage}x | {' | '.join(best['reasons'][:5])}", file=sys.stderr)
    _persist({"result": {"emitted": True, "asset": best["token"], "direction": best["direction"],
                         "score": best["score"], "leverage": leverage,
                         "candidates": len(candidates), "scored": len(all_scored),
                         "closed": sorted(closed_this_tick)}})
    return out
