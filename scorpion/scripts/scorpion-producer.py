#!/usr/bin/env python3
# Senpi SCORPION Producer v5.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""SCORPION v5.0.0 Producer — Multi-market signal emitter, senpi_runtime_helpers migration.

v4.1.2 (2026-05-05) — post-close cooldown backstop (Cheetah v6.0 pattern):
  Live evidence 2026-05-05 ZEC sequence (4 round trips in ~2.5h):
    #1: 3:12 → 3:23 — held 11m, +$19.62
    #2: 3:23 → 3:49 — opened 37s after #1 close, +$7.95
    #3: 3:50 → 4:04 — opened 63s after #2 close, +$44.65
    #4: 4:04 → 5:40 — opened 47s after #3 close, -$8.34
  Net realized: +$63.88 (3 wins, 1 loss). Three sub-1-minute re-entries
  paid out, the fourth chased a top and gave back. Pattern is fee-positive
  but tail-risk-positive even when the trend is real, AND it's the same
  pathway that produced v4.1.0's 154-emission ZEC runaway (just
  serialized instead of concurrent).

  v4.1.1 held-asset dedup blocks CONCURRENT duplicates but resets the
  moment a position closes — leaving SERIAL re-entry open. Three
  defense layers all silent on serial:
    (1) Runtime per_asset_cooldown_minutes: 120 — known broken,
        acknowledged in v4.1.1 comments. No fix landed yet.
    (2) Producer held_assets check — works for concurrent only.
    (3) LLM recentEntryCountThisAsset >=2 — counter feeds from
        held_assets; also resets after close.

  v4.1.2 ports Cheetah v6.0's pattern: producer diffs held_assets
  between scan ticks; anything that disappeared just closed → record
  timestamp → block emission for POST_CLOSE_COOLDOWN_MINUTES (10) on
  that asset. Two persistent state files (last_closed.json +
  previously_held.json) in state dir alongside the producer.lock fcntl
  guard. recent_entry_count() also returns 99 if asset is in cooldown
  (LLM-side backstop layer).

  Calibration: 10 min, deliberately short. Today's four re-entries were
  all <2 min gap; 10 min is well above that. 30-min default (Cheetah's
  number) would have killed today's three winning re-entries and cost
  +$44.26 in this session. 10 min blocks the diagnostic-clear pathology
  (sub-1-min = re-firing on stale state, not new conviction) while
  letting score-11 signals 15+ min after close go through. The runtime
  per_asset_cooldown 120 stays in place as policy intent.

v4.1.1 (2026-05-01) — held-asset dedup hot-patch (P0):
  After v4.1.0 ferry, Scorpion accumulated 154+ create_position calls
  in 2.5h all firing ZEC LONG at score 11. Three layers of defense
  silently failed simultaneously:
    (1) LLM prompt's HARD SKIP CONDITIONS list missed "held_assets
        contains this asset → SKIP" — only mentioned correlation risk.
    (2) Runtime per_asset_cooldown_minutes: 120 was not enforcing
        (separate Erik investigation).
    (3) recent_entry_count stub returned 0, making the >=2 over-traded
        rule mathematically unreachable.
  Each LLM-approved create_position became an HL "add to existing
  position" — the live ZEC LONG accumulated to $1,484 notional /
  $742 margin (essentially maxed out, $44 withdrawable).

v4.1.1 fixes ALL THREE LAYERS defensively:
  (A) Producer skips emission if signal asset is already held (the
      strongest fix — never even sends the signal to the LLM)
  (B) recent_entry_count returns 99 if asset is held — makes the
      LLM's existing >=2 rule trip
  (C) LLM prompt updated with explicit held-asset hard skip
  Defense in depth — any one of these would prevent the runaway. With
  all three, only a coordinated three-way silent failure could leak.

v4.1.0 (2026-04-30) — XYZ-fixed:
  - Asymmetric MIN_SCORE: crypto raised 9 → 11 (score-9 crypto bled
    -$10.07 across 11 trades in v4.0.2 sample); XYZ held at 9 (no
    accept data yet — let the new LLM rubric prove out)
  - btcMacroDirection set to NOT_APPLICABLE for XYZ signals (v4.0
    was injecting irrelevant crypto-macro context into oil/brent
    decisions; LLM was citing BTC macro to skip XYZ trades)
  - xyzPeerMomentum field added — counts other XYZ assets trending
    same direction; macro tailwind signal for the LLM rubric

