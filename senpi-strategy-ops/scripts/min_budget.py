"""Canonical per-strategy minimum-budget calculation — PURE (no I/O, no yaml import).

VENDORED BYTE-IDENTICALLY into two skills:
  - senpi-trading-runtime/scripts/min_budget.py  (imported by gen_catalog.py -> bakes the
    number into catalog.json, which discovery reads at card time)
  - senpi-strategy-ops/scripts/min_budget.py     (imported by deploy.py -> enforces the gate;
    computes locally because custom-authored packages never pass through catalog generation)
A checksum test (test_min_budget_vendor_parity) fails CI if the two copies drift.

WHAT IT IS. The smallest TOTAL strategy budget at which the design functions: every wallet
funds (>= the $10 platform floor) AND every wallet's SMALLEST slot can open at least the
engine's bumped-minimum notional ($12) at that slot's smallest marginPct and its LOWEST
leverage — i.e. worst case on BOTH axes, so even the least-leveraged, smallest-margin position
clears the floor.

    per_wallet_min = max( WALLET_FLOOR,  BUMPED_NOTIONAL / (marginPct/100 * min_leverage) + FEE_BUFFER )
    strategy_min   = max over wallets( per_wallet_min / funding_share )
                     floored at WALLET_FLOOR * wallet_count
                     rounded up to a clean step (STEPS); past the last rung, ceil to the next $25

Funding shares matter, not just wallet count: deploy splits the budget by share, so a small-
share sleeve must still receive its own per-wallet minimum -> it can DRIVE the total up.

The two platform constants deploy.py used to own (MIN_WALLET, FEE_BUFFER) live HERE now, so
there is one source of truth for the physics.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import math

WALLET_FLOOR = 10.0        # platform min per wallet — mirrors the backend's MINIMUM_STRATEGY_BUDGET
                           # default ($10; senpi-hyperliquid-mcp getMinimumStrategyBudget). Keep in sync.
BUMPED_NOTIONAL = 12.0     # the engine bumps a small order up to this notional
FEE_BUFFER = 1.5           # USDC reserved per wallet for the creation fee (observed ~$1)

# The clean rungs a minimum rounds UP to. Past the last rung we ceil to the next $25 — never clamp,
# or a design above $250 would silently ship a minimum below what it needs (e.g. ox $271.67 -> $275).
STEPS = (10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 200, 250)

# marginPct is read from an EXPLICIT allowlist of sizing keys — NOT a `*MarginPct*` regex. A regex
# would wrongly pick up non-sizing thresholds (e.g. sailfish's `leaderMarginPct`, an RS-gap in
# percentage points), collapsing its minimum from $25 to $250.
_MARGIN_KEYS = ("marginPct", "marginPctBase", "baseAllocationPct", "minMarginPct")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _round_step(x):
    for s in STEPS:
        if x <= s + 1e-9:
            return s
    return int(math.ceil(x / 25.0) * 25)     # past the last rung: ceil to the next $25, never clamp


def _external_scanner_inputs(runtime):
    """Every external_scanner's `inputs` map. A multi-scanner runtime is sized off the WORST
    (smallest-slot) of all of them, not just the first."""
    return [sc["inputs"] for sc in (runtime.get("scanners") or [])
            if isinstance(sc, dict) and sc.get("type") == "external_scanner" and isinstance(sc.get("inputs"), dict)]


def _margin_pct(strat, inputs_list):
    """The SMALLEST configured slot marginPct (PERCENT) — the min over the declared baseline and
    every scanner sizing input in the allowlist, plus every marginPctTiers value. Worst case, so a
    wallet funded at the minimum can open even its lowest-conviction slot. None if nothing resolves."""
    # marginPct is a PERCENT in (0,100]; a value <= 1 is either a pasted fraction or, for the
    # vol-parity sleeves (caribou/hydra), an internal risk-weight (baseRiskPct/hedgeRiskPct-style)
    # that is NOT a slot size. Reject it so those sleeves come back UNRESOLVED and warn, rather than
    # being read as a 0.03%-margin slot.
    def _pct(v):
        v = _f(v)
        return v if (v is not None and v > 1.0) else None
    cands = []
    v = _pct(strat.get("margin_pct"))
    if v:
        cands.append(v)
    for inp in inputs_list:
        for k in _MARGIN_KEYS:
            v = _pct(inp.get(k))
            if v:
                cands.append(v)
        tiers = inp.get("marginPctTiers")
        if isinstance(tiers, dict):
            cands += [x for x in (_pct(t) for t in tiers.values()) if x]
    return min(cands) if cands else None


def _lev_values(tiers):
    """Leverage values from a tiers config, dict ({apex,good,base}) or list-of-pairs ([[score, lev]])."""
    if isinstance(tiers, dict):
        return [_f(x) for x in tiers.values() if _f(x) and _f(x) > 0]
    if isinstance(tiers, list):
        out = []
        for row in tiers:
            if isinstance(row, (list, tuple)) and row:
                out.append(_f(row[-1]))
            elif _f(row):
                out.append(_f(row))
        return [x for x in out if x and x > 0]
    return []


def _min_leverage(strat, inputs_list):
    """The LOWEST leverage the wallet sizes a position at (worst case for notional): the lowest of
    every explicit tier + tiered-margin std + conservative multiplier across all scanners; else the
    default. A minLeverage clamp-floor is NOT used — it is a safety clamp, not a configured tier."""
    cands = []
    for inp in inputs_list:
        cands += _lev_values(inp.get("leverageTiers"))
        v = _f(inp.get("stdLeverage"))
        if v and v > 0:
            cands.append(v)
    lm = strat.get("leverage_multipliers")
    if isinstance(lm, dict) and _f(lm.get("conservative")):
        cands.append(_f(lm.get("conservative")))
    if cands:
        return min(cands)
    dl = _f(strat.get("default_leverage"))
    return dl if dl and dl > 0 else 1.0


def _per_wallet_min(margin_pct, min_leverage):
    return max(WALLET_FLOOR, BUMPED_NOTIONAL / ((margin_pct / 100.0) * min_leverage) + FEE_BUFFER)


def strategy_min_budget(manifest, runtimes):
    """PURE. `manifest` = parsed strategy.yaml dict; `runtimes` = {instance_name: parsed runtime.yaml
    dict}. Returns {min_budget, wallet_count, binding_wallet, unresolved_wallets, raw, breakdown}.

    `unresolved_wallets` lists sleeves whose marginPct could NOT be resolved (their per-wallet min
    fell back to the bare $10 floor) — gen_catalog warns on it and deploy refuses to assert a number
    it never computed, so a vol-parity sleeve can never silently advertise $10 and then no-trade."""
    insts = manifest.get("instances") or []
    cat = manifest.get("catalog") or {}
    per_wallet_over_share = []
    breakdown = []
    unresolved = []
    for inst in insts:
        rt = runtimes.get(inst.get("name")) or {}
        strat = rt.get("strategy") or {}
        inputs_list = _external_scanner_inputs(rt)
        mp = _margin_pct(strat, inputs_list)
        lev = _min_leverage(strat, inputs_list)
        fs = _f(inst.get("funding_share"))
        fs = fs if (fs is not None and fs > 0) else 1.0     # 0/absent share -> treat as the whole book
        if mp:
            pw = _per_wallet_min(mp, lev)
        else:
            pw = WALLET_FLOOR
            unresolved.append(inst.get("name"))
        per_wallet_over_share.append(pw / fs)
        breakdown.append({
            "wallet": inst.get("name"),
            "margin_pct": mp,
            "min_leverage": lev,
            "funding_share": fs,
            "per_wallet_min": round(pw, 2),
        })
    wallet_count = len(insts)
    raw = max(per_wallet_over_share) if per_wallet_over_share else WALLET_FLOOR
    floored = max(raw, WALLET_FLOOR * max(wallet_count, 1))
    computed = _round_step(floored)
    floor_override = _f(cat.get("min_budget_floor"))
    if floor_override:                                       # deliberate authored number — RAISE only,
        computed = max(computed, floor_override)             # and NEVER shrunk by the rounding
    binding = None
    if breakdown:
        binding = max(breakdown, key=lambda b: b["per_wallet_min"] / (b["funding_share"] or 1.0))["wallet"]
    return {
        "min_budget": computed,
        "wallet_count": wallet_count,
        "binding_wallet": binding,
        "unresolved_wallets": unresolved,
        "raw": round(floored, 2),
        "breakdown": breakdown,
    }
