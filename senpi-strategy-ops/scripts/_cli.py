#!/usr/bin/env python3
"""Shared helpers for the lifecycle scripts: openclaw CLI runner + tolerant JSON digging +
runtime/strategy lookups. Used by deploy.py and close.py.

The openclaw/MCP JSON shapes are not strictly pinned, so every extractor tries a few key
spellings and degrades gracefully (returns None / []) rather than throwing on a missing field.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import re
import subprocess


# ---- openclaw CLI ----

def run_cli(args, timeout=60):
    """Run a CLI command; return (returncode, stdout, stderr). rc=-1 on spawn failure/timeout.

    Suppresses the senpi plugin's info logs (which it prints to STDOUT and which otherwise corrupt
    `--json` output) by forcing SENPI_LOG_LEVEL=error in the child env."""
    env = dict(os.environ, SENPI_LOG_LEVEL="error")
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s: {' '.join(args)}"


def _extract_json(text):
    """Recover a JSON object/array from output that may be polluted with leading/trailing log lines
    (e.g. `[plugins] [senpi-runtime] …` printed to stdout). Tries a clean parse, then raw_decode at
    every `{`/`[` offset and returns the LARGEST successful parse (the real payload, not a log line)."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    best = None
    best_len = -1
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, (dict, list)) and (end - i) > best_len:
            best, best_len = obj, end - i
    return best


def cli_json(args, timeout=60):
    """Run a CLI command expected to emit JSON on stdout; return the parsed object or None."""
    rc, out, _err = run_cli(args, timeout)
    if rc != 0 or not out.strip():
        return None
    return _extract_json(out)


# ---- tolerant extraction ----

def dig(obj, *keys, default=None):
    """Return obj[k] for the first key present (case-insensitive), else default."""
    if not isinstance(obj, dict):
        return default
    lower = {k.lower(): v for k, v in obj.items()}
    for k in keys:
        if k in obj:
            return obj[k]
        if k.lower() in lower:
            return lower[k.lower()]
    return default


