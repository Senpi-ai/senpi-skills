#!/usr/bin/env python3
"""senpi-portfolio engine — real-time wallet/balance taxonomy + holdings analysis (hidden).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES a
portfolio analysis (see SKILL.md). The script does the precise, real-time data work — enumerate every
wallet, classify every dollar into the right bucket, attribute positions, and pull market context for
analysis — and the LLM does the prose, the comparison, and the CTAs.

  python3 portfolio.py              # full real-time pull (all wallets + market context)
  python3 portfolio.py --no-market  # skip the per-asset market enrichment
  python3 portfolio.py --fixture f.json   # offline: recorded MCP-response map (tests)
  python3 portfolio.py --dry        # dump raw MCP responses for schema debugging

WHY THIS EXISTS — the balance-bucket trap:
Agents conflate `total_withdrawable` (free margin sitting INSIDE strategy wallets) with "idle cash in
the main embedded wallet." They are different buckets. This engine computes three structurally
separate pools so the agent never mixes them:
  1. idle_in_embedded   = total_usdc_in_hyperliquid + EVM token_balances   (truly free; deploy or withdraw)
  2. idle_in_strategies = sum of each strategy wallet's `withdrawable`      (in a strategy, not a position)
  3. deployed           = margin backing open positions
Grand total = idle_in_embedded + idle_in_strategies + deployed.

REAL-TIME, NEVER CACHED: account_get_portfolio caches HL data 12h unless forceFetch=true — this
engine always passes forceFetch. Per-strategy truth comes from live strategy_get_clearinghouse_state.

⚠ All tools here are USER-scoped (your own account): needs a USER-scoped SENPI_AUTH_TOKEN.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET_ENRICH_CAP = 24      # cap the per-asset market pull
CLOSED_HISTORY_CAP = 5      # recent closed trades to surface per strategy (realized PnL is over the full pull)
CLOSED_HISTORY_PULL = 50    # closed positions to pull for the realized-PnL total (API default page)

# WHAT A STRATEGY DOES = its `profile`, and the load-bearing field is `profile.description`, read from
# the DEPLOYED runtime.yaml that the runtime itself registers (installed_runtimes.json). This is
# UNIVERSAL: it works for a user's OWN authored strategy, not just our catalog templates — every
# deployed strategy has a runtime.yaml, only templates are in the catalog. The catalog stays as
# OPTIONAL enrichment (archetype/belief_plain/asset_classes/…) for templates, keyed by skill_name.
#   registry (universal, runtime-registered runtime.yaml)  →  the "what it does / how it works"
#   catalog  (templates only, our packages)                →  extra facets when present
# Neither is agent memory; the runtime registry outranks the catalog.

# The runtime registers every deployed strategy in installed_runtimes.json in the state dir.
STATE_DIR_ENV = "SENPI_STATE_DIR"
DEFAULT_STATE_DIR = os.path.expanduser("~/.openclaw/senpi-state")
REGISTRY_FILENAME = "installed_runtimes.json"
# Telemetry liveness (health check): `openclaw senpi status -r <runtime_id> --json` says whether a
# REGISTERED runtime is actually WORKING (healthy vs degraded), not just present in the registry. Same
# fail-open + fixture pattern as senpi-improve-trades' event-log read. Offline test hook: a JSON file at
# $SENPI_STATUS_FIXTURE keyed {"<runtime_id>": {status payload}} is read instead of shelling out.
STATUS_FIXTURE_ENV = "SENPI_STATUS_FIXTURE"

CATALOG_REF = os.environ.get("SENPI_SKILLS_REF", "main")
CATALOG_URL = f"https://raw.githubusercontent.com/Senpi-ai/senpi-skills/{CATALOG_REF}/strategies/catalog.json"
# Compact catalog enrichment = the extra facets the agent judges a template strategy against (SKILL.md).
# Not the whole record. `description` is NOT sourced here — it comes from the runtime registry.
CATALOG_KEYS = ("belief_plain", "thesis", "archetype", "archetype_label", "sub_style", "direction",
                "asset_classes", "risk_level", "time_horizon", "tagline")


# ──────────────────────────────────────────────────────────────── guarded I/O helpers
def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(d, *keys, default=0.0):
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                n = _num(d[k])
                if n is not None:
                    return n
    return default


def _field(d, *names, default=None):
    if isinstance(d, dict):
        for n in names:
            if n in d and d[n] is not None:
                return d[n]
    return default


def _pct(mark, prev):
    m, p = _num(mark), _num(prev)
    if m is None or p is None or p == 0:
        return None
    return round((m - p) / p * 100, 2)


# ──────────────────────────────────────────────────────────────── vendored YAML (runtime.yaml parse)
def _yaml_loads(text):
    """Parse runtime.yaml text via the vendored stdlib loader (scripts/_yaml.py — no cross-skill
    import). Returns the parsed mapping or None; never raises here (caller guards)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import _yaml
    return _yaml.loads(text)


# ──────────────────────────────────────────────── runtime registry (deployed runtime.yaml — UNIVERSAL)
def _collapse_ws(s):
    """Collapse internal whitespace/newlines in a folded `description` block to single spaces + strip."""
    if not isinstance(s, str):
        return None
    out = " ".join(s.split()).strip()
    return out or None


def _dsl_preset_summary(exit_block):
    """From a runtime.yaml `exit:` block: (dsl_preset, has_exit). dsl_preset keeps a named string preset
    if that's what shipped, else True when a dsl_preset mapping is present (a bespoke inline preset)."""
    if not isinstance(exit_block, dict):
        return None, False
    has_exit = True
    dp = exit_block.get("dsl_preset")
    if isinstance(dp, str):
        return dp, has_exit            # a named preset ("conviction", …)
    if isinstance(dp, dict) and dp:
        return True, has_exit          # inline preset — protection present, no single name
    if dp is not None:
        return True, has_exit
    return None, has_exit              # an `exit:` with no dsl_preset still counts as an exit


def _dsl_ladder(exit_block):
    """Parse the DSL PROTECTION LADDER from a runtime.yaml `exit:` block for reporting. Returns a dict
    that says (a) HOW DSL works for this strategy and (b) the tier ladder — the config side of the
    "protected from entry" story. NEVER raises; fail-open to None.

    From `exit.dsl_preset`:
      - inline mapping with phase1/phase2 →
          {hard_stop_roe_pct: -phase1.max_loss_pct,      # the hard stop floor, active FROM ENTRY
           arm_at_roe_pct: tiers[0].trigger_pct,          # Tier 1 = where the profit-ratchet ARMS
           tiers: [{trigger_pct, lock_hw_pct}, …],        # the profit-lock ladder
           has_phase2: bool}
        If phase2 is absent/disabled → tiers: [], arm_at_roe_pct: null (phase1-only protection).
        If phase1 is absent → hard_stop_roe_pct: null (rare; still report the ladder we have).
      - a NAMED string preset ("conviction", …) → {preset_name: "<name>", note: "named preset — ladder
        not inlined"} (the ladder lives in the runtime's preset table, not here).
      - no dsl_preset (an `exit:` with none) → None.
    """
    if not isinstance(exit_block, dict):
        return None
    dp = exit_block.get("dsl_preset")
    if isinstance(dp, str):
        return {"preset_name": dp, "note": "named preset — ladder not inlined"}
    if not isinstance(dp, dict) or not dp:
        return None

    p1 = dp.get("phase1") if isinstance(dp.get("phase1"), dict) else {}
    p2 = dp.get("phase2") if isinstance(dp.get("phase2"), dict) else {}

    # hard stop = the phase1 floor (active from entry). ROE floor is negative: -max_loss_pct.
    hard = None
    if p1 and p1.get("enabled") is not False:
        ml = _num(p1.get("max_loss_pct"))
        if ml is not None:
            hard = -abs(ml)

    # the profit-lock ratchet ladder (phase2). Present + enabled → parse the tiers; else empty ladder.
    tiers, has_phase2, arm_at = [], False, None
    if p2 and p2.get("enabled") is not False:
        raw_tiers = p2.get("tiers")
        if isinstance(raw_tiers, list):
            for t in raw_tiers:
                if not isinstance(t, dict):
                    continue
                trig = _num(t.get("trigger_pct"))
                lock = _num(t.get("lock_hw_pct"))
                tiers.append({"trigger_pct": trig, "lock_hw_pct": lock})
        has_phase2 = bool(tiers)
        if tiers and tiers[0].get("trigger_pct") is not None:
            arm_at = tiers[0]["trigger_pct"]   # Tier 1 arms the trail (first trigger)

    return {
        "hard_stop_roe_pct": hard,       # e.g. -14.0 — floor, active FROM ENTRY (phase1)
        "arm_at_roe_pct": arm_at,        # e.g. 8 — where the profit-ratchet ARMS (Tier 1), or null
        "tiers": tiers,                  # the profit-lock ladder ([] when phase2 off)
        "has_phase2": has_phase2,
    }


