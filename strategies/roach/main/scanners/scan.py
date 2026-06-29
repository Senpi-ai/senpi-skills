"""ROACH — supervised scanner (Runtime 3.0 port of the v2 Roach Striker producer).

UNIVERSE scanner, Striker-ONLY (Stalker permanently disabled since v1.0). Per tick it:
  - fetches the top-50 smart-money leaderboard markets (XYZ banned at parse level),
  - builds the current scan snapshot and reads the prior-scan history from ctx.state,
  - runs the pure `scoring.detect_striker_signals` (FIRST_JUMP / IMMEDIATE_MOVER +
    rank-jump / contribution-velocity, 4h-trend hard block, score >= 10 w/ 4+ reasons,
    cc_15m >= 0.5 freshness, 1h price alignment),
  - applies the VOLUME-confirmation gate last (per-asset market_get_asset_data read,
    vol >= 1.5x of trailing 6h avg) — the exact position it held in v2,
  - drops candidates still in the producer-side 120min per-asset cooldown (ctx.state),
  - sorts highest-score-first and emits EVERY surviving candidate (v2 main() pushed
    all of them; the runtime's `slots`/per-asset cooldown/risk gates own the ceiling),
  - sized at a fixed margin PERCENT intent + fixed leverage; the runtime sizes dollars,
    owns cooldowns/risk gates, and trails the DSL exit.

Read-only + single-pass — no daemon, no while-loop, no sleep, no push_signal, no
create_position. The v2 producer's wallet-isolated JSON state files (scan-history,
asset-cooldowns, last-emitted) collapse into ctx.state records here (the runtime owns
per-wallet isolation + transactional rollback).

FIDELITY NOTES vs roach-producer.py v3.0.0:
  - SIZING: v2 emitted `marginUsd: 250` (a flat DOLLAR amount, ROACH_MARGIN_USD) plus
    `leverage: 7` inside data{}. The v2 runtime.yaml's strategy.margin_pct was 25 and
    config.json describes "$1k budget x 2 slots x 25% margin" — so $250 IS 25% of the
    intended budget. Per the Runtime 3.0 sizing contract this port emits `marginPct: 25`
    (PERCENT in (0,100]) + `leverage: 7` at the TOP level; the runtime sizes
    (marginPct/100)*withdrawable, which scales with any budget instead of pinning a
    literal $250. The dollar amount at the design budget is identical. The defensive
    "<=1.0 means a pasted fraction -> x100" guard is applied to the input.
  - VOLUME GATE is an MCP read (market_get_asset_data) and therefore lives in scan.py,
    not the pure scoring.py. It is applied in the IDENTICAL pipeline position as v2's
    `check_asset_volume` call inside detect_striker_signals (after the 1h-confirm gate,
    before the candidate is finalized). The `VOL_CONFIRMED {ratio}x` reason and the
    volRatio data field are produced here, verbatim.
  - EMIT-ALL: v2 main() sorted candidates by score desc and pushed ALL of them (each
    gated by the producer-side cooldown). This port preserves that (no top-1 cap) and
    lets the runtime slots:2 / per-asset cooldown / max_entries_per_day own the ceiling.
  - DROPPED v2 order-lifecycle: v2 had no cancel_order / resting-order purge in the
    producer (signal-only already), so nothing to drop on that axis. v2's
    push_signal()/SenpiClient HTTP POST is replaced by returning signal dicts.
  - STATE: v2 scan-history.json (MAX_HISTORY_SCANS=60) + asset-cooldowns.json collapse
    into one ctx.state record per tick: {scans:[...], cooldowns:{TOKEN: ts}, result:{}}.
  - `is_erratic_history` is preserved verbatim in scoring.py but, as in v2, is never
    called by the detector — dead in both. FLAGGED in the report.
"""

import sys
import time
from datetime import datetime, timezone

import scoring

# v2 producer constants (hardcoded fleet-tuned; only sizing + cadence-adjacent
# knobs are exposed via inputs).
_DEFAULT_TOP_N = 50                 # v2 TOP_N
_DEFAULT_MAX_HISTORY_SCANS = 60     # v2 MAX_HISTORY_SCANS
_DEFAULT_ASSET_COOLDOWN_SEC = 7200  # v2 ASSET_COOLDOWN_MINUTES=120 -> 7200s
_DEFAULT_LEVERAGE = 7               # v2 DEFAULT_LEVERAGE (ROACH_LEVERAGE)
_DEFAULT_MARGIN_PCT = 25.0          # v2 marginUsd $250 == 25% of design budget (see header)
_DEFAULT_MIN_VOL_RATIO = scoring.MIN_VOL_RATIO  # 1.5x