def find_list(obj, *wrapper_keys):
    """Locate a list payload: a bare list, or nested under a common wrapper key."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in wrapper_keys + ("data", "result", "items"):
            v = dig(obj, k)
            if isinstance(v, list):
                return v
        # single nested dict that itself wraps a list
        d = obj.get("data") if isinstance(obj.get("data"), dict) else None
        if d:
            return find_list(d, *wrapper_keys)
    return []


# ---- runtime lookups (openclaw senpi runtime ...) ----

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(s):
    return _ANSI.sub("", s)


def wallet_match(a, b):
    """Compare two wallet strings, tolerating the truncated `0xabc…wxyz` / `0xabc...wxyz` form that
    `runtime list` prints in a TTY (full addresses when piped). Case-insensitive."""
    if not a or not b:
        return False
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        for sep in ("...", "…"):
            if sep in x:
                pre, _, suf = x.partition(sep)
                if pre and suf and y.startswith(pre) and y.endswith(suf):
                    return True
    return False


def list_runtimes():
    """All runtimes (running AND stopped) by parsing `runtime list` text. NOTE on runtime v3: `runtime
    list` has no --json (human text only), and `status --json` is *flaky* — it transiently returns an
    empty `statuses[]` even while runtimes are running — so it is NOT a reliable inventory. The text
    table (id / wallet / source / status) is authoritative; use `status -r <id>` only for health."""
    rc, out, _err = run_cli(["openclaw", "senpi", "runtime", "list"])
    if rc != 0:
        return []
    rows, seen_header = [], False
    for line in out.splitlines():
        line = _strip_ansi(line).strip()
        if not line:
            continue
        low = line.lower()
        if not seen_header:
            if low.startswith("id") and "status" in low and "wallet" in low:
                seen_header = True
            continue
        if "no runtimes" in low:
            break
        parts = [p for p in re.split(r"\s{2,}|\t+", line) if p]
        if len(parts) >= 2:
            rows.append({"name": parts[0], "wallet": parts[1],
                         "source": parts[2] if len(parts) > 2 else None, "status": parts[-1]})
    return rows


def runtime_name(rt):
    return dig(rt, "name", "id", "runtime_id", "runtimeId", "runtimeName")


def runtime_wallet(rt):
    # text-list entries carry "wallet" directly; `status -r` entries nest it under components — deep search.
    w = dig(rt, "wallet", "address", "walletAddress", "strategyWalletAddress", "strategyWallet")
    return w or _deep_first(rt, ["address", "wallet", "walletAddress", "strategyWalletAddress"])


def runtime_running(rt):
    st = dig(rt, "status", "state", "running", "health", "overallHealth")
    if isinstance(st, bool):
        return st
    s = str(st).lower()
    if s in ("running", "active", "live", "ok", "true", "healthy", "degraded"):
        return True
    return False


def find_runtime(name):
    for rt in list_runtimes():
        if runtime_name(rt) == name:
            return rt
    return None


def find_runtime_by_wallet(wallet):
    """Find a runtime bound to a wallet address (close maps strategy→runtime by wallet,
    so it doesn't depend on the runtime name). Tolerates truncated TTY wallets."""
    if not wallet:
        return None
    for rt in list_runtimes():
        if wallet_match(runtime_wallet(rt), wallet):
            return rt
    return None


def _deep_first(obj, keys):
    """Deep-search a nested obj for the first value under any of `keys` (case-insensitive)."""
    if isinstance(obj, dict):
        v = dig(obj, *keys)
        if v is not None:
            return v
        for x in obj.values():
            r = _deep_first(x, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _deep_first(x, keys)
            if r is not None:
                return r
    return None


def runtime_status(name, timeout=15):
    """`openclaw senpi status -r <name> --json` — lightweight per-runtime health (or None).
    The gateway intermittently returns an empty statuses[] for a running runtime, so retry a few times."""
    obj = None
    for _ in range(4):
        obj = cli_json(["openclaw", "senpi", "status", "-r", name, "--json"], timeout)
        if obj and find_list(obj, "statuses"):
            return obj
    return obj


def health_verdict(status_json):
    """Map a `senpi status` payload to healthy | degraded | unhealthy | None (shape-tolerant)."""
    h = _deep_first(status_json, ["overallHealth", "health", "overall", "status"])
    h = str(h).lower() if h is not None else None
    if h in ("healthy", "ok", "running", "live", "true"):
        return "healthy"
    if h in ("degraded", "warn", "warning"):
        return "degraded"
    if h in ("unhealthy", "failed", "error", "down", "false"):
        return "unhealthy"
    return None


def active_positions(status_json):
    """Best-effort active-position count from a `senpi status` payload (None if not found)."""
    n = _deep_first(status_json, ["activePositions", "activePositionCount", "openPositions",
                                  "positionCount", "numPositions", "positions"])
    if isinstance(n, bool):
        return None
    if isinstance(n, (int, float)):
        return int(n)
    if isinstance(n, str) and n.strip().lstrip("-").isdigit():  # the gateway stringifies numbers
        return int(n)
    if isinstance(n, list):
        return len(n)
    return None


# ---- strategy lookups (MCP strategy_list) ----

def list_strategies(mcp, timeout=15):
    try:
        res = mcp.mcp_call("strategy_list", timeout=timeout)
    except Exception:  # noqa: BLE001 — degrade to empty on transport error
        return []
    return find_list(res, "strategies")


def strategy_obj(x):
    """Unwrap the strategy dict from a response or list entry. strategy_create_custom_strategy nests
    it at data.strategy; strategy_list entries may be flat or wrapped. Tries data.strategy → strategy
    → data → x, returning the first dict that carries an id/status field."""
    if not isinstance(x, dict):
        return {}
    for path in (("data", "strategy"), ("strategy",), ("data",)):
        cur = x
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, dict) and (dig(cur, "strategyId", "id", "strategy_id")
                                      or dig(cur, "status", "state")):
            return cur
    return x  # already the strategy object (flat)


def strategy_id_of(s):
    return dig(strategy_obj(s), "strategyId", "id", "strategy_id")


def strategy_status(s):
    return dig(strategy_obj(s), "status", "state")


def strategy_wallet(s):
    return dig(strategy_obj(s), "strategyWalletAddress", "walletAddress", "wallet", "address")


def strategy_skill(s):
    """The package id a strategy was created under. Lives in strategyMetadata.skillName (set by
    strategy_create_custom_strategy's skillName arg); falls back to tradingStrategyName."""
    o = strategy_obj(s)
    meta = dig(o, "strategyMetadata", "metadata")
    if isinstance(meta, dict):
        sk = dig(meta, "skillName", "skill_name")
        if sk:
            return sk
    return dig(o, "skillName", "skill_name", "skill") or dig(o, "tradingStrategyName", "name")


# strategies in these states are done — never close them again, and they must NOT block a new deploy.
DEAD_STATUSES = ("CLOSED", "FAILED", "INACTIVE", "TERMINATED", "CLOSING_DONE")


def strategy_trader(s):
    """The trader a COPY strategy follows (None for custom/manual). Distinguishes copy-trading
    (managed by the copy engine, no runtime) from autonomous custom strategies."""
    return dig(strategy_obj(s), "traderAddress", "trader")


def strategy_type(s):
    return dig(strategy_obj(s), "strategyType", "type")


def strategy_open(s):
    return str(strategy_status(s) or "").upper() not in DEAD_STATUSES


def strategies_for(mcp, skill_name=None, strategy_id=None, wallet=None, timeout=15):
    """Return strategies matching any provided filter (skill_name / strategyId / wallet)."""
    out = []
    for s in list_strategies(mcp, timeout):
        if strategy_id is not None and strategy_id_of(s) != strategy_id:
            continue
        if skill_name is not None and strategy_skill(s) != skill_name:
            continue
        if wallet is not None and str(strategy_wallet(s) or "").lower() != str(wallet).lower():
            continue
        out.append(s)
    return out
