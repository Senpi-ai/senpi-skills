"""JAGUAR — pure Striker rank-jump math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 JAGUAR producer's `detect_striker_signals`
+ scoring (SKILL.md v4.0.2 / jaguar-producer.py v4.0.2). The scoring/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on
the same scan-history snapshot. Behaviour-preserving quirks from v2 are kept and
flagged `# v2-quirk`; do NOT redesign — fix them only as a separate, labelled change
AFTER the port is validated.

Thesis: "one amazing trade per day." A Striker is a violent FIRST_JUMP — an asset
rocketing from rank 20+ into the top 10 with a >=10 rank jump while 15m smart-money
contribution velocity is actively building and 4h price is aligned with the SM
direction. Rare but high-conviction.

Single-pass, unit-testable on plain scan-snapshot dicts. The caller (scan.py) owns
all I/O: it fetches `leaderboard_get_markets`, builds the current snapshot, and reads
the previous snapshot(s) back out of `ctx.state`. This module only does the numbers.
"""


# ═══════════════════════════════════════════════════════════════
# CONSTANTS — preserved verbatim from v2 producer v4.0.2
# ═══════════════════════════════════════════════════════════════

MIN_SCORE = 9                       # v2 entry floor
XYZ_BANNED = True                   # v2 XYZ ban

# Striker thresholds (v3.2 / v3.3 — preserved verbatim)
STRIKER_MIN_RANK_JUMP = 10          # v3.2 floor — rank-jump detector
STRIKER_MIN_PREV_RANK = 20          # v3.3
STRIKER_MIN_VOLUME_RATIO = 1.5
STRIKER_MIN_REASONS = 3             # v3.3

# v3.4 absolute liquidity floor (replaces silent-None vol_ratio gate)
MIN_DAY_NOTIONAL_VOLUME_USD = 3_000_000   # $3M 24h liquidity floor

# Conviction-scaled leverage. Fleet analysis: >10x destroys edge via fee
# amplification. Striker apex still capped at 10x. tiers = [[min_score, leverage], ...]
DEFAULT_LEVERAGE_TIERS = [[10, 10], [9, 7]]
DEFAULT_LEVERAGE = 7
MAX_LEVERAGE = 10

# Score normaliser for the wire score (data{}.score carries the raw points).
# v2 emitted score/14.0 on the wire; we preserve the same denominator. # v2-quirk
SCORE_DENOMINATOR = 14.0


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score, tiers=None):
    """Conviction-tiered leverage. tiers = [[min_score, leverage], ...] desc by score.
    Returns (leverage, label). Clamped to MAX_LEVERAGE. Verbatim from v2
    get_leverage_for_score (the runtime owns the per-asset venue clamp downstream)."""
    tiers = tiers or DEFAULT_LEVERAGE_TIERS
    for t in tiers:
        if score >= t[0]:
            lev = min(int(t[1]), MAX_LEVERAGE)
            label = "apex" if t[0] >= 10 else ("conviction" if t[0] >= 9 else "tier")
            return lev, label
    return DEFAULT_LEVERAGE, "default"


def check_4h_alignment(direction, price_chg_4h):
    """v2-quirk: NEUTRAL/unknown direction never aligns (only LONG>0 or SHORT<0)."""
    if direction == "LONG" and price_chg_4h > 0:
        return True
    if direction == "SHORT" and price_chg_4h < 0:
        return True
    return False


def get_market_in_scan(scan, token, dex):
    for m in scan.get("markets", []):
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


def build_scan_snapshot(markets_data, now_iso):
    """Normalise a raw leaderboard_get_markets payload into the compact snapshot the
    detector compares across ticks. Verbatim from v2 build_scan_snapshot; `now_iso`
    is passed in so this stays clock-free."""
    markets = []
    for m in markets_data:
        if not isinstance(m, dict):
            continue
        markets.append({
            "token": str(m.get("token", m.get("asset", ""))).upper(),
            "dex": m.get("dex", ""),
            "rank": int(m.get("rank", m.get("position", 999))),
            "direction": str(m.get("direction", "")).upper(),
            "contribution": safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": safe_float(m.get("token_price_change_pct_1h",
                                       m.get("price_change_1h", 0))),
            "contrib_15m": safe_float(m.get("contribution_pct_change_15m", 0)),
            "contrib_1h": safe_float(m.get("contribution_pct_change_1h", 0)),
            "volume": safe_float(m.get("volume", 0)),
            "avg_volume": safe_float(m.get("avg_volume_6h", m.get("avgVolume", 0))),
            "day_notional_volume": safe_float(
                m.get("day_notional_volume",
                    m.get("dayNotionalVolume",
                        m.get("volume_24h_usd", 0)))),
            "vol_ratio": safe_float(m.get("vol_ratio", m.get("volume_ratio", 0))),
        })
    return {"markets": markets, "timestamp": now_iso}


# ═══════════════════════════════════════════════════════════════
# STRIKER SIGNAL DETECTION — preserved verbatim from v2 producer v4.0.2
# ═══════════════════════════════════════════════════════════════

