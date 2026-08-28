"""REMORA — pure whale-mirror math (no I/O, no MCP, no clock).

A faithful Runtime 3.0 port of the v2 Remora producer's pure mirror logic
(remora-producer.py v1.0.1). The mirror functions here are reproduced VERBATIM
from the v2 producer so a fidelity harness can diff this against v2 on the same
whale position snapshot. These are the functions v2 unit-tested in
tests/test_signal.py: position_notional, mirror_direction, position_asset,
top_position, consensus_bonus, plus the candidate aggregation/scoring.

Remora rides a set of whale traders: take each whale's highest-conviction
(largest-notional) open position, aggregate across whales into (asset, direction)
candidates, and score by consensus (how many whales agree) + whale quality. The
whale set is EITHER the operator's hand-picked override (inputs.whales) OR — by
default — an auto-built smart-money cohort. The cohort PARSING helper below
(`realized`, used by the scan's cohort builder) is reused VERBATIM from
WhaleHunter's scoring module so the two strategies bucket top traders
identically; the discovery shapes are token-gated, so the field accessors are
copied, not invented. The MCP fetches (top-trader pages, whale positions, whale
tier) are done by the caller (scan.py) and passed in, so this module stays pure
and unit-testable.
"""

# Whale-quality tiers that earn the discovery_get_trader_state bonus
# (verbatim from v2 QUALITY_TIERS).
QUALITY_TIERS = {"ELITE", "RELIABLE", "PROFITABLE"}

# v2 producer constants (remora-producer.py).
MAX_LEVERAGE = 10
DEFAULT_LEVERAGE = 4
DEFAULT_MIN_SCORE = 4
DEFAULT_MIN_NOTIONAL_USD = 5000   # ignore dust positions

# Auto-cohort defaults (mirror WhaleHunter's smart-money cohort engine). Used by
# scan._build_cohort when inputs.whales is empty so Remora is autonomous OOTB.
DEFAULT_COHORT_SIZE = 10          # top N proven traders to mirror by default
DEFAULT_COHORT_REFRESH_HOURS = 24
COHORT_CACHE_VERSION = 1          # bump if cohort-BUILDING logic changes (busts a stale cache)


def safe_float(v, default=0.0):
    """Verbatim from v2 safe_float."""
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return default


# ── cohort-build parsing (reused VERBATIM from WhaleHunter/scoring.py) ──

def realized(t):
    """LIFETIME realized PnL for a top-trader dict — reused VERBATIM from
    WhaleHunter's scoring.realized so Remora ranks the auto-cohort identically.
    Never falls back to total PnL (realized+unrealized), which is not monotonic
    with the realized-PnL sort and mis-ranks the cohort. Token-gated discovery
    shape — accessors copied, not invented."""
    def _f(x, *keys, default=0.0):
        if not isinstance(x, dict):
            return default
        for k in keys:
            if x.get(k) is not None:
                try:
                    return float(x[k])
                except (TypeError, ValueError):
                    continue
        return default
    return _f(t, "realizedProfitAndLoss", "realized_profit_and_loss",
              "profit_and_loss_realized", "realizedPnl", "realized_pnl", default=0.0)


def trader_address(t):
    """Lower-cased wallet/trader address from a top-trader dict, or "" — reused
    VERBATIM from WhaleHunter's cohort-build accessor (token-gated shape)."""
    if not isinstance(t, dict):
        return ""
    return (t.get("address") or t.get("trader_address") or "").lower()


def position_notional(pos):
    """USD notional of a position for conviction ranking: notional_size, else
    size x entry, falling back to marginUsed, then to raw size.

    Handles BOTH position shapes: the Hyperliquid clearinghouse one
    (szi/entryPx/marginUsed) and the leaderboard_get_trader_positions one
    (market/size/entry_price/notional_size). The leaderboard shape is what
    scan.py actually receives — without these keys `entry` resolved to 0 and
    the notional collapsed to the RAW TOKEN COUNT via the final fallback, so a
    184M-token PUMP dust position ($350K) outranked a 59K-token ETH short
    ($112M) and Remora mirrored meme dust instead of real conviction.

    `notional_size` is the documented USD field (|size| x current price) per
    senpi://guides/hyperfeed-trader-positions, so it is preferred over the
    size x entry product; `entry_price` backs it up if it is ever absent."""
    if not isinstance(pos, dict):
        return 0.0
    # leaderboard shape: USD notional is returned directly — no math needed.
    ns = abs(safe_float(pos.get("notional_size", pos.get("notionalSize", 0))))
    if ns > 0:
        return ns
    size = abs(safe_float(pos.get("szi", pos.get("size", 0))))
    entry = safe_float(pos.get("entryPx", pos.get("entryPrice",
                       pos.get("entry_price", pos.get("entry", 0)))))
    notional = size * entry
    if notional > 0:
        return notional
    margin = abs(safe_float(pos.get("marginUsed", pos.get("margin", 0))))
    return margin if margin > 0 else size


