#!/usr/bin/env python3
"""senpi-smart-money engine — cohort + divergence + near-term flow (hidden, deterministic).

The agent (LLM) runs this via the OpenClaw `exec` tool, reads the JSON on stdout, and NARRATES
"where smart money is moving" (see SKILL.md). The script does the heavy, deterministic data work —
build the proven cohort vs the crowd, aggregate net positioning, find the divergences, pull the
near-term Leaderboard/Hyperfeed flow — and the LLM does all the prose, the "why", and the CTAs.

  python3 smartmoney.py               # `all` (default): full pull (cohorts + divergence + near-term)
  python3 smartmoney.py cohorts       # STEP 1 — the proven-vs-crowd divergence table (the headline)
  python3 smartmoney.py near_term     # STEP 2 — the 4h leaderboard confirmation, layered on state
  python3 smartmoney.py all           # one-shot fallback: the full composed dict (unchanged output)
  python3 smartmoney.py --no-near     # skip the leaderboard / Hyperfeed near-term layer
  python3 smartmoney.py --state f.json     # override the shared step-state file path
  python3 smartmoney.py --fixture f.json   # offline: recorded MCP-response map (tests)
  python3 smartmoney.py --dry         # dump raw MCP responses for schema debugging

RUN IT IN STEPS: a full pull is several MCP round-trips (the per-wallet cohort fetch is the heavy
one). Run it as FAST, RESUMABLE STEPS the agent narrates between — `cohorts` (the headline
divergence table) then `near_term` (the 4h confirmation, layered onto the persisted cohorts). Each
step is a separate `exec` call, prints ONLY its slice + `meta`, and shares a JSON state file so the
second step never re-runs the first. `all` (the default) stays the byte-identical composed one-shot.

Modeled on the whalehunter strategy's cohort engine (same definitions + bias math), and on
senpi-strategy-discover's hidden-engine pattern: guarded I/O, fails open, always valid JSON.

⚠ discovery_* requires a USER-scoped SENPI_AUTH_TOKEN (it resolves a user id). With an app-scoped
token the cohort pulls return empty and the engine reports `meta.cohorts_unavailable` — narrate that
honestly rather than pretending the smart cohort is flat.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────────────────────── cohort definitions (mirror whalehunter)
SMART_MIN_REALIZED = 1_000_000      # "smartest money": lifetime realized gains >= $1M
CROWD_MIN_REALIZED = 10_000         # "crowd": $10k ..
CROWD_MAX_REALIZED = 100_000        #        .. $100k realized
PAGE_SIZE = 1000                    # discovery_get_top_traders page size (ALL_TIME realized ranking)
MAX_PAGES = 6                       # page this deep to REACH the crowd band (it sits far below the smart top)
SAMPLE_CAP = 150                    # cap each cohort's membership sample (bounds trader_state load)
STATE_BATCH = 50                    # discovery_get_trader_state batch size
MIN_MEMBERS = 5                     # need this many in a cohort on a coin to trust its net bias
LEAN_THRESHOLD = 0.40               # |net/gross| past this = the cohort is meaningfully directional
DIVERGENCE_MIN_GAP = 0.50           # smart-vs-crowd bias gap to flag a divergence (opposite signs always flag)


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


def _traders_of(data):
    """Normalize a discovery response into a list of trader dicts."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("traders", "data", "results"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


# ──────────────────────────────────────────────────────────────── client
def _get_client():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from mcp_client import MCPClient
    return MCPClient()


class _FixtureClient:
    """Offline stand-in. Keys a call by (tool, dex) or (tool, first trader_address) so a fixture can
    return DIFFERENT trader-state for the smart vs crowd cohort. Falls back to the bare tool name."""
    def __init__(self, recorded):
        self._r = recorded

    def mcp_call(self, tool, timeout=12, **kw):
        if "dex" in kw:
            k = f"{tool}::{kw['dex']}"
            if k in self._r:
                return self._r[k]
        addrs = kw.get("trader_addresses")
        if addrs:
            k = f"{tool}::{str(addrs[0]).lower()}"
            if k in self._r:
                return self._r[k]
        return self._r.get(tool)


