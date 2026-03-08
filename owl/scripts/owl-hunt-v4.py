#!/usr/bin/env python3
"""OWL Hunt v4 — Point-scored entries, conviction-scaled DSL, ALO-first.

Key changes from v3:
  1. Point-based scoring (min 8 points to enter, was binary 2-signal check)
  2. Multi-timeframe: 4h candle alignment + RSI divergence
  3. Time-of-day modifier (+1 for 04-14 UTC, -2 for 18-02 UTC)
  4. Conviction-scaled Phase 1 timeouts (score determines room)
  5. Higher conviction bar: crowding 0.65, persistence 8, funding 25% ann
  6. Tighter absolute floor: 0.06/leverage (was 0.10)
  7. 9-tier DSL (from FOX) with 1.2% phase 2 retrace
  8. ALO entries (FEE_OPTIMIZED_LIMIT + ensureExecutionAsTaker)
  9. Green-in-10 rule enabled
  10. 4-hour loss cooldown per asset (was 2)
  11. Stagnation TP: hw stale 60min at ROE ≥ 8%

Cron interval: 15 min, main session.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owl_config as ocfg

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")
WALLET, STRATEGY_ID = ocfg.get_wallet_and_strategy()
STATE_DIR = ocfg.get_state_dir()
CONFIG_PATH = STATE_DIR / "owl-config.json"
STATE_PATH = STATE_DIR / "owl-state.json"
HUNT_STATE_PATH = STATE_DIR / "hunt-state.json"
OWL_HUNT_STATE_PATH = STATE_DIR / "owl-hunt-state.json"

# ═══ v4 THRESHOLDS ═══
MIN_CROWDING_SCORE = 0.65       # Was 0.50 — need strong crowding
MIN_PERSISTENCE = 8             # ~2hrs at 15min intervals (was 5)
MIN_VOLUME_24H = 500_000        # Min 24h volume
MIN_FUNDING_ANN = 25            # 25% annualized (was 15)
MAX_SLOTS = 3                   # Max concurrent positions
MIN_ENTRY_SCORE = 8             # Point threshold to enter (NEW)
LOSS_COOLDOWN_HOURS = 4         # Hours to skip after a loss (was 2)

# Structural signals — need ≥2 of these (was 1)
STRUCTURAL_SIGNALS = {"funding_declining", "book_shifting", "volume_spike", "oi_declining"}

# ═══ SCORING TABLE ═══
# Each factor adds points. Min 8 to enter.
SCORE_CROWDING_BASE = 3         # Crowding ≥ 0.65
SCORE_CROWDING_STRONG = 2       # Crowding ≥ 0.75 (bonus)
SCORE_STRUCTURAL = 2            # Per structural signal
SCORE_PRICE_SIGNAL = 1          # Per price-only signal
SCORE_4H_DIVERGENCE = 3         # 4h RSI divergence
SCORE_OI_DROP_LARGE = 3         # OI declining > 5%
SCORE_FUNDING_DECLINING = 2     # Funding declining
SCORE_VOLUME_SPIKE = 1          # Volume spike
SCORE_BOOK_SHIFTING = 1         # Book imbalance shifting
SCORE_TIME_BONUS = 1            # 04-14 UTC
SCORE_TIME_PENALTY = -2         # 18-02 UTC

# ═══ CONVICTION-SCALED DSL ═══
CONVICTION_TIERS = [
    # (min_score, abs_floor_base, hard_timeout, weak_peak, dead_weight)
    (8,  0.06, 45, 20, 12),   # Base conviction
    (10, 0.06, 60, 25, 15),   # High conviction
    (12, 0.08, 75, 30, 20),   # Ultra conviction — slightly wider floor
]

# ═══ 9-TIER DSL (from FOX) ═══
DSL_TIERS = [
    {"triggerPct": 5,   "lockPct": 2},
    {"triggerPct": 10,  "lockPct": 5},
    {"triggerPct": 20,  "lockPct": 14},
    {"triggerPct": 30,  "lockPct": 24},
    {"triggerPct": 40,  "lockPct": 34},
    {"triggerPct": 50,  "lockPct": 44},
    {"triggerPct": 65,  "lockPct": 56},
    {"triggerPct": 80,  "lockPct": 72},
    {"triggerPct": 100, "lockPct": 90},
]


def mcporter_call(tool, args=None, timeout=15):
    if args:
        cmd = ["mcporter", "call", f"senpi.{tool}", "--args", json.dumps(args)]
    else:
        cmd = ["mcporter", "call", f"senpi.{tool}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        raw = json.loads(r.stdout)
        # Unwrap mcporter envelope
        content = raw.get("content")
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", "")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
        return raw
    except Exception:
        return None


def load_json(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    ocfg.atomic_write(path, data)


def output(data):
    print(json.dumps(data))
    sys.stdout.flush()


def extract_closes(candles):
    return [float(c.get("close") or c.get("c") or 0) for c in candles if (c.get("close") or c.get("c"))]


def extract_volumes(candles):
    return [float(c.get("volume") or c.get("v") or c.get("vlm") or 0) for c in candles if (c.get("volume") or c.get("v") or c.get("vlm"))]


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    g = gains[-period:]
    l = losses[-period:]
    ag = sum(g) / period
    al = sum(l) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def get_time_score():
    """Time-of-day scoring: +1 for 04-14 UTC, -2 for 18-02 UTC."""
    hour = datetime.now(timezone.utc).hour
    if 4 <= hour < 14:
        return SCORE_TIME_BONUS, "time_bonus_04_14_utc"
    elif hour >= 18 or hour < 2:
        return SCORE_TIME_PENALTY, "time_penalty_18_02_utc"
    return 0, None


def get_loss_cooldown_assets(owl_hunt_state):
    """Return set of assets still in cooldown from recent losses."""
    recent_losses = owl_hunt_state.get("recentLosses", {})
    now = datetime.now(timezone.utc)
    cooldown_assets = set()
    for asset, ts in recent_losses.items():
        try:
            loss_time = datetime.fromisoformat(ts)
            if now - loss_time < timedelta(hours=LOSS_COOLDOWN_HOURS):
                cooldown_assets.add(asset)
        except (ValueError, TypeError):
            pass
    return cooldown_assets


def check_4h_divergence(candles_4h, crowded_dir):
    """Check for 4h RSI divergence (bearish div if crowd LONG, bullish div if crowd SHORT).
    Returns (has_divergence, description).
    """
    closes = extract_closes(candles_4h)
    if len(closes) < 20:
        return False, None

    rsi_vals = []
    for i in range(15, len(closes)):
        rsi = calc_rsi(closes[:i+1], 14)
        if rsi is not None:
            rsi_vals.append((closes[i], rsi))

    if len(rsi_vals) < 3:
        return False, None

    # Check last 3 data points for divergence
    if crowded_dir == "LONG":
        # Bearish divergence: price making higher highs, RSI making lower highs
        if (rsi_vals[-1][0] > rsi_vals[-3][0] and rsi_vals[-1][1] < rsi_vals[-3][1]):
            return True, f"bearish_div_price_up_rsi_down"
        if (rsi_vals[-1][0] > rsi_vals[-2][0] and rsi_vals[-1][1] < rsi_vals[-2][1]):
            return True, f"bearish_div_price_up_rsi_down"
    elif crowded_dir == "SHORT":
        # Bullish divergence: price making lower lows, RSI making higher lows
        if (rsi_vals[-1][0] < rsi_vals[-3][0] and rsi_vals[-1][1] > rsi_vals[-3][1]):
            return True, f"bullish_div_price_down_rsi_up"
        if (rsi_vals[-1][0] < rsi_vals[-2][0] and rsi_vals[-1][1] > rsi_vals[-2][1]):
            return True, f"bullish_div_price_down_rsi_up"

    return False, None


def check_4h_alignment(candles_4h, contrarian_dir):
    """Check if 4h candle supports the contrarian direction.
    Returns True if the 4h trend is starting to turn our way.
    """
    closes = extract_closes(candles_4h)
    if len(closes) < 3:
        return False

    sma = calc_sma(closes, 10) if len(closes) >= 10 else calc_sma(closes, len(closes))
    if sma is None:
        return False

    current = closes[-1]
    if contrarian_dir == "LONG" and current > sma:
        return True
    if contrarian_dir == "SHORT" and current < sma:
        return True

    # Also check: last candle moved in contrarian direction
    if contrarian_dir == "LONG" and closes[-1] > closes[-2]:
        return True
    if contrarian_dir == "SHORT" and closes[-1] < closes[-2]:
        return True

    return False


def get_conviction_tier(score):
    """Get DSL params based on entry score. Returns (abs_floor_base, hard_timeout, weak_peak, dead_weight)."""
    result = CONVICTION_TIERS[0]  # default
    for tier in CONVICTION_TIERS:
        if score >= tier[0]:
            result = tier
    return result[1], result[2], result[3], result[4]


def run():
    config = load_json(CONFIG_PATH)
    state = load_json(STATE_PATH)
    hunt = load_json(HUNT_STATE_PATH)
    owl_hunt_state = load_json(OWL_HUNT_STATE_PATH)

    if not hunt:
        hunt = {"version": 4, "persistence": {}, "lastScores": {}, "updatedAt": None}

    active = state.get("activePositions", {})

    if len(active) >= MAX_SLOTS:
        output({"success": True, "heartbeat": "HEARTBEAT_OK", "reason": "slots_full"})
        return

    # Load loss cooldown assets
    cooldown_assets = get_loss_cooldown_assets(owl_hunt_state)

    # 1. Fetch instruments
    instruments = mcporter_call("market_list_instruments")
    if not instruments:
        output({"success": False, "error": "No instruments"})
        return

    data = instruments.get("data", instruments) if isinstance(instruments, dict) else instruments
    asset_list = data.get("instruments", data.get("assets", [])) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    # 2. Score all assets for crowding (prescreener — cheap, one API call)
    candidates = []
    for a in asset_list:
        name = a.get("name", a.get("coin", ""))
        if not name:
            continue
        ctx = a.get("context", {})
        funding = float(ctx.get("funding", 0) or 0)
        oi = float(ctx.get("openInterest", 0) or 0)
        volume = float(ctx.get("dayNtlVlm", 0) or 0)

        if oi == 0 or volume < MIN_VOLUME_24H:
            continue

        funding_ann = abs(funding) * 3 * 365 * 100

        if funding_ann < MIN_FUNDING_ANN:
            continue

        crowded_dir = "LONG" if funding > 0 else "SHORT"
        contrarian_dir = "SHORT" if crowded_dir == "LONG" else "LONG"

        # Crowding score from funding intensity + volume
        score = min(1.0, funding_ann / 60) * 0.4
        if volume > 10_000_000:
            score += 0.15
        elif volume > 1_000_000:
            score += 0.10
        elif volume > 500_000:
            score += 0.05

        # OI weight
        score += min(0.20, oi / 500_000_000 * 0.20)

        # Max leverage check
        max_lev = a.get("max_leverage", a.get("maxLeverage", 20))
        if max_lev and int(max_lev) < 5:
            continue

        candidates.append({
            "asset": name,
            "crowdingScore": round(score, 4),
            "crowdedDir": crowded_dir,
            "contrarianDir": contrarian_dir,
            "fundingAnn": round(funding_ann, 1),
            "funding": funding,
            "oi": oi,
            "volume": volume,
            "maxLev": int(max_lev) if max_lev else 20,
        })

    candidates.sort(key=lambda x: x["crowdingScore"], reverse=True)
    top = candidates[:10]

    # 3. Update persistence
    now_iso = datetime.now(timezone.utc).isoformat()
    for c in top:
        asset = c["asset"]
        if c["crowdingScore"] >= MIN_CROWDING_SCORE:
            hunt["persistence"][asset] = hunt["persistence"].get(asset, 0) + 1
        else:
            hunt["persistence"][asset] = max(0, hunt["persistence"].get(asset, 0) - 1)

    # Decay assets not in top anymore
    for asset in list(hunt["persistence"].keys()):
        if asset not in [c["asset"] for c in top]:
            hunt["persistence"][asset] = max(0, hunt["persistence"].get(asset, 0) - 1)

    hunt["lastScores"] = {c["asset"]: c["crowdingScore"] for c in top}
    hunt["updatedAt"] = now_iso

    # 4. Filter to entry candidates (basic thresholds)
    entry_candidates = []
    for c in top:
        asset = c["asset"]
        persistence = hunt["persistence"].get(asset, 0)

        if c["crowdingScore"] < MIN_CROWDING_SCORE:
            continue
        if asset in active:
            continue
        if persistence < MIN_PERSISTENCE:
            continue
        if asset in cooldown_assets:
            continue

        # BTC correlation isolation
        btc_candidate = next((x for x in top if x["asset"] == "BTC"), None)
        correlated = ["SOL", "ETH", "DOGE", "AVAX", "LINK", "ADA", "DOT", "NEAR", "ATOM", "SEI", "SUI", "PEPE", "WIF", "RENDER"]
        clean_name = asset.replace("xyz:", "")
        if btc_candidate and clean_name in correlated:
            if btc_candidate["crowdedDir"] == c["crowdedDir"] and btc_candidate["crowdingScore"] >= MIN_CROWDING_SCORE:
                continue

        entry_candidates.append(c)

    # 5. Deep scan + SCORING for top candidates
    signals = []
    for c in entry_candidates[:3]:  # Max 3 deep scans per cycle
        asset = c["asset"]
        dex = "xyz" if asset.startswith("xyz:") else None

        # Fetch multi-timeframe data
        detail = mcporter_call("market_get_asset_data", {
            "asset": asset,
            "candle_intervals": ["1h", "4h"],
            "include_order_book": True,
            "include_funding": True,
            **({"dex": dex} if dex else {}),
        }, timeout=20)

        if not detail or not isinstance(detail, dict):
            continue

        asset_data = detail.get("data", detail)

        # ═══ POINT SCORING ═══
        entry_score = 0
        score_reasons = []

        # Base crowding points
        entry_score += SCORE_CROWDING_BASE
        score_reasons.append(f"crowding_{c['crowdingScore']:.2f}:+{SCORE_CROWDING_BASE}")

        if c["crowdingScore"] >= 0.75:
            entry_score += SCORE_CROWDING_STRONG
            score_reasons.append(f"strong_crowding:+{SCORE_CROWDING_STRONG}")

        # ── Extract candle data ──
        candles_1h = asset_data.get("candles", {}).get("1h", [])
        candles_4h = asset_data.get("candles", {}).get("4h", [])
        closes_1h = extract_closes(candles_1h)
        closes_4h = extract_closes(candles_4h)

        # ── Exhaustion signals (structural vs price) ──
        structural_count = 0

        # Signal: OI declining
        if candles_1h:
            ois = []
            for candle in candles_1h:
                oi_val = candle.get("openInterest") or candle.get("oi")
                if oi_val:
                    ois.append(float(oi_val))
            if len(ois) >= 4:
                peak = max(ois[-12:]) if len(ois) >= 12 else max(ois)
                current_oi = ois[-1]
                if peak > 0:
                    drop = (peak - current_oi) / peak
                    if drop >= 0.05:
                        entry_score += SCORE_OI_DROP_LARGE
                        score_reasons.append(f"oi_drop_{drop*100:.1f}%:+{SCORE_OI_DROP_LARGE}")
                        structural_count += 1
                    elif drop >= 0.03:
                        entry_score += 1
                        score_reasons.append(f"oi_drop_small_{drop*100:.1f}%:+1")
                        structural_count += 1

        # Signal: Funding declining
        funding_hist = asset_data.get("fundingHistory", asset_data.get("funding", []))
        if len(funding_hist) >= 8:
            recent = [abs(float(f.get("fundingRate", f.get("rate", 0)))) for f in funding_hist[-4:]]
            older = [abs(float(f.get("fundingRate", f.get("rate", 0)))) for f in funding_hist[-8:-4]]
            avg_recent = sum(recent) / len(recent) if recent else 0
            avg_older = sum(older) / len(older) if older else 0
            if avg_older > 0 and avg_recent < avg_older * 0.85:
                entry_score += SCORE_FUNDING_DECLINING
                score_reasons.append(f"funding_declining:+{SCORE_FUNDING_DECLINING}")
                structural_count += 1

        # Signal: Volume spike
        if len(candles_1h) >= 6:
            vols = extract_volumes(candles_1h)
            if len(vols) >= 6:
                recent_vol = sum(vols[-2:]) / 2
                trailing_vol = sum(vols[-6:-2]) / 4
                if trailing_vol > 0:
                    ratio = recent_vol / trailing_vol
                    if ratio >= 1.5:
                        entry_score += SCORE_VOLUME_SPIKE
                        score_reasons.append(f"volume_spike_{ratio:.1f}x:+{SCORE_VOLUME_SPIKE}")
                        structural_count += 1

        # Signal: Order book shifting
        book = asset_data.get("orderBook", asset_data.get("book", {}))
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            bid_depth = sum(float(b.get("size", b.get("sz", 0))) for b in bids[:10])
            ask_depth = sum(float(a.get("size", a.get("sz", 0))) for a in asks[:10])
            if ask_depth > 0 and bid_depth > 0:
                ratio = bid_depth / ask_depth
                shifted = False
                if c["crowdedDir"] == "LONG" and ratio < 0.7:
                    shifted = True
                elif c["crowdedDir"] == "SHORT" and ratio > 1.4:
                    shifted = True
                if shifted:
                    entry_score += SCORE_BOOK_SHIFTING
                    score_reasons.append(f"book_shifting:+{SCORE_BOOK_SHIFTING}")
                    structural_count += 1

        # Signal: Price reversal (price-only, +1)
        if len(closes_1h) >= 3:
            if c["crowdedDir"] == "LONG":
                recent_high = max(closes_1h[-3:])
                if recent_high > 0:
                    drop_pct = (recent_high - closes_1h[-1]) / recent_high
                    if drop_pct >= 0.005:
                        entry_score += SCORE_PRICE_SIGNAL
                        score_reasons.append(f"price_reversal_{drop_pct*100:.2f}%:+{SCORE_PRICE_SIGNAL}")
            elif c["crowdedDir"] == "SHORT":
                recent_low = min(closes_1h[-3:])
                if recent_low > 0:
                    rise_pct = (closes_1h[-1] - recent_low) / recent_low
                    if rise_pct >= 0.005:
                        entry_score += SCORE_PRICE_SIGNAL
                        score_reasons.append(f"price_reversal_{rise_pct*100:.2f}%:+{SCORE_PRICE_SIGNAL}")

        # Signal: SMA cross (price-only, +1)
        if len(closes_1h) >= 20:
            sma20 = sum(closes_1h[-20:]) / 20
            if sma20 > 0:
                if c["crowdedDir"] == "LONG":
                    dist = (sma20 - closes_1h[-1]) / sma20
                    if dist >= 0.003:
                        entry_score += SCORE_PRICE_SIGNAL
                        score_reasons.append(f"below_sma20_{dist*100:.2f}%:+{SCORE_PRICE_SIGNAL}")
                elif c["crowdedDir"] == "SHORT":
                    dist = (closes_1h[-1] - sma20) / sma20
                    if dist >= 0.003:
                        entry_score += SCORE_PRICE_SIGNAL
                        score_reasons.append(f"above_sma20_{dist*100:.2f}%:+{SCORE_PRICE_SIGNAL}")

        # ── 4h TIMEFRAME SIGNALS (v4 new) ──

        # 4h RSI divergence (+3, strong signal)
        has_div, div_desc = check_4h_divergence(candles_4h, c["crowdedDir"])
        if has_div:
            entry_score += SCORE_4H_DIVERGENCE
            score_reasons.append(f"4h_{div_desc}:+{SCORE_4H_DIVERGENCE}")

        # 4h alignment check (not scored, but required as a gate)
        aligned_4h = check_4h_alignment(candles_4h, c["contrarianDir"])

        # ── TIME-OF-DAY MODIFIER ──
        time_pts, time_reason = get_time_score()
        if time_pts != 0:
            entry_score += time_pts
            score_reasons.append(f"{time_reason}:{time_pts:+d}")

        # ═══ ENTRY DECISION ═══

        # Gate 1: Minimum score
        if entry_score < MIN_ENTRY_SCORE:
            continue

        # Gate 2: Need ≥2 structural signals
        if structural_count < 2:
            continue

        # Gate 3: 4h alignment (soft — skip only if score < 10)
        if not aligned_4h and entry_score < 10:
            continue

        # ═══ BUILD ENTRY SIGNAL ═══
        current_price = closes_1h[-1] if closes_1h else None
        if not current_price:
            continue

        leverage = min(8, c["maxLev"])
        direction = c["contrarianDir"]

        # Conviction-scaled DSL params
        floor_base, hard_timeout, weak_peak, dead_weight = get_conviction_tier(entry_score)

        # Position sizing (score-weighted)
        slot_num = len(active) + len(signals) + 1
        base_margins = {1: 375, 2: 320, 3: 268}
        base_margin = base_margins.get(slot_num, 200)

        if c["crowdingScore"] >= 0.75:
            size_mult = 1.2
        elif c["crowdingScore"] >= 0.65:
            size_mult = 1.0
        else:
            size_mult = 0.8
        margin = round(base_margin * size_mult, 2)

        # Calculate DSL floors
        if direction == "LONG":
            abs_floor = current_price * (1 - floor_base / leverage)
        else:
            abs_floor = current_price * (1 + floor_base / leverage)

        signals.append({
            "action": "ENTER",
            "asset": asset,
            "direction": direction,
            "leverage": leverage,
            "entryPrice": current_price,
            "marginAmount": margin,
            "entryScore": entry_score,
            "scoreReasons": score_reasons,
            "crowdingScore": c["crowdingScore"],
            "structuralSignals": structural_count,
            "fundingAnn": c["fundingAnn"],
            "crowdedDirection": c["crowdedDir"],
            "fundingRate": c["funding"],
            "persistence": hunt["persistence"].get(asset, 0),
            "aligned4h": aligned_4h,
            "sizeMultiplier": size_mult,
            # ALO entry
            "orderType": "FEE_OPTIMIZED_LIMIT",
            "ensureExecutionAsTaker": True,
            # Conviction-scaled DSL
            "dsl": {
                "absoluteFloor": round(abs_floor, 6),
                "floorBase": floor_base,
                "hardTimeoutMin": hard_timeout,
                "weakPeakCutMin": weak_peak,
                "deadWeightCutMin": dead_weight,
                "greenIn10TightenPct": 50,
                "phase2RetraceThreshold": 0.012,
                "phase2ConsecutiveBreaches": 2,
                "tiers": DSL_TIERS,
                "stagnationTpEnabled": True,
                "stagnationTpRoeMin": 8,
                "stagnationTpStaleMin": 60,
            },
            "wallet": WALLET,
            "strategyId": STRATEGY_ID,
        })

    save_json(HUNT_STATE_PATH, hunt)

    if signals:
        output({
            "success": True,
            "signals": signals,
            "signalCount": len(signals),
            "topScores": [{
                "asset": c["asset"],
                "crowdingScore": c["crowdingScore"],
                "dir": c["crowdedDir"],
                "persist": hunt["persistence"].get(c["asset"], 0),
            } for c in top[:5]],
        })
    else:
        output({
            "success": True,
            "heartbeat": "HEARTBEAT_OK",
            "topScores": [{
                "asset": c["asset"],
                "crowdingScore": c["crowdingScore"],
                "dir": c["crowdedDir"],
                "persist": hunt["persistence"].get(c["asset"], 0),
            } for c in top[:5]],
        })


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        output({"success": False, "error": str(e)})
