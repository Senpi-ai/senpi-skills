"""KOALA — supervised scanner (Runtime 3.0 port of the v2 KOALA Set-and-Forget HODL).

The simplest scanner in the catalog. Single asset (default BTC). There is NO
scoring, NO multi-timeframe analysis, NO smart-money gate — the entire decision
is a tiny state machine over a persisted entry-history record (the pure
`scoring.should_enter` / `record_entry` / `record_exit`). Per tick it:
  - reads account state + held positions (dual-DEX equity via max(), never sum()),
  - detects a closed position (had a first_entry but the asset is no longer held
    and no exit was recorded -> record the exit),
  - if the asset is held -> HOLD (the DSL owns exits),
  - else asks the state machine `should_enter` (fire-once vs re-entry-cooldown),
  - emits ONE LONG signal at fixed margin/leverage when eligible, then records
    the entry into ctx.state so the next tick stays silent.

Read-only + single-pass. Emits a `marginPct` intent (PERCENT, fixed) plus a
fixed `leverage` (clamped to MAX_LEVERAGE); the runtime sizes the dollars, owns
cooldowns/risk gates, and trails the DSL exit. No daemon, no push_signal, no
create_position.

FIDELITY NOTES vs the v2 producer (koala-producer.py producer VERSION 1.0.1,
file-header label "v1.0.0"; the header and the VERSION constant disagree — the
VERSION constant 1.0.1 is cited as authoritative):
  - The v2 producer persisted TWO files: koala-state.json (first_entry_at /
    last_entry_at / last_exit_at / total_entries) AND recent-signals.json
    (race-window dedup). Both collapse into ctx.state here: each tick appends
    {"signaled":..., "result":..., "koala":<state>}; the next tick reads the
    latest record back. Semantics are identical (TTL, 4x-TTL prune, fire-once
    one-shot, re-entry cooldown, exit detection).
  - v2 DEFAULT_MARGIN_PCT was 0.50 (a FRACTION) * account_value -> marginUsd.
    This port carries marginPct=50 (a PERCENT) in runtime.yaml and emits a
    top-level `marginPct`; the runtime sizes (marginPct/100)*withdrawable. A
    defensive guard converts a value <= 1 (an operator who pasted the v2
    fraction) to a PERCENT (*100) and logs it. FLAGGED below.
  - v2 leverage default 2, hard-capped at MAX_LEVERAGE=3 (Koala is HODL, not
    gambling). Preserved verbatim (min(leverage, maxLeverage)).
  - v2 emitted exactly one signal (or none). Preserved: scan() emits <= 1.
  - v2 `push_signal` skipped if the asset was already in held_assets; here the
    held check happens earlier (held -> HOLD branch) AND the signal carries
    heldAssets for the rule action's belt-and-braces.
  - The v2 LLM entry gate was an explicit pass-through ("honor the signal",
    always confidence 7). The Runtime 3.0 action is decision_mode: rule — the
    scan IS the decision, so the pass-through LLM step is correctly dropped.
  - v2 score in data{} was a fixed 5 to satisfy the schema (Koala has no
    scoring). Preserved verbatim (data.score = 5).
"""

import sys
import time

import scoring

# v2 defaults (koala-producer.py / koala-config.json / koala_config.py)
_DEFAULT_ASSET = "BTC"
_DEFAULT_FIRE_ONCE = True
_DEFAULT_RE_ENTRY_COOLDOWN_HOURS = 168.0   # v2 DEFAULT_RE_ENTRY_COOLDOWN_HOURS (7d)
_DEFAULT_MARGIN_PCT = 50.0                 # PERCENT (v2 fraction 0.50 -> 50)
_DEFAULT_LEVERAGE = 2                      # v2 DEFAULT_LEVERAGE
_DEFAULT_MAX_LEVERAGE = 3                  # v2 MAX_LEVERAGE (hardcoded)
_DEFAULT_TTL = 240                         # v2 RECENT_SIGNAL_TTL_SEC (race-window dedup)
_FIXED_SCORE = 5                           # v2 data.score — fixed (Koala has no scoring)


def _dex_for(asset, inputs):
    dex = inputs.get("dex")
    if dex is not None:
        return dex
    return "xyz" if asset.lower().startswith("xyz:") else ""


