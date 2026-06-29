"""JACKAL — pure copy-trade scoring math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Jackal v3.0.1 producer (jackal-producer.py).
Given trader/position/candle dicts already fetched by scan.py, this module reproduces
VERBATIM the v2 producer's:
  - trader-pool quality filter + composite quality score (_compute_quality_score, v2.0.6)
  - new-entry diff key derivation (coin, LONG/SHORT-from-szi)
  - pool-consensus aggregation (same coin+direction; same-asset-any-direction)
  - per-asset trend labels (open->close % over a candle window) + funding annualization
so a fidelity harness can diff it against the v2 producer on the same snapshot.

The v2 producer pushed `score = quality_score / 100` (0..1 confidence) and let the
runtime size each entry from a FLAT `margin_pct: 30`. Jackal applied NO producer-side
conviction scaling, so this port emits a FLAT base marginPct by default
(qualityMarginScale defaults to 0 => no scale) to preserve v2 sizing exactly; the knob
is exposed for operators but defaults to faithful flat behaviour.

Pure / single-pass / unit-testable on plain dicts. All clock/MCP lives in scan.py.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT


def _f(x, *keys, default=0.0):
    """Float from x. If keys given, x is a dict and we try each key in order
    (first non-None wins). Mirrors the v2 producer's camelCase/snake_case fallbacks."""
    if keys:
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ── trader pool: filter floors + quality score (ported verbatim from v2) ──

def pool_field_win_rate(t):
    # v2: discovery_get_top_traders returns winRate as a 0-100 PERCENTAGE (already, not a fraction).
    return _f(t, "win_rate", "winRate", default=0.0)


def pool_field_roi(t):
    # v2.0.4: MCP returns returnOnInvestment (camelCase); keep all fallbacks.
    return _f(t, "return_on_investment", "roi", "returnOnInvestment", default=0.0)


def pool_field_age_days(t):
    # v2.0.4: MCP returns traderAgeSeconds (not days); convert. Keep snake/camel fallbacks.
    direct = _f(t, "trader_age_days", "traderAgeDays", default=0.0)
    if direct > 0:
        return direct
    secs = _f(t, "traderAgeSeconds", "trader_age_seconds", default=0.0)
    return secs / 86400.0 if secs > 0 else 0.0


def compute_quality_score(trader):
    """Composite quality score 0-100, ported VERBATIM from v2.0.6 _compute_quality_score.

    Weighting reflects Jackal's actual edge — tail-winner trend-followers (low win-rate,
    huge winners), NOT high-win-rate scalpers:
      - ROI            50 pts cap (reward big-winner distributions; 150%+ -> full)
      - gain-to-pain   25 pts cap (risk-adjusted pnl, the real quality signal; g/p>=5 -> full)
      - age            15 pts cap (survivorship buffer; 90+ days -> full)
      - win rate       10 pts cap (CEILING not requirement; 60%+ -> full)
    NOTE: winRate is treated as a 0-100 PERCENTAGE here (v2.0.6 bug fix — the prior
    `win_rate * 100` mis-pegged every trader at the cap)."""
    win_rate = pool_field_win_rate(trader)            # already 0-100 %
    roi_30d = pool_field_roi(trader)
    age_days = pool_field_age_days(trader)
    gain_to_pain = _f(trader, "gain_to_pain_ratio", "gainToPainRatio", default=0.0)

    score = 0.0
    score += min(roi_30d / 150.0 * 50, 50)            # 50 pts for 150%+ ROI
    score += min(gain_to_pain / 5.0 * 25, 25)         # 25 pts for g/p >= 5
    score += min(age_days / 90.0 * 15, 15)            # 15 pts for 90+ day age
    score += min(win_rate / 60.0 * 10, 10)            # 10 pts for 60%+ winrate (ceiling)
    return round(score, 2)