v4.0 history:

v3.x was a full-agency scanner: scored signals, counted daily entries,
managed asset cooldowns + scalp re-entry windows, called create_position.
The scalp-reentry bypass meant MAX_DAILY_ENTRIES=3 silently leaked to
43 fills / 18h in Week 5.

v4.0 flips to pure producer:
  - Scan crypto + XYZ markets via leaderboard_get_markets
  - Apply the multi-factor score gate (MIN_SCORE >= 9)
  - Enrich with BTC macro + funding regime + current-position context
  - Push to the runtime via `SenpiClient.push_signal()`

The runtime handles everything else:
  - LLM gate (decision_mode: llm) filters each signal
  - risk.guard_rails enforces max_entries_per_day, per_asset_cooldown,
    drawdown_halt — no Python counters that can drift
  - DSL manages exits (maker-preferred)

NO execution code. NO trade counters. NO cooldown state. NO scalp
re-entry logic. The runtime owns all of that.

v5.0.0 (2026-05-08) plumbing migration: senpi_runtime_helpers in-process
wrapper replaces openclaw-CLI subprocess + mcporter subprocess.
Also fixes a v2.0.9 contamination-rule violation: SCORPION wallet was
read from the BANNED generic STRATEGY_ADDRESS env var. Now reads from
per-agent SCORPION_WALLET env var (with backward-compat fallback to
STRATEGY_ADDRESS for one cycle, with deprecation warning).

Environment:
  SCORPION_WALLET   — Scorpion wallet (REQUIRED; per-agent per v2.0.9 rule)
  SENPI_AUTH_TOKEN  — Bearer token for MCP + signal POST (REQUIRED)
  SCORPION_DECISION_MODEL — bare LLM model name (no provider prefix)
  SENPI_MCP_URL     — optional, default https://mcp.prod.senpi.ai/mcp
  SENPI_RUNTIME_API_HOST — optional, default 127.0.0.1
  SENPI_RUNTIME_API_PORT — optional, default 8787
  OPENCLAW_WORKSPACE — optional, default /data/workspace

  STRATEGY_ADDRESS is BANNED (v2.0.9 contamination rule). If set,
  v5.0.0 emits a deprecation warning and uses it as fallback if
  SCORPION_WALLET is not set. Operators should migrate to
  SCORPION_WALLET ASAP.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorpion_config as cfg

from senpi_runtime_helpers import SenpiClientError, producer_daemon  # type: ignore  # noqa: E402


# Reentrancy guard removed in v5.0.0. producer_daemon owns the
# per-tick scanner_lock with stale-PID auto-recovery via os.kill(pid, 0).
# Do NOT add an inner scanner_lock(...) inside main() — fcntl flock is
# not reentrant; nested call raises BlockingIOError every tick.

_LOCK_DIR = Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "scorpion-tracker" / "state"
_LOCK_DIR.mkdir(parents=True, exist_ok=True)

# v4.1.2 — post-close cooldown state (Cheetah v6.0 pattern).
# Backstop for the runtime per_asset_cooldown_minutes silent-enforcement
# bug. Producer diffs held_assets between ticks; anything that
# disappeared from current_held vs previously_held just closed.
_LAST_CLOSED_FILE = _LOCK_DIR / "last_closed.json"
_PREV_HELD_FILE = _LOCK_DIR / "previously_held.json"


VERSION = "5.0.0"

# Hardcoded — must match runtime.yaml external_scanner.name.
SCANNER_NAME = "scorpion_signals"

# Signal type passed explicitly to push_signal() per Rachin's review
# of Cheetah PR #209.
SIGNAL_TYPE = "SCORPION_TREND_FOLLOW"


def _resolve_wallet():
    """Resolve Scorpion wallet — per-agent SCORPION_WALLET env var,
    with backward-compat fallback to STRATEGY_ADDRESS (BANNED, emits
    deprecation warning) and then to scorpion-config.json."""
    env_val = (os.environ.get("SCORPION_WALLET") or "").strip()
    if env_val:
        return env_val
    legacy = (os.environ.get("STRATEGY_ADDRESS") or "").strip()
    if legacy:
        sys.stderr.write(
            "[scorpion v5.0.0] DEPRECATION: STRATEGY_ADDRESS env var is BANNED "
            "per Turbine v2.0.9 contamination rule. Falling back to it for "
            "compatibility. Migrate to SCORPION_WALLET ASAP.\n"
        )
        return legacy
    try:
        return (cfg.load_config().get("wallet") or "").strip()
    except Exception:
        return ""