def _read(ctx, name, args):
    """Guarded MCP read: a transient/permission error on a read must NOT roll back
    the whole tick. Returns None on failure so the existing degrade paths apply
    (markets empty -> persist history-only, emit nothing; volume read empty -> the
    candidate fails the volume gate and is dropped, exactly as v2)."""
    try:
        return ctx.senpi_mcp.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[roach.scan] {name} read failed: {exc!r}", file=sys.stderr)
        return None


# ── MARKET FETCH (port of v2 fetch_markets) ──

def _fetch_markets(ctx, limit):
    """v2 fetch_markets: leaderboard_get_markets(limit=100). Returns raw market
    list or None (None -> v2 returned status:error 'failed to fetch markets')."""
    raw = _read(ctx, "leaderboard_get_markets", {"limit": limit})
    if not raw:
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    markets = data.get("markets", data) if isinstance(data, dict) else data
    if isinstance(markets, dict):
        markets = markets.get("markets", [])
    if not isinstance(markets, list):
        return None
    return markets


# ── VOLUME CONFIRMATION (port of v2 check_asset_volume) ──

def _check_asset_volume(ctx, token, dex, min_vol_ratio):
    """Check if raw asset volume is alive. Returns (ratio, is_strong).
    Verbatim parse from v2 check_asset_volume (1h candles, latest vs trailing-5 avg).
    READ-GUARDED — a failed read returns (0, False) -> candidate dropped, as v2."""
    asset_name = f"{dex}:{token}" if dex else token
    data = _read(ctx, "market_get_asset_data", {
        "asset": asset_name,
        "candle_intervals": ["1h"],
        "include_funding": False,
        "include_order_book": False,
        "dex": dex or "",
    })
    if not data:
        return 0, False

    candle_data = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(candle_data, dict):
        candles = candle_data.get("candles", {}).get("1h", [])
    else:
        return 0, False

    if len(candles) < 6:
        return 0, False

    vols = [scoring._f(c.get("volume", c.get("v", c.get("vlm", 0)))) for c in candles[-6:]]
    avg_vol = sum(vols[:-1]) / len(vols[:-1]) if len(vols) > 1 else 1
    latest_vol = vols[-1] if vols else 0
    ratio = latest_vol / avg_vol if avg_vol > 0 else 0
    return ratio, ratio >= min_vol_ratio


# ── ctx.state: scan-history + per-asset cooldowns (port of v2 JSON state files) ──

def _load_state(ctx):
    """Returns (history_dict, cooldowns_dict) from the latest ctx.state record."""
    if ctx.state is None or len(ctx.state) == 0:
        return {"scans": []}, {}
    last = ctx.state.last() or {}
    scans = last.get("scans") or []
    cooldowns = last.get("cooldowns") or {}
    history = {"scans": list(scans) if isinstance(scans, list) else []}
    return history, dict(cooldowns) if isinstance(cooldowns, dict) else {}


def _is_asset_cooled_down(cooldowns, token, now, cooldown_sec):
    """Producer-side cooldown — emit-suppression. Port of v2 is_asset_cooled_down
    (seconds instead of minutes). Runtime ALSO enforces per_asset_cooldown_seconds
    independently as a second line of defense."""
    entry = cooldowns.get(token)
    if not entry:
        return False
    last_emit_ts = entry.get("emittedTimestamp", 0) if isinstance(entry, dict) else 0
    return (now - last_emit_ts) < cooldown_sec