def mirror_direction(pos):
    """LONG / SHORT for a position (explicit direction/side field, else szi
    sign). None if undeterminable. Verbatim from v2."""
    if not isinstance(pos, dict):
        return None
    d = str(pos.get("direction", pos.get("side", ""))).upper()
    if d in ("LONG", "SHORT"):
        return d
    szi = safe_float(pos.get("szi", pos.get("size", 0)))
    if szi > 0:
        return "LONG"
    if szi < 0:
        return "SHORT"
    return None


def position_asset(pos):
    """Asset symbol for a position, CASE-PRESERVED.

    v2 upper-cased this. That is wrong for a derived universe: the emitted
    symbol goes straight into a Senpi tool call, and Hyperliquid coin names are
    CASE-SENSITIVE. The 1000x-denominated names carry a lowercase k (kPEPE,
    kSHIB, kBONK) and `KPEPE` is rejected as INVALID_ARGUMENT; HIP-3 assets
    carry a lowercase dex prefix (`xyz:GOLD`, not `XYZ:GOLD`). Remora mirrors
    whatever the whales hold, so both forms occur live — upper-casing turned
    those into silent no-trades. Callers that need a case-insensitive COMPARISON
    (held-asset set, dedup map) upper-case at the comparison site instead."""
    if not isinstance(pos, dict):
        return ""
    return str(pos.get("coin", pos.get("market", pos.get("asset", pos.get("symbol", "")))))


def top_position(positions, min_notional=0.0):
    """The single largest-notional position with a determinable direction and
    notional >= min_notional. None if the whale holds nothing qualifying.
    Verbatim from v2."""
    best, best_n = None, -1.0
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        if mirror_direction(p) is None or not position_asset(p):
            continue
        n = position_notional(p)
        if n < min_notional:
            continue
        if n > best_n:
            best_n, best = n, p
    return best


def consensus_bonus(count):
    """Score bonus for how many whales independently hold the same
    asset+direction. 3+ whales is a strong consensus. Verbatim from v2."""
    if count >= 3:
        return 3
    if count == 2:
        return 2
    return 0


def aggregate_candidates(whale_tops, use_tier):
    """Aggregate per-whale (top_position, tier) tuples into (asset, direction)
    candidates with consensus count + max notional + whale quality.

    `whale_tops` is a list of (trader_id, top_position_dict_or_None,
    tier_str_or_None) tuples — the caller (scan.py) does the MCP fetches and
    passes the resolved tops here so this module stays pure. Reproduces the
    aggregation body of v2 gather_candidates() verbatim."""
    agg = {}
    for trader_id, top, tier in whale_tops:
        if not top:
            continue
        asset = position_asset(top)
        direction = mirror_direction(top)
        notional = position_notional(top)
        if not asset or direction is None:
            continue
        key = (asset, direction)
        entry = agg.setdefault(key, {
            "asset": asset, "direction": direction,
            "count": 0, "max_notional": 0.0, "quality": False,
            "whales": [],
        })
        entry["count"] += 1
        entry["max_notional"] = max(entry["max_notional"], notional)
        if use_tier and tier in QUALITY_TIERS:
            entry["quality"] = True
        entry["whales"].append(str(trader_id)[:10])
    return list(agg.values())


def score_candidate(cand):
    """(score, reasons) for an aggregated candidate. Verbatim from v2
    score_candidate: +3 base (a tracked whale's top conviction position),
    +consensus_bonus (2 whales -> +2, 3+ -> +3), +1 if any agreeing whale is
    ELITE/RELIABLE/PROFITABLE tier."""
    count = cand["count"]
    score = 3  # a tracked whale's top conviction position
    reasons = [
        f"tracked whales are {cand['direction']} {cand['asset']}",
        f"held by {count} tracked whale(s)",
        f"largest whale position ${cand['max_notional']:,.0f}",
    ]
    cb = consensus_bonus(count)
    if cb:
        score += cb
        reasons.append(f"{count} whales agree on this trade")
    if cand.get("quality"):
        score += 1
        reasons.append("a top-rated whale is in this trade")
    return score, reasons
