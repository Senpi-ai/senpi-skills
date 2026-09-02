#!/usr/bin/env python3
"""senpi-signals ranker — dual-lens (trade + social) stateful diff engine.

ONE sweep → TWO ranked feeds from the same detected signals:
  - trade  : Senpi USERS building ideas — actionable EDGE, price CONFIRMATION, credible enough to
             act on. A static funding extreme scores LOW here (carry, not a directional edge); a
             winning smart-money divergence scores HIGH.
  - social : team AUTOMATION / market-news content — surprising, non-obvious, and FRESH (anti-repeat
             so a cron doesn't re-post the same six), credibility-gated. Static extremes are fine here.

Core principle: rank credible CHANGE and actionable EDGE — and never repeat yourself.

Input JSON: { "asset_metrics": { "<asset>": {oi, price, price_change_pct, smart_share, smart_dir,
              crowd_dir, funding_pctile, funding_annualized_pct, notional_vol, dex, oi_side} },
              "events": [ pre-formed signals (whale_move / momentum_event / cross_asset_laggard) ] }

State is a RING of snapshots: the change-detectors diff `cur` against the snapshot ~DIFF_TARGET_MIN
old (not merely the last run), so a 3-minute re-run still sees a meaningful delta. `surfaced` records
when each (asset,detector) last appeared, for the social freshness penalty. Stdlib only.

Thresholds mirror references/detectors.md — change them in BOTH places.
"""
import argparse
import datetime
import json
import os
import sys

try:
    import fcntl  # POSIX advisory lock for the shared state file (the agents are Linux)
except ImportError:  # pragma: no cover — non-POSIX fallback is last-writer-wins
    fcntl = None

# ── detector thresholds (keep in sync with references/detectors.md) ──
OI_SURGE_PCT = 0.10
PRICE_FLAT = 0.01
# `smart_share` needs a declared BASIS — the same number means different things per source.
# senpi-smart-money's `bias` is net/gross NOTIONAL in [-1,+1] (dollar-weighted, signed) — NOT a
# headcount %. Printing "83% of the cohort hold shorts" for bias=-0.83 is a false claim: the true
# statement is "net exposure is 83% short-weighted". Declare which one you passed.
SMART_SHARE_KIND = {
    "net_bias": "net exposure {v:.0f}% {side}-weighted",        # abs(bias)*100 from senpi-smart-money
    "cohort_pct": "{v:.0f}% of {src} positioned {side}",        # positioned members / cohort sampled
    None: "{v:.0f}% lean of {src} (BASIS UNSTATED — net-exposure or headcount?)",
}
# Per-basis gates. These are NOT interchangeable: 25% of a 150-member cohort in ONE name out of ~200
# instruments is extraordinary, while |net/gross| = 0.25 is routine.
SMART_NET_BIAS_MIN = 40.0     # matches senpi-smart-money's own LEAN_THRESHOLD (0.40) — don't call a
                              # lean directional when the engine that computed it wouldn't
SMART_COHORT_PCT_MIN = 8.0    # a single name holding this much of the cohort is already rare
SMART_SHARE_MIN = 25.0        # fallback when the basis is unstated
SMART_JUMP_PP = 12.0
FUNDING_PCTILE = 95.0
WHALE_MIN_USD = 1_000_000
FUNDING_FLIP_FULL_PCT = 10.0  # post-flip |rate| that counts as a full-magnitude carry turn. A "flip"
                              # is a ZERO-CROSSING, so it is near zero by construction — the size of
                              # the move PAST zero is the story. Without this every flip scored 1.0
                              # and −0.09%/yr tied −8.2%/yr.

# ── flow: "money MOVED IN", never "holdings look bigger" ──────────────────────
# The 4h gain leaderboard is CIRCULAR: if a name falls, everyone short it is mechanically at the top.
# It identifies who benefited from a move that already happened — never who saw it coming.
# A notional-weighted lean carries the same circularity one level down: `bias` = net/gross NOTIONAL,
# so if a name falls 10% and NOBODY TRADES, every short's notional grows and every long's shrinks and
# the lean drifts toward the winning side on price alone. Mark-to-market drift scales with the price
# move, so a net_bias "build" is only readable when price barely moved.
# Headcount (`cohort_pct`) and BASE-UNIT size are immune by construction: neither changes with price.
MTM_SAFE_PCT = 1.5            # |4h move| above which a net_bias build is unreadable → suppressed
FLOW_MIN_WALLETS = 3          # distinct wallets opening/adding before it is flow rather than noise
FLOW_MIN_BASE_PCT = 0.10      # min growth in BASE UNITS held on the side (+10%)
FLOW_EPS = 1e-9

# ── credibility: a MULTIPLIER on the whole score, not a 10% additive term ──
FULL_CRED_VOL = 25_000_000    # notional vol at/above which liquidity is a non-issue (mult 1.0)
CRED_FLOOR_VOL = 1_000_000    # below this a market is too thin to trust the read at all → dropped
CRED_MIN_MULT = 0.45          # multiplier at the drop floor, ramping to 1.0 by FULL_CRED_VOL
TRADE_CRED_FLOOR = 5_000_000  # you can't trade a book this thin → excluded from the TRADE feed only

# ── provenance of the smart-money read (declare it in asset_metrics as `smart_source`) ──
# The engine must never assert a source it cannot verify. A leaderboard-derived read is momentum
# (survivorship-biased: whoever is short a falling name tops the 4h board) and is NOT the proven
# cohort — labelling it as such is the exact overstatement golden rule 12 exists to prevent.
SMART_SOURCE_LABEL = {
    "proven_cohort": "the proven cohort",
    "leaderboard_4h": "the live 4h leaderboard (what's winning now, not track record)",
    None: "top traders (SOURCE UNSTATED — do not call this smart money)",
}
SMART_SOURCE_TRUST = {          # folded into credibility: how much we believe the reading itself
    "proven_cohort": 1.0,
    "leaderboard_4h": 0.7,      # momentum read — real, but weaker evidence of conviction
    None: 0.8,                  # unstated: don't reward it, don't pretend it's worthless
}