def _profile_from_runtime_yaml(text):
    """Parse one deployed runtime.yaml TEXT into the universal profile fields. Returns a dict (possibly
    partial) or None if the text doesn't parse to a mapping. Never raises."""
    doc = _yaml_loads(text)
    if not isinstance(doc, dict):
        return None
    exit_block = doc.get("exit")
    dsl_preset, has_exit = _dsl_preset_summary(exit_block)
    return {
        "runtime_name": doc.get("name"),
        "group": doc.get("group"),
        "version": doc.get("version"),
        "description": _collapse_ws(doc.get("description")),   # the UNIVERSAL "what it does / how it works"
        "dsl_preset": dsl_preset,
        # The DSL protection LADDER — how DSL works for this strategy: phase1 hard-stop floor (active
        # FROM ENTRY) + the phase2 profit-lock tiers. Reported per strategy; the per-position tier state
        # comes live from ratchet_stop_list (see hydrate). None when there's no dsl_preset to parse.
        "dsl": _dsl_ladder(exit_block),
        "has_exit": bool(has_exit),
    }


def load_runtime_registry(meta):
    """wallet_lower → runtime-profile for every deployed strategy the runtime has registered.

    SOURCE OF TRUTH for a strategy's "what it does / how it works" — read from the DEPLOYED runtime.yaml
    the runtime itself registers in installed_runtimes.json (state dir). UNIVERSAL: covers user-authored
    strategies too, not just catalog templates. Read-guarded + fail-open: any problem → ({}, None). A
    meta.warnings note is added ONLY for a real parse error, not for a simply-absent registry file.
    Also returns a wallet_lower → runtime_id map (the `senpi status --runtime <id>` address, for the
    telemetry liveness read). Returns (profiles_map, id_map, source)."""
    state_dir = os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR
    path = os.path.join(state_dir, REGISTRY_FILENAME)
    if not os.path.isfile(path):          # absent registry is normal, not an error
        return {}, {}, None
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as e:  # noqa — a corrupt registry is a real parse error worth surfacing
        meta.setdefault("warnings", []).append(
            f"runtime registry unreadable ({e}); mandates fall back to catalog")
        return {}, {}, None
    entries = raw.get("runtimes", raw) if isinstance(raw, dict) else raw
    out = {}
    id_map = {}                            # wallet_lower → runtime id (the `senpi status --runtime <id>` key)
    for entry in (entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        wallet = entry.get("wallet")
        if not wallet:
            continue
        rid = _field(entry, "id", "runtimeId", "runtime_id")   # address for the status/liveness read
        if rid:
            id_map[str(wallet).lower()] = rid
        text = entry.get("runtimeYamlContent")
        if text is None:
            ypath = entry.get("runtimeYamlPath")   # rarer form — a file path instead of inline content
            if ypath and os.path.isfile(ypath):
                try:
                    with open(ypath) as yf:
                        text = yf.read()
                except Exception:  # noqa — a missing/unreadable path is fail-open, skip this entry
                    text = None
        if not text:
            continue
        try:
            prof = _profile_from_runtime_yaml(text)
        except Exception as e:  # noqa — one bad runtime.yaml must not sink the whole registry
            meta.setdefault("warnings", []).append(
                f"runtime.yaml parse failed for {str(wallet)[:8]} ({e})")
            prof = None
        if prof:
            out[str(wallet).lower()] = prof
    return out, id_map, "registry"


# ──────────────────────────────────────────── telemetry liveness (is a REGISTERED runtime actually working?)
# Registry presence says a runtime was DEPLOYED; telemetry says it's actually RUNNING/healthy. Same
# fail-open + fixture pattern as senpi-improve-trades' event-log read: absence degrades to "unknown" (never
# a false "broken"), and once a host shows no CLI we stop shelling out.
def _note_telemetry_unavailable(meta, msg):
    """One-time telemetry warning; marks meta so the rollup can say liveness is unverified (registry-only)."""
    if not meta.get("_telemetry_warned"):
        meta.setdefault("warnings", []).append(f"telemetry: {msg}")
        meta["_telemetry_warned"] = True


def _deep_first(obj, keys, _depth=0):
    """First non-None value for any of `keys`, depth-first through dicts/lists — the `senpi status --json`
    payload shape isn't strictly pinned, so dig tolerantly."""
    if _depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k) is not None:
                return obj[k]
        for v in obj.values():
            r = _deep_first(v, keys, _depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_first(v, keys, _depth + 1)
            if r is not None:
                return r
    return None


def _fetch_runtime_status(runtime_id, meta):
    """`openclaw senpi status -r <id> --json` → parsed dict or None. FAIL-OPEN: no `openclaw` / non-zero
    exit / unknown method / parse error → None + a one-time note; NEVER raises. `meta._telemetry_dead`
    short-circuits once the host has no CLI. Offline test hook: $SENPI_STATUS_FIXTURE = JSON
    {"<runtime_id>": {status payload}} is read instead of shelling out (tests use this — no subprocess)."""
    if not runtime_id or meta.get("_telemetry_dead"):
        return None
    fixture = os.environ.get(STATUS_FIXTURE_ENV)
    if fixture:
        try:
            with open(fixture) as fh:
                data = json.load(fh)
            v = data.get(str(runtime_id)) if isinstance(data, dict) else None
            return v if isinstance(v, dict) else None
        except Exception as e:  # noqa — a bad fixture is fail-open too
            _note_telemetry_unavailable(meta, f"status fixture unreadable ({e})")
            return None
    try:
        proc = subprocess.run(["openclaw", "senpi", "status", "-r", str(runtime_id), "--json"],
                              capture_output=True, text=True, timeout=15)
    except FileNotFoundError:                # not a runtime host → every call fails; stop shelling out
        meta["_telemetry_dead"] = True
        _note_telemetry_unavailable(meta, "openclaw CLI not found — runtime liveness unverified (registry-only)")
        return None
    except Exception as e:  # noqa — timeout / OS error → fail-open
        _note_telemetry_unavailable(meta, f"status read failed ({e})")
        return None
    if proc.returncode != 0:
        err = (proc.stderr or "")[:200]
        if "unknown method" in err.lower():
            meta["_telemetry_dead"] = True
            _note_telemetry_unavailable(meta, "runtime build predates the status RPC — liveness unverified")
        else:
            _note_telemetry_unavailable(meta, f"status read exit {proc.returncode} ({err.strip()})")
        return None
    try:
        return json.loads(proc.stdout or "null")
    except Exception as e:  # noqa — malformed JSON → fail-open
        _note_telemetry_unavailable(meta, f"status JSON parse failed ({e})")
        return None


def _liveness_from_status(status):
    """Map a `senpi status` payload → runtime_health. The payload existing at all means the runtime is up
    (the gateway answered for it); the health field refines healthy→'live' vs degraded/unhealthy→'degraded'.
    None ⇒ 'unknown' (telemetry unavailable — never asserted as broken)."""
    if not isinstance(status, dict) or not status:
        return "unknown"
    h = _deep_first(status, ["overallHealth", "health", "overall", "status"])
    h = str(h).lower() if h is not None else None
    if h in ("degraded", "warn", "warning", "unhealthy", "failed", "error", "down", "false", "stopped"):
        return "degraded"
    return "live"   # healthy/ok/running/live, or answered with no explicit health field → it's running


# ──────────────────────────────────────────────────────────────── strategy profile (catalog enrichment)
def _catalog_facets(rec):
    """The OPTIONAL template-only enrichment facets, pulled from a strategy's catalog record (its
    strategy.yaml). None if the strategy isn't in the catalog (e.g. a user-authored/custom strategy)."""
    if not isinstance(rec, dict):
        return None
    m = {k: rec[k] for k in CATALOG_KEYS if rec.get(k) is not None}
    return m or None


def _merge_profile(registry_prof, catalog_facets):
    """Merge the universal registry profile (load-bearing `description`) with optional catalog facets
    into a single `profile` dict. Sparse-safe: registry-only, catalog-only, or neither.
      - registry present            → `description` + runtime_name/group/dsl_preset (source "registry")
      - catalog present             → belief_plain/thesis/archetype/… (source adds "+catalog"/"catalog")
      - neither                     → None
    """
    if not registry_prof and not catalog_facets:
        return None
    prof = {
        "description": None, "runtime_name": None, "group": None, "dsl_preset": None, "dsl": None,
        "belief_plain": None, "thesis": None, "archetype": None, "sub_style": None,
        "asset_classes": None, "risk_level": None, "time_horizon": None, "tagline": None,
        "source": None,
    }
    if registry_prof:
        prof["description"] = registry_prof.get("description")
        prof["runtime_name"] = registry_prof.get("runtime_name")
        prof["group"] = registry_prof.get("group")
        prof["dsl_preset"] = registry_prof.get("dsl_preset")
        prof["dsl"] = registry_prof.get("dsl")   # the DSL protection ladder (how DSL works for this strat)
    if catalog_facets:
        for k in ("belief_plain", "thesis", "archetype", "sub_style", "asset_classes",
                  "risk_level", "time_horizon", "tagline"):
            if catalog_facets.get(k) is not None:
                prof[k] = catalog_facets[k]
    if registry_prof and catalog_facets:
        prof["source"] = "registry+catalog"
    elif registry_prof:
        prof["source"] = "registry"
    else:
        prof["source"] = "catalog"
    return prof


def _catalog_local_paths():
    """Candidate local catalog.json locations, freshest-first. A local copy (repo checkout or a
    co-installed senpi-strategy-discover) is fresh + offline; first that parses wins."""
    cands = []
    env = os.environ.get("SENPI_CATALOG_PATH")
    if env:
        cands.append(env)
    root = os.path.dirname(HERE)          # senpi-portfolio/       (HERE = .../scripts)
    repo = os.path.dirname(root)          # senpi-skills/  (dev/repo checkout)
    cands += [
        os.path.join(repo, "strategies", "catalog.json"),
        os.path.join(repo, "senpi-strategy-discover", "catalog.json"),
        os.path.expanduser("~/.openclaw/senpi-skills/senpi-strategy-discover/catalog.json"),
        os.path.expanduser("~/.claude/skills/senpi-strategy-discover/catalog.json"),
    ]
    return cands


def load_catalog(meta):
    """id → catalog record for every template strategy (its strategy.yaml facets, compiled by
    gen_catalog). OPTIONAL enrichment only — the universal mandate `description` comes from the runtime
    registry, not here; the catalog just adds template facets (archetype/belief_plain/…), keyed by
    skill_name. Local copy first (fresh, offline), then the remote catalog, then degrade to {} + a
    warning. Never raises. Returns (map, src)."""
    raw, src = None, None
    for p in _catalog_local_paths():
        try:
            if p and os.path.isfile(p):
                with open(p) as fh:
                    raw = json.load(fh)
                src = "local"
                break
        except Exception:  # noqa — a bad local copy shouldn't block the remote fallback
            continue
    if raw is None:
        try:
            import urllib.request
            with urllib.request.urlopen(CATALOG_URL, timeout=6) as r:
                raw = json.loads(r.read().decode("utf-8"))
            src = "remote"
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(
                f"strategy catalog unavailable ({e}); template facets omitted — registry description still applies")
            return {}, None
    recs = raw.get("skills", raw) if isinstance(raw, dict) else raw
    out = {}
    for rec in (recs if isinstance(recs, list) else []):
        sid = rec.get("id") if isinstance(rec, dict) else None
        if sid:
            out[sid] = rec
    return out, src


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from mcp_client import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by (tool, strategy_wallet) or (tool, asset/dex) so a fixture can
    return per-wallet clearinghouse state. Falls back to the bare tool name."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        for keyer in ("strategy_wallet", "strategy_wallet_address", "trader_address", "strategyId", "asset"):
            if kw.get(keyer):
                k = f"{tool}::{str(kw[keyer]).lower()}"
                if k in self._r:
                    return self._r[k]
        if "dex" in kw:
            k = f"{tool}::{kw['dex']}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── wallet discovery
def fetch_embedded(client, meta):
    """Main/embedded wallet idle cash — the ONLY truly-free pool. Real-time (forceFetch)."""
    out = {"address": None, "idle_hl_usdc": None, "evm_usdc": [], "spot_usd": None,
           "idle_total": None}
    try:
        me = _ok(client.mcp_call("user_get_me", timeout=12)) or {}
        wallets = _field(me, "wallets", default=[]) or (me.get("user", {}) or {}).get("wallets", [])
        for w in wallets if isinstance(wallets, list) else []:
            if str(_field(w, "walletType", "type", default="")).lower() == "embedded":
                out["address"] = _field(w, "walletAddress", "address")
                break
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"user_get_me failed: {e}")

    try:
        # forceFetch=True → bypass the 12h HL cache. This is the cache-freshness guarantee.
        p = _ok(client.mcp_call("account_get_portfolio", forceFetch=True, strategyStatus="ALL", timeout=25)) or {}
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"account_get_portfolio failed: {e}")
        return out, {}

    # account_get_portfolio (GetPortfolioV3) nests the balance fields under a `portfolio` key
    # ({data: {portfolio: {...}}}); _ok() strips only the outer `data`. Unwrap `portfolio` here so the
    # field reads below hit real values (else the whole embedded read is $0). Robust to both shapes.
    # This nesting + the wrong field name below is why a $10k+ embedded infusion read as $0.
    if isinstance(p, dict) and isinstance(p.get("portfolio"), dict):
        p = p["portfolio"]

    # Idle HL balance is `total_in_hyperliquid` (per the account_get_portfolio schema + ops deploy.py) —
    # NOT `total_usdc_in_hyperliquid` (does not exist; the wrong name made this $0). Old name kept as a
    # harmless fallback so it can never regress.
    out["idle_hl_usdc"] = _f(p, "total_in_hyperliquid", "total_usdc_in_hyperliquid", default=0.0)
    out["spot_usd"] = _f(p, "total_spot_usd_in_hyperliquid", default=0.0)
    evm = 0.0
    for tb in (_field(p, "token_balances", default=[]) or []):
        sym = str(_field(tb, "symbol", "tokenSymbol", default="")).upper()
        if sym in ("USDC", "USDC.E", "USDT"):
            amt = _f(tb, "usdValue", "usd_value", "amountUsd", "balanceUsd", "amount", default=0.0)
            chain = _field(tb, "chain", "network", "chainName", default="EVM")
            if amt:
                out["evm_usdc"].append({"chain": chain, "usd": round(amt, 2)})
                evm += amt
    out["idle_total"] = round((out["idle_hl_usdc"] or 0.0) + evm, 2)
    portfolio_totals = {
        "total_balance_usd": _f(p, "total_balance_usd", default=None),
        "total_allocated_in_strategy": _f(p, "total_allocated_in_strategy", default=None),
        "total_withdrawable": _f(p, "total_withdrawable", default=None),
    }
    return out, portfolio_totals