def build_pool(raw_traders, inputs):
    """Filter + quality-score + rank the raw discovery_get_top_traders list.
    Ported verbatim from v2 refresh_pool: win_rate/roi/age floors, top-N by quality.
    Returns a list of pool-member dicts (the producer's `traders` cache shape)."""
    min_win_rate = float(inputs.get("poolMinWinRate", 0.50))
    min_roi_30d = float(inputs.get("poolMinRoi30d", 10.0))
    min_age_days = float(inputs.get("poolMinTraderAgeDays", 14))
    pool_size = int(inputs.get("poolSize", 25))

    # v2 floor compared win_rate against POOL_MIN_WIN_RATE=0.50 while win_rate is a
    # 0-100 percentage — i.e. the v2 floor was effectively winRate >= 0.50% (a no-op
    # for any real trader). Reproduced VERBATIM (do not "fix" to *100 in the port).
    filtered = []
    for t in raw_traders:
        if not isinstance(t, dict):
            continue
        win_rate = pool_field_win_rate(t)
        roi_30d = pool_field_roi(t)
        age_days = pool_field_age_days(t)
        address = t.get("address") or t.get("trader_address")
        if not address:
            continue
        if win_rate < min_win_rate:
            continue
        if roi_30d < min_roi_30d:
            continue
        if age_days < min_age_days:
            continue
        filtered.append({
            "address": address.lower(),
            "user_id": t.get("user_id") or t.get("userId"),
            "username": t.get("username") or t.get("userName"),
            "quality_score": compute_quality_score(t),
            "win_rate": win_rate,
            "roi_30d": roi_30d,
            "trader_age_days": age_days,
            "consecutive_wins": int(_f(t, "consecutive_wins", "consecutiveWins", default=0)),
        })
    filtered.sort(key=lambda x: x["quality_score"], reverse=True)
    return filtered[:pool_size]


# ── position diff + direction derivation (ported verbatim from v2) ──

def derive_key(p):
    """(coin, LONG/SHORT-from-szi-sign) or (coin, None). Ported verbatim from v2
    detect_new_entries._derive_key — MCP positions carry no explicit direction key."""
    coin = p.get("coin") or p.get("asset")
    szi = _f(p, "szi", "size", default=0.0)
    direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
    return (coin, direction)


def detect_new_entries(pool, current_positions, last_seen, now, max_entry_age_seconds):
    """Diff current vs last-seen positions per pool member; return candidate dicts for
    anything that newly appeared (coin+direction not in prev) AND whose entry is within
    max_entry_age_seconds. Ported verbatim from v2 detect_new_entries."""
    candidates = []
    for trader in pool:
        addr = trader["address"]
        cur = current_positions.get(addr, [])
        prev = last_seen.get(addr, [])
        prev_keys = {derive_key(p) for p in prev if isinstance(p, dict)}

        for pos in cur:
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            if not coin:
                continue
            szi = _f(pos, "szi", "size", default=0.0)
            direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
            if not direction:
                continue
            key = (coin, direction)
            if key in prev_keys:
                continue  # not new

            # v2.0.4: MCP returns startTime (not openedAtTs/openTime).
            entry_ts = _f(pos, "openedAtTs", "openTime", "startTime", default=0.0)
            if entry_ts > 0 and (now - entry_ts) > max_entry_age_seconds:
                continue

            candidates.append({
                "trader": trader,
                "coin": coin,
                "direction": direction,
                "entry_price": _f(pos, "entryPx", "entry_price", default=0.0),
                "leverage": _leverage_of(pos),
                "size_usd": abs(_f(pos, "positionValue", "notional", default=0.0)),
                "entry_ts": entry_ts or now,
            })
    return candidates


def _leverage_of(pos):
    lev = pos.get("leverage")
    if isinstance(lev, dict):
        return _f(lev, "value", default=0.0)
    return _f(lev, default=0.0)


# ── pool consensus (ported verbatim from v2 enrich_with_consensus) ──

def enrich_with_consensus(candidates, current_positions):
    """For each candidate, count other pool members in the SAME coin+direction
    (poolConsensusCount, excluding self) and same-asset-any-direction
    (poolConsensusAssetCount, excluding self). Ported verbatim from v2."""
    by_key = {}
    for addr, positions in current_positions.items():
        for pos in positions or []:
            if not isinstance(pos, dict):
                continue
            coin = pos.get("coin") or pos.get("asset")
            szi = _f(pos, "szi", "size", default=0.0)
            direction = "LONG" if szi > 0 else ("SHORT" if szi < 0 else None)
            if not coin or not direction:
                continue
            by_key.setdefault((coin, direction), set()).add(addr)

    for c in candidates:
        key = (c["coin"], c["direction"])
        consensus_set = by_key.get(key, set()) - {c["trader"]["address"]}
        c["pool_consensus_count"] = len(consensus_set)

        asset_addrs = set()
        for (coin, _), addrs in by_key.items():
            if coin == c["coin"]:
                asset_addrs.update(addrs)
        asset_addrs.discard(c["trader"]["address"])
        c["pool_consensus_asset_count"] = len(asset_addrs)
    return candidates