# ── sample size: an observed ratio from 5 wallets is not the same evidence as one from 500 ──
# 4-short-vs-1-long and 400-vs-100 are both "80% one-sided", but only one of them is a fact about the
# market; the other is four people. Shrink the observed ratio toward 50/50 (no information) in
# proportion to how little data stands behind it — n/(n+SAMPLE_PRIOR_N), a plain Bayesian shrink.
SAMPLE_PRIOR_N = 20           # a ratio needs ~this many positioned traders before it counts at ~half weight
SMALL_SAMPLE_N = 10           # at/below this, say so in the output — the reader must see the n

# ── cadence / freshness ──
DIFF_TARGET_MIN = 60          # diff the change-detectors against the snapshot ~this old (fix 3-min noise)
TREND_LOOKBACK_MIN = 720      # the SLOW lookback (~12h) for cohort-positioning trend
TREND_MIN_PP = 3.0            # min move in cohort share (pts) to call it a build/unwind
TREND_MIN_AGE_MIN = 360       # refuse to call it a trend if the baseline is younger than this (~6h)
SNAP_MAX_AGE_MIN = 1500       # keep ~25h of snapshots so the 12h lookback always has a partner
SNAP_RING_MAX = 48            # cap the ring length
FRESH_WINDOW_MIN = 45         # an (asset,detector) shown within this window is penalized, decaying to 1.0
FRESH_MIN_MULT = 0.30         # freshness multiplier the instant after it was surfaced

# ── output ──
MIN_SOCIAL = 30.0   # content lens is inclusive-but-flagged (a thin curiosity can be a tweet)…
MIN_TRADE = 45.0    # …the trade lens is strict (you can't act on a thin book)
TOP_N = 6
FAMILY_CAP = 2               # max signals per detector FAMILY in a single feed (kills the funding flood)

