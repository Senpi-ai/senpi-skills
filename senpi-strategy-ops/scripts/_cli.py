#!/usr/bin/env python3
"""Shared helpers for the lifecycle scripts: openclaw CLI runner + tolerant JSON digging +
runtime/strategy lookups. Used by deploy.py and close.py.

The openclaw/MCP JSON shapes are not strictly pinned, so every extractor tries a few key
spellings and degrades gracefully (returns None / []) rather than throwing on a missing field.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
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

def list_runtimes():
    obj = cli_json(["openclaw", "senpi", "runtime", "list", "--json"])
    return find_list(obj, "runtimes")


def runtime_name(rt):
    return dig(rt, "name", "id", "runtime_id", "runtimeId")


def runtime_wallet(rt):
    return dig(rt, "address", "wallet", "walletAddress", "strategyWalletAddress")


def runtime_running(rt):
    st = dig(rt, "status", "state", "running")
    if isinstance(st, bool):
        return st
    return str(st).lower() in ("running", "active", "live", "ok", "true")


def find_runtime(name):
    for rt in list_runtimes():
        if runtime_name(rt) == name:
            return rt
    return None


def find_runtime_by_wallet(wallet):
    """Find a live runtime bound to a wallet address (close maps strategy→runtime by wallet,
    so it doesn't depend on the runtime name)."""
    if not wallet:
        return None
    w = str(wallet).lower()
    for rt in list_runtimes():
        if str(runtime_wallet(rt) or "").lower() == w:
            return rt
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
