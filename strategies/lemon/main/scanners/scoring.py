"""LEMON — pure thesis math (no I/O, no MCP, no clock-dependent reads).

A faithful Runtime 3.0 port of the v2 Lemon producer's `evaluate_fade` +
`get_leverage_for_score` (lemon-producer.py v2.0.1). The scoring, gates, leverage
tiers, and direction-inversion are reproduced VERBATIM so a fidelity harness can
diff this against the v2 producer on the same market snapshot. Behaviour-preserving
quirks from v2 are kept and flagged `# v2-quirk`.

LEMON is a DEGEN FADER: it counter-trades a crowded, exhausting smart-money
consensus. The emitted `direction` is the OPPOSITE of the SM consensus by design
(`fade_direction = SHORT if SM is LONG else LONG`).

Pure module: `evaluate_fade` scores ONE normalized market dict. The two
clock/MCP-dependent contributors the v2 producer computed INSIDE evaluate_fade are
hoisted to the caller (scan.py) and passed in to keep this module pure:
  - `funding`  : asset funding rate (v2 fetched market_get_asset_data inline) — None
                 if the read failed (the +1 FUNDING_PAYS_FADE bonus then can't fire,
                 exactly matching v2's try/except-pass degrade).
  - `us_session`: whether the current UTC hour is 13..21 (v2 used datetime.utcnow()).
The arithmetic, gate order, point weights, and thresholds are unchanged."""


# ── v2 producer constants (verbatim from lemon-producer.py v2.0.1) ──
MIN_SCORE = 9
MACRO_GATE_BTC_4H_PCT = 3.0
PER_ASSET_GATE_4H_PCT = 5.0          # 2026-05-14 HYPE +12.8% post-mortem gate
MIN_SM_PCT = 3.0
MIN_SM_TRADERS = 20

# v2 LEVERAGE_TIERS (verbatim) — desc by min_score; DEFAULT_LEVERAGE=5 fallback,
# MAX_LEVERAGE=10 hard clamp.
DEFAULT_LEVERAGE_TIERS = [[13, 10], [11, 7], [9, 5]]
DEFAULT_LEVERAGE = 5
MAX_LEVERAGE = 10


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def get_leverage_for_score(score, tiers=None, max_leverage=MAX_LEVERAGE):
    """v2 get_leverage_for_score — first tier whose min_score the candidate clears,
    clamped to max_leverage. Falls back to DEFAULT_LEVERAGE (5) when no tier matches
    (verbatim; a score below 9 can't reach here because MIN_SCORE gates candidates)."""
    if tiers is None:
        tiers = DEFAULT_LEVERAGE_TIERS
    for t in tiers:
        if score >= t[0]:
            return min(int(t[1]), int(max_leverage))
    return DEFAULT_LEVERAGE