def scan(inputs, ctx):
    now = time.time()
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_hour = now_dt.hour

    top_n = int(inputs.get("topN", _DEFAULT_TOP_N))
    leaderboard_limit = int(inputs.get("leaderboardLimit", 100))
    max_history = int(inputs.get("maxHistoryScans", _DEFAULT_MAX_HISTORY_SCANS))
    cooldown_sec = float(inputs.get("assetCooldownSeconds", _DEFAULT_ASSET_COOLDOWN_SEC))
    leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    min_vol_ratio = float(inputs.get("minVolRatio", _DEFAULT_MIN_VOL_RATIO))

    # sizing intent: PERCENT in (0,100]. Defensive fraction->percent guard (dire/koala
    # pattern): a value <= 1.0 is almost certainly a pasted fraction (0.25) -> x100.
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        margin_pct *= 100.0

    history, cooldowns = _load_state(ctx)

    def _persist(result):
        if ctx.state is None:
            return
        try:
            ctx.state.append({
                "scans": history.get("scans", [])[-max_history:],
                "cooldowns": cooldowns,
                "result": result,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[roach.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)

    # ── fetch + parse current scan ──
    raw = _fetch_markets(ctx, leaderboard_limit)
    if raw is None:
        # v2: status:error 'failed to fetch markets'. Do NOT append a snapshot or
        # advance history on a failed fetch (rank-jump detection needs clean history).
        print("[roach.scan] failed to fetch leaderboard_get_markets; skip tick",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "gate": "no_markets"})
        return []

    current_scan = scoring.parse_scan(raw, now_iso, top_n)

    # ── detect striker candidates (pure; up to but not including volume gate) ──
    candidates = scoring.detect_striker_signals(current_scan, history, now_hour)

    # ── persist scan history ALWAYS (v2 appends the snapshot even with 0 signals;
    #    rank-jump detection requires history continuity) ──
    history["scans"].append(current_scan)

    # ── VOLUME confirmation gate (MCP read; same pipeline position as v2) ──
    confirmed = []
    for s in candidates:
        vol_ratio, vol_strong = _check_asset_volume(
            ctx, s["token"], s.get("dex") or "", min_vol_ratio)
        if not vol_strong:
            continue
        s["reasons"].append(f"VOL_CONFIRMED {vol_ratio:.1f}x")
        s["volRatio"] = round(vol_ratio, 2)
        confirmed.append(s)

    # ── producer-side per-asset cooldown (defense-in-depth; runtime gate is primary) ──
    confirmed = [s for s in confirmed
                 if not _is_asset_cooled_down(cooldowns, s["token"], now, cooldown_sec)]

    # ── sort highest score first (v2 main()) ──
    confirmed.sort(key=lambda s: s["score"], reverse=True)

    if not confirmed:
        print(f"[roach.scan] WAITING — no Striker explosion; markets={len(current_scan['markets'])} "
              f"scansInHistory={len(history['scans'])} candidates={len(candidates)}",
              file=sys.stderr)
        _persist({"ts": now, "emitted": False, "totalMarkets": len(current_scan["markets"]),
                  "scansInHistory": len(history["scans"]), "candidates": len(candidates)})
        return []

    # ── emit every surviving candidate; mark each emitted asset's cooldown ──
    out = []
    emitted_assets = []
    for s in confirmed:
        cooldowns[s["token"]] = {"emittedTimestamp": now, "setAt": now_iso}
        emitted_assets.append(s["asset"])
        out.append({
            "asset": s["asset"],
            "direction": s["direction"],
            "marginPct": margin_pct,          # PERCENT in (0,100] — runtime sizes the dollars
            "leverage": leverage,             # 7x default; runtime applies + venue-clamps
            "data": {
                "mode": s["mode"],
                "score": s["score"],
                "rankJump": s["rankJump"],
                "currentRank": s["currentRank"],
                "prevRank": s.get("prevRank", 0),
                "contribVelocity": s.get("contribVelocity", 0),
                "volRatio": s.get("volRatio", 0),
                "priceChg4h": s.get("priceChg4h", 0),
                "priceChg1h": s.get("priceChg1h", 0),
                "cc15m": s.get("cc15m", 0),
                "traders": s.get("traders", 0),
                "contribution": s.get("contribution", 0),
                "isFirstJump": bool(s.get("isFirstJump", False)),
                "isContribExplosion": bool(s.get("isContribExplosion", False)),
                "reasons": " | ".join(s.get("reasons", [])),
                "leverage": leverage,
            },
        })

    print(f"[roach.scan] EMIT {len(out)} striker(s): "
          + ", ".join(f"{s['asset']} {s['direction']} score={s['score']} "
                      f"+{s['rankJump']}->#{s['currentRank']}" for s in confirmed[:4]),
          file=sys.stderr)
    _persist({"ts": now, "emitted": True, "totalMarkets": len(current_scan["markets"]),
              "scansInHistory": len(history["scans"]), "candidates": len(candidates),
              "signals": len(out), "assets": emitted_assets})
    return out
