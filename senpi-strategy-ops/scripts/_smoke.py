#!/usr/bin/env python3
"""Run a strategy's `scan(inputs, ctx)` ONCE, out of the runtime, against live MCP — the smoke test.

Two consumers share this engine:
  - deploy.py    — a pre-fund gate: refuse to fund wallets if scan() throws / returns a non-list /
                   emits a signal whose shape violates signal_data_schema (the whole divergence-play
                   incident was the cost of NOT having this gate).
  - diagnose.py  — `--run-scan`: show the literal scan() return + traceback + any schema violations,
                   to tell "runs but emits nothing" (thresholds) from "emits a bad shape" (dropped).

Why a subprocess: scan() is author code that does live I/O and (when buggy) can hang or loop. Running
it in a child with a wall-clock timeout means a bad scan can't wedge or crash the deploy, and its
module/sys.path state never leaks between instances. Invoke this file as
`python3 _smoke.py --run …` to BE that child; import it for the library API (smoke / validate_signals).

FIDELITY / SAFETY — the child rebuilds the runtime's `ctx` faithfully enough to catch the failure
modes, and enforces the SAME read-only MCP boundary the real scaffold does (see scan-contract.md):
every money/state-mutating tool raises PermissionError BEFORE any network call. The block is enforced on
BOTH ctx.senpi_mcp AND (via _install_readonly_guard) the mcp_client class itself, so a scan that imports
MCPClient directly can't bypass it. A smoke test can read the market with the strategy's token; it can
NEVER place, close, or edit a trade.

  verdict = smoke(runtime_path, external_scanner_dict, wallet)   # -> {status, detail, signals, ...}
  status ∈ clean | empty | threw | bad-return | bad-shape | unloadable | timeout | setup-error
           ^pass    ^warn   ^block   ^block       ^block       ^block       ^warn     ^warn
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

# The read-only boundary the real scaffold enforces (scan-contract.md → "Mutations blocked"). Kept
# verbatim so a smoke run can never mutate money/state even if the scan is buggy or hostile.
BLOCKED_TOOLS = frozenset({
    "create_position", "close_position", "edit_position", "cancel_order", "send_usdc",
    "transfer_spot_to_perps", "strategy_create", "strategy_create_custom_strategy", "strategy_close",
    "strategy_close_positions", "strategy_update", "strategy_pause", "strategy_top_up",
    "strategy_withdraw_funds", "strategy_bridge_funds_from_hyperliquid_to_evm", "ratchet_stop_add",
    "ratchet_stop_edit", "ratchet_stop_delete", "user_claim_referral_rewards",
})

# A syntactically valid zero wallet — used when a smoke test runs BEFORE the strategy wallet exists
# (deploy pre-fund gate). Read-only clearinghouse/account calls on it just return "no positions",
# which is the correct view for a fresh deploy; the scan still exercises its full market-read path.
ZERO_WALLET = "0x0000000000000000000000000000000000000000"

BLOCK = ("threw", "bad-return", "bad-shape", "unloadable")  # a real package defect — do not fund
WARN = ("empty", "timeout", "setup-error")                  # inconclusive or benignly quiet — proceed with a note
_CALL_TIMEOUT = 20                             # per read-only MCP call inside the child (s)

_SCHEMA_PY = {"string": str, "number": (int, float), "boolean": bool,
              "object": dict, "array": list}


# ======================================================================================
# signal validation — the same rules the scaffold applies at intake (scan-contract.md)
# ======================================================================================

def validate_signals(signals, schema):
    """Return a flat list of human violation strings ([] == every signal is well-formed). Mirrors the
    scaffold's intake validation: top-level asset/direction/margin/leverage + the data{} schema
    (unknown key / missing required / wrong type). A signal the scaffold would silently DROP shows up
    here as a violation — that silent drop is exactly how a BARREN scanner "emits nothing"."""
    out = []
    schema = schema if isinstance(schema, dict) else {}
    for idx, sig in enumerate(signals):
        tag = f"signal[{idx}]"
        if not isinstance(sig, dict):
            out.append(f"{tag}: not a dict (got {type(sig).__name__})")
            continue
        asset = sig.get("asset")
        if not (isinstance(asset, str) and asset.strip()):
            out.append(f"{tag}: 'asset' missing/empty (required non-empty string)")
        d = sig.get("direction")
        if not (isinstance(d, str) and d.strip().upper() in ("LONG", "SHORT")):
            out.append(f"{tag}: 'direction' must be LONG/SHORT (got {d!r})")
        for fld in ("marginPct", "marginUsd", "leverage"):
            if fld in sig:
                v = sig[fld]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                    out.append(f"{tag}: '{fld}' present but not a positive number ({v!r})")
        if "marginPct" in sig and isinstance(sig["marginPct"], (int, float)) \
                and not isinstance(sig["marginPct"], bool) and sig["marginPct"] > 100:
            out.append(f"{tag}: 'marginPct' {sig['marginPct']} > 100 (it is a percent of withdrawable)")
        # data{} vs signal_data_schema
        data = sig.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            out.append(f"{tag}: 'data' must be a map (got {type(data).__name__})")
            continue
        for key, spec in schema.items():
            spec = spec if isinstance(spec, dict) else {}
            required = spec.get("required", True) is not False
            if required and key not in data:
                out.append(f"{tag}: data.{key} missing (required by signal_data_schema)")
        for key, val in data.items():
            if key not in schema:
                out.append(f"{tag}: data.{key} not declared in signal_data_schema (unknown key → reject)")
                continue
            want = (schema[key] or {}).get("type") if isinstance(schema.get(key), dict) else None
            pytype = _SCHEMA_PY.get(want)
            if pytype is None:
                continue
            bad = isinstance(val, bool) and pytype is not bool  # bool is an int subclass — exclude
            if bad or not isinstance(val, pytype):
                out.append(f"{tag}: data.{key} is {type(val).__name__}, schema wants {want}")
    return out


def sizing_warnings(signals, strategy_margin_pct=None):
    """Flag economically-broken-but-SCHEMA-VALID sizing — chiefly the decimal/percent confusion that
    sizes 0.18% instead of 18% (marginPct is a PERCENT: 18 == 18%, NEVER a fraction 0.18). A signal with
    marginPct 0.18 passes every schema check — positive, ≤100 — yet opens ~$3.60 on a $2k wallet. The
    strongest tell: the signal's marginPct is ~100× smaller than the runtime.yaml's strategy.margin_pct
    (the author set the percent in YAML but emitted the fraction in scan). Advisory, not a hard reject."""
    out = []
    for i, s in enumerate(signals if isinstance(signals, list) else []):
        if not isinstance(s, dict):
            continue
        mp = s.get("marginPct")
        if isinstance(mp, bool) or not isinstance(mp, (int, float)) or mp <= 0:
            continue
        if isinstance(strategy_margin_pct, (int, float)) and not isinstance(strategy_margin_pct, bool) \
                and strategy_margin_pct > 0:
            ratio = strategy_margin_pct / mp
            if 40 <= ratio <= 250:  # ~100× → the signal is a fraction where the runtime speaks percent
                out.append(f"signal[{i}] marginPct {mp} vs runtime strategy.margin_pct {strategy_margin_pct} "
                           f"(~{round(ratio)}×) — the runtime sizes off the SIGNAL's {mp}%, i.e. ~{mp}% of the "
                           f"wallet. marginPct is a PERCENT: emit {strategy_margin_pct:g}, not {mp:g}.")
                continue
        if mp < 1:
            out.append(f"signal[{i}] marginPct {mp:g} sizes <1% of the wallet (~${mp * 20:.2f} on a $2k wallet)"
                       f" — marginPct is a PERCENT (18 == 18%); did you mean {mp * 100:g}?")
    return out


# ======================================================================================
# orchestration (imported by deploy.py / diagnose.py)
# ======================================================================================

def _unknown_asset_violations(signals, live_assets):
    """Emitted assets whose EXACT string is not a live Hyperliquid instrument — the runtime sends them
    as-is and the venue REJECTS (a bare `NVDA` when only `xyz:NVDA` is live). The xyz-prefix incident
    class. Returns violation strings with the prefixed suggestion. No live set ⇒ [] (can't check)."""
    live = live_assets if isinstance(live_assets, (set, frozenset)) else set(live_assets or [])
    if not live:
        return []
    out = []
    for i, s in enumerate(signals if isinstance(signals, list) else []):
        if not isinstance(s, dict):
            continue
        a = s.get("asset")
        if not isinstance(a, str) or not a or a in live:
            continue
        sug = next((c for c in (f"xyz:{a}", a.upper(), f"xyz:{a.upper()}") if c in live), None)
        hint = f" — did you mean '{sug}'?" if sug else " — not a live instrument"
        out.append(f"signal[{i}]: asset '{a}' will REJECT on Hyperliquid{hint} "
                   f"(XYZ equities/indices/metals need the 'xyz:' prefix)")
    return out


def _resolve_scan_dir(runtime_path, es):
    sub = (es.get("path") or ".").lstrip("./")
    return (Path(runtime_path).parent / sub).resolve()


def smoke(runtime_path, es, wallet=None, timeout=None, strategy_margin_pct=None, live_assets=None):
    """Run one scan() and classify it. Returns:
      {status, detail, signals, violations, sizing_warnings, traceback, returned_repr, n_signals}
    status ∈ clean|empty|threw|bad-return|bad-shape|unloadable|timeout|setup-error (BLOCK/WARN sets above).
    `sizing_warnings` (ALWAYS present) flags schema-valid-but-broken sizing (marginPct percent/fraction confusion)."""
    scan_dir = _resolve_scan_dir(runtime_path, es)
    entry = es.get("entrypoint", "scan.py")
    inputs = es.get("inputs") or {}
    schema = es.get("signal_data_schema") or {}
    name = es.get("name") or "external_scanner"
    interval = es.get("interval_seconds", 30)
    tmo = int(timeout or es.get("timeout_seconds") or es.get("interval_seconds") or 60)

    raw = _run_child(scan_dir, entry, inputs, wallet or ZERO_WALLET, name, interval, tmo)
    if raw.get("timed_out"):
        return {"status": "timeout", "detail": f"scan() did not finish in {tmo}s "
                "(a single-pass scan should be quick — check for a loop/sleep or a slow MCP read)",
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": raw.get("stderr", ""),
                "returned_repr": "", "n_signals": 0}
    if not raw.get("spawned"):
        return {"status": "setup-error", "detail": raw.get("error", "could not run the scan child"),
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": raw.get("stderr", ""),
                "returned_repr": "", "n_signals": 0}
    if raw.get("threw"):
        return {"status": "threw", "detail": raw.get("error", "scan() raised"),
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": raw.get("traceback", ""),
                "returned_repr": "", "n_signals": 0}
    if raw.get("setup_error"):  # child could not import the module / build ctx
        # A package DEFECT (module won't import, no callable scan, entrypoint missing) can NEVER register a
        # scanner — funding it just parks capital that can't trade (the divergence-play failure). BLOCK it.
        # A mere HARNESS problem (no python, or no token to build ctx here) is inconclusive — WARN + proceed.
        if raw.get("defect"):
            return {"status": "unloadable", "detail": raw.get("error", "scan module failed to load"),
                    "signals": [], "violations": [], "sizing_warnings": [], "traceback": raw.get("traceback", ""),
                    "returned_repr": "", "n_signals": 0}
        return {"status": "setup-error", "detail": raw.get("error", "scan module did not load"),
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": raw.get("traceback", ""),
                "returned_repr": "", "n_signals": 0}
    signals = raw.get("signals")
    if not isinstance(signals, list):
        return {"status": "bad-return",
                "detail": f"scan() returned {type(signals).__name__}, must return a list[dict] "
                          "(return [] when there's nothing to trade — never None)",
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": "",
                "returned_repr": raw.get("returned_repr", ""), "n_signals": 0}
    violations = validate_signals(signals, schema)
    if live_assets:  # strict: an emitted asset not in the live instrument list will REJECT on-venue
        violations = violations + _unknown_asset_violations(signals, live_assets)
    sizing = sizing_warnings(signals, strategy_margin_pct)
    if violations:
        return {"status": "bad-shape",
                "detail": f"{len(signals)} signal(s) emitted but the shape is invalid — the runtime "
                          "would DROP these silently (→ a BARREN scanner that 'never trades')",
                "signals": signals, "violations": violations, "sizing_warnings": sizing, "traceback": "",
                "returned_repr": raw.get("returned_repr", ""), "n_signals": len(signals)}
    if not signals:
        return {"status": "empty",
                "detail": "scan() ran cleanly and returned [] (no candidates this tick). Normal for a "
                          "quiet strategy (regime/tail-risk); for an always-on scanner it suggests the "
                          "thresholds are too tight or the universe is empty.",
                "signals": [], "violations": [], "sizing_warnings": [], "traceback": "",
                "returned_repr": raw.get("returned_repr", ""), "n_signals": 0}
    detail = f"scan() ran cleanly and emitted {len(signals)} valid signal(s)"
    if sizing:
        detail += f" — but {len(sizing)} SIZING WARNING(S): {sizing[0]}"
    return {"status": "clean", "detail": detail,
            "signals": signals, "violations": [], "sizing_warnings": sizing, "traceback": "",
            "returned_repr": raw.get("returned_repr", ""), "n_signals": len(signals)}


def _run_child(scan_dir, entry, inputs, wallet, name, interval, timeout):
    """Spawn `python3 _smoke.py --run …` and parse its JSON verdict. Never raises — every failure
    (spawn error, timeout, garbage output) comes back as a dict the caller classifies."""
    payload = json.dumps({"dir": str(scan_dir), "entry": entry, "inputs": inputs, "wallet": wallet,
                          "name": name, "interval": interval})
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--run"],
            input=payload, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired as e:
        return {"spawned": True, "timed_out": True, "stderr": (e.stderr or "")[-2000:] if isinstance(e.stderr, str) else ""}
    except Exception as e:  # noqa: BLE001 — spawn itself failed (no python? bad path?)
        return {"spawned": False, "error": f"could not spawn scan child: {e}"}
    out = (proc.stdout or "").strip()
    # the child prints exactly one JSON line on its last line; tolerate leading stray prints
    doc = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                doc = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if doc is None:
        return {"spawned": True, "setup_error": True,
                "error": f"scan child produced no parseable result (exit {proc.returncode})",
                "traceback": (proc.stderr or "")[-2000:]}
    doc["spawned"] = True
    return doc


# ======================================================================================
# the child: python3 _smoke.py --run   (reads one JSON job on stdin, prints one JSON verdict)
# ======================================================================================

class _ReadOnlyMCP:
    """ctx.senpi_mcp — .call_tool(name, args) with the scaffold's read-only boundary enforced."""

    def __init__(self, client):
        self._c = client

    def call_tool(self, name, args=None):
        if name in BLOCKED_TOOLS:
            raise PermissionError(
                f"{name} is a mutation tool — blocked by the read-only scan boundary (scan() may only read)")
        return self._c.mcp_call(name, timeout=_CALL_TIMEOUT, **(args or {}))


class _MemState:
    """ctx.state — a bounded, in-memory stand-in for the runtime's transactional store. Faithful to
    the API scan() sees (last/recent/append/len); persistence/rollback are the runtime's job, not the
    smoke test's, so append here just mutates the local ring."""

    def __init__(self, bound):
        self._bound = int(bound or 0)
        self._rows = []

    def last(self):
        return self._rows[-1] if self._rows else None

    def recent(self, n):
        return self._rows[-int(n):] if n else []

    def append(self, record):
        if self._bound <= 0:
            raise RuntimeError("state history is disabled (state_history_max_count 0/unset)")
        if not isinstance(record, dict):
            raise TypeError("ctx.state.append(record): record must be a dict")
        self._rows.append(record)
        if len(self._rows) > self._bound:
            self._rows = self._rows[-self._bound:]

    def __len__(self):
        return len(self._rows)


class _Ctx:
    """ctx — frozen (exactly the 5 members scan-contract.md lists; setattr is rejected)."""
    __slots__ = ("senpi_mcp", "state", "wallet", "scanner_name", "interval_seconds", "_frozen")

    def __init__(self, senpi_mcp, state, wallet, scanner_name, interval_seconds):
        object.__setattr__(self, "senpi_mcp", senpi_mcp)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "wallet", wallet)
        object.__setattr__(self, "scanner_name", scanner_name)
        object.__setattr__(self, "interval_seconds", interval_seconds)
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, k, v):
        raise AttributeError("ctx is frozen — scan() must not mutate it")


