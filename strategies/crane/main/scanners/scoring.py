"""CRANE — Managed Pairs / Stat-Arb engine (pure: no I/O, no MCP, no clock).

Market-neutral. For a correlated pair (A, B) it tracks the log price spread
ln(A/B), z-scores it over a rolling window, and trades the SPREAD: when the pair
dislocates it shorts the rich leg / longs the cheap leg (equal notional), and it
manages the two legs as ONE position — they open together and MUST close together
on reversion, on a blowout stop, or the instant one leg goes missing.

That last rule is the safety core: a pair with only one leg left is a naked
directional bet. `decide_pair_action` returns CLOSE_NAKED whenever exactly one leg
is held, so the manager can flatten the survivor immediately.

⚠️ BLOCKED ON A RUNTIME CAPABILITY — see crane/DESIGN.md. The Runtime 3.0 scan
contract emits OPEN-only signals (no close/reduce intent) and blocks close
mutations, and CLOSE_POSITION has no scanner-signal path. So CLOSE_BOTH /
CLOSE_NAKED cannot currently be executed — which is exactly why crane ships as a
tested engine + design, NOT a deployable package. This module is complete and
unit-tested so it is ready to wire the moment coordinated multi-leg close exists.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import math

# action verbs returned by decide_pair_action
OPEN_BOTH = "OPEN_BOTH"
CLOSE_BOTH = "CLOSE_BOTH"
CLOSE_NAKED = "CLOSE_NAKED"
HOLD = "HOLD"


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def log_spread(px_a, px_b):
    """ln(A/B). None if either price is non-positive/unreadable."""
    a, b = _num(px_a), _num(px_b)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return math.log(a / b)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def zscore(history, window):
    """z-score of the LAST spread vs the trailing `window`. None until the window
    is full and dispersion is non-degenerate (guards a divide-by-zero blowup)."""
    w = int(window)
    hist = [h for h in (history or []) if isinstance(h, (int, float))]
    if len(hist) < w:
        return None
    ref = hist[-w:]
    sd = _std(ref)
    if sd <= 0:
        return None
    return (hist[-1] - _mean(ref)) / sd


def decide_pair_action(z, a_held, b_held, inputs):
    """The pair state machine. Returns (action, reason).

    SAFETY: exactly-one-leg-held ⇒ CLOSE_NAKED, unconditionally and before any
    other branch — never leave a single directional leg. Both-held ⇒ CLOSE_BOTH on
    reversion (|z|≤exitZ) or blowout (|z|≥stopZ), else HOLD. Neither-held ⇒
    OPEN_BOTH when |z|≥entryZ (needs a valid z, i.e. a full window), else HOLD."""
    entry_z = _f(inputs.get("entryZ"), 2.0)
    exit_z = _f(inputs.get("exitZ"), 0.5)
    stop_z = _f(inputs.get("stopZ"), 3.5)

    if a_held != b_held:                      # exactly one leg — NAKED
        return CLOSE_NAKED, "one leg missing — flatten survivor (never hold a naked leg)"

    if a_held and b_held:                     # in a pair
        if z is None:
            return HOLD, "z unavailable — hold managed pair"
        az = abs(z)
        if az <= exit_z:
            return CLOSE_BOTH, f"reverted |z|={az:.2f}≤{exit_z:g}"
        if az >= stop_z:
            return CLOSE_BOTH, f"blowout |z|={az:.2f}≥{stop_z:g} — stop"
        return HOLD, f"managed |z|={az:.2f}"

    # neither leg held — look for an entry
    if z is None:
        return HOLD, "z unavailable (window not full)"
    if abs(z) >= entry_z:
        return OPEN_BOTH, f"pair spread stretched {abs(z):.2f} std devs (entry {entry_z:g})"
    return HOLD, f"in-band |z|={abs(z):.2f}"


def entry_legs(z, pair):
    """Given a dislocation z and a pair {a, b}, the two legs to open (equal
    notional). z>0 ⇒ A rich vs B ⇒ SHORT A / LONG B; z<0 ⇒ LONG A / SHORT B."""
    a, b = pair["a"], pair["b"]
    if z > 0:
        return [{"asset": a, "direction": "SHORT"}, {"asset": b, "direction": "LONG"}]
    return [{"asset": a, "direction": "LONG"}, {"asset": b, "direction": "SHORT"}]


def leg_sizing(z, inputs):
    """(leverage, marginPct) applied to EACH leg — equal notional per side. z-scaled
    conviction within fleet caps; marginPct is a PERCENT of withdrawable per leg."""
    base = _f(inputs.get("legMarginPct"), 8)
    cap = _f(inputs.get("maxLegMarginPct"), 12)
    stretch = _f(inputs.get("entryZ"), 2.0)
    mgn = base * (1.0 + min(0.5, max(0.0, (abs(z) - stretch) / stretch))) if z else base
    mgn = max(1.0, min(mgn, cap))
    lev = int(_f(inputs.get("legLeverage"), 3))
    lev = max(1, min(lev, int(_f(inputs.get("maxLeverage"), 5))))
    return lev, round(mgn, 2)


def push_spread(history, spread, window):
    """Append a spread sample to the bounded rolling history (keep ~3×window)."""
    h = list(history or [])
    if spread is not None:
        h.append(spread)
    keep = max(4, int(window) * 3)
    return h[-keep:]
