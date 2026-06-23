"""SPIDER SWING — supervised external scanner entrypoint.

Port of senpi-skills/spider (producer v5.1.1, SPIDER_LEG=swing) to the v2
`scan(inputs, ctx)` contract. The daemon loop is gone — the runtime supervisor
calls scan() every interval_seconds.

What stayed faithful to the source:
  - score_swing scoring + all thresholds (see scoring.py — ported verbatim).
  - The dynamic universe (build_universe): static crypto alts + a live XYZ pool
    rebuilt from market_list_instruments, with the include-set / fresh-listing /
    exclude-set logic and the xyzMaxNames volume cap.
  - The recent-signal dedup (RECENT_SIGNAL_TTL): a coin signaled in the last
    `recentSignalTtlSeconds` is skipped. Source kept this in a JSON file; here it
    lives in ctx.state.
  - The XYZ first-seen ledger (new-listing auto-catch): source kept it in a JSON
    file; here it lives in ctx.state alongside the dedup map.
  - Leverage clamp to the per-asset HL venue max (clamp_leverage).
  - The dual-DEX equity collapse via max() (NOT sum()) when reading account value.

What was simplified vs the source (FLAGGED):
  - The producer also computed an `affordable` slot count from free margin and
    emitted at most min(open_slots, affordable) signals. The v2 runtime owns
    slots/affordability (strategy.slots + risk gates), so scan() emits ALL
    qualifying candidates above minScore (held + recently-signaled filtered).
    The signal-GATING thresholds (minScore, held-set, recent-dedup, venue min
    notional) are preserved exactly; only the per-tick emission CAP moves to the
    runtime. marginUsd is still computed (account_value * marginPct) and carried
    on data{} so the action sizes identically to the source.
"""

import sys
import time

import scoring


# How long after emitting a signal we treat the asset as "in-flight" and refuse
# to re-emit (source RECENT_SIGNAL_TTL_SEC = 180). Overridable via inputs.
_DEFAULT_RECENT_TTL = 180


# ── MCP data fetchers (route the producer's calls through ctx.senpi_mcp) ──

def _dex_for(asset):
    """XYZ (HIP-3) assets must pass dex='xyz'; main-DEX assets pass ''."""
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_universe_meta(ctx):
    """{name: {"max_leverage": int|None, "ctx": {...}}} for every live
    instrument on both dexes (one market_list_instruments call)."""
    try:
        data = ctx.senpi_mcp.call_tool("market_list_instruments", {})
    except Exception:
        return {}
    out = {}
    if not data:
        return out
    insts = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(insts, dict):
        insts = insts.get("instruments", [])
    for inst in insts or []:
        if not isinstance(inst, dict):
            continue
        if inst.get("is_delisted"):
            continue
        name = inst.get("name") or inst.get("context", {}).get("coin")
        if not name:
            continue
        entry = {
            "max_leverage": inst.get("max_leverage", inst.get("maxLeverage")),
            "ctx": inst.get("context", {}) if isinstance(inst.get("context"), dict) else {},
        }
        out[name] = entry
        out[name.upper()] = entry
    return out


def _get_sm_map(ctx):
    """{COIN: long_ratio_pct} from smart-money leaderboard markets."""
    try:
        data = ctx.senpi_mcp.call_tool("leaderboard_get_markets", {})
    except Exception:
        return {}
    out = {}
    if not data:
        return out
    markets = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", markets.get("leaderboard", []))
    agg = {}
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        token = m.get("token", m.get("coin", m.get("asset", "")))
        if not token:
            continue
        direction = m.get("direction", "").lower()
        pct = float(m.get("pct_of_top_traders_gain", m.get("longPct", 0)) or 0)
        a = agg.setdefault(token.upper(), {"long": 0.0, "short": 0.0})
        if direction == "long":
            a["long"] = pct
        elif direction == "short":
            a["short"] = pct
    for tok, a in agg.items():
        total = a["long"] + a["short"]
        if total > 0:
            out[tok] = a["long"] / total * 100
    return out