def evaluate_fade(sm, btc_4h, funding, us_session, thresholds=None):
    """Score a fade for ONE normalized market dict `sm`. Returns a thesis dict (with
    `score` and the FADE `direction`) or None when a hard gate rejects or the final
    score < MIN_SCORE. Ported VERBATIM from v2 `evaluate_fade`.

    `sm` keys (see scan._normalize_market): asset (canonical, may be 'xyz:NAME'),
      is_xyz, direction (SM consensus), pct, traders, price_chg_4h, price_chg_1h,
      contrib_15m, contrib_1h, contrib_4h.
    `btc_4h`     : BTC 4h price change % (for the crypto-only MACRO_TREND_GATE).
    `funding`    : asset funding rate, or None if unavailable (FUNDING bonus skipped).
    `us_session` : bool — current UTC hour in [13, 21] (US_SESSION +1 bonus)."""
    th = thresholds or {}
    min_score = int(th.get("minScore", MIN_SCORE))
    min_sm_pct = safe_float(th.get("minSmPct", MIN_SM_PCT))
    min_sm_traders = int(th.get("minSmTraders", MIN_SM_TRADERS))
    macro_gate = safe_float(th.get("macroGateBtc4hPct", MACRO_GATE_BTC_4H_PCT))
    per_asset_gate = safe_float(th.get("perAssetGate4hPct", PER_ASSET_GATE_4H_PCT))

    sm_direction = sm["direction"]
    if sm_direction not in ("LONG", "SHORT"):
        return None
    fade_direction = "SHORT" if sm_direction == "LONG" else "LONG"

    pct = sm["pct"]
    traders = sm["traders"]
    p4h = sm["price_chg_4h"]
    p1h = sm["price_chg_1h"]
    c15m = sm["contrib_15m"]
    c1h = sm["contrib_1h"]
    c4h = sm["contrib_4h"]

    # ── HARD GATES (verbatim) ──
    if pct < min_sm_pct or traders < min_sm_traders:
        return None
    # 15m must be fading (move exhausting)
    if c15m > 0.1:
        return None

    # MACRO_TREND_GATE (crypto only; XYZ bypasses): don't fade during a BTC trend.
    if not sm.get("is_xyz", False):
        if abs(btc_4h) > macro_gate:
            return None

    # PER_ASSET_TREND_GATE (crypto + XYZ): 2026-05-14 HYPE +12.8% post-mortem. When
    # the asset itself is in a strong directional run >= 5% over 4h AND the crowd is
    # correctly riding it, the fade thesis fails — that's real trend, not exhaustion.
    if sm_direction == "LONG" and p4h > per_asset_gate:
        return None
    if sm_direction == "SHORT" and p4h < -per_asset_gate:
        return None

    score = 0
    reasons = []

    # SM concentration tiers (+4 / +3 / +2 / +1)
    if pct >= 20:
        score += 4
        reasons.append(f"DEGEN_PILE {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 12:
        score += 3
        reasons.append(f"HEAVY_CROWD {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 7:
        score += 2
        reasons.append(f"CROWDED {pct:.1f}% ({traders}t) {sm_direction}")
    elif pct >= 3:
        score += 1
        reasons.append(f"LEANING {pct:.1f}% ({traders}t) {sm_direction}")

    # 15m velocity exhaustion (+3 / +2 / +1)
    if c15m < -2.0:
        score += 3
        reasons.append(f"15M_COLLAPSING {c15m:.2f}")
    elif c15m < -0.5:
        score += 2
        reasons.append(f"15M_FADING {c15m:.2f}")
    elif c15m < -0.1:
        score += 1
        reasons.append(f"15M_COOLING {c15m:.2f}")

    # 1h velocity fading (+1)
    if c1h < -0.5:
        score += 1
        reasons.append(f"1H_FADING {c1h:.2f}")

    # 4h overextension in SM direction (+2 / +1)
    if sm_direction == "LONG" and p4h > 3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "LONG" and p4h > 1.5:
        score += 1
        reasons.append(f"EXTENDED_LONG +{p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -3.0:
        score += 2
        reasons.append(f"OVEREXTENDED_SHORT {p4h:.1f}%")
    elif sm_direction == "SHORT" and p4h < -1.5:
        score += 1
        reasons.append(f"EXTENDED_SHORT {p4h:.1f}%")

    # 1h reversing toward the fade direction (+1)
    if fade_direction == "LONG" and p1h > 0.1:
        score += 1
        reasons.append(f"1H_REVERSING +{p1h:.2f}%")
    elif fade_direction == "SHORT" and p1h < -0.1:
        score += 1
        reasons.append(f"1H_REVERSING {p1h:.2f}%")

    # 4h SM contribution weakening (+1)
    if c4h < -1.0:
        score += 1
        reasons.append(f"4H_SM_WEAKENING {c4h:.1f}")

    # Funding alignment (+1) — funding pays the fade side. None => read failed, skip
    # (v2 wrapped this in try/except: pass, so an unavailable funding never fires it).
    if funding is not None:
        if fade_direction == "SHORT" and funding > 0.0002:
            score += 1
            reasons.append(f"FUNDING_PAYS_FADE +{funding * 100:.4f}%")
        elif fade_direction == "LONG" and funding < -0.0002:
            score += 1
            reasons.append(f"FUNDING_PAYS_FADE {funding * 100:.4f}%")

    # US session (+1) — 13..21 UTC (caller computes from the clock)
    if us_session:
        score += 1
        reasons.append("US_SESSION")

    if score < min_score:
        return None

    return {
        "asset": sm["asset"],
        "is_xyz": sm.get("is_xyz", False),
        "direction": fade_direction,
        "score": score,
        "reasons": reasons,
        "smDirection": sm_direction,
        "smPct": pct,
        "smTraders": traders,
        "priceChg4h": p4h,
        "contrib15m": c15m,
    }
