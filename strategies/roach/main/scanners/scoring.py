"""ROACH — pure thesis math (no I/O, no MCP, no clock-as-arg-free-state).

A faithful Runtime 3.0 port of the v2 Roach producer's Striker-detection logic
(`detect_striker_signals` + helpers, roach-producer.py v3.0.0). The math/indexing
is reproduced VERBATIM so a fidelity harness can diff this against the v2 producer
on the same scan snapshot + history. Behaviour-preserving quirks from v2 are kept
and flagged `# v2-quirk`; fix them only as a separate, labelled change AFTER the
port is validated.

UNIVERSE scanner: the caller (scan.py) fetches the top-50 smart-money leaderboard
markets (XYZ banned), builds the current scan snapshot, hands in the prior-scan
history (from ctx.state), runs `detect_striker_signals`, and — for each surviving
candidate — does the per-asset volume confirmation read and the final volume gate.

Two pure pieces here:
  - `parse_scan(raw_markets, now_iso, top_n)` — normalize raw leaderboard markets
    into a scan snapshot (XYZ filtered).
  - `detect_striker_signals(current_scan, history, now_hour)` — the verbatim v1.2
    Striker detector EXCEPT the volume gate (which needs an MCP read). It returns
    candidates that have passed every gate UP TO volume; scan.py applies the volume
    gate last (exactly the order in v2 detect_striker_signals).

`now_hour` (UTC hour 0-23) is passed in so the time-of-day modifier stays a pure
function (no datetime.now inside scoring)."""


# ── STRIKER CONSTANTS (HARDCODED — fleet-tuned, not operator-set) ──
# Verbatim from roach-producer.py v3.0.0.
TOP_N = 50
ERRATIC_REVERSAL_THRESHOLD = 5
MIN_SCORE = 10                  # v1.1: tightened from 9
MIN_REASONS = 4
MIN_RANK_JUMP = 15
MIN_VELOCITY_OVERRIDE = 15
MIN_VELOCITY_FLOOR = 15         # v1.1: tightened from 10
MIN_VOL_RATIO = 1.5             # v1.1: loosened from 2.0
MIN_CC_15M = 0.5                # v1.2: tightened from 0
MIN_PRICE_1H_ALIGN_PCT = 0.1    # v1.2


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── SCAN SNAPSHOT (port of v2 parse_scan) ──

def parse_scan(raw_markets, now_iso, top_n=TOP_N):
    """Parse raw leaderboard markets into a scan snapshot.
    HARDCODED: xyz: assets filtered at scan level — never enter the signal pipeline.
    Verbatim from v2 parse_scan (now-string passed in to keep this pure)."""
    scan = {"time": now_iso, "markets": []}
    for i, m in enumerate((raw_markets or [])[:top_n]):
        if not isinstance(m, dict):
            continue
        token = m.get("token", "")
        dex = m.get("dex", "")
        # XYZ ban (Fox data: SNDK -$57, GOLD -$8, CRCL -$9, MU -$7)
        if dex and str(dex).lower() == "xyz":
            continue
        if str(token).lower().startswith("xyz:"):
            continue
        scan["markets"].append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": m.get("direction", ""),
            "contribution": round(_f(m.get("pct_of_top_traders_gain", 0)), 6),
            "traders": m.get("trader_count", 0),
            "price_chg_4h": round(_f(m.get("token_price_change_pct_4h", 0)) or 0, 4),
            "price_chg_1h": round(_f(m.get("token_price_change_pct_1h",
                                          m.get("price_change_1h", 0))) or 0, 4),
            "cc_15m": _f(m.get("contribution_pct_change_15m", 0) or 0),
        })
    return scan


def get_market_in_scan(scan, token, dex=""):
    """Verbatim from v2 get_market_in_scan."""
    for m in scan["markets"]:
        if m["token"] == token and m.get("dex", "") == dex:
            return m
    return None


# ── HELPERS (verbatim from v2) ──

def is_erratic_history(rank_history, exclude_last=False):
    """Detect zigzag rank patterns. Verbatim from v2 (kept for fidelity; v2 never
    calls it from detect_striker_signals, so it is dead in both v2 and this port —
    preserved verbatim, FLAGGED as unused-in-v2)."""
    nums = [r for r in rank_history if r is not None]
    if exclude_last and len(nums) > 1:
        nums = nums[:-1]
    if len(nums) < 3:
        return False
    for i in range(1, len(nums) - 1):
        prev_delta = nums[i] - nums[i - 1]
        next_delta = nums[i + 1] - nums[i]
        if prev_delta < 0 and next_delta > ERRATIC_REVERSAL_THRESHOLD:
            return True
        if prev_delta > 0 and next_delta < -ERRATIC_REVERSAL_THRESHOLD:
            return True
    return False


def time_of_day_modifier(now_hour):
    """UTC time-of-day scoring adjustment. Verbatim from v2 (UTC hour passed in
    to keep this pure)."""
    hour = now_hour
    if 4 <= hour < 14:
        return 1, "time_bonus_optimal_window"
    elif hour >= 18 or hour < 2:
        return -2, "time_penalty_chop_zone"
    return 0, None


def check_4h_alignment(direction, price_chg_4h):
    """4H trend must agree with signal direction. Hard block. Verbatim from v2."""
    if direction == "LONG" and price_chg_4h < 0:
        return False
    if direction == "SHORT" and price_chg_4h > 0:
        return False
    return True


# ── STRIKER DETECTION (v1.2 logic, verbatim EXCEPT the trailing volume gate) ──