STRATEGY_ADDRESS = _resolve_wallet()


# ═══════════════════════════════════════════════════════════════
# UNIVERSE + SCORING CONFIG (preserved from v3.2)
# ═══════════════════════════════════════════════════════════════

CRYPTO_ASSETS = {
    "BTC", "ETH", "SOL", "HYPE", "ZEC", "LIT", "GRASS", "FARTCOIN",
    "TAO", "ONDO", "SUI", "ARB", "WLD", "DOGE", "AVAX",
}
XYZ_ASSETS = {"CL", "BRENTOIL", "GOLD", "SPX"}

# v4.1.0 — asymmetric score floor by asset class.
# Crypto at score 9 demonstrably bleeds in the v4.0.2 sample
# (11 trades, -$10.07 realized, all weak_peak / dead_weight exits).
# XYZ had zero acceptances in the sample because the LLM rubric
# was gating it out — we have no data on whether XYZ at score 9
# wins or loses. Keep XYZ permissive until we collect that data.
MIN_SCORE_CRYPTO = 11   # raised from 9 — score-9 crypto loses
MIN_SCORE_XYZ    = 9    # held — no data yet; let the new rubric prove out

# 4H price-alignment thresholds — XYZ moves less than crypto
MIN_4H_ALIGNED_PCT_CRYPTO = 1.0
MIN_4H_ALIGNED_PCT_XYZ = 0.5

# v4.1.2 — post-close cooldown (Cheetah v6.0 pattern, calibrated short).
#
# 2026-05-05 ZEC sequence (4 round trips in ~2.5h, $456→$474→$482→$517):
#   #1: 3:12 → 3:23 — held 11m, +$19.62
#   #2: 3:23 → 3:49 — opened 37s after #1 close, +$7.95
#   #3: 3:50 → 4:04 — opened 1m 3s after #2 close, +$44.65
#   #4: 4:04 → 5:40 — opened 47s after #3 close, -$8.34
# Net ZEC: +$63.88 realized. Three sub-1-minute re-entries paid out;
# the fourth chased a top and gave back a small loss. Pattern is
# fee-positive and tail-risk-positive even when the trend is real.
#
# v4.1.0 ran the SAME pathway differently — 154 ZEC LONG emissions in
# 2.5h all firing because held-asset check was missing. v4.1.1 fixed
# concurrent dedup. v4.1.2 fixes the SERIAL re-entry pathway (sub-1-min
# emit-after-close) without strangling legitimate trend follow-ups.
#
# Calibration: 10 min. Today's four re-entries were all <2 min gap;
# 10 min is well above that. A 30-min default (Cheetah's pattern) would
# have killed today's three winning re-entries and cost +$44.26. 10 min
# blocks the diagnostic-clear pathology (sub-1-min = re-firing on stale
# state, not new conviction) while letting score-11 signals 15+ min
# after close go through.
#
# The runtime per_asset_cooldown_minutes: 120 in runtime.yaml remains
# the policy intent (a slow-grade safety net); this 10-min is the
# producer-side fast backstop until Erik's runtime fix lands.
POST_CLOSE_COOLDOWN_MINUTES = 10


def safe_float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_xyz(token):
    return token in XYZ_ASSETS


def coin_label(token, is_xyz_asset):
    return f"xyz:{token}" if is_xyz_asset else token


# ═══════════════════════════════════════════════════════════════
# SIGNAL SCORING (preserved from v3.2 — unchanged logic)
# ═══════════════════════════════════════════════════════════════