def fetch_strategies(client, meta):
    """Live per-strategy state: enumerate strategies, then clearinghouse state per wallet (real-time,
    both DEXes). withdrawable = free margin idle IN that strategy; positions = deployed."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    # UNIVERSAL source of "what it does / how it works": the deployed runtime.yaml the runtime registers,
    # keyed by wallet — works for user-authored strategies, not just our catalog templates.
    registry, runtime_id_map, registry_src = load_runtime_registry(meta)   # profiles + wallet→runtime_id
    meta["registry_source"] = registry_src
    # OPTIONAL template enrichment (archetype/belief_plain/…), keyed by skill_name.
    catalog, catalog_src = load_catalog(meta)
    meta["catalog_source"] = catalog_src
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        # Attribution: the package a strategy was deployed under. Lives in strategyMetadata.skillName/
        # skillVersion (set by strategy_create_custom_strategy's skillName arg), with flat fallbacks.
        skill_name, skill_version = None, None
        meta_obj = _field(s, "strategyMetadata", "metadata")
        if isinstance(meta_obj, dict):
            skill_name = _field(meta_obj, "skillName", "skill_name")
            skill_version = _field(meta_obj, "skillVersion", "skill_version")
        if not skill_name:
            skill_name = _field(s, "skillName", "skill_name", "skill")
        if not skill_version:
            skill_version = _field(s, "skillVersion", "skill_version")
        # UNIVERSAL profile: the registry's runtime.yaml `description` (keyed by wallet) is the
        # load-bearing "what it does / how it works" — present for user-authored strategies too. Catalog
        # facets enrich templates only. Merged into a single `profile`; None only if BOTH are absent.
        registry_prof = registry.get(str(wallet).lower())
        catalog_facets = _catalog_facets(catalog.get(skill_name) if skill_name else None)
        profile = _merge_profile(registry_prof, catalog_facets)
        # PROTECTED — universal: the deployed runtime.yaml ships an `exit` block (has_exit), OR it's a
        # template deploy (skill_name present ⟹ built-in DSL exit by the validator invariant). Config-
        # level protection posture, not a live per-position DSL-tracking check.
        has_exit = bool(registry_prof and registry_prof.get("has_exit"))
        # RUNTIME LIVENESS — is a runtime actually REGISTERED for this strategy? The runtime records every
        # deployed strategy in installed_runtimes.json (keyed by wallet). A skill_name strategy that is
        # ACTIVE + funded but ABSENT from that registry has NO runtime behind it — its scanner never ran,
        # so it has NO DSL and NO guardrails despite "status: ACTIVE" (the exact trap that let a user think
        # a funded-but-never-registered strategy was live and protected). Only judgeable when the registry
        # is actually present on THIS host; an absent registry ⇒ unknown (never claim not-running from it).
        runtime_registered = bool(registry_prof) if registry_src == "registry" else None
        not_running = bool(skill_name) and runtime_registered is False
        strategies.append({
            "name": _field(s, "tradingStrategyName", "name", default="strategy"),
            "wallet": wallet,
            # strategyId — needed for the live per-position DSL/ratchet lookup (ratchet_stop_list keys
            # on strategyId + wallet). Kept off the presentation surface; used only by hydrate().
            "strategy_id": _field(s, "id", "strategyId", "strategy_id"),
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
            "skill_name": skill_name,
            "skill_version": skill_version,
            # PROTECTED — config posture (deployed runtime.yaml ships an `exit` block, or a template deploy
            # carries the validator-guaranteed DSL). But a strategy with NO registered runtime is running
            # NOTHING, so it is NOT protected regardless of config — force False when not_running.
            "protected": False if not_running else bool(has_exit or skill_name),
            "runtime_registered": runtime_registered,   # True | False | None (unknown — no registry here)
            "not_running": not_running,                  # ACTIVE + funded skill strategy, no runtime → dead
            # The strategy's declared job — `profile.description` from its DEPLOYED runtime.yaml (the
            # runtime registers it), plus optional catalog facets. The yardstick to judge it against.
            "profile": profile,
        })

    # Where did the strategies' profiles come from, in aggregate: registry / catalog / mixed / None.
    prof_srcs = {s["profile"]["source"] for s in strategies if s.get("profile")}
    if not prof_srcs:
        meta["profile_source"] = None
    elif prof_srcs <= {"registry"}:
        meta["profile_source"] = "registry"
    elif prof_srcs <= {"catalog"}:
        meta["profile_source"] = "catalog"
    else:
        meta["profile_source"] = "mixed"

    def hydrate(strat):
        try:
            ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=strat["wallet"], timeout=20))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"clearinghouse {strat['wallet'][:8]} failed: {e}")
            return strat
        dex_av, dex_wd, positions = {}, {}, []
        for dex in ("main", "xyz"):
            d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
            ms = _field(d, "marginSummary", "margin_summary", default={}) or {}
            dex_av[dex] = _f(ms, "accountValue", "account_value", default=0.0)
            dex_wd[dex] = _f(d, "withdrawable", default=0.0)
            for ap in (_field(d, "assetPositions", "asset_positions", default=[]) or []):
                pos = _field(ap, "position", default=ap) or {}
                szi = _f(pos, "szi", "size", default=0.0)
                if szi == 0:
                    continue
                lev = pos.get("leverage") or {}
                positions.append({
                    "asset": _field(pos, "coin", "asset"),
                    "dex": dex,
                    "direction": "long" if szi > 0 else "short",
                    "leverage": _f(lev, "value", default=None) if isinstance(lev, dict) else _num(lev),
                    "notional": round(abs(_f(pos, "positionValue", "position_value", default=0.0)), 2),
                    "margin": round(_f(pos, "marginUsed", "margin_used", default=0.0), 2),
                    "entry_px": _f(pos, "entryPx", "entry_px", default=None),
                    "upnl": round(_f(pos, "unrealizedPnl", "unrealized_pnl", default=0.0), 2),
                    "return_on_equity_pct": round(_f(pos, "returnOnEquity", "return_on_equity", default=0.0) * 100, 2),
                    "liq_px": _f(pos, "liquidationPx", "liquidation_px", default=None),
                })
        # CRITICAL — main and xyz are two VIEWS of ONE wallet, not separate pools. `withdrawable` is
        # the SHARED idle collateral, mirrored identically in both views — count it ONCE (max == either).
        # Each view's accountValue = shared idle + that DEX's own position equity (margin + uPnL), so:
        #   wallet_value = main.av + xyz.av − shared_idle   (subtract the duplicated base exactly once)
        # Summing av (or summing withdrawable) double-counts the shared collateral — the bug this fixes.
        shared_idle = max(dex_wd.get("main", 0.0), dex_wd.get("xyz", 0.0))
        deployed = sum(max(0.0, dex_av.get(dex, 0.0) - shared_idle) for dex in ("main", "xyz"))
        strat["idle_withdrawable"] = round(shared_idle, 2)         # shared free margin (counted once)
        strat["deployed"] = round(deployed, 2)                     # position equity across BOTH dexes
        strat["account_value"] = round(shared_idle + deployed, 2)  # = main.av + xyz.av − shared_idle
        strat["position_margin"] = round(sum(p["margin"] for p in positions), 2)   # initial margin detail
        strat["positions"] = positions
        # RECONCILE status vs live wallet — the clearinghouse is the TRUTH, `status` is not. `strategy_list`
        # can report a just-closed strategy as ACTIVE (the status lags the close). A $0 account value with
        # NO positions AND NO idle is an EMPTY wallet: the strategy was CLOSED/DRAINED (funds returned to
        # the embedded wallet) or never funded. Flag it so the narrator never presents `total_funded` as
        # live/idle/reserved capital and never counts a ghost as a live strategy. (A FLAT sleeve merely
        # waiting for a signal still holds idle margin → account_value > 0 → NOT flagged empty.)
        tf, tw = strat.get("total_funded"), strat.get("total_withdrawn")
        strat["empty"] = (strat["account_value"] <= 0.01 and strat["idle_withdrawable"] <= 0.01 and not positions)
        if strat["empty"]:
            drained = bool(tf and tf > 0 and tw is not None and tw >= tf - 0.01)
            strat["empty_reason"] = "closed_or_drained" if drained else "unfunded"
        # LIVE per-position DSL/ratchet tier — read-guarded + fail-open. Attaches a `dsl` object to each
        # open position (armed → tier/lock; not armed → "protected from entry, ratchet arms at +X%").
        # NEVER leaves a live position looking "unprotected." (See attach_position_dsl.)
        attach_position_dsl(client, strat, meta)
        strat["closed"] = fetch_closed(client, strat["wallet"], meta)
        return strat

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(hydrate, strategies))
    except Exception:  # noqa
        strategies = [hydrate(s) for s in strategies]
    # TELEMETRY LIVENESS — for each strategy WITH a registered runtime, ask the runtime itself (senpi
    # status) whether it's actually healthy, not just registered. runtime_health: not_running (no registry)
    # / degraded (registered but reports unhealthy) / live (healthy) / unknown (telemetry unavailable — do
    # NOT assert broken). Fail-open + short-circuited by _telemetry_dead; sequential (few per user).
    for s in strategies:
        if s.get("not_running"):
            s["runtime_health"] = "not_running"
        elif s.get("runtime_registered") is not True:
            s["runtime_health"] = "unknown"        # no registry on this host, or a custom/no-skill one-off
        else:
            rid = runtime_id_map.get(str(s.get("wallet")).lower())
            s["runtime_health"] = _liveness_from_status(_fetch_runtime_status(rid, meta) if rid else None)
    # Roll up any strategy reported ACTIVE but holding $0 (empty wallet) — status/clearinghouse mismatch.
    dormant = [s["name"] for s in strategies if s.get("empty")]
    if dormant:
        meta["dormant_active"] = dormant
        meta.setdefault("warnings", []).append(
            f"{len(dormant)} strategy(ies) report status ACTIVE but hold $0 (empty wallet) — likely just "
            f"closed, funds returned to embedded (or never funded): {', '.join(str(d) for d in dormant)}")
    # Roll up any ACTIVE + funded strategy with NO runtime registered — status says ACTIVE but there is no
    # runtime, so it is NOT running: no scanner, no DSL, no guardrails. The "ACTIVE record ≠ live runtime"
    # trap — must be surfaced as unprotected/not-running, never as "alive and waiting".
    not_running = [s["name"] for s in strategies if s.get("not_running")]
    if not_running:
        meta["not_running"] = not_running
        meta.setdefault("warnings", []).append(
            f"{len(not_running)} strategy(ies) show status ACTIVE but have NO runtime registered — NOT "
            f"running: no scanner, no DSL, no guardrails despite 'ACTIVE'. Report as UNPROTECTED / not "
            f"running, never as live or 'waiting for a setup': {', '.join(str(n) for n in not_running)}")
    # Registered but telemetry says the runtime is DEGRADED/unhealthy — running, but not cleanly (scanner
    # erroring, monitor stalled, etc.). Distinct from not_running (no runtime) and from live (healthy).
    degraded = [s["name"] for s in strategies if s.get("runtime_health") == "degraded"]
    if degraded:
        meta["degraded_runtimes"] = degraded
        meta.setdefault("warnings", []).append(
            f"{len(degraded)} strategy(ies) have a runtime that telemetry reports DEGRADED/unhealthy — "
            f"registered but not working cleanly (check `openclaw senpi status`): "
            f"{', '.join(str(d) for d in degraded)}")
    return strategies


def fetch_closed(client, wallet, meta):
    """Read-guarded closed-position ledger for a strategy wallet: total realized PnL + a short list of
    recent closed trades. Extraction matches the real `discovery_get_trader_history` shape
    (senpi://guides/trader-closed-positions): a `closedPositions[]` of records with `coin`, signed `szi`
    (>0 closed long / <0 closed short), string `realizedPnl`, Unix-ms `closeTime`, `entryPx`/`exitPx`.
    Fails OPEN — any read/parse error → empty closed block + a meta.warning, never crashes."""
    empty = {"realized_pnl": None, "trade_count": 0, "recent": []}
    try:
        h = _ok(client.mcp_call("discovery_get_trader_history", trader_address=wallet,
                                sort_by="CLOSED_TIME", sort_direction="DESC",
                                limit=CLOSED_HISTORY_PULL, timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} failed: {e}")
        return empty
    if h is None:
        # _ok returns None on an explicit success:false envelope
        meta.setdefault("warnings", []).append(f"trader_history {wallet[:8]} returned no data")
        return empty
    rows = h if isinstance(h, list) else _field(h, "closedPositions", "closed_positions", "positions", default=[])
    if not isinstance(rows, list):
        rows = []
    realized_total = 0.0
    recent = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        pnl = _f(p, "realizedPnl", "realized_pnl", default=0.0)   # often a string → _f coerces
        realized_total += pnl
        if len(recent) < CLOSED_HISTORY_CAP:
            szi = _f(p, "szi", "size", default=0.0)
            recent.append({
                "asset": _field(p, "coin", "coinDisplayName", "asset"),
                "direction": "long" if szi >= 0 else "short",   # closed-side sign (szi>0 closed a long)
                "realized_pnl": round(pnl, 2),
                "entry_px": _field(p, "entryPx", "entry_px"),
                "exit_px": _field(p, "exitPx", "exit_px"),
                "closed_time": _field(p, "closeTime", "closed_time", "closeTimeMs"),
            })
    return {"realized_pnl": round(realized_total, 2), "trade_count": len(rows), "recent": recent}


# ──────────────────────────────────────────────────────────────── live per-position DSL / ratchet tier
def _locked_pct_at_tier(ladder, tier_index):
    """The lock_hw_pct configured at `tier_index` in the parsed profile.dsl ladder (what % of the peak
    is locked once the ratchet reaches that tier). None if the ladder/index isn't available."""
    if not isinstance(ladder, dict):
        return None
    tiers = ladder.get("tiers")
    if not isinstance(tiers, list) or tier_index is None:
        return None
    try:
        i = int(tier_index)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(tiers) and isinstance(tiers[i], dict):
        return tiers[i].get("lock_hw_pct")
    return None


def _unarmed_dsl(ladder, roe):
    """The DSL object for an open position that has NOT yet crossed Tier 1 (no ratchet record). This is
    the WHOLE POINT of the fix: an empty ratchet record is NOT "no DSL / unprotected" — the phase1 hard
    stop protects the position FROM ENTRY, and the profit-ratchet simply hasn't ARMED yet. Frame it that
    way, NEVER as unmonitored. `ladder` = the strategy's profile.dsl (config); `roe` = this position's
    return_on_equity_pct. Stands alone even if the live ratchet call failed (config + ROE only)."""
    ladder = ladder if isinstance(ladder, dict) else {}
    hard = ladder.get("hard_stop_roe_pct")
    arm = ladder.get("arm_at_roe_pct")
    obj = {
        "armed": False,
        "hard_stop_roe_pct": hard,        # floor active FROM ENTRY (phase1) — always protecting
        "arm_at_roe_pct": arm,            # where the profit-ratchet ARMS (Tier 1)
        "roe": roe,                       # this position's current ROE
    }
    # A plain-language note the narrator can lean on — never reads as "unprotected."
    if arm is not None:
        roe_txt = f"+{roe}%" if (roe is not None and roe >= 0) else (f"{roe}%" if roe is not None else "n/a")
        floor_txt = f"; hard stop at {hard}% ROE" if hard is not None else ""
        obj["note"] = (f"protected from entry by the phase1 hard stop{floor_txt}; profit-ratchet arms at "
                       f"Tier 1 (+{arm}%) — currently {roe_txt}")
    elif hard is not None:
        obj["note"] = (f"protected from entry by the phase1 hard stop (floor {hard}% ROE); "
                       f"phase1-only preset — no profit-ratchet tiers")
    else:
        obj["note"] = ("protected by the strategy's DSL exit from entry; live ratchet tier not yet armed")
    return obj


def attach_position_dsl(client, strat, meta):
    """Attach a `dsl` object to each open position of ONE strategy instance — its LIVE ratchet tier state.

    Read-guarded + FAIL-OPEN. One `ratchet_stop_list(strategyId, wallet, status:ACTIVE)` call per
    instance, indexed by asset:
      - a record exists (position crossed Tier 1) → armed: true, tier_index, high_water_roe, status,
        locked (= lock_hw_pct at that tier from the parsed ladder).
      - NO record (sub-Tier-1) → the `_unarmed_dsl` object: armed: false + the "protected from entry,
        ratchet arms at +X%" framing. This is EXPECTED, not a gap — never "unprotected."
      - the ratchet call fails entirely → EVERY position still gets the config-based `_unarmed_dsl`
        object (config + ROE stands alone), plus a meta.warnings note.
    NEVER emits anything that reads as "no DSL / no monitoring."
    """
    positions = strat.get("positions") or []
    if not positions:
        return
    prof = strat.get("profile") or {}
    ladder = prof.get("dsl")
    sid = strat.get("strategy_id")
    wallet = strat.get("wallet")

    records = None
    try:
        rl = _ok(client.mcp_call("ratchet_stop_list", strategyId=sid,
                                 strategy_wallet_address=wallet, status="ACTIVE", timeout=15))
        rows = rl if isinstance(rl, list) else _field(rl, "configs", "ratchetStops", "data", "items", default=[])
        records = {}
        for r in (rows if isinstance(rows, list) else []):
            if not isinstance(r, dict):
                continue
            asset = _field(r, "asset", "coin")
            if asset:
                records[str(asset)] = r
    except Exception as e:  # noqa — fail-open: config-based framing stands alone
        meta.setdefault("warnings", []).append(
            f"ratchet_stop_list {str(wallet)[:8]} failed: {e}; DSL tier from config only")
        records = None

    for p in positions:
        roe = p.get("return_on_equity_pct")
        rec = records.get(str(p.get("asset"))) if isinstance(records, dict) else None
        if rec is not None:
            ti = _field(rec, "currentTierIndex", "current_tier_index")
            p["dsl"] = {
                "armed": True,
                "tier_index": ti,
                "high_water_roe": _field(rec, "highWaterRoe", "high_water_roe"),
                "status": _field(rec, "status", default="ACTIVE"),
                "locked": _locked_pct_at_tier(ladder, ti),   # lock_hw_pct at the active tier
            }
        else:
            # no ratchet record (sub-Tier-1) OR the list call failed — either way, config-based framing
            p["dsl"] = _unarmed_dsl(ladder, roe)


# ──────────────────────────────────────────────────────────────── market context (for analysis)
def enrich_market(client, strategies, meta):
    """Per-held-asset 24h move so the LLM can compare each position to the broader market."""
    assets = []
    for s in strategies:
        for p in s.get("positions", []):
            tag = (p["asset"], p["dex"])
            if p["asset"] and tag not in assets:
                assets.append(tag)
    assets = assets[:MARKET_ENRICH_CAP]

    def one(item):
        asset, dex = item
        kw = dict(asset=asset, candle_intervals=["1h"], include_order_book=False, timeout=12)
        if dex == "xyz" or str(asset).startswith("xyz:"):
            kw["dex"] = "xyz"
        try:
            data = _ok(client.mcp_call("market_get_asset_data", **kw))
            ctx = _field(data, "asset_context", "context", default={}) or {}
            # live schema nests the quote under `context`; handle both
            inner = ctx if ("markPx" in ctx) else (_field(data, "context", default={}) or {})
            mark = _field(ctx, "markPx", default=None) or _field(inner, "markPx", default=None)
            prev = _field(ctx, "prevDayPx", default=None) or _field(inner, "prevDayPx", default=None)
            return (asset, _pct(mark, prev))
        except Exception:  # noqa
            return (asset, None)

    facts = {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            for a, chg in ex.map(one, assets):
                facts[a] = chg
    except Exception:  # noqa
        for item in assets:
            a, chg = one(item)
            facts[a] = chg
    # fold onto positions + tag alignment
    for s in strategies:
        for p in s.get("positions", []):
            chg = facts.get(p["asset"])
            if chg is None:
                continue
            p["market_24h_pct"] = chg
            # a short is "working" when the asset is down; a long when it's up
            working = (p["direction"] == "short" and chg < 0) or (p["direction"] == "long" and chg > 0)
            p["vs_market"] = "with the move" if working else "against the move"
    return facts


# ──────────────────────────────────────────────────────────────── taxonomy + signals
def compute(embedded, strategies, portfolio_totals):
    idle_strat = round(sum(_num(s.get("idle_withdrawable")) or 0.0 for s in strategies), 2)
    deployed = round(sum(_num(s.get("deployed")) or 0.0 for s in strategies), 2)
    idle_emb = embedded.get("idle_total") or 0.0
    strat_acct = round(sum(_num(s.get("account_value")) or 0.0 for s in strategies), 2)
    grand_total = round(idle_emb + strat_acct, 2)

    # exposure
    gross_long = gross_short = 0.0
    by_asset = {}
    upnl_total = 0.0
    largest = None
    for s in strategies:
        for p in s.get("positions", []):
            n = p["notional"]
            upnl_total += p["upnl"]
            if p["direction"] == "long":
                gross_long += n
            else:
                gross_short += n
            sign = n if p["direction"] == "long" else -n
            by_asset[p["asset"]] = round(by_asset.get(p["asset"], 0.0) + sign, 2)
            if largest is None or n > largest["notional"]:
                largest = {"asset": p["asset"], "notional": n, "strategy": s["name"]}

    totals = {
        "grand_total_usd": grand_total,
        "idle_in_embedded": round(idle_emb, 2),
        "idle_in_strategies": idle_strat,
        "deployed_in_positions": deployed,
        "strategy_account_value": strat_acct,
        "unrealized_pnl": round(upnl_total, 2),
        # cross-check against the (cached-bypassed) portfolio aggregate, if present
        "portfolio_total_balance_usd": portfolio_totals.get("total_balance_usd"),
        "portfolio_total_withdrawable": portfolio_totals.get("total_withdrawable"),
    }
    # reconciliation flag — surfaces silent drift between the two sources
    pbal = portfolio_totals.get("total_balance_usd")
    totals["reconciles"] = (pbal is None) or (abs(pbal - grand_total) <= max(2.0, 0.01 * grand_total))

    net = round(gross_long - gross_short, 2)
    exposure = {
        "net_notional_usd": net, "net_bias": ("long" if net > 0 else "short" if net < 0 else "flat"),
        "gross_long_usd": round(gross_long, 2), "gross_short_usd": round(gross_short, 2),
        "by_asset_net_usd": by_asset, "largest_position": largest,
    }
    working_cap = idle_emb + strat_acct
    signals = {
        "idle_drag_pct": round((idle_emb + idle_strat) / working_cap * 100, 1) if working_cap else None,
        "deployed_pct": round(deployed / working_cap * 100, 1) if working_cap else None,
        "largest_position_pct_of_deployed": round(largest["notional"] / (gross_long + gross_short) * 100, 1)
            if largest and (gross_long + gross_short) else None,
    }
    return totals, exposure, signals


# ──────────────────────────────────────────────────────────────── strategy grouping (A STRATEGY IS ALL ITS WALLETS)
def _group_key(strat):
    """The grouping key for a per-wallet `strategies[]` row → the STRATEGY it belongs to.

    A single strategy can deploy as MULTIPLE instances on SEPARATE wallets (ox = core+ballast,
    cougar = long+short, cub = long+short+preipo). `strategy_list` returns each instance/wallet as its
    OWN row, so the engine lists them as separate `strategies[]` entries. Re-uniting them is the whole
    point: `profile.group` (from the deployed runtime.yaml, shared by every instance of a strategy) is
    the authoritative key → fall back to `skill_name` (package attribution) → fall back to the wallet
    itself (a genuinely ungrouped / custom one-off is its own group of one). Fail-open: never raises."""
    prof = strat.get("profile") or {}
    grp = prof.get("group")
    if grp:
        return str(grp)
    if strat.get("skill_name"):
        return str(strat["skill_name"])
    return str(strat.get("wallet") or id(strat))


def _short_wallet(w):
    w = str(w or "")
    return f"{w[:6]}...{w[-4:]}" if len(w) > 12 else w


def group_strategies(strategies, meta):
    """Collapse the per-wallet `strategies[]` rows into `strategy_groups[]` — ONE entry per real strategy.

    SUPPLEMENTS `strategies[]` (does not replace it — the bucket math + per-wallet detail still rely on
    the flat list). Each group re-unites every instance/wallet of a strategy so the agent reasons at the
    STRATEGY level: a multi-wallet strategy (long+short, core+ballast, multi-sleeve) is ONE strategy
    across N wallets, never N separate strategies. Order is preserved by first appearance. Fail-open:
    a malformed row can't sink the grouping; worst case it lands in its own wallet-keyed group."""
    order = []          # group keys, first-seen order
    buckets = {}        # key → list of strategies
    for s in (strategies or []):
        try:
            key = _group_key(s)
        except Exception:  # noqa — a malformed row must not sink the grouping
            key = str(s.get("wallet") or id(s))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(s)

    groups = []
    for key in order:
        insts = buckets[key]
        # Instances share the strategy's identity (profile is per-strategy, mirrored on every wallet);
        # pull the shared facets from the first instance that carries a profile, else the first row.
        prof = next((s.get("profile") for s in insts if s.get("profile")), None) or {}
        first = insts[0]
        # mandate = the strategy's declared job — description (universal, from the deployed runtime.yaml)
        # or belief_plain (catalog facet); instances share it.
        mandate = prof.get("description") or prof.get("belief_plain")
        skill_name = next((s.get("skill_name") for s in insts if s.get("skill_name")), None)

        instances = []
        for s in insts:
            instances.append({
                "name": s.get("name"),                          # = runtime_name, e.g. ox-core
                "wallet": s.get("wallet"),
                "wallet_short": _short_wallet(s.get("wallet")),
                "account_value": s.get("account_value"),
                "idle_withdrawable": s.get("idle_withdrawable"),
                "deployed": s.get("deployed"),
                "upnl": round(sum(_num(p.get("upnl")) or 0.0 for p in (s.get("positions") or [])), 2),
                "positions": s.get("positions", []),
                "closed": s.get("closed"),
            })

        # totals — summed across every instance/wallet of the strategy (a strategy is all its wallets)
        def _sum(field):
            vals = [_num(i.get(field)) for i in instances]
            vals = [v for v in vals if v is not None]
            return round(sum(vals), 2) if vals else None
        realized_vals = [_num((s.get("closed") or {}).get("realized_pnl")) for s in insts]
        realized_vals = [v for v in realized_vals if v is not None]
        totals = {
            "account_value": _sum("account_value"),
            "idle_withdrawable": _sum("idle_withdrawable"),
            "deployed": _sum("deployed"),
            "upnl": _sum("upnl"),
            "realized_pnl": round(sum(realized_vals), 2) if realized_vals else None,
        }

        # flat instances = an instance with NO open positions. For a multi-wallet strategy this is its
        # OTHER sleeve waiting for its signal (e.g. cougar's long book flat while its short book trades),
        # NOT redeployable idle. Named so the agent never calls it "dead money."
        flat_instances = [i["name"] for i, s in zip(instances, insts) if not (s.get("positions") or [])]

        groups.append({
            "label": key,                                       # the group id, e.g. ox / cougar / cub
            "skill_name": skill_name,
            "archetype": prof.get("archetype"),
            "archetype_label": prof.get("archetype_label"),
            "direction": prof.get("direction"),
            "mandate": mandate,                                 # the strategy's declared job (shared)
            # HOW the strategy's DSL works — the phase1 hard-stop floor + phase2 tier ladder, shared by
            # every instance (one config per strategy). Surfaced once here; per-position tier state lives
            # on each position's `dsl` object. None for a named-preset/no-phase2 strategy handled inline.
            "dsl": prof.get("dsl"),
            "is_multi_wallet": len(insts) > 1,
            "instances": instances,                             # per-wallet detail
            "totals": totals,                                   # summed across all wallets
            # protected ONLY if ALL instances are protected — a strategy with one unguarded sleeve is not
            # fully protected. (An instance with no registered runtime is forced unprotected upstream.)
            "protected": all(bool(s.get("protected")) for s in insts),
            # not_running: ANY instance is ACTIVE + funded but has no runtime registered → the strategy (or
            # a sleeve of it) isn't actually running. runtime_registered: True (all registered) / False
            # (some missing) / None (unknown — no registry on this host, don't assert either way).
            "not_running": any(bool(s.get("not_running")) for s in insts),
            "runtime_registered": (None if any(s.get("runtime_registered") is None for s in insts)
                                   else all(bool(s.get("runtime_registered")) for s in insts)),
            # runtime_health = the WORST across instances (not_running > degraded > unknown > live) — one
            # dead/degraded sleeve makes the whole strategy not-fully-live.
            "runtime_health": next((v for v in ("not_running", "degraded", "unknown", "live")
                                    if any(s.get("runtime_health") == v for s in insts)), "unknown"),
            "flat_instances": flat_instances,
            "profile_source": prof.get("source"),
        })

    if any(g["is_multi_wallet"] for g in groups):
        meta["has_multi_wallet_strategy"] = True
    return groups


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_market=True):
    meta = {"warnings": [], "real_time": True, "force_fetch": True}
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)   # default; group_strategies flips it to True
    # A STRATEGY IS ALL ITS WALLETS — re-unite the per-wallet rows into one entry per real strategy.
    # SUPPLEMENTS `strategies[]` (kept — bucket math + detail rely on it); groups add the strategy-level view.
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    return {
        "as_of": "live",
        "totals": totals,           # the three buckets — NEVER conflate them
        "embedded_wallet": embedded,
        "strategies": strategies,
        # ONE entry per real strategy (a strategy is ALL its wallets); reason + recommend at THIS level.
        "strategy_groups": strategy_groups,
        "exposure": exposure,
        "signals": signals,
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────── shared state file (resumable steps)
# The step subcommands (money → strategies → positions) are FAST, resumable slices that persist their work
# to a shared JSON state file so a later step never re-fetches what an earlier one already pulled. The
# agent runs them in sequence and NARRATES between — no single call carries the whole multi-wallet pull
# (which trips the exec timeout and pushes the agent to raw MCP, losing every guardrail). Each step is
# idempotent + fail-open: a missing/corrupt state file → recompute (self-heal); every step also works
# STANDALONE (just slower). `all` writes the same state but prints run()'s full composed dict
# (byte-identical to the pre-steps output). State default: <tempdir>/senpi-portfolio/state.json.
STATE_SUBDIR = "senpi-portfolio"


def _default_state_path():
    """Default shared-state path <tempdir>/senpi-portfolio/state.json. Uses tempfile.gettempdir()
    (never $HOME — the state dir may live somewhere else on a runtime host)."""
    return os.path.join(tempfile.gettempdir(), STATE_SUBDIR, "state.json")


def _load_state(path):
    """Read the shared state JSON. Never raises — a missing/corrupt/unreadable file → {} (fail-open: the
    step then recomputes its prerequisites and self-heals)."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa — corrupt/unreadable state is fail-open → recompute
        return {}


def _save_state(path, state):
    """Merge-write the shared state JSON (best-effort; a write failure never sinks the step — the slice was
    already printed to stdout). Creates the parent dir. Atomic-ish via a temp file + replace."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception:  # noqa — persistence is best-effort; the printed slice is the contract
        pass


def _fresh_meta():
    """A meta skeleton seeded like run()'s — the same real_time/force_fetch scaffolding, so every step's
    meta reads consistently whether it ran standalone or off state."""
    return {"warnings": [], "real_time": True, "force_fetch": True}


# ─────────────────────────────────────────── money-lite hydrate (fast bucket math, no positions detail)
def _hydrate_money(client, strat, meta):
    """The FAST per-strategy money pull for the `money` step: ONE strategy_get_clearinghouse_state call
    per wallet → account_value / idle_withdrawable / deployed ONLY. This is exactly the bucket math from
    fetch_strategies.hydrate (the shared-idle de-dup across the main+xyz views), WITHOUT the positions
    detail, the live DSL/ratchet pull, the closed-history read, or the market enrichment — those are the
    slow parts and belong to the `strategies`/`positions` steps. Fail-open: a read error leaves the
    strategy money-less + a meta.warnings note, never crashes."""
    try:
        ch = _ok(client.mcp_call("strategy_get_clearinghouse_state", strategy_wallet=strat["wallet"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"clearinghouse {strat['wallet'][:8]} failed: {e}")
        return strat
    dex_av, dex_wd = {}, {}
    for dex in ("main", "xyz"):
        d = _field(ch, dex, default={}) if isinstance(ch, dict) else {}
        ms = _field(d, "marginSummary", "margin_summary", default={}) or {}
        dex_av[dex] = _f(ms, "accountValue", "account_value", default=0.0)
        dex_wd[dex] = _f(d, "withdrawable", default=0.0)
    # main + xyz are two VIEWS of ONE wallet — `withdrawable` is the SHARED idle, mirrored in both; count
    # it ONCE (see fetch_strategies.hydrate for the full derivation). deployed = each DEX's own position
    # equity (accountValue − shared idle), summed. wallet_value = main.av + xyz.av − shared_idle.
    shared_idle = max(dex_wd.get("main", 0.0), dex_wd.get("xyz", 0.0))
    deployed = sum(max(0.0, dex_av.get(dex, 0.0) - shared_idle) for dex in ("main", "xyz"))
    strat["idle_withdrawable"] = round(shared_idle, 2)
    strat["deployed"] = round(deployed, 2)
    strat["account_value"] = round(shared_idle + deployed, 2)
    return strat


def fetch_strategy_money(client, meta):
    """The FAST money-map strategy fetch: enumerate ACTIVE strategies (same strategy_list call + wallet
    extraction as fetch_strategies) and money-lite-hydrate each wallet in parallel. Returns lightweight
    strategy rows carrying name / wallet / strategy_id / status / total_funded / total_withdrawn plus the
    account_value / idle_withdrawable / deployed money fields — NO profile / dsl / protected / positions /
    closed (those are the `strategies` step). Deliberately DOES NOT read the runtime registry or catalog
    (both are for the mandate read, not the money map). Fail-open: []. Mirrors fetch_strategies' skeleton
    so the two agree on the wallet set + the bucket math."""
    try:
        sl = _ok(client.mcp_call("strategy_list", status=["ACTIVE"], timeout=20))
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"strategy_list failed: {e}")
        return []
    rows = sl if isinstance(sl, list) else _field(sl, "strategies", "data", default=[])
    strategies = []
    for s in (rows or []):
        wallet = _field(s, "strategyWalletAddress", "strategy_wallet_address", "walletAddress")
        if not wallet:
            continue
        strategies.append({
            "name": _field(s, "tradingStrategyName", "name", default="strategy"),
            "wallet": wallet,
            "strategy_id": _field(s, "id", "strategyId", "strategy_id"),
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
        })
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(lambda s: _hydrate_money(client, s, meta), strategies))
    except Exception:  # noqa — fail-open to sequential
        strategies = [_hydrate_money(client, s, meta) for s in strategies]
    return strategies


def _money_totals(embedded, strategies, portfolio_totals):
    """The three-bucket money map — the SAME classification compute() does, over money-lite strategy rows
    (which carry idle_withdrawable / deployed / account_value). idle_in_embedded / idle_in_strategies /
    deployed_in_positions + grand_total_usd + reconciles. No exposure/signals here (those need the full
    positions detail — the `positions` step)."""
    idle_strat = round(sum(_num(s.get("idle_withdrawable")) or 0.0 for s in strategies), 2)
    deployed = round(sum(_num(s.get("deployed")) or 0.0 for s in strategies), 2)
    idle_emb = embedded.get("idle_total") or 0.0
    strat_acct = round(sum(_num(s.get("account_value")) or 0.0 for s in strategies), 2)
    grand_total = round(idle_emb + strat_acct, 2)
    totals = {
        "grand_total_usd": grand_total,
        "idle_in_embedded": round(idle_emb, 2),
        "idle_in_strategies": idle_strat,
        "deployed_in_positions": deployed,
        "strategy_account_value": strat_acct,
        "portfolio_total_balance_usd": portfolio_totals.get("total_balance_usd"),
        "portfolio_total_withdrawable": portfolio_totals.get("total_withdrawable"),
    }
    pbal = portfolio_totals.get("total_balance_usd")
    totals["reconciles"] = (pbal is None) or (abs(pbal - grand_total) <= max(2.0, 0.01 * grand_total))
    return totals


# ─────────────────────────────────────────────── self-heal: full strategies[] in state (for step 2 / 3)
def _ensure_full_strategies_in_state(client, state, want_market, meta):
    """Return the FULLY-hydrated strategies[] (positions + DSL + closed + profile/mandate) — from the state
    file when the `strategies` step already ran, else recompute the full fetch right here (so the
    `strategies` and `positions` steps each work STANDALONE). Also rehydrates the embedded wallet +
    portfolio totals from state (or re-fetches). Merges its work back into state for the next step.
    Returns (embedded, strategies, portfolio_totals)."""
    embedded = state.get("embedded_wallet")
    portfolio_totals = state.get("portfolio_totals")
    strategies = state.get("strategies_full")
    if isinstance(embedded, dict) and isinstance(strategies, list) and isinstance(portfolio_totals, dict):
        return embedded, strategies, portfolio_totals
    # state absent/partial → recompute the full pull (embedded + fully-hydrated strategies). The market
    # enrichment is the `positions` step's job — skip it here (want_market only gates step 3's fold).
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    state["embedded_wallet"] = embedded
    state["portfolio_totals"] = portfolio_totals
    state["strategies_full"] = strategies
    state.setdefault("meta_warnings", [])
    state["meta_warnings"] = meta.get("warnings", [])
    state["registry_source"] = meta.get("registry_source")
    state["catalog_source"] = meta.get("catalog_source")
    state["profile_source"] = meta.get("profile_source")
    return embedded, strategies, portfolio_totals


# ──────────────────────────────────────────── step subcommands (fast, resumable, standalone)
def step_money(client, want_market=True, state_path=None):
    """STEP 1 `money` — the FAST money map the agent NARRATES FIRST. Embedded idle + each strategy wallet's
    account_value/withdrawable → the three buckets (idle_in_embedded / idle_in_strategies /
    deployed_in_positions) + grand_total_usd + reconciles. Persists the strategy list + wallets so
    `strategies`/`positions` don't re-enumerate. FAST: no positions detail, no DSL/ratchet, no closed
    history, no market. `want_market` is accepted for a uniform step signature but unused here."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = _fresh_meta()
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategy_money(client, meta)
    totals = _money_totals(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    # persist the money-lite strategy rows (name/wallet/id/status/money) so the later steps reuse the
    # wallet set; the full hydrate (positions/DSL/closed/profile) is the `strategies` step's self-heal.
    state["embedded_wallet"] = embedded
    state["portfolio_totals"] = portfolio_totals
    state["strategies_money"] = strategies
    state["totals"] = totals
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {"totals": totals, "embedded_wallet": embedded, "strategies": strategies, "meta": meta}


def step_strategies(client, want_market=True, state_path=None):
    """STEP 2 `strategies` — the per-strategy detail (the verdict surface). Reads state (or self-heals the
    full fetch when state is absent): fully-hydrated `strategies[]` (mandate/DSL from the registry +
    `protected` + closed/realized) + `strategy_groups[]` (a strategy is ALL its wallets). Runs the runtime
    registry + catalog reads here (the mandate source). NO market enrichment (that's `positions`)."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = _fresh_meta()
    embedded, strategies, portfolio_totals = _ensure_full_strategies_in_state(
        client, state, want_market, meta)
    # carry forward any warnings the self-heal fetch (or an earlier step) recorded
    for w in state.get("meta_warnings", []):
        if w not in meta["warnings"]:
            meta["warnings"].append(w)
    meta["registry_source"] = state.get("registry_source", meta.get("registry_source"))
    meta["catalog_source"] = state.get("catalog_source", meta.get("catalog_source"))
    meta["profile_source"] = state.get("profile_source", meta.get("profile_source"))
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    state["strategies_full"] = strategies
    state["strategy_groups"] = strategy_groups
    state["meta_warnings"] = meta.get("warnings", [])
    state["has_multi_wallet_strategy"] = meta.get("has_multi_wallet_strategy", False)
    _save_state(state_path, state)
    return {"strategies": strategies, "strategy_groups": strategy_groups, "meta": meta}


def step_positions(client, want_market=True, state_path=None):
    """STEP 3 `positions` — position-level analysis. Reads the full strategies[] from state (self-heals if
    absent), runs the per-asset market enrichment (`market_24h_pct`/`vs_market` — the fan-out isolated
    HERE), then computes `exposure` + `signals` off the full positions detail. Skipped-to-no-fold when
    --no-market (positions keep their bucket math; market fields stay absent)."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = _fresh_meta()
    embedded, strategies, portfolio_totals = _ensure_full_strategies_in_state(
        client, state, want_market, meta)
    for w in state.get("meta_warnings", []):
        if w not in meta["warnings"]:
            meta["warnings"].append(w)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    meta.setdefault("has_multi_wallet_strategy", False)
    # REBUILD strategy_groups AFTER the market fold so the persisted groups reference the market-enriched
    # positions — this is exactly run()'s order (enrich_market → group_strategies), keeping the shared
    # state after the full pipeline byte-consistent with `all`.
    strategy_groups = group_strategies(strategies, meta)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    # persist the enriched strategies (market fields now folded onto positions) + exposure/signals + the
    # refreshed groups (over the enriched positions).
    state["strategies_full"] = strategies
    state["strategy_groups"] = strategy_groups
    state["exposure"] = exposure
    state["signals"] = signals
    state["totals"] = totals            # the full totals (incl. unrealized_pnl from positions)
    state["meta_warnings"] = meta.get("warnings", [])
    state["has_multi_wallet_strategy"] = meta.get("has_multi_wallet_strategy", False)
    _save_state(state_path, state)
    return {"strategies": strategies, "strategy_groups": strategy_groups, "exposure": exposure,
            "signals": signals, "totals": totals, "meta": meta}


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    for label, tool, kw in (("user_get_me", "user_get_me", {}),
                            ("account_get_portfolio", "account_get_portfolio", {"forceFetch": True}),
                            ("strategy_list", "strategy_list", {})):
        try:
            out[label] = client.mcp_call(tool, timeout=20, **kw)
        except Exception as e:  # noqa
            out[label] = {"error": str(e)}
    return out


_STEPS = ("money", "strategies", "positions", "all")
_STEP_FNS = {"money": step_money, "strategies": step_strategies, "positions": step_positions}


def _all_and_persist(client, want_market, state_path):
    """`all` = the composed one-shot. Runs the UNCHANGED `run()` (its output is byte-identical to the
    pre-steps engine) and ALSO writes the shared state file (the same shape the steps build) so an `all`
    run can seed a later narrow step. The state write never alters the printed dict."""
    result = run(client, want_market=want_market)
    if state_path is None:
        state_path = _default_state_path()
    state = {
        "embedded_wallet": result.get("embedded_wallet"),
        "strategies_full": result.get("strategies"),
        "strategy_groups": result.get("strategy_groups"),
        "totals": result.get("totals"),
        "exposure": result.get("exposure"),
        "signals": result.get("signals"),
        "meta_warnings": (result.get("meta") or {}).get("warnings", []),
        "registry_source": (result.get("meta") or {}).get("registry_source"),
        "catalog_source": (result.get("meta") or {}).get("catalog_source"),
        "profile_source": (result.get("meta") or {}).get("profile_source"),
        "has_multi_wallet_strategy": (result.get("meta") or {}).get("has_multi_wallet_strategy", False),
    }
    _save_state(state_path, state)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # optional leading positional STEP (money|strategies|positions|all); default `all` = the composed
    # one-shot (unchanged output + shape). Parsed before argparse so the flags stay shared.
    step = "all"
    if argv and not argv[0].startswith("-"):
        cand = argv[0]
        if cand not in _STEPS:
            print(json.dumps({"strategies": [], "meta": {"error": f"unknown step {cand!r}; "
                                                         f"expected one of {', '.join(_STEPS)}"}}))
            return 1
        step, argv = cand, argv[1:]

    ap = argparse.ArgumentParser(
        description="senpi portfolio engine (real-time wallet taxonomy + analysis). Optional leading STEP: "
                    "money|strategies|positions|all (default all = the composed one-shot). Steps share a "
                    "state file so later steps don't re-fetch.")
    ap.add_argument("--no-market", action="store_true", help="skip per-asset market enrichment")
    ap.add_argument("--state", default=None,
                    help="shared state file path (default <tempdir>/senpi-portfolio/state.json)")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    # `step` was already peeled off argv above; feed the remainder (flags only).
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"strategies": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    want_market = not args.no_market
    try:
        if step == "all":
            result = _all_and_persist(client, want_market, args.state)
        else:
            fn = _STEP_FNS[step]
            result = fn(client, want_market=want_market, state_path=args.state)
    except Exception as e:  # noqa
        print(json.dumps({"strategies": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