def detect_striker_signals(current_scan, prev_scans):
    """Detect violent FIRST_JUMP signals with Hyperfeed velocity scoring.

    Preserved verbatim from the v2 producer (detect_striker_signals). The v2
    producer returned ALL qualifying signals sorted by score, descending; the
    runtime decides which (if any) to execute. `prev_scans` is the bounded history
    list (oldest..newest) the caller pulled from ctx.state — the LAST element is the
    actual previous scan (the v4.0.1 detect-then-append contract guarantees this).
    """
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]

    prev_top50_tokens = set()
    for m in latest_prev.get("markets", []):
        prev_top50_tokens.add((m.get("token", ""), m.get("dex", "")))

    signals = []

    for market in current_scan.get("markets", []):
        token = market.get("token", "")
        dex = market.get("dex", "")
        current_rank = market.get("rank", 999)
        direction = market.get("direction", "").upper()
        current_contrib = market.get("contribution", 0)
        traders = market.get("traders", 0)

        # v2-quirk: the producer SKIPS assets already in the top 10 (current_rank <= 10)
        # and only fires on ones still CLIMBING (rank 11+) despite the thesis text saying
        # "into the top 10". Preserved verbatim — do NOT redesign as part of the port.
        if current_rank <= 10:
            continue

        price_chg_4h = market.get("price_chg_4h", 0)
        if not check_4h_alignment(direction, price_chg_4h):
            continue

        if XYZ_BANNED and dex == "xyz":
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        if not prev_market:
            continue

        rank_jump = prev_market.get("rank", 999) - current_rank
        prev_rank = prev_market.get("rank", 999)

        is_first_jump = False
        is_immediate = False
        reasons = []

        if rank_jump >= STRIKER_MIN_RANK_JUMP and prev_rank >= STRIKER_MIN_PREV_RANK:
            is_immediate = True
            reasons.append(f"jumped {rank_jump} spots from rank #{prev_rank}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_rank >= 30:
                is_first_jump = True
                reasons.append(f"first jump from #{prev_rank} to #{current_rank}")

        if not is_first_jump and not is_immediate:
            continue

        if rank_jump < STRIKER_MIN_RANK_JUMP:
            continue

        # Contribution explosion
        if prev_market.get("contribution", 0) > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                reasons.append(f"smart-money share exploded {contrib_ratio:.1f}x")

        # Contribution velocity from history
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m.get("contribution", 0))
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i] for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        # ── Scoring ── (verbatim from v2)
        score = 0

        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2

        if abs(contrib_velocity) > 10:
            score += 2
            reasons.append(f"smart-money share moving fast ({abs(contrib_velocity):.1f})")

        if prev_rank >= 40:
            score += 1
            reasons.append("climbed from rank #40 or deeper")

        # 4H strength bonus
        if abs(price_chg_4h) > 3:
            score += 1
            reasons.append(f"price moved {price_chg_4h:+.1f}% in 4h")

        # Trader count (SM depth)
        if traders >= 30:
            score += 1
            reasons.append(f"{traders} smart-money traders on board")

        # Hyperfeed 15m/1h contribution velocity + freshness gate
        contrib_15m = market.get("contrib_15m", 0)
        contrib_1h = market.get("contrib_1h", 0)

        # Striker-class hard gate: SM must be actively building right now
        if contrib_15m <= 0:
            continue  # Signal not fresh, skip

        if contrib_15m > 2.0:
            score += 3
            reasons.append(f"smart-money share spiked +{contrib_15m:.2f} in 15m")
        elif contrib_15m > 0.5:
            score += 2
            reasons.append(f"smart-money share up +{contrib_15m:.2f} in 15m")
        elif contrib_15m > 0.1:
            score += 1
            reasons.append(f"smart-money share building +{contrib_15m:.2f} in 15m")

        if contrib_1h > 1.0:
            score += 1
            reasons.append(f"smart-money share up +{contrib_1h:.2f} in 1h")

        # Acceleration pattern
        if contrib_15m > 0 and contrib_1h > 0 and contrib_15m > contrib_1h:
            score += 1
            reasons.append(f"accelerating: 15m {contrib_15m:.2f} beats 1h {contrib_1h:.2f}")

        if score < MIN_SCORE or len(reasons) < STRIKER_MIN_REASONS:
            continue

        # v3.4: absolute liquidity floor (replaces silent-None vol_ratio gate)
        day_notional = safe_float(
            market.get("day_notional_volume",
                market.get("dayNotionalVolume",
                    market.get("volume_24h_usd", 0)))
        )
        if day_notional > 0 and day_notional < MIN_DAY_NOTIONAL_VOLUME_USD:
            continue  # liquidity too thin

        # Soft vol_ratio bonus — only add reason if data genuinely available
        vol_ratio = safe_float(market.get("vol_ratio", market.get("volume_ratio", 0)))
        if vol_ratio == 0:
            volume = safe_float(market.get("volume", 0))
            avg_volume = safe_float(market.get("avg_volume", market.get("avgVolume", 0)))
            if avg_volume > 0:
                vol_ratio = volume / avg_volume
        if vol_ratio >= STRIKER_MIN_VOLUME_RATIO:
            reasons.append(f"volume running {vol_ratio:.1f}x its average")
        elif day_notional > 0:
            reasons.append(f"${day_notional/1e6:.1f}M traded in 24h")

        signals.append({
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "contribVelocity": round(contrib_velocity, 4),
            "volRatio": round(vol_ratio, 2),
            "contribution": round(current_contrib * 100, 3),
            "traders": traders,
            "priceChg4h": price_chg_4h,
            "contrib15m": contrib_15m,
            "contrib1h": contrib_1h,
            "dayNotionalUsd": day_notional,
        })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals
