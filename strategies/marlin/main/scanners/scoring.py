"""MARLIN — pure thesis math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Marlin producer's microstructure helpers +
`build_thesis` order-book-imbalance scoring (SKILL.md v1.0.0). The math/indexing is
reproduced VERBATIM so a fidelity harness can diff this against the v2 producer on the
same market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`; fix them only as a separate, labelled change AFTER the port is validated.

Multi-asset, single-pass, unit-testable on plain candle lists + a raw asset_data dict.
`sm` (smart-money lean) is fetched by the caller (leaderboard_get_markets) and passed
in, so this module stays pure.

The thesis is GATED (not a pure scorer): three hard gates must all pass —
  GATE 1: order-book imbalance picks the side (bid-heavy -> LONG, ask-heavy -> SHORT),
  GATE 2: 15m momentum confirms that side,
  GATE 3: smart-money direction agrees AND tilt >= smTiltMinPct.
If any gate fails, build_thesis returns None. minScore is applied by the CALLER
(scan.py), not here."""


# v2 producer defaults (marlin-producer.py / marlin-config.json)
DEFAULT_LEVELS_N = 10
DEFAULT_IMBALANCE_MIN = 1.5
DEFAULT_IMBALANCE_STRONG = 2.5
DEFAULT_MOM_MIN_PCT = 0.1
DEFAULT_SM_TILT_MIN = 55


def _f(v, d=0.0):
    try:
        return float(v if v is not None else d)
    except (TypeError, ValueError):
        return d


def _f2(c, primary, alt=None, default=0.0):
    """Dual-key float accessor (v2 _f): try `primary`, then `alt`, else default."""
    val = c.get(primary)
    if val is None and alt:
        val = c.get(alt)
    return _f(val, default)


# ── microstructure helpers (ported VERBATIM from v2 marlin-producer.py) ──

def book_imbalance(asset_data, levels_n=DEFAULT_LEVELS_N):
    """Return (imbalance_ratio, bid_depth, ask_depth) over the top `levels_n` book
    levels per side. ratio > 1 = bid-heavy (buy pressure); < 1 = ask-heavy (sell
    pressure). ratio is None when the book is unusable.

    market_get_asset_data order_book shape: {"levels": [bids, asks]} where each side
    is a list of {"px","sz","n"}; bids[0] is best bid, asks[0] best ask. Verbatim from
    v2 (reads asset_data["data"]["order_book"])."""
    ob = (asset_data.get("data", {}) or {}).get("order_book", {}) or {}
    levels = ob.get("levels") or []
    if not isinstance(levels, list) or len(levels) < 2:
        return None, 0.0, 0.0
    bids = levels[0][:levels_n] if isinstance(levels[0], list) else []
    asks = levels[1][:levels_n] if isinstance(levels[1], list) else []
    bid_depth = sum(_f2(l, "sz") for l in bids if isinstance(l, dict))
    ask_depth = sum(_f2(l, "sz") for l in asks if isinstance(l, dict))
    if bid_depth <= 0 and ask_depth <= 0:
        return None, bid_depth, ask_depth
    if ask_depth <= 0:
        return float("inf"), bid_depth, ask_depth
    return bid_depth / ask_depth, bid_depth, ask_depth


def imbalance_direction(ratio, imbalance_min):
    """Map an imbalance ratio to a trade side, or None if too balanced. Verbatim.
    bid-heavy (ratio >= min) -> LONG; ask-heavy (ratio <= 1/min) -> SHORT."""
    if ratio is None or imbalance_min <= 0:
        return None
    if ratio >= imbalance_min:
        return "LONG"
    if ratio <= (1.0 / imbalance_min):
        return "SHORT"
    return None


