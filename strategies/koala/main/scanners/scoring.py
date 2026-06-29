"""KOALA — pure entry-decision logic (no I/O, no MCP, no clock-owning).

A faithful Runtime 3.0 port of the v2 KOALA producer's pure functions
(koala-producer.py producer VERSION 1.0.1 / SKILL.md v1.0.0). Koala is the
simplest agent in the catalog: ONE asset, fire LONG ONCE on deploy, hold with
an ultra-wide DSL trail. There is NO scoring, NO multi-timeframe analysis, NO
smart-money gate — the "thesis" is a tiny state machine over a persisted
entry-history record. Those pure functions live here so a fidelity harness can
diff them against the v2 producer on the same state snapshot.

`should_enter` / `record_entry` / `record_exit` are reproduced VERBATIM from
koala-producer.py. The dedup helpers (`prune_signaled`, `was_recently_signaled`)
mirror the v2 koala_config recent-signals cache semantics (4x-TTL prune, TTL
membership check) — the only change is that the store is now an in-memory dict
handed in by scan.py (ctx.state) instead of a JSON file.

Single-pass, unit-testable on plain dicts. The caller (scan.py) owns the clock
and passes `now_ts`, so this module stays pure.
"""


# ── value coercion ──

def _f(v, d=0.0):
    if v is None:
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ═══════════════════════════════════════════════════════════════
# Pure entry-decision logic (VERBATIM from koala-producer.py v1.0.1)
# ═══════════════════════════════════════════════════════════════

def should_enter(state, fire_once, re_entry_cooldown_hours, now_ts):
    """Decide whether Koala should emit an entry signal RIGHT NOW.

    - state: koala state record (first_entry_at / last_entry_at / last_exit_at / total_entries)
    - fire_once: True -> only one entry ever (lifetime one-shot)
    - re_entry_cooldown_hours: minimum hours between exit and next entry
                              (only relevant if fire_once is False)
    - now_ts: epoch seconds (current time)

    Returns True if Koala should emit; False otherwise.
    """
    first_entry_at = state.get("first_entry_at") if state else None

    if fire_once:
        # Lifetime one-shot — fire if and only if we've never entered.
        return first_entry_at is None

    # Re-entry allowed. Two conditions for "should fire":
    #   1. Never entered -> fire immediately.
    #   2. Last exit happened >= cooldown ago.
    if first_entry_at is None:
        return True

    last_exit_at = state.get("last_exit_at") if state else None
    if last_exit_at is None:
        # We've entered before but never recorded an exit -> position must
        # still be held (or the close went undetected). Don't fire again.
        return False

    try:
        last_exit = float(last_exit_at)
    except (TypeError, ValueError):
        return False

    cooldown_sec = float(re_entry_cooldown_hours) * 3600.0
    return (now_ts - last_exit) >= cooldown_sec


def record_entry(state, now_ts):
    """Pure: return a new state dict with the entry recorded."""
    new_state = dict(state or {})
    if not new_state.get("first_entry_at"):
        new_state["first_entry_at"] = float(now_ts)
    new_state["last_entry_at"] = float(now_ts)
    new_state["total_entries"] = int(new_state.get("total_entries", 0)) + 1
    return new_state


def record_exit(state, now_ts):
    """Pure: return a new state dict with the exit recorded."""
    new_state = dict(state or {})
    new_state["last_exit_at"] = float(now_ts)
    return new_state


def empty_state():
    """The v2 koala_config.read_koala_state() default shape."""
    return {"first_entry_at": None, "last_entry_at": None, "last_exit_at": None, "total_entries": 0}


# ═══════════════════════════════════════════════════════════════
# Race-window dedup (mirrors v2 koala_config recent-signals cache)
# ═══════════════════════════════════════════════════════════════

def prune_signaled(signaled, ttl, now):
    """Drop entries older than 4x TTL (verbatim from v2 _prune_recent_signals)."""
    cutoff = now - (ttl * 4)
    return {k: v for k, v in (signaled or {}).items() if v >= cutoff}


def was_recently_signaled(signaled, coin, ttl, now):
    """TTL membership check (verbatim from v2 was_recently_signaled)."""
    if not coin:
        return False
    last = (signaled or {}).get(coin.upper())
    if last is None:
        return False
    return (now - last) < ttl
