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
import sys

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

CATALOG_REF = os.environ.get("SENPI_SKILLS_REF", "strategy-v2")
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


def _profile_from_runtime_yaml(text):
    """Parse one deployed runtime.yaml TEXT into the universal profile fields. Returns a dict (possibly
    partial) or None if the text doesn't parse to a mapping. Never raises."""
    doc = _yaml_loads(text)
    if not isinstance(doc, dict):
        return None
    dsl_preset, has_exit = _dsl_preset_summary(doc.get("exit"))
    return {
        "runtime_name": doc.get("name"),
        "group": doc.get("group"),
        "version": doc.get("version"),
        "description": _collapse_ws(doc.get("description")),   # the UNIVERSAL "what it does / how it works"
        "dsl_preset": dsl_preset,
        "has_exit": bool(has_exit),
    }


def load_runtime_registry(meta):
    """wallet_lower → runtime-profile for every deployed strategy the runtime has registered.

    SOURCE OF TRUTH for a strategy's "what it does / how it works" — read from the DEPLOYED runtime.yaml
    the runtime itself registers in installed_runtimes.json (state dir). UNIVERSAL: covers user-authored
    strategies too, not just catalog templates. Read-guarded + fail-open: any problem → ({}, None). A
    meta.warnings note is added ONLY for a real parse error, not for a simply-absent registry file.
    Returns (map, source)."""
    state_dir = os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR
    path = os.path.join(state_dir, REGISTRY_FILENAME)
    if not os.path.isfile(path):          # absent registry is normal, not an error
        return {}, None
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as e:  # noqa — a corrupt registry is a real parse error worth surfacing
        meta.setdefault("warnings", []).append(
            f"runtime registry unreadable ({e}); mandates fall back to catalog")
        return {}, None
    entries = raw.get("runtimes", raw) if isinstance(raw, dict) else raw
    out = {}
    for entry in (entries if isinstance(entries, list) else []):
        if not isinstance(entry, dict):
            continue
        wallet = entry.get("wallet")
        if not wallet:
            continue
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
    return out, "registry"


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
        "description": None, "runtime_name": None, "group": None, "dsl_preset": None,
        "belief_plain": None, "thesis": None, "archetype": None, "sub_style": None,
        "asset_classes": None, "risk_level": None, "time_horizon": None, "tagline": None,
        "source": None,
    }
    if registry_prof:
        prof["description"] = registry_prof.get("description")
        prof["runtime_name"] = registry_prof.get("runtime_name")
        prof["group"] = registry_prof.get("group")
        prof["dsl_preset"] = registry_prof.get("dsl_preset")
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
        for keyer in ("strategy_wallet", "trader_address", "asset"):
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
    registry, registry_src = load_runtime_registry(meta)   # wallet_lower → runtime profile
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
        strategies.append({
            "name": _field(s, "tradingStrategyName", "name", default="strategy"),
            "wallet": wallet,
            "status": _field(s, "status", default="ACTIVE"),
            "total_funded": _f(s, "totalFunded", "total_funded", default=None),
            "total_withdrawn": _f(s, "totalWithdrawn", "total_withdrawn", default=None),
            "skill_name": skill_name,
            "skill_version": skill_version,
            "protected": bool(has_exit or skill_name),
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
        strat["closed"] = fetch_closed(client, strat["wallet"], meta)
        return strat

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            strategies = list(ex.map(hydrate, strategies))
    except Exception:  # noqa
        strategies = [hydrate(s) for s in strategies]
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


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_market=True):
    meta = {"warnings": [], "real_time": True, "force_fetch": True}
    embedded, portfolio_totals = fetch_embedded(client, meta)
    strategies = fetch_strategies(client, meta)
    if want_market and strategies:
        enrich_market(client, strategies, meta)
    totals, exposure, signals = compute(embedded, strategies, portfolio_totals)
    meta["strategy_count"] = len(strategies)
    if not strategies and not embedded.get("address"):
        meta["degraded"] = "no wallet data — check the token is USER-scoped"
    return {
        "as_of": "live",
        "totals": totals,           # the three buckets — NEVER conflate them
        "embedded_wallet": embedded,
        "strategies": strategies,
        "exposure": exposure,
        "signals": signals,
        "meta": meta,
    }


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi portfolio engine (real-time wallet taxonomy + analysis)")
    ap.add_argument("--no-market", action="store_true", help="skip per-asset market enrichment")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
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

    try:
        result = run(client, want_market=not args.no_market)
    except Exception as e:  # noqa
        print(json.dumps({"strategies": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