def _fetch_candles(ctx, asset, intervals):
    """{"candles": {iv: [...]}, "ctx": {...}} or None on failure."""
    try:
        data = ctx.senpi_mcp.call_tool("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": intervals,
            "dex": _dex_for(asset),
            "include_funding": False,
            "include_order_book": False,
        })
    except Exception:
        return None
    if not data or (isinstance(data, dict) and not data.get("success", True)):
        return None
    d = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(d, dict):
        return None
    return {"candles": d.get("candles", {}) or {}, "ctx": d.get("asset_context", {}) or {}}


def _get_account(ctx):
    """(account_value, [position_dicts]) from strategy_get_clearinghouse_state.

    Dual-DEX equity collapse: account_value is taken via max() across the
    main/xyz sections — they are TWO VIEWS of ONE cross-margined wallet, never
    summed (summing would double every position size). assetPositions ARE
    per-sub-DEX, so those are enumerated across both sections.
    """
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception:
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
        account_value = max(account_value, float(ms.get("accountValue", 0) or 0))
        for ap in s.get("assetPositions", []):
            pos = ap.get("position", ap)
            szi = float(pos.get("szi", 0) or 0)
            if szi == 0:
                continue
            positions.append({"coin": pos.get("coin", ""),
                              "margin": float(pos.get("marginUsed", 0) or 0)})
    return account_value, positions


# ── ctx.state: recent-signal dedup + xyz first-seen ledger ──
#
# The bounded ctx.state series stores one snapshot record per tick:
#   {"signaled": {COIN: epoch_seconds}, "first_seen": {name: epoch_seconds}}
# We read the latest record to recover both maps, prune the dedup map by TTL,
# and append the updated snapshot at the end of the tick.

def _load_state_maps(ctx):
    if ctx.state is None or len(ctx.state) == 0:
        return {}, {}
    last = ctx.state.last() or {}
    signaled = dict(last.get("signaled", {})) if isinstance(last.get("signaled"), dict) else {}
    first_seen = dict(last.get("first_seen", {})) if isinstance(last.get("first_seen"), dict) else {}
    return signaled, first_seen


def _was_recently_signaled(signaled, coin, ttl, now):
    last = signaled.get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl


def _prune_signaled(signaled, ttl, now):
    cutoff = now - (ttl * 4)
    return {k: v for k, v in signaled.items() if v >= cutoff}


# ── Universe resolution (static crypto alts + dynamic XYZ pool) ──