# ──────────────────────────────────────────────────────────────── cohort building (mirror whalehunter)
def _realized(t):
    # LIFETIME realized PnL — never fall back to total profitAndLoss (not monotonic with the realized sort)
    return _f(t, "realizedProfitAndLoss", "realized_profit_and_loss", "profit_and_loss_realized",
              "realizedPnl", "realized_pnl", default=0.0)


def build_cohorts(client, meta):
    """Smart cohort (realized >= $1M) + crowd cohort ($10k..$100k) from the ALL_TIME realized-PnL
    ranking. The ranking is DESC by realized, so the smart cohort is at the top and the crowd lives
    thousands of ranks deeper — page by offset until both are sampled or the page drops below the
    crowd floor."""
    smart, crowd, seen = [], [], set()
    pages = 0
    for page in range(MAX_PAGES):
        try:
            resp = client.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME",
                                   sort_by="PROFIT_AND_LOSS_REALIZED", open_position_filter=False,
                                   limit=PAGE_SIZE, offset=page * PAGE_SIZE, timeout=20)
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"top_traders page {page} failed: {e}")
            break
        rows = _traders_of(_ok(resp))
        if not rows:
            break
        pages += 1
        page_top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            addr = str(_field(t, "address", "trader_address", "wallet", default="")).lower()
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= SMART_MIN_REALIZED:
                if len(smart) < SAMPLE_CAP:
                    smart.append(addr); seen.add(addr)
            elif CROWD_MIN_REALIZED <= rp <= CROWD_MAX_REALIZED:
                if len(crowd) < SAMPLE_CAP:
                    crowd.append(addr); seen.add(addr)
        if len(smart) >= SAMPLE_CAP and len(crowd) >= SAMPLE_CAP:
            break
        if page_top is not None and page_top < CROWD_MIN_REALIZED:
            break   # whole page below the crowd floor — we've paged past both cohorts
    meta["cohort_pages"] = pages
    return smart, crowd


def _signed_notional(p):
    szi = _f(p, "szi", "size")
    val = _f(p, "positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * _f(p, "entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def cohort_bias(client, addrs, meta, label):
    """Aggregate a cohort's NET positioning per coin: bias = net/gross in [-1,+1]
    (+1 = all long, -1 = all short), plus long/short member counts. Batched."""
    per = {}
    for i in range(0, len(addrs), STATE_BATCH):
        batch = addrs[i:i + STATE_BATCH]
        try:
            resp = client.mcp_call("discovery_get_trader_state", trader_addresses=batch, timeout=20)
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"{label} trader_state batch failed: {e}")
            continue
        for t in _traders_of(_ok(resp)):
            for p in (t.get("openPositions") or t.get("open_positions") or []):
                if not isinstance(p, dict):
                    continue
                coin = p.get("coin") or p.get("asset")
                sn = _signed_notional(p) if coin else 0.0
                if not coin or sn == 0:
                    continue
                d = per.setdefault(coin, {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0})
                d["net"] += sn
                d["gross"] += abs(sn)
                d["n_long" if sn > 0 else "n_short"] += 1
    for d in per.values():
        d["bias"] = round(d["net"] / d["gross"], 3) if d["gross"] > 0 else 0.0
        d["members"] = d["n_long"] + d["n_short"]
        d["net"] = round(d["net"], 2)
    return per


# ──────────────────────────────────────────────────────────────── signal computation
def _dir(bias):
    return "long" if bias > 0 else ("short" if bias < 0 else "flat")


def smart_conviction(smart_per):
    """Where the proven cohort is most net-directional (the 'where smart money is leaning' headline)."""
    out = []
    for coin, d in smart_per.items():
        if d["members"] >= MIN_MEMBERS and abs(d["bias"]) >= LEAN_THRESHOLD:
            out.append({"asset": coin, "bias": d["bias"], "direction": _dir(d["bias"]),
                        "members": d["members"], "n_long": d["n_long"], "n_short": d["n_short"],
                        "net_usd": d["net"]})
    out.sort(key=lambda x: abs(x["bias"]) * x["members"], reverse=True)
    return out