def _install_readonly_guard():
    """Harden the read-only boundary at the mcp_client layer, so a scan that imports MCPClient DIRECTLY
    (bypassing ctx.senpi_mcp) still cannot mutate money/state. Idempotent; no-op if mcp_client is absent."""
    try:
        import mcp_client as _mc
    except Exception:  # noqa: BLE001 — no client to guard; the scan can't reach the network anyway
        return
    if getattr(_mc.MCPClient.mcp_call, "_senpi_readonly", False):
        return
    _orig = _mc.MCPClient.mcp_call

    def _guarded(self, tool, *args, **kwargs):
        if tool in BLOCKED_TOOLS:
            raise PermissionError(
                f"{tool} is a mutation tool — blocked by the read-only smoke boundary (scan() may only read)")
        return _orig(self, tool, *args, **kwargs)

    _guarded._senpi_readonly = True
    _mc.MCPClient.mcp_call = _guarded


def _child_main():
    """Import the scan module, build ctx, call scan() once, print one JSON verdict line."""
    try:
        job = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"setup_error": True, "error": f"bad job payload: {e}"}))
        return
    scan_dir = Path(job["dir"])
    entry = job.get("entry", "scan.py")
    scan_file = scan_dir / entry

    # sys.path: scan_dir FIRST so `import scoring` resolves to the sibling; then this scripts/ dir so
    # the child can import mcp_client. (No scoring.py in scripts/, so no shadowing.)
    scripts_dir = str(Path(__file__).resolve().parent)
    sys.path.insert(0, scripts_dir)
    sys.path.insert(0, str(scan_dir))
    _install_readonly_guard()  # block mutating tools even if the scan imports MCPClient directly (before exec)

    if not scan_file.is_file():
        print(json.dumps({"setup_error": True, "defect": True,
                          "error": f"entrypoint not found: {scan_file}"}))
        return
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_smoke_scan_target", str(scan_file))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)   # runs top-level `import scoring`
    except Exception:  # noqa: BLE001 — import/scoring failure is a real package defect
        print(json.dumps({"setup_error": True, "defect": True, "error": "scan module failed to import",
                          "traceback": traceback.format_exc()[-4000:]}))
        return
    scan = getattr(mod, "scan", None)
    if not callable(scan):
        print(json.dumps({"setup_error": True, "defect": True,
                          "error": f"{entry} does not export a callable scan(inputs, ctx)"}))
        return

    try:
        from mcp_client import MCPClient
        ctx = _Ctx(_ReadOnlyMCP(MCPClient()), _MemState(job.get("state_bound", 200)),
                   job.get("wallet", ZERO_WALLET), job.get("name", "external_scanner"),
                   job.get("interval", 30))
    except Exception:  # noqa: BLE001
        print(json.dumps({"setup_error": True, "error": "could not build scan ctx",
                          "traceback": traceback.format_exc()[-4000:]}))
        return

    try:
        result = scan(job.get("inputs") or {}, ctx)
    except Exception as e:  # noqa: BLE001 — scan() raising IS the finding (block the deploy)
        print(json.dumps({"threw": True, "error": f"scan() raised: {type(e).__name__}: {e}",
                          "traceback": traceback.format_exc()[-4000:]}))
        return

    # JSON-round-trip the return so the parent gets plain data (and we detect non-serializable junk)
    try:
        signals = json.loads(json.dumps(result, default=str)) if result is not None else None
    except (TypeError, ValueError):
        signals = None
    print(json.dumps({"signals": signals, "returned_repr": repr(result)[:2000]}))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true", help="(internal) act as the scan-runner child.")
    a = ap.parse_args(argv[1:])
    if a.run:
        _child_main()
        return 0
    ap.error("nothing to do — import this module, or run the child with --run")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