def _get_account(ctx):
    """(account_value, [held_coin_strings]) from strategy_get_clearinghouse_state.

    READ-GUARDED — a read error must never roll back the whole tick (degrade to
    (0.0, []) so the runtime's own slot/cooldown gates stay the backstop).
    Dual-DEX equity collapse: account_value via max() across main/xyz (two views
    of ONE cross-margined wallet — summing double-counts the shared free balance
    -> 2x sizing). Ported verbatim from v2 cfg.get_positions, including the
    read-sanity guard (margin in use + empty positions -> skip tick)."""
    if not ctx.wallet:
        return 0.0, []
    try:
        ch = ctx.senpi_mcp.call_tool("strategy_get_clearinghouse_state",
                                     {"strategy_wallet": ctx.wallet})
    except Exception as exc:  # noqa: BLE001 — read error must not roll back the tick
        print(f"[koala.scan] clearinghouse read failed (degrade): {exc!r}", file=sys.stderr)
        return 0.0, []
    if not ch:
        return 0.0, []
    data = ch.get("data", ch) if isinstance(ch, dict) else ch
    if not isinstance(data, dict):
        return 0.0, []

    held, account_value = [], 0.0
    for section in ("main", "xyz"):
        s = data.get(section, {})
        if not isinstance(s, dict):
            continue
        ms = s.get("marginSummary", {}) or {}
        account_value = max(account_value, scoring._f(ms.get("accountValue", 0)))
        for ap in s.get("assetPositions", []) or []:
            pos = ap.get("position", ap) if isinstance(ap, dict) else {}
            szi = scoring._f(pos.get("szi", 0))
            if szi == 0:
                continue
            coin = pos.get("coin", "")
            if coin:
                held.append(coin)

    # read-sanity guard (funding/$0 glitch 2026-06, ported verbatim from v2): a
    # corrupt clearinghouse read can report margin/notional IN USE while returning
    # an EMPTY positions list; sizing or running the held-asset dedup off that
    # re-enters held names (pyramiding) and mis-sizes. Skip the tick.
    _use = 0.0
    for _sec in ("main", "xyz"):
        _s = data.get(_sec, {}) if isinstance(data, dict) else {}
        _ms = _s.get("marginSummary", {}) if isinstance(_s, dict) else {}
        _use = max(_use, scoring._f(_ms.get("totalMarginUsed", 0)), abs(scoring._f(_ms.get("totalNtlPos", 0))))
    if _use > 1.0 and not held:
        print("[koala.scan] read-sanity guard: margin in use but empty positions — skipping tick",
              file=sys.stderr)
        return 0.0, []
    return account_value, held


# ── ctx.state: koala state machine + recent-signal dedup ──

def _load_state(ctx):
    """Latest persisted record -> (koala_state, signaled_map)."""
    if ctx.state is None or len(ctx.state) == 0:
        return scoring.empty_state(), {}
    last = ctx.state.last() or {}
    koala = last.get("koala")
    koala = dict(koala) if isinstance(koala, dict) else scoring.empty_state()
    sig = last.get("signaled", {})
    signaled = dict(sig) if isinstance(sig, dict) else {}
    return koala, signaled


