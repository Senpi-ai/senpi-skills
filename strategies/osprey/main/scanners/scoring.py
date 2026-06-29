"""OSPREY — pure cross-venue lag math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Osprey producer's pure functions
(osprey-producer.py v1.0.1 + osprey_config.py). The math/indexing is reproduced
VERBATIM so a fidelity harness can diff this against the v2 producer on the same
market snapshot. Behaviour-preserving quirks from v2 are kept and flagged
`# v2-quirk`.

THESIS (cross-VENUE lag): when a crypto leader (BTC) makes a strong move,
crypto-correlated XYZ equities (COIN/MSTR) tend to follow but on a different
venue with a lag. Each tick measures, per proxy:

    expected_move = leader_move_pct * proxy_beta
    gap           = expected_move - proxy_actual_move

A gap in the LEADER'S direction (same sign) means the proxy still owes catch-up
-> trade the proxy in the leader's direction. An overshot proxy (gap flips
sign) is skipped — the catch-up is already done.

`sm` (smart-money lean on the proxy) is fetched by the caller and passed in, so
this module stays pure. The proxy candles (for the volume-trend bonus) are also
passed in by the caller as plain lists.
"""


# ── candle accessors (dual-shape: dict {close|c} OR list [t,o,h,l,c,v]) ──
# v2 read via _f(c, "close", "c") / _f(c, "volume", "v"); the list branch is
# defensive and never fires on dict candles, so it does not change v2 behaviour.

def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _close(c):
    if isinstance(c, dict):
        return _f(c.get("close", c.get("c", 0)))
    if isinstance(c, (list, tuple)) and len(c) >= 5:
        return _f(c[4])
    return 0.0


def _vol(c):
    if isinstance(c, dict):
        return _f(c.get("volume", c.get("v", c.get("vlm", 0))))
    if isinstance(c, (list, tuple)) and len(c) >= 6:
        return _f(c[5])
    return 0.0


# ═══════════════════════════════════════════════════════════════
# Pure cross-venue lag math (ported verbatim from v2 osprey-producer.py)
# ═══════════════════════════════════════════════════════════════

def move_pct(closes, lookback):
    """% change of the latest close vs the close `lookback` bars ago.
    None if insufficient data or the reference price is non-positive.
    Verbatim from v2 move_pct."""
    if not closes or len(closes) <= lookback:
        return None
    ref = closes[-(lookback + 1)]
    latest = closes[-1]
    if ref is None or ref <= 0:
        return None
    return ((latest - ref) / ref) * 100.0


def catchup_gap(leader_move, proxy_move, beta):
    """How much of the expected catch-up move the proxy still owes.
    expected = leader_move * beta; gap = expected - actual. Verbatim from v2."""
    return (leader_move * beta) - proxy_move


def lag_direction(leader_move, gap, min_leader_move, min_gap):
    """Direction to trade the proxy so it profits from closing the gap.
    None unless the leader moved enough AND the proxy still owes a gap in the
    LEADER'S direction (same sign). An overshot proxy (gap flips sign) is
    skipped — the catch-up is already done. Verbatim from v2 lag_direction."""
    if leader_move is None or gap is None:
        return None
    if abs(leader_move) < min_leader_move:
        return None
    if abs(gap) < min_gap:
        return None
    if (leader_move > 0) != (gap > 0):   # gap must share the leader's sign
        return None
    return "LONG" if gap > 0 else "SHORT"


def volume_trend(candles, lookback=6):
    """Recent-half vs earlier-half average volume, % change. Verbatim from v2
    volume_trend (default lookback 6)."""
    if len(candles) < lookback:
        return 0.0
    vols = [_vol(c) for c in candles[-lookback:]]
    half = lookback // 2
    if half <= 0:
        return 0.0
    recent = sum(vols[-half:]) / half
    earlier = sum(vols[:half]) / half
    if earlier <= 0:
        return 0.0
    return ((recent - earlier) / earlier) * 100


# ═══════════════════════════════════════════════════════════════
# Thesis builder — one proxy (ported verbatim from v2 build_thesis)
# ═══════════════════════════════════════════════════════════════

def build_thesis(proxy_cfg, leader_move, proxy_closes, proxy_candles, sm, inputs):
    """Port of v2 build_thesis. Returns a thesis dict (with `score`) or None.

    None is returned when:
      - the proxy has no measurable move (insufficient candle history), OR
      - lag_direction resolves no direction (leader didn't move enough, the gap
        is below threshold, or the proxy overshot / already caught up).
    minScore is NOT applied here — the caller (scan.py) gates on thesis['score'].

    `sm` is the smart-money tuple (direction, pct) or (None, 0.0) — the caller
    fetches it (leaderboard_get_markets). SM on XYZ equities is sparse, so SM is
    a SCORE BONUS, not a gate (verbatim from v2)."""
    proxy = proxy_cfg["proxy"]
    beta = float(proxy_cfg.get("beta", 1.0))
    lookback = int(inputs.get("moveLookbackBars", 4))
    min_leader = float(inputs.get("minLeaderMovePct", 2.0))
    min_gap = float(inputs.get("minGapPct", 2.0))
    strong_gap = float(inputs.get("strongGapPct", 5.0))
    sm_min = float(inputs.get("smTiltMinPct", 55))
    sm_strong = float(inputs.get("smStrongTiltPct", 70))

    proxy_move = move_pct(proxy_closes, lookback)
    if proxy_move is None:
        return None
    gap = catchup_gap(leader_move, proxy_move, beta)
    direction = lag_direction(leader_move, gap, min_leader, min_gap)
    if direction is None:
        return None

    sm_dir, sm_tilt = sm if sm else (None, 0.0)
    vol_trend = volume_trend(proxy_candles)

    score = 0
    reasons = [f"leader_{leader_move:+.1f}%", f"{proxy}_lag_{proxy_move:+.1f}%", f"gap_{gap:+.1f}%"]
    score += 2  # leader moved + proxy owes a gap in the leader's direction (gate)
    if abs(gap) >= strong_gap:
        score += 2
        reasons.append(f"gap_strong_{gap:+.1f}%")
    # SM is often sparse on XYZ equities — agreement is a bonus, not a gate.
    if sm_dir == direction and sm_tilt >= sm_min:
        score += 1
        reasons.append(f"sm_confirms_{sm_tilt:.0f}%")
        if sm_tilt >= sm_strong:
            score += 1
            reasons.append("sm_strong")
    if vol_trend > 15:
        score += 1
        reasons.append(f"vol_rising_{vol_trend:+.0f}%")

    return {
        "coin": proxy,
        "direction": direction,
        "score": score,
        "reasons": reasons,
        "leader_move_pct": round(leader_move, 2),
        "proxy_move_pct": round(proxy_move, 2),
        "gap_pct": round(gap, 2),
        "beta": beta,
        "sm_direction": sm_dir if sm_dir else "NONE",
        "sm_tilt_pct": _f(sm_tilt),
        "volume_trend_pct": round(vol_trend, 2),
    }