def _build_universe(inputs, meta_map, first_seen, now):
    """Resolve the asset list to score this tick (port of build_universe).

    Returns (universe, updated_first_seen). The first-seen ledger is updated
    in-place semantics: pre-existing names on the first run are backdated as
    already-old so the fresh-listing auto-catch only fires for names that appear
    AFTER deploy.
    """
    crypto = list(inputs.get("cryptoAlts", []))
    include = {t.upper() for t in inputs.get("xyzIncludeSet", [])}
    exclude = {t.upper() for t in inputs.get("xyzExcludeSet", [])}
    vol_floor = float(inputs.get("xyzVolFloorUsd", 5000000))
    fresh_days = float(inputs.get("xyzFreshDays", 21))
    max_names = int(inputs.get("xyzMaxNames", 20))

    # Fallback if the instrument board is unavailable: static include-set.
    if not meta_map:
        return crypto + [f"xyz:{t}" for t in sorted(include)], first_seen

    xyz_names = sorted(n for n in meta_map if isinstance(n, str) and n.startswith("xyz:"))

    fresh_window = fresh_days * 86400
    first_run = not first_seen
    backdate = now - fresh_window - 1  # mark pre-existing names as already-old
    first_seen = dict(first_seen)
    for n in xyz_names:
        if n not in first_seen:
            first_seen[n] = backdate if first_run else now

    qualifiers = []
    for n in xyz_names:
        ctx_meta = (meta_map.get(n) or {}).get("ctx", {})
        try:
            vol = float(ctx_meta.get("dayNtlVlm", 0) or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol < vol_floor:
            continue
        bare = n.split(":", 1)[1].upper()
        is_fresh = (now - first_seen.get(n, backdate)) < fresh_window
        if bare in include or (is_fresh and bare not in exclude):
            qualifiers.append((n, vol))

    qualifiers.sort(key=lambda x: x[1], reverse=True)
    return crypto + [n for n, _ in qualifiers[:max_names]], first_seen


# ── Entry point ──

def scan(inputs, ctx):
    now = time.time()
    min_score = inputs.get("minScore", 5)
    margin_pct = float(inputs.get("marginPct", 0.28))
    leg_max_lev = inputs.get("maxLeverage", 10)
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_RECENT_TTL))
    venue_min_notional = float(inputs.get("venueMinNotionalUsd", 10))
    min_notional_pct = float(inputs.get("minNotionalPctOfEquity", 0.01))

    account_value, positions = _get_account(ctx)
    held_assets = [p["coin"] for p in positions if p.get("coin")]
    held_set = {h.upper() for h in held_assets}
    if account_value <= 0:
        return []

    min_notional = max(account_value * min_notional_pct, venue_min_notional)
    margin_usd = round(account_value * margin_pct, 2)

    signaled, first_seen = _load_state_maps(ctx)
    signaled = _prune_signaled(signaled, ttl, now)

    meta_map = _get_universe_meta(ctx)
    sm_map = _get_sm_map(ctx) if inputs.get("useSmBonus", True) else {}
    allowed, first_seen = _build_universe(inputs, meta_map, first_seen, now)

    candidates = []
    for asset in allowed:
        au = asset.upper()
        if au in held_set:
            continue
        if _was_recently_signaled(signaled, asset, ttl, now):
            continue
        meta = meta_map.get(asset) or meta_map.get(au)
        if not meta:
            continue
        md = _fetch_candles(ctx, asset, ["1h", "4h"])
        if not md:
            continue
        c1 = md["candles"].get("1h", [])
        c4 = md["candles"].get("4h", [])
        thesis = scoring.score_swing(
            asset, c1, c4, meta.get("ctx", {}), sm_map.get(au), inputs
        )
        if thesis and thesis["score"] >= min_score:
            thesis["_meta"] = meta
            candidates.append(thesis)

    candidates.sort(key=lambda x: x["score"], reverse=True)

    out = []
    for th in candidates:
        leverage = scoring.clamp_leverage(leg_max_lev, th["_meta"].get("max_leverage"))
        notional = margin_usd * leverage
        if leverage <= 0 or notional < min_notional:
            continue
        out.append({
            "asset": th["coin"],
            "direction": th["direction"],
            "marginUsd": margin_usd,
            "leverage": leverage,
            "data": {
                "score": th["score"],
                "direction": th["direction"],
                "reasons": th["reasons"],
                "heldAssets": held_assets,
                "trend4h": th.get("trend4h"),
                "rs": round(th.get("rs", 0), 2),
                "smPct": round(th.get("smPct", 0), 1),
            },
        })
        signaled[th["coin"].upper()] = now

    # Persist the updated dedup + first-seen snapshot for next tick.
    # If this append fails (e.g. state_history_max_count is 0/unset so append
    # raises, or a transient persist error), the next tick reloads stale dedup
    # state and may re-emit already-suppressed signals. Log-and-continue: don't
    # crash the tick, but make the failure visible on stderr (the runtime
    # supervisor captures the scaffold child's stderr) instead of swallowing it.
    if ctx.state is not None:
        try:
            ctx.state.append({"signaled": signaled, "first_seen": first_seen})
        except Exception as exc:
            print(
                f"[spider.swing.scan] WARNING: dedup-state append failed; next "
                f"tick may re-emit suppressed signals: {exc!r}",
                file=sys.stderr,
            )

    return out