def divergences(smart_per, crowd_per):
    """Where the proven cohort and the crowd are on OPPOSITE sides (or far apart) — the core signal."""
    out = []
    for coin, sd in smart_per.items():
        if sd["members"] < MIN_MEMBERS:
            continue
        cd = crowd_per.get(coin)
        if not cd or cd["members"] < MIN_MEMBERS:
            continue
        gap = round(sd["bias"] - cd["bias"], 3)
        opposite = (sd["bias"] > 0) != (cd["bias"] > 0) and sd["bias"] != 0 and cd["bias"] != 0
        if opposite or abs(gap) >= DIVERGENCE_MIN_GAP:
            out.append({
                "asset": coin, "gap": gap, "opposite_sides": opposite,
                "smart_bias": sd["bias"], "smart_direction": _dir(sd["bias"]),
                "smart_members": sd["members"], "smart_net_usd": sd["net"],
                "crowd_bias": cd["bias"], "crowd_direction": _dir(cd["bias"]),
                "crowd_members": cd["members"],
            })
    out.sort(key=lambda x: (x["opposite_sides"], abs(x["gap"])), reverse=True)
    return out


# ──────────────────────────────────────────────────────────────── near-term layer (Leaderboard / Hyperfeed)
def fetch_near_term(client, meta):
    """The 4h-window momentum layer — health-gated. Returns None cleanly if Hyperfeed is down.
    leaderboard_get_markets = where the hot cohort's gains concentrate; momentum_events = the live
    entry/scale/exit flow (is the move building or fading)."""
    try:
        status = client.mcp_call("leaderboard_get_status", timeout=8)
    except Exception as e:  # noqa
        meta.setdefault("warnings", []).append(f"near-term layer unavailable (status: {e})")
        return None
    if _ok(status) is None:
        meta.setdefault("warnings", []).append("near-term layer unavailable (Hyperfeed unreachable)")
        return None
    near = {"status": _ok(status)}
    for label, tool in (("concentration", "leaderboard_get_markets"),
                        ("hot_traders", "leaderboard_get_top"),
                        ("momentum_events", "leaderboard_get_momentum_events")):
        try:
            near[label] = _ok(client.mcp_call(tool, timeout=10))
        except Exception as e:  # noqa
            meta.setdefault("warnings", []).append(f"{tool} failed: {e}")
            near[label] = None
    return near