def score_market(m):
    """Score a single market candidate. Returns dict or None if below threshold."""
    token = str(m.get("token", "")).upper()
    dex = str(m.get("dex", "")).lower()

    is_xyz_asset = (dex == "xyz" and token in XYZ_ASSETS)
    is_crypto_asset = (dex != "xyz" and token in CRYPTO_ASSETS)
    if not is_xyz_asset and not is_crypto_asset:
        return None

    sm_direction = str(m.get("direction", "")).upper()
    if sm_direction not in ("LONG", "SHORT"):
        return None

    pct = safe_float(m.get("pct_of_top_traders_gain", 0))
    traders = int(m.get("trader_count", 0))
    p4h = safe_float(m.get("token_price_change_pct_4h", 0))
    p1h = safe_float(m.get("token_price_change_pct_1h",
                           m.get("price_change_1h", 0)))
    cc_15m = safe_float(m.get("contribution_pct_change_15m", 0))
    cc_1h = safe_float(m.get("contribution_pct_change_1h", 0))
    cc_4h = safe_float(m.get("contribution_pct_change_4h", 0))

    if traders < 5:
        return None

    # 4H price alignment gate — SM direction must match price trend
    min_4h = MIN_4H_ALIGNED_PCT_XYZ if is_xyz_asset else MIN_4H_ALIGNED_PCT_CRYPTO
    price_aligned = (sm_direction == "LONG" and p4h >= min_4h) or \
                    (sm_direction == "SHORT" and p4h <= -min_4h)
    if not price_aligned:
        return None

    score = 0
    reasons = []

    # SM concentration (0-3)
    if pct >= 15:
        score += 3; reasons.append(f"DOMINANT_SM {pct:.1f}% ({traders}t)")
    elif pct >= 10:
        score += 2; reasons.append(f"STRONG_SM {pct:.1f}% ({traders}t)")
    elif pct >= 5:
        score += 1; reasons.append(f"SM_PRESENT {pct:.1f}% ({traders}t)")

    # 4H price alignment (0-3)
    big_move = 3.0 if is_xyz_asset else 5.0
    med_move = 1.5 if is_xyz_asset else 3.0
    if abs(p4h) >= big_move:
        score += 3; reasons.append(f"STRONG_TREND {p4h:+.1f}%")
    elif abs(p4h) >= med_move:
        score += 2; reasons.append(f"TREND {p4h:+.1f}%")
    elif abs(p4h) >= min_4h:
        score += 1; reasons.append(f"ALIGNED {p4h:+.1f}%")

    # 15m SM velocity (0-2, penalty -1 on fade)
    if cc_15m > 1.0:
        score += 2; reasons.append(f"15M_SM_BUILDING {cc_15m:+.2f}")
    elif cc_15m > 0.3:
        score += 1; reasons.append(f"15M_SM_FRESH {cc_15m:+.2f}")
    elif cc_15m < -0.5:
        score -= 1; reasons.append(f"15M_SM_FADING {cc_15m:+.2f}")

    # 1H acceleration
    if sm_direction == "LONG" and p1h > 0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")
    elif sm_direction == "SHORT" and p1h < -0.5:
        score += 1; reasons.append(f"1H_ACCEL {p1h:+.2f}%")

    # Trader depth
    if traders >= 50:
        score += 1; reasons.append(f"DEEP_SM ({traders}t)")

    # 4H contribution shift
    if abs(cc_4h) >= 5.0:
        score += 1; reasons.append(f"4H_CONVICTION {cc_4h:+.1f}")

    min_score = MIN_SCORE_XYZ if is_xyz_asset else MIN_SCORE_CRYPTO
    if score < min_score:
        return None

    reasons.insert(0, f"TREND_FOLLOW {coin_label(token, is_xyz_asset)} {sm_direction}")

    return {
        "asset": coin_label(token, is_xyz_asset),
        "token": token,
        "is_xyz": is_xyz_asset,
        "direction": sm_direction,
        "score": score,
        "reasons": reasons,
        "sm_pct": pct,
        "sm_traders": traders,
        "p4h": p4h,
        "p1h": p1h,
        "cc_15m": cc_15m,
        "cc_1h": cc_1h,
        "cc_4h": cc_4h,
    }


# ═══════════════════════════════════════════════════════════════
# CONTEXT ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def fetch_btc_macro():
    try:
        ad = cfg.mcporter_call(
            "market_get_asset_data",
            asset="BTC",
            candle_intervals=["1h"],
            include_funding=False,
            include_order_book=False,
        )
        if not ad:
            return {"direction": None, "pct": None}
        data = ad.get("data", ad)
        candles_1h = (data.get("candles", {}) or {}).get("1h", [])
        if len(candles_1h) < 24:
            return {"direction": None, "pct": None}
        opens = [float(c.get("open") or c.get("o") or 0) for c in candles_1h[-24:]]
        closes = [float(c.get("close") or c.get("c") or 0) for c in candles_1h[-24:]]
        if opens[0] <= 0:
            return {"direction": None, "pct": None}
        pct = (closes[-1] - opens[0]) / opens[0] * 100
        return {"direction": "UP" if pct > 0 else "DOWN", "pct": round(pct, 2)}
    except Exception:
        return {"direction": None, "pct": None}