# how invisible-on-a-chart each detector is (the social "moat" weight)
NON_OBVIOUS = {
    "sm_flow": 1.0,                # base-unit position flow — the cleanest read we have
    "sm_positioning_build": 1.0,   # needs cohort history — nobody else is tracking this
    "oi_surge": 1.0, "funding_flip": 1.0, "sm_divergence": 1.0, "whale_move": 1.0,
    "funding_extreme": 0.9, "sm_conviction": 0.85, "cross_asset_laggard": 0.8,
    "momentum_event": 0.6, "regime_shift": 0.6,
}
# how tradeable each detector is (a clear, actionable directional edge)
EDGE = {
    "sm_flow": 1.0,               # wallets actually OPENED/ADDED size — immune to mark-to-market
    "sm_positioning_build": 1.0,  # proven traders MOVING onto a side — the strongest read we have
    "sm_divergence": 1.0, "sm_conviction": 0.9, "whale_move": 0.85, "oi_surge": 0.75,
    "cross_asset_laggard": 0.75, "funding_flip": 0.7, "momentum_event": 0.6, "regime_shift": 0.6,
    "funding_extreme": 0.35,  # a static extreme is carry, not a directional edge → low trade score
}
# collapse detectors to families for the per-feed diversity cap
FAMILY = {
    "funding_flip": "funding", "funding_extreme": "funding",
    "sm_divergence": "smart_money", "sm_conviction": "smart_money",
    "sm_positioning_build": "smart_money", "sm_flow": "smart_money",
    "oi_surge": "oi", "whale_move": "whale", "cross_asset_laggard": "cross_asset",
    "momentum_event": "momentum", "regime_shift": "regime",
}
# detectors that fire from a CHANGE vs the prior snapshot (vs a static level)
CHANGE_DETECTORS = {"oi_surge", "sm_conviction", "funding_flip", "whale_move",
                    "cross_asset_laggard", "momentum_event", "regime_shift",
                    "sm_positioning_build", "sm_flow"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sign(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


def _parse_ts(s):
    try:
        dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def credibility(vol):
    """Liquidity → a 0..1 credibility MULTIPLIER on the whole score. Unknown/0 vol → neutral 0.8
    (not proof of thinness). Below CRED_FLOOR_VOL the signal is dropped upstream."""
    v = _num(vol)
    if v is None or v <= 0:
        return 0.8
    if v >= FULL_CRED_VOL:
        return 1.0
    if v <= CRED_FLOOR_VOL:
        return CRED_MIN_MULT
    frac = (v - CRED_FLOOR_VOL) / (FULL_CRED_VOL - CRED_FLOOR_VOL)
    return round(CRED_MIN_MULT + frac * (1.0 - CRED_MIN_MULT), 3)


def freshness(asset, detector, surfaced, now):
    """1.0 if this (asset,detector) wasn't surfaced recently; decays toward FRESH_MIN_MULT the more
    recently it was. Keeps a continuous cron from re-posting the same six every run. Social-only."""
    last = _parse_ts((surfaced or {}).get(f"{asset}|{detector}"))
    if last is None or now is None:
        return 1.0
    age_min = (now - last).total_seconds() / 60.0
    if age_min < 0 or age_min >= FRESH_WINDOW_MIN:
        return 1.0
    return round(FRESH_MIN_MULT + (age_min / FRESH_WINDOW_MIN) * (1.0 - FRESH_MIN_MULT), 3)


def base_flow(cur_pos, prior_pos, side):
    """Did money actually MOVE IN, in BASE UNITS, on `side`?

    `smart_positions` is {wallet: signed base size} — coins/contracts, NOT notional. Base units do
    not change when price changes, so every number below is a decision somebody made:

        opened  wallets that were flat/absent and are now on this side
        added   wallets already on this side whose size grew
        closed  wallets that were on this side and are now flat
        base_delta_pct  growth in total base units held on the side

    This is the only measure immune to the mark-to-market circularity — cf. MTM_SAFE_PCT.
    Returns None when either side of the diff is missing (no data ≠ no flow).
    """
    if not isinstance(cur_pos, dict) or not isinstance(prior_pos, dict) or not cur_pos:
        return None
    want = 1 if str(side).lower() == "long" else -1
    def on_side(d, w):
        v = _num(d.get(w))
        return abs(v) if (v is not None and _sign(v) == want) else 0.0
    wallets = set(cur_pos) | set(prior_pos)
    opened = added = closed = 0
    now_base = then_base = 0.0
    for w in wallets:
        c, b = on_side(cur_pos, w), on_side(prior_pos, w)
        now_base += c
        then_base += b
        if c > FLOW_EPS and b <= FLOW_EPS:
            opened += 1
        elif c > b + FLOW_EPS:
            added += 1
        elif b > FLOW_EPS and c <= FLOW_EPS:
            closed += 1
    delta = ((now_base - then_base) / then_base) if then_base > FLOW_EPS else (
        1.0 if now_base > FLOW_EPS else 0.0)
    return {"opened": opened, "added": added, "closed": closed,
            "base_delta_pct": delta, "now_base": now_base, "then_base": then_base}


def one_sidedness(m, smart_dir):
    """Of the traders actually POSITIONED in this name, what fraction sit on the smart side?

    The cohort % alone cannot tell a rout from noise: "43% of the cohort is short" is 429-vs-40
    (~91% one-sided — real conviction) or 429-vs-380 (~53% — noise). The un-positioned remainder is
    NOT the other side, so it must not be counted as one. None when the split wasn't supplied.
    """
    ln, sn = _num(m.get("smart_long_n")), _num(m.get("smart_short_n"))
    if ln is None or sn is None:
        return None
    total = ln + sn
    if total <= 0:
        return None
    on_side = sn if str(smart_dir).lower() == "short" else ln
    return round(on_side / total, 3)


def sample_shrink(n):
    """0..1 — how much of an observed ratio survives, given the sample behind it. n=5 → 0.20,
    n=20 → 0.50, n=100 → 0.83, n=469 → 0.96. Unknown/zero n → 0 (claim nothing)."""
    if n is None or n <= 0:
        return 0.0
    return round(n / (n + SAMPLE_PRIOR_N), 3)


def positioned_n(m):
    """How many traders are actually positioned in this name (the sample the lean rests on)."""
    ln, sn = _num(m.get("smart_long_n")), _num(m.get("smart_short_n"))
    return None if ln is None or sn is None else ln + sn


def effective_one_sidedness(raw, n):
    """The observed lean, shrunk toward 50/50 by how thin the sample is. This is what may drive a
    score; the RAW figure is what gets reported, alongside its n, so the reader can judge it."""
    if raw is None:
        return None
    return round(0.5 + (raw - 0.5) * sample_shrink(n), 3)


def earliness(s):
    """0..1 — is this signal EARLY (flow is there, the move is not) or LATE (price already ran)?

    This REPLACES the old `confirmation` term, which rewarded price for having already moved the
    signal's way. That double-counts one price move: a leaderboard-sourced read is *itself* a
    statement that price already moved, so confirming it with the same move scores the same
    evidence twice — and it systematically prefers late signals. For "spot it as it emerges",
    flat price is the valuable state: the positioning is in and the move has not happened.

        price contradicts the side   → 0.15   being disproven
        price already ran with it    → 0.50   real, but you are late
        price flat                   → 1.00   early — this is the whole point
        no direction at all          → 0.30   absence of a read is NOT half-evidence

    (The old function scored "no direction" at 0.50 — the exact median — so a signal with no side
    outranked every signal price was disproving, and tied a half-confirmed one. That is why a
    non-directional momentum_event could rank #1 in the trade feed.)
    """
    d = s.get("direction")
    pc = _num(s.get("price_change_pct"))
    if not d:
        return 0.30
    if pc is None:
        return 0.50
    ran = min(1.0, abs(pc) / 3.0)
    aligned = (d == "long" and pc > 0) or (d == "short" and pc < 0)
    return round(1.0 - (0.50 if aligned else 0.85) * ran, 3)


def social_score(s, cred, fresh):
    """Content lens: surprising · non-obvious · a story · fresh. cred + freshness are multipliers."""
    no = NON_OBVIOUS.get(s["detector"], 0.6)
    mag = max(0.0, min(1.0, _num(s.get("magnitude")) or 0.0))
    conf = 1.0 if s.get("conflict") else 0.0          # a divergence is a story
    concr = 1.0 if s.get("concrete_entity") else 0.3  # a named whale is a great story
    change = 1.0 if s.get("is_change") else 0.0
    base = 0.34 * no + 0.22 * mag + 0.20 * conf + 0.14 * change + 0.10 * concr
    return round(100 * base * cred * fresh, 1)


def trade_score(s, cred):
    """Trade lens: directional EDGE · EARLINESS · CHANGE · a divergence · size. cred multiplies.
    NOT freshness-gated — a standing edge is still an edge even if it showed last run."""
    edge = EDGE.get(s["detector"], 0.5)
    cfm = earliness(s)
    mag = max(0.0, min(1.0, _num(s.get("magnitude")) or 0.0))
    conf = 1.0 if s.get("conflict") else 0.0
    change = 1.0 if s.get("is_change") else 0.0
    base = 0.30 * edge + 0.22 * cfm + 0.18 * change + 0.16 * conf + 0.14 * mag
    return round(100 * base * cred, 1)


def detect_from_metrics(cur, prior, prior_slow=None, slow_age_min=None):
    """Fire the diff/threshold detectors from current asset_metrics.

    Two baselines: `prior` ≈ DIFF_TARGET_MIN old (the fast diff — OI, funding, conviction) and
    `prior_slow` ≈ TREND_LOOKBACK_MIN old (~12h — the cohort-positioning trend, which needs a longer
    arm to show a real build). `prior_slow` falls back to `prior` when the ring isn't deep enough yet.
    """
    out = []
    for asset, m in (cur or {}).items():
        if not isinstance(m, dict):
            continue
        p = (prior or {}).get(asset, {}) if isinstance(prior, dict) else {}
        src = m.get("smart_source") if m.get("smart_source") in SMART_SOURCE_LABEL else None
        # The slow partner is resolved PER ASSET and PER SOURCE: comparing a proven-cohort reading
        # against a leaderboard one 12h ago is apples-to-oranges and would invent a trend out of a
        # source switch. A callable lets main() search the ring for a genuinely comparable snapshot;
        # a plain dict (tests / simple callers) keeps the old behaviour with an explicit age.
        if callable(prior_slow):
            ps, ps_age = prior_slow(asset, src)
        else:
            ps = (prior_slow or {}).get(asset, {}) if isinstance(prior_slow, dict) else {}
            ps_age = slow_age_min
            # even in dict mode, refuse a source mismatch rather than diff across provenances
            if ps and (ps.get("smart_source")
                       if ps.get("smart_source") in SMART_SOURCE_LABEL else None) != src:
                ps, ps_age = {}, None
        src_label = SMART_SOURCE_LABEL[src]
        src_trust = SMART_SOURCE_TRUST[src]
        kind = m.get("smart_share_kind") if m.get("smart_share_kind") in SMART_SHARE_KIND else None
        share_min = {"net_bias": SMART_NET_BIAS_MIN,
                     "cohort_pct": SMART_COHORT_PCT_MIN}.get(kind, SMART_SHARE_MIN)
        dex = m.get("dex", "")
        vol = _num(m.get("notional_vol")) or _num(m.get("day_notional_volume"))
        pcp = _num(m.get("price_change_pct"))
        if pcp is None:
            pcp = _num(m.get("token_price_change_pct_4h"))

        def sig(detector, direction, magnitude, numbers, conflict=False, flip=False, is_change=None):
            out.append({
                "asset": asset, "dex": dex, "detector": detector, "direction": direction,
                "numbers": numbers, "notional_vol": vol, "concrete_entity": None,
                "price_change_pct": pcp, "magnitude": max(0.0, min(1.0, magnitude)),
                "conflict": conflict, "flip": flip,
                "smart_source": src, "source_trust": src_trust,
                "is_change": (detector in CHANGE_DETECTORS if is_change is None else is_change),
            })

        # oi_surge (change)
        oi, poi = _num(m.get("oi")), _num(p.get("oi"))
        if oi is not None and poi:
            pct = (oi - poi) / poi
            if pct >= OI_SURGE_PCT:
                price, pprice = _num(m.get("price")), _num(p.get("price"))
                flat = (price is not None and pprice not in (None, 0)
                        and abs((price - pprice) / pprice) < PRICE_FLAT)
                nums = [f"OI +{pct * 100:.0f}% vs baseline"]
                if flat:
                    nums.append("price ~flat")
                sig("oi_surge", m.get("oi_side"), min(1.0, pct * 2), nums, conflict=flat)

        # smart-money divergence (state; a fresh flip counts as change)
        sd, cd, share = m.get("smart_dir"), m.get("crowd_dir"), _num(m.get("smart_share"))
        if sd and cd and sd != cd and share is not None and share >= share_min:
            flip = bool(p.get("smart_dir")) and p.get("smart_dir") != sd
            one_sided = one_sidedness(m, sd)
            ln, sn = _num(m.get("smart_long_n")), _num(m.get("smart_short_n"))
            # share = % of the PROVEN COHORT on the smart side (never the 4h leaderboard's PnL share)
            nums = [SMART_SHARE_KIND[kind].format(v=share, side=str(sd).upper(), src=src_label)]
            n_pos = positioned_n(m)
            if one_sided is not None:   # the split is what separates a rout from noise — always cite it
                line = (f"{sn:.0f} short vs {ln:.0f} long among those positioned "
                        f"({one_sided * 100:.0f}% one-sided)")
                if n_pos is not None and n_pos <= SMALL_SAMPLE_N:
                    line += f" — SMALL SAMPLE (n={n_pos:.0f}), treat as weak evidence"
                nums.append(line)
            else:                        # …and when it's missing, say so — never imply the rest are opposite
                nums.append("positioned long/short split unknown")
            nums.append(f"crowd {str(cd).upper()}")
            # magnitude from one-sidedness, SHRUNK by sample size (50/50 ⇒ no directional information;
            # 4-vs-1 must not score like 400-vs-100), else fall back to the declared share
            eff = effective_one_sidedness(one_sided, n_pos)
            mag = max(0.0, (eff - 0.5) * 2) if eff is not None else share / 100.0
            sig("sm_divergence", sd, mag, nums, conflict=True, flip=flip, is_change=flip)

        # cohort POSITIONING TREND (change) — the same cohort's share on a name moving over ~12h.
        # "43% of the top 1,000 now hold HYPE shorts, up from 38% 12h ago" — change on the best data
        # we have. A build is a far stronger read than a standing divergence, so it outranks one.
        sshare = _num(ps.get("smart_share"))
        # The baseline's REAL age — never the nominal target. A fallback baseline can be minutes old,
        # and printing "~12h ago" over it would be a fabricated number (golden rule 1). Too young ⇒
        # this is not a trend at all, so it must not fire.
        age_h = (ps_age / 60.0) if ps_age is not None else None
        if (sd and share is not None and sshare is not None
                and ps.get("smart_dir") == sd                      # same side — a genuine build, not a rotation
                and abs(share - sshare) >= TREND_MIN_PP
                and ps_age is not None and ps_age >= TREND_MIN_AGE_MIN):
            d_pp = share - sshare
            building = d_pp > 0
            window = f"~{age_h:.0f}h" if age_h >= 1 else f"~{ps_age:.0f}min"
            now_txt = SMART_SHARE_KIND[kind].format(v=share, side=str(sd).upper(), src=src_label)
            nums = [f"{now_txt} — {'up' if building else 'down'} from {sshare:.0f}% {window} ago",
                    f"{'+' if building else '−'}{abs(d_pp):.0f}pp over {window}"]
            os_now = one_sidedness(m, sd)
            if os_now is not None:
                n_pos = positioned_n(m)
                line = f"{os_now * 100:.0f}% one-sided among those positioned"
                if n_pos is not None and n_pos <= SMALL_SAMPLE_N:
                    line += f" — SMALL SAMPLE (n={n_pos:.0f})"
                nums.append(line)
            # A net_bias lean drifts toward the winning side on PRICE ALONE (mark-to-market), so a
            # "build" on that basis is unreadable once price has moved. Headcount cannot drift.
            mtm_unreadable = (kind == "net_bias" and pcp is not None and abs(pcp) >= MTM_SAFE_PCT)
            if mtm_unreadable:
                nums.append(f"SUPPRESSED — net-exposure basis with price {pcp:+.1f}%: this lean grows "
                            f"on mark-to-market alone, so it cannot be called money moving in")
            else:
                if kind == "net_bias" and pcp is not None:
                    nums.append(f"net-exposure basis, price {pcp:+.1f}% — flat enough that "
                                f"mark-to-market drift is negligible")
                # magnitude scales with the size of the move (a 15pp swing in 12h is enormous)
                sig("sm_positioning_build", sd, min(1.0, abs(d_pp) / 15.0), nums,
                    conflict=bool(cd and cd != sd), is_change=True)

        # ── FLOW (base units) — the honest "has more money moved in?" read ──────────────
        # Everything above measures HOLDINGS (a share, a lean). This measures DECISIONS: wallets
        # that opened or added size, counted in coins/contracts. Price cannot manufacture it.
        flow = base_flow(m.get("smart_positions"), ps.get("smart_positions"), sd) if sd else None
        if flow is not None and ps_age is not None and ps_age >= TREND_MIN_AGE_MIN:
            movers = flow["opened"] + flow["added"]
            if movers >= FLOW_MIN_WALLETS and flow["base_delta_pct"] >= FLOW_MIN_BASE_PCT:
                w = f"~{age_h:.0f}h" if (age_h or 0) >= 1 else f"~{ps_age:.0f}min"
                fnums = [f"{flow['opened']} wallets OPENED and {flow['added']} ADDED "
                         f"{str(sd).upper()} in the last {w}",
                         f"base size held on the side +{flow['base_delta_pct'] * 100:.0f}% "
                         f"(units, not notional — immune to the price move)"]
                if flow["closed"]:
                    fnums.append(f"{flow['closed']} closed out")
                if cd and cd != sd:
                    fnums.append(f"crowd still {str(cd).upper()}")
                sig("sm_flow", sd, min(1.0, flow["base_delta_pct"] / 0.5), fnums,
                    conflict=bool(cd and cd != sd), is_change=True)

        # smart-money conviction jump (change) — fires on a move in EITHER direction (piling in OR unwinding)
        pshare = _num(p.get("smart_share"))
        if share is not None and pshare is not None and abs(share - pshare) >= SMART_JUMP_PP:
            delta = share - pshare
            flow = "piling in" if delta > 0 else "unwinding"
            side = (str(sd).upper() + " ") if sd else ""
            sig("sm_conviction", sd, share / 100.0,
                [f"top traders {flow} {side}".rstrip()
                 + f" — concentration {'+' if delta > 0 else '−'}{abs(delta):.0f}pp to {share:.0f}%"])

        # funding: split a FLIP (change → tradeable regime shift) from a static EXTREME (state → social)
        fp = _num(m.get("funding_pctile"))
        fa, pfa = _num(m.get("funding_annualized_pct")), _num(p.get("funding_annualized_pct"))
        flipped = (fa is not None and pfa is not None
                   and _sign(fa) != _sign(pfa) and _sign(pfa) != 0)
        if flipped:
            # magnitude = how far PAST zero it went. A flip is a zero-crossing, so it is always
            # near zero at the moment it fires; the distance travelled is the whole story.
            sig("funding_flip", None, min(1.0, abs(fa) / FUNDING_FLIP_FULL_PCT),
                [f"funding flipped to {fa:+.2f}%/yr (was {pfa:+.2f})"], flip=True, is_change=True)
        elif fp is not None and fp >= FUNDING_PCTILE:
            nums = [f"funding {fp:.0f}th pctile"]
            if fa is not None:
                nums.append(f"{fa:+.0f}%/yr")
            sig("funding_extreme", None, fp / 100.0, nums, is_change=False)
    return out


def coverage(cur):
    """Which lenses actually had DATA this run — so a dark detector is never reported as
    "looked and found nothing".

    `SKILL.md`: "Missing fields just skip their detectors." That silence is indistinguishable from a
    genuine null result, and the two mean opposite things. Without the proven-cohort fan-out the
    smart-money detectors cannot fire at all, and the sweep quietly degrades into OI + funding +
    price — i.e. into market-pulse. This makes that visible.
    """
    tot = sum(1 for v in (cur or {}).values() if isinstance(v, dict))
    def pct(f):
        if not tot:
            return 0.0
        return round(100.0 * sum(1 for v in cur.values() if isinstance(v, dict) and f(v)) / tot, 1)
    div = pct(lambda v: v.get("smart_dir") and v.get("crowd_dir") and v.get("smart_share") is not None)
    flow = pct(lambda v: isinstance(v.get("smart_positions"), dict) and v.get("smart_positions"))
    proven = pct(lambda v: v.get("smart_source") == "proven_cohort")
    return {"assets": tot, "smart_divergence_inputs_pct": div, "base_flow_inputs_pct": flow,
            "proven_cohort_pct": proven,
            "smart_money_lens": ("ok" if div >= 25.0 else ("thin" if div > 0 else "NO DATA")),
            "flow_lens": ("ok" if flow >= 25.0 else ("thin" if flow > 0 else "NO DATA"))}


def normalize_event(e):
    if not isinstance(e, dict) or not e.get("asset") or not e.get("detector"):
        return None
    det = e["detector"]
    if det == "whale_move":
        # MOVES, not holdings. A big position held from an old entry with no recent change is NOT a
        # signal — a whale sitting on $78M from a months-old entry is holdings. Require a recent size
        # change (opened/added/flipped) or a large 4h PnL swing; magnitude from the CHANGE, not size.
        chg = _num(e.get("change_usd")) or _num(e.get("pnl_swing_usd"))
        if not (chg or e.get("opened") or e.get("flipped")):
            return None
        mag = min(1.0, (abs(chg) if chg else WHALE_MIN_USD) / (10 * WHALE_MIN_USD))
    else:
        mag = _num(e.get("magnitude"))
        if mag is None:
            usd = _num(e.get("usd"))
            mag = min(1.0, usd / WHALE_MIN_USD / 10) if usd else 0.6
    return {
        "asset": e["asset"], "dex": e.get("dex", ""), "detector": e["detector"],
        "direction": e.get("direction"), "numbers": e.get("numbers") or [],
        "notional_vol": _num(e.get("notional_vol")), "concrete_entity": e.get("concrete_entity"),
        "price_change_pct": _num(e.get("price_change_pct")),
        "magnitude": max(0.0, min(1.0, mag)), "conflict": bool(e.get("conflict")),
        "flip": bool(e.get("flip")), "is_change": bool(e.get("is_change", True)),
    }


def _smart_lead(s):
    """How to NAME the actors in prose — governed by the declared source, never assumed. Calling the
    4h leaderboard's winners "smart money" is the overstatement golden rule 12 exists to prevent."""
    return {"proven_cohort": "Smart money",
            "leaderboard_4h": "The last 4h's top performers",
            None: "Top traders (source unstated)"}[s.get("smart_source")
                                                   if s.get("smart_source") in SMART_SOURCE_TRUST else None]


def frame(s):
    """Content voice (social feed)."""
    a, d = s["asset"], (s.get("direction") or "")
    nums = "; ".join(s.get("numbers") or [])
    det = s["detector"]
    if det == "sm_divergence":
        return f"{_smart_lead(s)} lean {d.upper()} on {a} while the crowd leans the other way — {nums}."
    if det == "oi_surge":
        return (f"{nums} on {a}" + (f" {d}s" if d else "")
                + " — positioning building under a quiet chart.")
    if det in ("funding_extreme", "funding_flip"):
        return f"{a}: {nums} — a funding dislocation most screens never show."
    if det == "whale_move":
        return f"{s.get('concrete_entity') or 'A top trader'} on {a}: {nums}."
    if det == "sm_flow":
        return (f"Money is moving into {a} {d}s while the chart is quiet — {nums}. "
                f"Not who is winning right now; who is buying in.")
    if det == "sm_positioning_build":
        return f"{a} {d}s are what to watch — {nums}."
    if det == "sm_conviction":
        return f"{nums} on {a}."   # nums already states the flow (piling in / unwinding) + side
    if det == "cross_asset_laggard":
        return f"{a} hasn't followed the move — {nums}."
    return f"{a}: {nums}."


def badge(sc):
    """Severity flag by score — so the eye lands on the biggest first (a round-3 output convention)."""
    return "🔥" if sc >= 80 else ("🟠" if sc >= 65 else "🟡")


def trade_read(s):
    """Actionable framing (trade feed) — names the side and whether price is confirming it."""
    a, d = s["asset"], (s.get("direction") or "")
    nums = "; ".join(s.get("numbers") or [])
    det = s["detector"]
    early = earliness(s)
    tag = ("price hasn't moved yet — early" if early >= 0.8
           else ("price already ran — late" if early >= 0.45 else "price is going against it"))
    if det == "sm_divergence":
        return f"{_smart_lead(s)} {d} vs the crowd on {a}, {tag}."
    if det == "sm_flow":
        return (f"Proven wallets are OPENING and ADDING {d} size on {a} ({tag}) — {nums}.")
    if det == "sm_positioning_build":
        return f"Top-trader positioning is shifting {d} on {a} ({tag}) — {nums}."
    if det == "sm_conviction":
        return f"{nums} on {a} ({tag}) — a conviction shift to weigh."
    if det == "oi_surge":
        return (f"Positioning building on {a}" + (f" {d}" if d else "")
                + " with price quiet — a coil; watch for the break.")
    if det == "whale_move":
        return f"A proven wallet is adding {d} size on {a} — size following conviction."
    if det == "funding_flip":
        return f"Funding just flipped on {a} — the carry regime changed; the paid side moved."
    if det == "funding_extreme":
        return f"{a}: extreme funding — a carry read (collect the paid side), not a directional call."
    if det == "cross_asset_laggard":
        return f"{a} is the rotation laggard — a catch-up watch."
    return f"{a}: {'; '.join(s.get('numbers') or [])}."


def rank(signals, score_key, min_score, top_n, family_cap):
    """Sort by a score, drop below the floor, cap per detector FAMILY, one entry per (asset,family) so
    near-duplicate angles collapse to the strongest — and a 2nd, DIFFERENT-family angle on an
    already-shown asset (real corroboration) only if it's strong."""
    kept, per_asset_fams, per_family = [], {}, {}
    for s in sorted(signals, key=lambda s: -(s.get(score_key) or 0)):
        if (s.get(score_key) or 0) < min_score:
            continue
        fam = FAMILY.get(s["detector"], s["detector"])
        if per_family.get(fam, 0) >= family_cap:
            continue
        fams = per_asset_fams.setdefault(s["asset"], set())
        if fam in fams:
            continue                       # same asset+family already taken (e.g. divergence + conviction)
        if fams and (s.get(score_key) or 0) < 60:
            continue                       # a 2nd, different-family angle on one asset only if strong
        per_family[fam] = per_family.get(fam, 0) + 1
        fams.add(fam)
        kept.append(s)
        if len(kept) >= top_n:
            break
    return kept


def _pick_baseline(ring, now, target_min=None):
    """The most-recent snapshot that is still at least `target_min` old (so a 3-min re-run diffs
    against ~1h ago, and the trend detector against ~12h ago). If everything is younger, the OLDEST
    available; empty ring → None (first run, no diff)."""
    target_min = DIFF_TARGET_MIN if target_min is None else target_min
    dated = [(s, _parse_ts(s.get("ts"))) for s in (ring or [])]
    dated = [(s, t) for s, t in dated if t is not None]
    if not dated:
        return None
    old_enough = [(s, t) for s, t in dated if (now - t).total_seconds() / 60.0 >= target_min]
    if old_enough:
        return max(old_enough, key=lambda x: x[1])[0]   # most-recent snapshot that's still ≥ target old
    return min(dated, key=lambda x: x[1])[0]             # else the oldest we have


def make_slow_lookup(ring, now):
    """Per-asset finder for the trend baseline: the snapshot closest to TREND_LOOKBACK_MIN old that is
    at least TREND_MIN_AGE_MIN old, holds a reading for THIS asset, and carries the SAME
    `smart_source`. Returns (metrics, age_minutes) or ({}, None).

    Per-asset and per-source rather than one global baseline because the ring is heterogeneous: an
    asset may be newly listed, and a sweep may or may not have run the proven-cohort engine. Diffing
    across a source switch would manufacture a trend from a change of instrument, not of positioning.
    """
    dated = []
    for snap in ring or []:
        ts = _parse_ts(snap.get("ts"))
        if ts is None:
            continue
        dated.append(((now - ts).total_seconds() / 60.0, snap.get("asset_metrics") or {}))

    def lookup(asset, src):
        best, best_gap = None, None
        for age, metrics in dated:
            if age < TREND_MIN_AGE_MIN:
                continue
            m = metrics.get(asset)
            if not isinstance(m, dict) or _num(m.get("smart_share")) is None:
                continue
            msrc = m.get("smart_source") if m.get("smart_source") in SMART_SOURCE_LABEL else None
            if msrc != src:
                continue
            gap = abs(age - TREND_LOOKBACK_MIN)      # closest to the intended ~12h arm
            if best_gap is None or gap < best_gap:
                best, best_gap = (m, age), gap
        return best if best else ({}, None)

    return lookup


def _prune_ring(ring, now):
    out = []
    for s in ring or []:
        t = _parse_ts(s.get("ts"))
        if t is not None and (now - t).total_seconds() / 60.0 <= SNAP_MAX_AGE_MIN:
            out.append(s)
    return out[-SNAP_RING_MAX:]


def _prune_surfaced(surfaced, now):
    out = {}
    for k, v in (surfaced or {}).items():
        t = _parse_ts(v)
        if t is not None and (now - t).total_seconds() / 60.0 <= FRESH_WINDOW_MIN * 2:
            out[k] = v
    return out


def _render_md(now, social, trade, lens, cov=None):
    """Two badged, ranked feeds. Badges (🔥/🟠/🟡), ⭐ top-of-feed, ⚑ named-wallet — keep them."""
    ts = now.isoformat()[:16]
    out = [f"# 🔭 Senpi Signals — {ts} UTC", "",
           "_Observation, not advice. Every number is from a live read this run — verify before posting._", ""]
    cov = cov or {}
    if cov.get("smart_money_lens") == "NO DATA" and cov.get("flow_lens") == "NO DATA":
        out += ["> ⛔ **Smart-money lens UNAVAILABLE this run — no cohort positioning was supplied.**",
                "> Everything below is OI, funding and price: the same inputs as market-pulse. The",
                "> divergence and flow detectors did not find nothing — they were never fed. Run the",
                "> proven-cohort fan-out (`senpi-smart-money`) and re-run before reading this as a",
                "> smart-money report.", ""]
    elif "thin" in (cov.get("smart_money_lens"), cov.get("flow_lens")):
        out += [f"> ⚠️ **Partial smart-money coverage** — divergence inputs on "
                f"{cov.get('smart_divergence_inputs_pct')}% of the universe, base-unit flow on "
                f"{cov.get('base_flow_inputs_pct')}%. Absence of a signal on an uncovered name means "
                f"nothing was measured, not that nothing is happening.", ""]
    if lens in ("both", "trade") and trade:
        out += ["## Tradeable dislocations — for building ideas", ""]
        for i, s in enumerate(trade):
            star = "⭐ " if i == 0 else ""
            out.append(f"{star}{badge(s['trade_score'])} **{s['trade_score']}** · `{s['asset']}` — "
                       f"{s['detector']} · {s.get('credibility', 0):.2f} cred"
                       + ("  ⚑" if s.get("concrete_entity") else ""))
            out.append(f"  {trade_read(s)}")
            out.append(f"  _{'; '.join(s.get('numbers') or [])}_")
            out.append("")
    if lens in ("both", "social") and social:
        out += ["## Market news — for content", ""]
        for i, s in enumerate(social):
            star = "⭐ " if i == 0 else ""
            out.append(f"{star}{badge(s['social_score'])} **{s['social_score']}** · `{s['asset']}` — "
                       f"{s['detector']}" + ("  ⚑" if s.get("concrete_entity") else ""))
            out.append(f"  {frame(s)}")
            out.append("")
    if not social and not trade:
        out += ["_Nothing notable stands out right now — a quiet read is a correct answer._", ""]
    return "\n".join(out)


def _default_state_path():
    """A DURABLE path co-located with the Senpi RUNTIME's own state, so it survives across chats AND
    redeploys exactly as runtimes do — never a per-chat scratchpad or /tmp, where the diff engine
    silently resets every chat. Mirrors the runtime's resolver (senpi-trading-runtime
    resolveSenpiBaseStateDir): $SENPI_SIGNALS_STATE (explicit file) › $SENPI_STATE_DIR/signals/state.json
    › ~/.openclaw/senpi-state/signals/state.json. In the hyperclaw container the claw already exports
    SENPI_STATE_DIR=/data/.openclaw/senpi-state — the Railway persistent volume mounted at /data — so
    this lands beside installed_runtimes.json on durable storage with no configuration."""
    f = os.environ.get("SENPI_SIGNALS_STATE")
    if f:
        return f
    base = os.environ.get("SENPI_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".openclaw", "senpi-state")
    return os.path.join(base, "signals", "state.json")


def _read_state(path):
    """(ring, surfaced_by) from the state file, migrating older shapes. The snapshot ring is SHARED
    across consumers (one market baseline for everyone); surfaced_by = {consumer: {asset|detector: ts}}
    keeps each consumer's anti-repeat memory separate."""
    try:
        st = json.load(open(path)) or {}
    except Exception:
        return [], {}
    if isinstance(st.get("snapshots"), list):
        ring = st["snapshots"]
    elif st.get("asset_metrics"):                       # oldest shape: a single bare snapshot
        ring = [{"ts": st.get("ts"), "asset_metrics": st["asset_metrics"]}]
    else:
        ring = []
    sb = st.get("surfaced_by")
    if not isinstance(sb, dict):                        # migrate a non-empty flat `surfaced` → adhoc bucket
        flat = st.get("surfaced")
        sb = {"adhoc": flat} if isinstance(flat, dict) and flat else {}
    return ring, sb


def _commit_state(path, cur_metrics, now, consumer, social_picks):
    """Locked read-merge-write: re-read under an exclusive lock and MERGE this snapshot into whatever
    ring is current, so a concurrent cron/user run never clobbers the other's baseline; update only
    THIS consumer's freshness map; prune; write. Best-effort — never sinks the run."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        lock = open(path + ".lock", "w")
        try:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            ring, sb = _read_state(path)                 # re-read INSIDE the lock
            by_ts = {s.get("ts"): s for s in ring if s.get("ts")}
            by_ts[now.isoformat()] = {"ts": now.isoformat(), "asset_metrics": cur_metrics}
            ring = _prune_ring(sorted(by_ts.values(), key=lambda s: s.get("ts") or ""), now)
            seen = dict(sb.get(consumer) or {})
            for s in social_picks:                       # only the social feed drives anti-repeat
                seen[f"{s['asset']}|{s['detector']}"] = now.isoformat()
            sb[consumer] = _prune_surfaced(seen, now)
            json.dump({"ts": now.isoformat(), "snapshots": ring, "surfaced_by": sb}, open(path, "w"))
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    except Exception as e:  # noqa — a state-write failure must not fail the read
        print(f"[warn] state commit failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="current signals JSON (asset_metrics + events)")
    ap.add_argument("--state", default=None,
                    help="state file (default: durable ~/.senpi/signals/state.json — survives across chats)")
    ap.add_argument("--consumer", default="adhoc",
                    help="freshness namespace: 'adhoc' for a user's on-demand run, e.g. 'social' for the "
                         "content cron. The market baseline (ring) is shared; only anti-repeat is per-consumer.")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--now", default=None, help="ISO timestamp (default: now UTC)")
    ap.add_argument("--out", default="signals.md")
    ap.add_argument("--lens", choices=["both", "trade", "social"], default="both")
    ap.add_argument("--snapshot-only", action="store_true",
                    help="record this reading into the ring and exit — no detection, no ranking, no "
                         "output feed, and freshness is NOT touched. The cheap keep-the-history-warm "
                         "job: sm_positioning_build needs a ~12h-old partner snapshot to diff against.")
    a = ap.parse_args()
    state_path = a.state or _default_state_path()

    data = json.load(open(a.input))
    cur_metrics = data.get("asset_metrics") or {}
    events = [e for e in (normalize_event(e) for e in (data.get("events") or [])) if e]
    now = _parse_ts(a.now) or datetime.datetime.now(datetime.timezone.utc)

    # shared snapshot ring + THIS consumer's freshness memory (migrates older state shapes)
    ring, surfaced_by = _read_state(state_path)
    surfaced = surfaced_by.get(a.consumer, {})

    if a.snapshot_only:
        # Warm the ring and stop. Passing no social picks leaves the freshness map untouched, so a
        # frequent snapshot job never burns the anti-repeat budget the content run depends on.
        _commit_state(state_path, cur_metrics, now, a.consumer, [])
        ring_after, _ = _read_state(state_path)
        oldest = min((s.get("ts") for s in ring_after if s.get("ts")), default=None)
        span_h = None
        if oldest is not None and _parse_ts(oldest) is not None:
            span_h = round((now - _parse_ts(oldest)).total_seconds() / 3600.0, 1)
        print(json.dumps({"snapshot_only": True, "ts": now.isoformat(), "assets": len(cur_metrics),
                          "snapshots": len(ring_after), "history_span_hours": span_h,
                          "trend_ready": bool(span_h is not None and span_h >= TREND_LOOKBACK_MIN / 60.0)},
                         indent=2))
        print(f"[snapshot · {len(cur_metrics)} assets · ring {len(ring_after)} deep · "
              f"{span_h}h history · state {state_path}]", file=sys.stderr)
        return

    baseline = _pick_baseline(ring, now)                                  # fast (~1h) — OI/funding/conviction
    prior = (baseline or {}).get("asset_metrics", {})
    # slow (~12h) arm for the trend detector — resolved per asset AND per source (see make_slow_lookup)
    slow_lookup = make_slow_lookup(ring, now)
    signals = detect_from_metrics(cur_metrics, prior, slow_lookup) + events
    # observability: the best comparable arm we could find for any asset this run
    ages = [a for a in (slow_lookup(k, (v.get("smart_source")
                                        if isinstance(v, dict) else None))[1]
                        for k, v in cur_metrics.items() if isinstance(v, dict)) if a is not None]
    slow_age_min = max(ages) if ages else None
    slow = _pick_baseline(ring, now, TREND_LOOKBACK_MIN) or baseline
    for s in signals:
        # two independent reasons to discount a reading: a thin book, and an unverified/momentum source
        cred = round(credibility(s.get("notional_vol")) * float(s.get("source_trust", 1.0)), 3)
        s["credibility"] = cred
        s["freshness"] = freshness(s["asset"], s["detector"], surfaced, now)
        s["social_score"] = social_score(s, cred, s["freshness"])
        s["trade_score"] = trade_score(s, cred)

    # drop markets too thin to trust the read at all
    live = [s for s in signals if (_num(s.get("notional_vol")) or CRED_FLOOR_VOL) >= CRED_FLOOR_VOL]
    tradeable = [s for s in live if (_num(s.get("notional_vol")) or TRADE_CRED_FLOOR) >= TRADE_CRED_FLOOR]

    social = rank(live, "social_score", MIN_SOCIAL, a.top, FAMILY_CAP)
    trade = rank(tradeable, "trade_score", MIN_TRADE, a.top, FAMILY_CAP)

    # advance state: locked read-merge-write into the SHARED ring + THIS consumer's freshness map
    _commit_state(state_path, cur_metrics, now, a.consumer, social)

    cov = coverage(cur_metrics)
    open(a.out, "w").write(_render_md(now, social, trade, a.lens, cov))
    print(json.dumps({"generated": now.isoformat(),
                      "diff_baseline_ts": (baseline or {}).get("ts"),
                      "trend_baseline_ts": (slow or {}).get("ts"),
                      "trend_baseline_age_hours": (round(slow_age_min / 60.0, 1)
                                                   if slow_age_min is not None else None),
                      "trend_ready": bool(slow_age_min is not None and slow_age_min >= TREND_MIN_AGE_MIN),
                      "coverage": cov,
                      "trade": trade, "social": social}, indent=2))
    if cov["smart_money_lens"] == "NO DATA" and cov["flow_lens"] == "NO DATA":
        print("[warn] NO cohort positioning supplied — the smart-money and flow detectors could not "
              "fire. This run is OI/funding/price only (i.e. market-pulse). Do not report it as a "
              "smart-money read.", file=sys.stderr)
    print(f"[wrote {a.out} · trade {len(trade)} · social {len(social)} · baseline "
          f"{(baseline or {}).get('ts', 'none')} · consumer {a.consumer} · state {state_path}]", file=sys.stderr)


if __name__ == "__main__":
    main()