# ──────────────────────────────────────────────────────────────── orchestration
def run(client, want_near=True):
    meta = {"warnings": []}
    smart_addrs, crowd_addrs = build_cohorts(client, meta)
    meta["smart_cohort_size"] = len(smart_addrs)
    meta["crowd_cohort_size"] = len(crowd_addrs)

    if not smart_addrs and not crowd_addrs:
        meta["cohorts_unavailable"] = (
            "no cohort data — discovery_get_top_traders returned empty. discovery_* needs a "
            "USER-scoped SENPI_AUTH_TOKEN; an app-scoped token returns nothing here.")

    smart_per = cohort_bias(client, smart_addrs, meta, "smart") if smart_addrs else {}
    crowd_per = cohort_bias(client, crowd_addrs, meta, "crowd") if crowd_addrs else {}

    leaning = smart_conviction(smart_per)
    diverge = divergences(smart_per, crowd_per)
    near = fetch_near_term(client, meta) if want_near else None
    meta["near_term_available"] = near is not None

    return {
        "as_of": "live",
        "cohorts": {
            "smart": {"min_realized_usd": SMART_MIN_REALIZED, "members_sampled": len(smart_addrs),
                      "coins": len(smart_per)},
            "crowd": {"realized_band_usd": [CROWD_MIN_REALIZED, CROWD_MAX_REALIZED],
                      "members_sampled": len(crowd_addrs), "coins": len(crowd_per)},
        },
        "smart_leaning": leaning,        # where the proven cohort is concentrated (headline)
        "divergences": diverge,          # smart vs crowd, opposite sides (the core signal)
        "near_term": near,               # Leaderboard / Hyperfeed 4h flow (confirm or contradict)
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── shared state file (resumable steps)
# The step subcommands (cohorts → near_term) are FAST, resumable slices that persist their work to a
# shared JSON state file so `near_term` never re-runs the heavy per-wallet cohort fetch. The agent runs
# them in sequence and NARRATES between — no single call carries the whole multi-round-trip pull (the
# per-wallet trader_state fetch is the slow one; running it inside one blocking call risks the exec
# timeout and pushes the agent to raw MCP, losing the guardrails). Each step is idempotent + fail-open:
# a missing/corrupt state file → recompute (self-heal); every step also works STANDALONE (just slower).
# `all` writes the same state but prints the full composed dict (byte-identical to the pre-steps output).
# State default: <tempdir>/senpi-smart-money/state.json.
STATE_SUBDIR = "senpi-smart-money"


def _default_state_path():
    """Default shared-state path: <tempdir>/senpi-smart-money/state.json. Uses tempfile.gettempdir()
    (never $HOME)."""
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
    """Merge-write the shared state JSON (best-effort; a write failure never sinks the step — the slice
    was already printed to stdout). Creates the parent dir. Atomic-ish via a temp file + replace."""
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


def _cohorts_slice(client, meta):
    """The `cohorts` engine core — the heavy per-wallet read, factored so both the step and the self-heal
    use it. Builds the smart/crowd cohorts, aggregates each cohort's per-coin bias, and computes the
    `smart_leaning` headline + the `divergences` table. Produces EXACTLY the values run() folds into the
    composed dict (same cohort order → same batched fixture lookups → byte-identical to `all`). Returns
    (smart_addrs, crowd_addrs, smart_per, crowd_per, leaning, diverge)."""
    smart_addrs, crowd_addrs = build_cohorts(client, meta)
    meta["smart_cohort_size"] = len(smart_addrs)
    meta["crowd_cohort_size"] = len(crowd_addrs)
    if not smart_addrs and not crowd_addrs:
        meta["cohorts_unavailable"] = (
            "no cohort data — discovery_get_top_traders returned empty. discovery_* needs a "
            "USER-scoped SENPI_AUTH_TOKEN; an app-scoped token returns nothing here.")
    smart_per = cohort_bias(client, smart_addrs, meta, "smart") if smart_addrs else {}
    crowd_per = cohort_bias(client, crowd_addrs, meta, "crowd") if crowd_addrs else {}
    leaning = smart_conviction(smart_per)
    diverge = divergences(smart_per, crowd_per)
    return smart_addrs, crowd_addrs, smart_per, crowd_per, leaning, diverge


def _cohorts_payload(smart_addrs, crowd_addrs, smart_per, crowd_per, leaning, diverge):
    """Assemble the `cohorts` step's output slice — the same `cohorts`/`smart_leaning`/`divergences`
    fields (identical shape) that run() emits."""
    return {
        "cohorts": {
            "smart": {"min_realized_usd": SMART_MIN_REALIZED, "members_sampled": len(smart_addrs),
                      "coins": len(smart_per)},
            "crowd": {"realized_band_usd": [CROWD_MIN_REALIZED, CROWD_MAX_REALIZED],
                      "members_sampled": len(crowd_addrs), "coins": len(crowd_per)},
        },
        "smart_leaning": leaning,        # where the proven cohort is concentrated (headline)
        "divergences": diverge,          # smart vs crowd, opposite sides (the core signal)
    }


# ──────────────────────────────────────────────────── step subcommands (fast, resumable, standalone)
def step_cohorts(client, want_near=True, state_path=None):
    """STEP 1 `cohorts` — the headline read the agent NARRATES FIRST. Runs the heavy per-wallet cohort
    fetch (build_cohorts + cohort_bias) → the proven-vs-crowd `divergences` table + the `smart_leaning`
    headline + the `cohorts` sample sizes. Persists the cohort payload to state so `near_term` layers on
    top without re-running the heavy fetch. Prints ONLY its slice + `meta`."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = {"warnings": []}
    (smart_addrs, crowd_addrs, smart_per, crowd_per,
     leaning, diverge) = _cohorts_slice(client, meta)
    payload = _cohorts_payload(smart_addrs, crowd_addrs, smart_per, crowd_per, leaning, diverge)
    # persist the cohort slice so near_term reuses it (and can report the same as_of/cohorts).
    state["as_of"] = "live"
    state["cohorts"] = payload["cohorts"]
    state["smart_leaning"] = payload["smart_leaning"]
    state["divergences"] = payload["divergences"]
    state["meta_warnings"] = meta.get("warnings", [])
    if "cohorts_unavailable" in meta:
        state["cohorts_unavailable"] = meta["cohorts_unavailable"]
    else:
        state.pop("cohorts_unavailable", None)
    _save_state(state_path, state)
    result = {"as_of": "live"}
    result.update(payload)
    result["meta"] = meta
    return result


def step_near_term(client, want_near=True, state_path=None):
    """STEP 2 `near_term` — the lighter 4h overlay, layered onto the persisted cohorts. Reads the cohort
    slice from state (self-heals by re-running the cohort fetch when state is absent/corrupt), pulls the
    health-gated Leaderboard/Hyperfeed flow, and emits `near_term` alongside the persisted cohort headline
    so the agent can narrate confirm/contradict in one slice. Prints its slice + `meta`."""
    if state_path is None:
        state_path = _default_state_path()
    state = _load_state(state_path)
    meta = {"warnings": list(state.get("meta_warnings", []))}
    # self-heal: reuse the persisted cohort slice, else recompute it here so near_term works standalone.
    cohorts = state.get("cohorts")
    leaning = state.get("smart_leaning")
    diverge = state.get("divergences")
    if not (isinstance(cohorts, dict) and isinstance(leaning, list) and isinstance(diverge, list)):
        cmeta = {"warnings": []}
        (smart_addrs, crowd_addrs, smart_per, crowd_per,
         leaning, diverge) = _cohorts_slice(client, cmeta)
        payload = _cohorts_payload(smart_addrs, crowd_addrs, smart_per, crowd_per, leaning, diverge)
        cohorts = payload["cohorts"]
        leaning, diverge = payload["smart_leaning"], payload["divergences"]
        # fold the recompute's warnings/flag in (dedup-preserving order) and reseed the cohort slice.
        for w in cmeta.get("warnings", []):
            if w not in meta["warnings"]:
                meta["warnings"].append(w)
        state["as_of"] = "live"
        state["cohorts"] = cohorts
        state["smart_leaning"] = leaning
        state["divergences"] = diverge
        if "cohorts_unavailable" in cmeta:
            state["cohorts_unavailable"] = cmeta["cohorts_unavailable"]
    if state.get("cohorts_unavailable"):
        meta["cohorts_unavailable"] = state["cohorts_unavailable"]

    near = fetch_near_term(client, meta) if want_near else None
    meta["near_term_available"] = near is not None

    state["near_term"] = near
    state["meta_warnings"] = meta.get("warnings", [])
    _save_state(state_path, state)
    return {
        "as_of": "live",
        "cohorts": cohorts,              # the persisted headline, so the overlay reads in context
        "smart_leaning": leaning,
        "divergences": diverge,
        "near_term": near,               # Leaderboard / Hyperfeed 4h flow (confirm or contradict)
        "meta": meta,
    }


# ──────────────────────────────────────────────────────────────── CLI
def _dry(client):
    out = {}
    try:
        out["discovery_get_top_traders(page0)"] = client.mcp_call(
            "discovery_get_top_traders", time_frame="ALL_TIME", sort_by="PROFIT_AND_LOSS_REALIZED",
            open_position_filter=False, limit=5, offset=0, timeout=20)
    except Exception as e:  # noqa
        out["discovery_get_top_traders(page0)"] = {"error": str(e)}
    for tool, kw in (("leaderboard_get_status", {}), ("leaderboard_get_markets", {})):
        try:
            out[tool] = client.mcp_call(tool, timeout=8, **kw)
        except Exception as e:  # noqa
            out[tool] = {"error": str(e)}
    return out


_STEPS = ("cohorts", "near_term", "all")
_STEP_FNS = {"cohorts": step_cohorts, "near_term": step_near_term}


def _all_and_persist(client, want_near, state_path):
    """`all` = the composed one-shot. Runs the UNCHANGED `run()` (its output is byte-identical to the
    pre-steps engine) and ALSO writes the shared state file (same shape the steps build) so an `all` run
    can seed a later `near_term`. The state write never alters the printed dict."""
    result = run(client, want_near=want_near)
    if state_path is None:
        state_path = _default_state_path()
    state = {
        "as_of": result.get("as_of"),
        "cohorts": result.get("cohorts"),
        "smart_leaning": result.get("smart_leaning"),
        "divergences": result.get("divergences"),
        "near_term": result.get("near_term"),
        "meta_warnings": (result.get("meta") or {}).get("warnings", []),
    }
    cu = (result.get("meta") or {}).get("cohorts_unavailable")
    if cu:
        state["cohorts_unavailable"] = cu
    _save_state(state_path, state)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # optional leading positional STEP (cohorts|near_term|all); default `all` = the composed one-shot
    # (unchanged output + shape). Parsed before argparse so the flags stay shared.
    step = "all"
    if argv and not argv[0].startswith("-"):
        cand = argv[0]
        if cand not in _STEPS:
            print(json.dumps({"smart_leaning": [], "meta": {"error": f"unknown step {cand!r}; "
                                                            f"expected one of {', '.join(_STEPS)}"}}))
            return 1
        step, argv = cand, argv[1:]

    ap = argparse.ArgumentParser(
        description="senpi smart-money engine (cohort + divergence + near-term). Optional leading STEP: "
                    "cohorts|near_term|all (default all = the composed one-shot). Steps share a state "
                    "file so near_term doesn't re-run the heavy cohort fetch.")
    ap.add_argument("--no-near", action="store_true", help="skip the leaderboard / Hyperfeed near-term layer")
    ap.add_argument("--state", default=None,
                    help="shared state file path (default <tempdir>/senpi-smart-money/state.json)")
    ap.add_argument("--fixture", help="offline: path to a recorded MCP-response map (tests only)")
    ap.add_argument("--dry", action="store_true", help="dump raw MCP responses for schema debugging")
    # `step` was already peeled off argv above; feed the remainder (flags only).
    args = ap.parse_args(argv)

    if args.fixture:
        try:
            with open(args.fixture) as f:
                client = _FixtureClient(json.load(f))
        except Exception as e:  # noqa
            print(json.dumps({"smart_leaning": [], "meta": {"error": f"fixture load failed: {e}"}}))
            return 1
    else:
        try:
            client = _get_client()
        except Exception as e:  # noqa
            print(json.dumps({"smart_leaning": [], "meta": {"error": f"mcp client init failed: {e}"}}))
            return 1

    if args.dry:
        print(json.dumps(_dry(client), ensure_ascii=False, indent=2, default=str))
        return 0

    want_near = not args.no_near
    try:
        if step == "all":
            result = _all_and_persist(client, want_near, args.state)
        else:
            fn = _STEP_FNS[step]
            result = fn(client, want_near=want_near, state_path=args.state)
    except Exception as e:  # noqa  — last-resort guard; layer functions already fail open
        print(json.dumps({"smart_leaning": [], "meta": {"error": f"engine failure: {e}"}}))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