def scan(inputs, ctx):
    now = time.time()
    asset = str(inputs.get("asset", _DEFAULT_ASSET) or _DEFAULT_ASSET)   # PRESERVE case for emit (kPEPE/xyz:*)
    asset_key = asset.upper()   # UPPER — held-membership + signaled-dedup join key (symmetric, matches held_set/scoring.was_recently_signaled)
    fire_once = bool(inputs.get("fireOnceMode", _DEFAULT_FIRE_ONCE))
    cooldown_hours = float(inputs.get("reEntryCooldownHours", _DEFAULT_RE_ENTRY_COOLDOWN_HOURS))
    leverage = int(inputs.get("leverage", _DEFAULT_LEVERAGE))
    max_leverage = int(inputs.get("maxLeverage", _DEFAULT_MAX_LEVERAGE))
    ttl = float(inputs.get("recentSignalTtlSeconds", _DEFAULT_TTL))

    # marginPct is a PERCENT in (0,100]. FLAGGED: defensively convert a value <= 1
    # (an operator who pasted the v2 FRACTION 0.50) into a PERCENT so it never
    # silently sizes ~100x small (resolve-margin sizes (marginPct/100)*withdrawable).
    margin_pct = float(inputs.get("marginPct", _DEFAULT_MARGIN_PCT))
    if margin_pct <= 1.0:
        print(f"[koala.scan] marginPct={margin_pct} looks like a v2 fraction; "
              f"converting to PERCENT ({margin_pct * 100})", file=sys.stderr)
        margin_pct = margin_pct * 100.0

    # leverage clamp: min(leverage, maxLeverage) — v2 MAX_LEVERAGE cap (HODL, not gambling)
    leverage = min(leverage, max_leverage)

    koala, signaled = _load_state(ctx)
    signaled = scoring.prune_signaled(signaled, ttl, now)

    account_value, held = _get_account(ctx)
    held_set = {h.upper() for h in held}

    out = []
    result = None

    if account_value <= 0:
        # No account value (or read degraded / read-sanity guard tripped) -> hold.
        result = {"ts": now, "asset": asset, "emitted": False, "note": "no_account_value"}
        print(f"[koala.scan] {asset} WAITING — no account value (read degraded or zero equity)",
              file=sys.stderr)
    else:
        # Detect a closed position: had a first_entry but the asset is no longer
        # held and no exit was recorded -> log the exit (verbatim v2 main()).
        if koala.get("first_entry_at") and asset_key not in held_set and koala.get("last_exit_at") is None:
            koala = scoring.record_exit(koala, now)

        if asset_key in held_set:
            # Currently held -> do nothing (DSL is in charge).
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "holding",
                      "first_entry_at": koala.get("first_entry_at"),
                      "total_entries": koala.get("total_entries", 0)}
            print(f"[koala.scan] {asset} HOLDING — DSL owns exits "
                  f"(entries={koala.get('total_entries', 0)})", file=sys.stderr)
        elif not scoring.should_enter(koala, fire_once, cooldown_hours, now):
            # Not eligible — surface the reason for ops visibility (verbatim v2 reasons).
            if fire_once:
                reason = f"fire-once AND already entered (first_entry_at={koala.get('first_entry_at')})"
            else:
                last_exit = koala.get("last_exit_at")
                if last_exit is None:
                    reason = "re-entry mode but no exit recorded yet (position state ambiguous)"
                else:
                    wait_left_h = max(0.0, (cooldown_hours * 3600 - (now - float(last_exit))) / 3600.0)
                    reason = f"re-entry cooldown active ({wait_left_h:.1f}h remaining)"
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "not_eligible",
                      "note": reason, "fire_once": fire_once}
            print(f"[koala.scan] {asset} WAITING — {reason}", file=sys.stderr)
        elif scoring.was_recently_signaled(signaled, asset_key, ttl, now):
            # Race-window dedup (defence-in-depth alongside the runtime cooldown gate).
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "recently_signaled"}
            print(f"[koala.scan] {asset} WAITING — recently signaled (race-window dedup)",
                  file=sys.stderr)
        elif leverage <= 0 or margin_pct <= 0:
            result = {"ts": now, "asset": asset, "emitted": False, "gate": "sizing_unresolved",
                      "leverage": leverage, "marginPct": margin_pct}
            print(f"[koala.scan] {asset} WAITING — sizing unresolved lev={leverage} marginPct={margin_pct}",
                  file=sys.stderr)
        else:
            # ── EMIT: one LONG, fixed sizing. Record the entry into state. ──
            signaled[asset_key] = now
            koala = scoring.record_entry(koala, now)
            koala["last_exit_at"] = None     # new lifecycle started (verbatim v2)
            result = {"ts": now, "asset": asset, "emitted": True, "gate": "pass",
                      "direction": "LONG", "leverage": leverage, "marginPct": round(margin_pct, 4),
                      "first_entry_at": koala.get("first_entry_at"),
                      "total_entries": koala.get("total_entries")}
            print(f"[koala.scan] {asset} EMIT: LONG {leverage}x marginPct={margin_pct:.2f}% "
                  f"(entry #{koala.get('total_entries')}, hodl_first_entry)", file=sys.stderr)
            out = [{
                "asset": asset,
                "direction": "LONG",
                "marginPct": margin_pct,        # PERCENT in (0,100] — runtime sizes (marginPct/100)*withdrawable
                "leverage": leverage,           # 2x (clamped to maxLeverage 3); runtime applies it
                "data": {
                    "score": _FIXED_SCORE,      # fixed — Koala has no scoring (verbatim v2)
                    "leverage": float(leverage),
                    "direction": "LONG",
                    "reasons": ["hodl_first_entry"],
                    "heldAssets": held,
                    "firstEntryAt": scoring._f(koala.get("first_entry_at")),
                    "totalEntries": float(koala.get("total_entries", 0)),
                },
            }]

    # ── persist koala state + dedup map + this tick's result EVERY tick; bounded
    #    by state_history_max_count. Read back via ctx.state.last(). ──
    if ctx.state is not None:
        try:
            ctx.state.append({"koala": koala, "signaled": signaled, "result": result})
        except Exception as exc:  # noqa: BLE001
            print(f"[koala.scan] WARNING: state append failed; next tick may re-emit "
                  f"a suppressed signal: {exc!r}", file=sys.stderr)
    return out