def detect_striker_signals(current_scan, history, now_hour):
    """Detect violent FIRST_JUMP / IMMEDIATE_MOVER signals.

    Logic preserved verbatim from roach-producer.py v3.0.0 `detect_striker_signals`
    EXCEPT the final volume-confirmation gate (`check_asset_volume`), which requires
    an MCP read (`market_get_asset_data`) and therefore cannot live in this pure
    module. scan.py applies that gate immediately after, in the SAME position in the
    pipeline, so emit semantics are identical.

    Returns a list of candidate dicts that have passed every gate up to (but not
    including) volume confirmation. Each candidate carries the `reasons` list as
    built so far; scan.py appends `VOL_CONFIRMED ...` after the volume read, exactly
    as v2 did."""
    prev_scans = history.get("scans", [])
    if not prev_scans:
        return []

    latest_prev = prev_scans[-1]
    oldest_available = prev_scans[-min(len(prev_scans), 5)]

    prev_top50_tokens = set()
    for m in latest_prev["markets"]:
        prev_top50_tokens.add((m["token"], m.get("dex", "")))

    signals = []

    for market in current_scan["markets"]:
        token = market["token"]
        dex = market.get("dex", "")
        current_rank = market["rank"]
        direction = str(market["direction"]).upper()
        current_contrib = market["contribution"]

        # Destination ceiling: reject if in top 10
        if current_rank <= 10:
            continue

        # 4H trend alignment (hard block)
        if not check_4h_alignment(direction, market.get("price_chg_4h", 0)):
            continue

        prev_market = get_market_in_scan(latest_prev, token, dex)
        old_market = get_market_in_scan(oldest_available, token, dex)

        if not prev_market:
            continue

        rank_jump = prev_market["rank"] - current_rank

        # ── FIRST_JUMP detection ──
        is_first_jump = False
        is_immediate = False
        is_contrib_explosion = False
        reasons = []

        if rank_jump >= 10 and prev_market["rank"] >= 25:
            is_immediate = True
            reasons.append(f"IMMEDIATE_MOVER +{rank_jump} from #{prev_market['rank']}")

            was_in_prev = (token, dex) in prev_top50_tokens
            if not was_in_prev or prev_market["rank"] >= 30:
                is_first_jump = True
                reasons.append(f"FIRST_JUMP #{prev_market['rank']}->#{current_rank}")

        # Contribution explosion
        if prev_market["contribution"] > 0:
            contrib_ratio = current_contrib / prev_market["contribution"]
            if contrib_ratio >= 3.0:
                is_contrib_explosion = True
                reasons.append(f"CONTRIB_EXPLOSION {contrib_ratio:.1f}x")

        if not is_first_jump and not is_immediate:
            continue

        # Velocity computation
        contrib_velocity = 0
        recent_contribs = []
        for scan in prev_scans[-5:]:
            m = get_market_in_scan(scan, token, dex)
            if m:
                recent_contribs.append(m["contribution"])
        recent_contribs.append(current_contrib)
        if len(recent_contribs) >= 2:
            deltas = [recent_contribs[i + 1] - recent_contribs[i]
                      for i in range(len(recent_contribs) - 1)]
            contrib_velocity = sum(deltas) / len(deltas) * 100

        abs_velocity = abs(contrib_velocity)

        if rank_jump < MIN_RANK_JUMP and abs_velocity < MIN_VELOCITY_OVERRIDE:
            continue

        # Velocity floor
        if abs_velocity < MIN_VELOCITY_FLOOR:
            if is_first_jump and contrib_velocity > 0:
                pass
            else:
                continue

        # ── SCORING ──
        score = 0
        if is_first_jump:
            score += 3
        if is_immediate:
            score += 2
        if is_contrib_explosion:
            score += 2
        if abs_velocity > 10:
            score += 2
            reasons.append(f"HIGH_VELOCITY {abs_velocity:.1f}")

        if prev_market["rank"] >= 40:
            score += 1
            reasons.append("DEEP_CLIMBER")

        if old_market:
            total_climb = old_market["rank"] - current_rank
            if total_climb >= 10:
                score += 1
                reasons.append(f"CLIMBING +{total_climb} over scans")

        tod_mod, tod_reason = time_of_day_modifier(now_hour)
        score += tod_mod
        if tod_reason:
            reasons.append(tod_reason)

        if score < MIN_SCORE or len(reasons) < MIN_REASONS:
            continue

        # 15m velocity freshness gate
        cc_15m = _f(market.get("cc_15m", 0))
        if cc_15m < MIN_CC_15M:
            continue

        # 1h price confirmation of direction
        p1h = _f(market.get("price_chg_1h", 0))
        if direction == "LONG" and p1h < MIN_PRICE_1H_ALIGN_PCT:
            continue
        if direction == "SHORT" and p1h > -MIN_PRICE_1H_ALIGN_PCT:
            continue
        reasons.append(f"1H_CONFIRMS {p1h:+.2f}%")

        # NOTE: the volume-confirmation gate (check_asset_volume / vol >= 1.5x) is
        # applied by scan.py immediately after this, via an MCP read. Candidates
        # returned here have NOT yet passed volume; scan.py drops the ones that
        # fail and appends "VOL_CONFIRMED {ratio}x" to those that pass — verbatim
        # position in the v2 pipeline.

        # Build canonical asset id (no XYZ — already filtered out)
        asset_id = token

        signals.append({
            "asset": asset_id,
            "token": token,
            "dex": dex if dex else None,
            "direction": direction,
            "mode": "STRIKER",
            "score": score,
            "reasons": reasons,
            "currentRank": current_rank,
            "prevRank": prev_market["rank"],
            "rankJump": rank_jump,
            "isFirstJump": is_first_jump,
            "isContribExplosion": is_contrib_explosion,
            "contribVelocity": round(contrib_velocity, 4),
            "contribution": round(current_contrib * 100, 3),
            "traders": market["traders"],
            "priceChg4h": market.get("price_chg_4h", 0),
            "priceChg1h": p1h,
            "cc15m": cc_15m,
        })

    return signals