def fetch_funding_regime():
    try:
        fr = cfg.mcporter_call("market_get_funding_regime")
        if fr:
            data = fr.get("data", fr)
            if isinstance(data, dict):
                return data.get("regime")
    except Exception:
        pass
    return None


def fetch_held_assets():
    """Read current positions from the strategy wallet. Returns list of asset labels."""
    wallet = STRATEGY_ADDRESS or os.environ.get("WALLET_ADDRESS", "")
    if not wallet:
        return []
    try:
        ch = cfg.mcporter_call("strategy_get_clearinghouse_state",
                                strategy_wallet=wallet)
        if not ch:
            return []
        data = ch.get("data", ch)
        held = []
        for section in ("main", "xyz"):
            s = data.get(section, {})
            if not isinstance(s, dict):
                continue
            for ap in s.get("assetPositions", []):
                pos = ap.get("position", ap)
                szi = float(pos.get("szi", 0) or 0)
                if szi == 0:
                    continue
                coin = pos.get("coin", "")
                if coin:
                    held.append(coin)
        return held
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# v4.1.2 — POST-CLOSE COOLDOWN STATE (Cheetah v6.0 pattern)
# ═══════════════════════════════════════════════════════════════

def _atomic_write_json(path, data):
    """Best-effort atomic write: tmp file + rename. State files only;
    if a crash mid-write loses the file, post-close cooldown is a
    backstop and we'll just miss one tick of cooldown enforcement."""
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def load_last_closed():
    """{coin: {closedTimestamp: float, closedAt: iso}}"""
    try:
        with open(_LAST_CLOSED_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_last_closed(d):
    _atomic_write_json(_LAST_CLOSED_FILE, d)


def load_previously_held():
    try:
        with open(_PREV_HELD_FILE) as f:
            data = json.load(f)
        return set(data.get("assets", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_previously_held(held):
    _atomic_write_json(_PREV_HELD_FILE, {
        "assets": sorted(list(held)),
        "updatedAt": cfg.now_iso(),
    })


def detect_closes(current_held_set, previously_held_set):
    """Diff held_assets across ticks. Anything that disappeared just
    closed — record close timestamp into last-closed.json. Returns the
    set of newly-closed coins (uppercase)."""
    closed = previously_held_set - current_held_set
    if not closed:
        return closed
    last = load_last_closed()
    now_ts = time.time()
    now_iso = cfg.now_iso()
    for asset in closed:
        last[asset] = {"closedTimestamp": now_ts, "closedAt": now_iso}
    save_last_closed(last)
    return closed


def is_in_post_close_cooldown(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    rec = last.get(token.upper())
    if not rec:
        return False
    last_close = rec.get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    return elapsed_min < cooldown_minutes


def post_close_cooldown_remaining_min(token, cooldown_minutes=POST_CLOSE_COOLDOWN_MINUTES):
    last = load_last_closed()
    rec = last.get(token.upper())
    if not rec:
        return 0
    last_close = rec.get("closedTimestamp", 0)
    elapsed_min = (time.time() - last_close) / 60
    remaining = cooldown_minutes - elapsed_min
    return round(max(0.0, remaining), 1)


def recent_entry_count(asset, held_assets=None):
    """v4.1.1: return 99 if asset is in held_assets so the LLM's
    'recentEntryCountThisAsset >= 2 within 24h' rule trips on duplicates.

    The runtime per_asset_cooldown was supposed to backstop this but isn't
    enforcing reliably (separate Erik investigation post-v4.1.0 runaway).
    Tripping the LLM's existing rule is a defense-in-depth backstop;
    the producer-level held_assets skip in main() is the primary fix.

    v4.1.2: Also returns 99 if asset is in post-close cooldown — same
    rationale as held_assets. This makes the LLM's >=2 rule trip on
    just-closed scalp-re-entry attempts (third defense layer).
    """
    if held_assets:
        held_norm = {h.upper() for h in held_assets}
        if asset.upper() in held_norm:
            return 99
        # XYZ-prefixed signals: also check token portion (e.g. xyz:BRENTOIL)
        if ":" in asset:
            token = asset.split(":", 1)[1].upper()
            if token in held_norm:
                return 99
    # v4.1.2: post-close cooldown backstop layer
    asset_check = asset.upper()
    if ":" in asset:
        asset_check = asset.split(":", 1)[1].upper()
    if is_in_post_close_cooldown(asset_check):
        return 99
    return 0


# ═══════════════════════════════════════════════════════════════
# v4.1.0 — XYZ ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def build_macro_context(candidate, btc_macro):
    """BTC macro is structurally irrelevant to XYZ assets — don't anchor
    the LLM on it. v4.0 was injecting btcMacroDirection into XYZ signal
    payloads; the LLM then cited BTC macro as a reason to skip oil/brent
    trades. v4.1 sets NOT_APPLICABLE for XYZ so the rubric ignores it."""
    if candidate["is_xyz"]:
        return {"direction": "NOT_APPLICABLE", "pct": 0.0}
    return {
        "direction": btc_macro["direction"] or "UNKNOWN",
        "pct": btc_macro["pct"] or 0,
    }


def compute_xyz_peer_momentum(candidates):
    """For each XYZ direction, count peer XYZ assets trending the same
    way in the same scan. Oil + Brent + SPX up together = macro
    tailwind; single-asset XYZ move on noise = no peer support.
    Returns dict: {(token, direction): peer_count}."""
    xyz_signals = [c for c in candidates if c["is_xyz"]]
    result = {}
    for c in xyz_signals:
        peers = sum(
            1 for p in xyz_signals
            if p["token"] != c["token"] and p["direction"] == c["direction"]
        )
        result[(c["token"], c["direction"])] = peers
    return result


# ═══════════════════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════════════════

def push_signal(candidate, btc_macro, funding_regime, held_assets, xyz_peer_count=0):
    """Push a signal payload to the runtime via senpi_runtime_helpers.

    Direct HTTP POST to runtime API on 127.0.0.1; no subprocess.
    asset/direction/signal_type at top-level kwargs (Rachin's PR #209).
    Score normalized 0..1 for SignalItem.score (Scorpion's composite
    0-20 scaled), with raw int score in `data` for telemetry.
    """
    if not STRATEGY_ADDRESS:
        print("ERROR: SCORPION_WALLET env var not set (or banned STRATEGY_ADDRESS fallback also empty)", file=sys.stderr)
        return False

    macro_ctx = build_macro_context(candidate, btc_macro)

    data_block = {
        "score": candidate["score"],
        "isXyz": candidate["is_xyz"],
        "reasons": candidate["reasons"],
        "smPct": candidate["sm_pct"],
        "smTraders": candidate["sm_traders"],
        "priceChange4hPct": candidate["p4h"],
        "priceChange1hPct": candidate["p1h"],
        "contribChange15m": candidate["cc_15m"],
        "contribChange1h": candidate["cc_1h"],
        "contribChange4h": candidate["cc_4h"],
        "btcMacroDirection": macro_ctx["direction"],
        "btcMacro24hPct": macro_ctx["pct"],
        "fundingRegime": funding_regime or "UNKNOWN",
        "heldAssets": held_assets,
        "recentEntryCountThisAsset": recent_entry_count(candidate["asset"], held_assets),
        "xyzPeerMomentum": xyz_peer_count,
    }

    try:
        cfg._wrapper_client.push_signal(
            address=STRATEGY_ADDRESS,
            scanner=SCANNER_NAME,
            asset=candidate["asset"],
            direction=candidate["direction"],
            score=candidate["score"] / 20.0,
            signal_type=SIGNAL_TYPE,
            data=data_block,
        )
        return True
    except SenpiClientError as e:
        print(f"INGEST_REJECTED {candidate['asset']}: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — transport / protocol surface
        print(f"INGEST_EXCEPTION {candidate['asset']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Single tick. NO inner scanner_lock — daemon owns it."""
    run_start = time.time()

    raw = cfg.mcporter_call("leaderboard_get_markets", limit=100)
    if not raw:
        print(json.dumps({"status": "no_markets", "_scorpion_producer_version": VERSION}))
        return

    markets = raw.get("data", raw)
    if isinstance(markets, dict):
        markets = markets.get("markets", markets)
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        print(json.dumps({"status": "bad_shape", "_scorpion_producer_version": VERSION}))
        return

    # Score all markets; keep only those at/above MIN_SCORE
    candidates = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        c = score_market(m)
        if c is not None:
            candidates.append(c)

    if not candidates:
        elapsed = time.time() - run_start
        print(json.dumps({
            "status": "ok",
            "scanned": len(markets),
            "candidates": 0,
            "elapsed_sec": round(elapsed, 2),
            "_scorpion_producer_version": VERSION,
        }))
        return

    # Enrich once per run (shared context for all candidates)
    btc_macro = fetch_btc_macro()
    funding_regime = fetch_funding_regime()
    held_assets = fetch_held_assets()

    # v4.1.2: detect-closes via held-set diff (Cheetah v6.0 pattern).
    # Anything that disappeared from current_held vs previously_held
    # closed since the last scan tick → record timestamp for
    # post-close cooldown enforcement below. Persist current held
    # set for the next tick's diff.
    current_held_set = {h.upper() for h in held_assets}
    previously_held_set = load_previously_held()
    closed_this_tick = detect_closes(current_held_set, previously_held_set)
    save_previously_held(current_held_set)

    # v4.1.1: PRIMARY held-asset dedup fix. Skip emission entirely
    # if the candidate's asset is already in held_assets. This is
    # the strongest layer of the three-layer fix — a candidate that
    # never reaches the LLM can't possibly trigger a duplicate add.
    held_norm = {h.upper() for h in held_assets}

    def _asset_already_held(candidate):
        asset = candidate["asset"]
        if asset.upper() in held_norm:
            return True
        # XYZ-prefixed: also check token portion (e.g. xyz:BRENTOIL → BRENTOIL)
        if ":" in asset:
            token = asset.split(":", 1)[1].upper()
            if token in held_norm:
                return True
        return False

    def _asset_in_post_close_cooldown(candidate):
        """v4.1.2: backstop for the runtime per_asset_cooldown silent
        enforcement bug. Block emission if asset closed within the
        POST_CLOSE_COOLDOWN_MINUTES window."""
        asset = candidate["asset"]
        check = asset.upper()
        if ":" in asset:
            check = asset.split(":", 1)[1].upper()
        return is_in_post_close_cooldown(check)

    # Push all qualifying candidates. Runtime's LLM gate + risk guard
    # rails will decide which (if any) to execute.
    candidates.sort(key=lambda c: c["score"], reverse=True)
    xyz_peer_map = compute_xyz_peer_momentum(candidates)
    pushed = 0
    skipped_held = 0
    skipped_post_close = 0
    post_close_skips = []
    for c in candidates:
        if _asset_already_held(c):
            skipped_held += 1
            continue
        if _asset_in_post_close_cooldown(c):
            skipped_post_close += 1
            check = c["token"].upper() if ":" not in c["asset"] else c["asset"].split(":", 1)[1].upper()
            post_close_skips.append({
                "asset": c["asset"],
                "remaining_min": post_close_cooldown_remaining_min(check),
            })
            continue
        peer_count = xyz_peer_map.get((c["token"], c["direction"]), 0) if c["is_xyz"] else 0
        if push_signal(c, btc_macro, funding_regime, held_assets, peer_count):
            pushed += 1

    elapsed = time.time() - run_start
    warn = "WARN_OVER_60S" if elapsed > 60 else None
    print(json.dumps({
        "status": "ok",
        "scanned": len(markets),
        "candidates": len(candidates),
        "signals_pushed": pushed,
        "skipped_held_assets": skipped_held,
        "skipped_post_close": skipped_post_close,
        "post_close_skips": post_close_skips,
        "closed_this_tick": sorted(list(closed_this_tick)),
        "btc_macro": btc_macro,
        "funding_regime": funding_regime,
        "held_assets": held_assets,
        "elapsed_sec": round(elapsed, 2),
        "warn": warn,
        "_scorpion_producer_version": VERSION,
    }))


if __name__ == "__main__":
    # v5.0.0 — long-lived daemon. Replaces openclaw cron + agentTurn.
    # producer_daemon owns the per-tick scanner_lock with stale-PID
    # auto-recovery.
    _wallet_lock_id = (
        hashlib.sha256(STRATEGY_ADDRESS.lower().encode()).hexdigest()[:12]
        if STRATEGY_ADDRESS
        else "unset"
    )
    producer_daemon(
        fn=main,
        interval_seconds=60,           # Scorpion's v4.x cron was every 60s
        name=f"scorpion-producer-{_wallet_lock_id}",
        wallet=STRATEGY_ADDRESS,
        scanner=SCANNER_NAME,
        tick_timeout=120,
    )