def price_move_pct(candles, n_bars=1):
    """% change over the last n_bars. Verbatim from v2 price_move_pct."""
    if len(candles) < n_bars + 1:
        return 0.0
    old = _f2(candles[-(n_bars + 1)], "close", "c")
    new = _f2(candles[-1], "close", "c")
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2.

    v2-quirk: requires len(candles) >= lookback (NOT lookback+2 like Bison), and the
    earlier-half window is vols[:half] over the SAME last-`lookback` slice as the
    recent-half. Reproduced exactly."""
    if len(candles) < lookback:
        return 0.0
    vols = [_f2(c, "volume", "v") for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ── the thesis (3 hard gates + composite score), ported VERBATIM ──

def build_thesis(coin, asset_data, candles_5m, candles_15m, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when:
      - insufficient candle history (len(15m) < 2 or len(5m) < 2), OR
      - GATE 1: order book does not resolve a side (too balanced / unusable), OR
      - GATE 2: 15m momentum does not confirm the imbalance side, OR
      - GATE 3: smart-money direction is missing / NEUTRAL / disagrees, OR tilt below floor.
    minScore is NOT applied here — the caller gates on thesis['score'].

    `asset_data` is the raw market_get_asset_data document (so book_imbalance can read
    asset_data["data"]["order_book"]). `sm` is the smart-money tuple (direction, tilt)
    or (None, 0) — the caller fetches it (fetch_sm_direction)."""
    levels_n = int(inputs.get("levelsN", DEFAULT_LEVELS_N))
    imb_min = float(inputs.get("imbalanceMin", DEFAULT_IMBALANCE_MIN))
    imb_strong = float(inputs.get("imbalanceStrong", DEFAULT_IMBALANCE_STRONG))
    mom_min = float(inputs.get("momMinPct", DEFAULT_MOM_MIN_PCT))
    sm_min = float(inputs.get("smTiltMinPct", DEFAULT_SM_TILT_MIN))

    if len(candles_15m) < 2 or len(candles_5m) < 2:
        return None

    # GATE 1 — order-book imbalance picks the side
    ratio, bid_depth, ask_depth = book_imbalance(asset_data, levels_n)
    direction = imbalance_direction(ratio, imb_min)
    if direction is None:
        return None

    # GATE 2 — short-term momentum must confirm the imbalance side
    mom_15m = price_move_pct(candles_15m, 1)
    if direction == "LONG" and mom_15m < mom_min:
        return None
    if direction == "SHORT" and mom_15m > -mom_min:
        return None

    # GATE 3 — Smart-Money agreement
    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    if sm_dir not in ("LONG", "SHORT") or sm_dir != direction:
        return None
    if sm_tilt < sm_min:
        return None

    mom_5m = price_move_pct(candles_5m, 1)
    vol_pct = volume_trend(candles_5m)
    # display ratio (inf -> large sentinel for telemetry; verbatim from v2)
    disp_ratio = 99.0 if ratio == float("inf") else round(ratio, 3)

    score = 0
    reasons = []

    # Imbalance magnitude (gate-confirmed) + strong bonus
    score += 2
    reasons.append(f"book_imbalance_{disp_ratio}x")
    strong = (ratio == float("inf")) or (direction == "LONG" and ratio >= imb_strong) or \
             (direction == "SHORT" and ratio <= (1.0 / imb_strong))
    if strong:
        score += 1
        reasons.append("imbalance_strong")

    # Momentum confirms (gate) + 5m alignment
    score += 2
    reasons.append(f"mom_15m_{mom_15m:+.2f}%")
    if (direction == "LONG" and mom_5m > 0) or (direction == "SHORT" and mom_5m < 0):
        score += 1
        reasons.append(f"mom_5m_aligned_{mom_5m:+.2f}%")

    # SM aligned (gate) + strong
    score += 2
    reasons.append(f"sm_aligned_{sm_tilt:.0f}%")
    if sm_tilt >= 70:
        score += 1
        reasons.append("sm_strongly_tilted")

    # Volume rising
    if vol_pct > 10:
        score += 1
        reasons.append(f"vol_rising_{vol_pct:+.0f}%")

    return {
        "coin": coin,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "imbalance_ratio": disp_ratio,
        "bid_depth": round(bid_depth, 2),
        "ask_depth": round(ask_depth, 2),
        "mom_15m_pct": round(mom_15m, 3),
        "mom_5m_pct": round(mom_5m, 3),
        "sm_direction": sm_dir,
        "sm_tilt_pct": _f(sm_tilt),
        "volume_trend_pct": round(vol_pct, 2),
    }