# ── per-asset TA (open->close % trend) + funding (ported verbatim from v2) ──

def trend_pct(candles):
    """% change open(first)->close(last) over a candle window. Verbatim from v2 _trend_pct."""
    if not candles or not isinstance(candles, list) or len(candles) < 2:
        return None
    try:
        open_price = _f(candles[0], "open", "o", default=0.0)
        close_price = _f(candles[-1], "close", "c", default=0.0)
        if open_price <= 0:
            return None
        return round((close_price - open_price) / open_price * 100, 3)
    except (TypeError, ValueError):
        return None


def trend_label(pct):
    """BULLISH/BEARISH/NEUTRAL from a % change. Verbatim from v2 _trend_label
    (>= 0.3 BULLISH, <= -0.3 BEARISH, else NEUTRAL)."""
    if pct is None:
        return None
    if pct >= 0.3:
        return "BULLISH"
    if pct <= -0.3:
        return "BEARISH"
    return "NEUTRAL"


def annualize_funding(hourly_funding):
    """HL funding is HOURLY -> annualize x24x365 (x8760), as %. Verbatim from v2 v3.0.1
    (the v3.0.1 fix: was x3x365, 8x too low). Returns None on bad input."""
    if hourly_funding is None:
        return None
    try:
        return round(float(hourly_funding) * 8760 * 100, 2)
    except (TypeError, ValueError):
        return None


def macro_pct(candles_1h):
    """BTC 24h % from the last 24 1h candles (open[0]->close[-1]). Verbatim from v2
    fetch_btc_macro. Returns (direction, pct) or (None, None)."""
    if not candles_1h or len(candles_1h) < 24:
        return None, None
    try:
        window = candles_1h[-24:]
        open0 = _f(window[0], "open", "o", default=0.0)
        closeN = _f(window[-1], "close", "c", default=0.0)
        if open0 <= 0:
            return None, None
        pct = (closeN - open0) / open0 * 100
        return ("UP" if pct > 0 else "DOWN"), round(pct, 2)
    except (TypeError, ValueError):
        return None, None


# ── sizing (top-level marginPct) ──

def confidence_score(quality_score):
    """v2 pushed score = quality_score / 100 as the 0..1 confidence. Preserved."""
    return round(float(quality_score) / 100.0, 4)


def margin_pct_for(quality_score, inputs):
    """marginPct INTENT as a PERCENT of withdrawable in (0,100] (runtime sizes
    (marginPct/100)*withdrawable).

    v2 applied NO producer-side conviction scaling — the runtime sized every entry
    from a flat margin_pct: 30. To preserve that EXACTLY, qualityMarginScale defaults
    to 0 (flat base). When an operator opts in (>0), margin scales up to maxMarginPct
    by quality above a 55 floor (the v2.0.6 LLM-gate trust floor). Defaults reproduce
    v2 flat sizing.

    Defensive fraction guard (dire/koala pattern): a base/cap pasted as a fraction
    (<= 1.0, e.g. 0.30) is multiplied x100 to a percent."""
    base = float(inputs.get("marginPct", 30))
    if base <= 1.0:                       # pasted fraction (0.30) -> percent (30)
        base *= 100.0
    cap = float(inputs.get("maxMarginPct", base))
    if cap <= 1.0:
        cap *= 100.0
    scale_per_pt = float(inputs.get("qualityMarginScale", 0.0))   # 0 => faithful flat
    if scale_per_pt <= 0:
        return round(min(base, cap), 4)
    floor = float(inputs.get("qualityFloor", 55))
    bonus = max(0.0, float(quality_score) - floor) * scale_per_pt
    return round(min(base * (1.0 + bonus), cap), 4)
