#!/usr/bin/env python3
"""Shared helpers for the lifecycle scripts: openclaw CLI runner + tolerant JSON digging +
runtime/strategy lookups. Used by deploy.py and close.py.

The openclaw/MCP JSON shapes are not strictly pinned, so every extractor tries a few key
spellings and degrades gracefully (returns None / []) rather than throwing on a missing field.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import subprocess


# ---- openclaw CLI ----

def run_cli(args, timeout=60):
    """Run a CLI command; return (returncode, stdout, stderr). rc=-1 on spawn failure/timeout."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s: {' '.join(args)}"


def cli_json(args, timeout=60):
    """Run a CLI command expected to emit JSON on stdout; return the parsed object or None."""
    rc, out, _err = run_cli(args, timeout)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # tolerate a leading log line or two before the JSON body
        for i, ch in enumerate(out):
            if ch in "[{":
                try:
                    return json.loads(out[i:])
                except json.JSONDecodeError:
                    break
        return None


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


# ---- strategy lookups (MCP strategy_list) ----

def list_strategies(mcp, timeout=15):
    try:
        res = mcp.mcp_call("strategy_list", timeout=timeout)
    except Exception:  # noqa: BLE001 — degrade to empty on transport error
        return []
    return find_list(res, "strategies")


def strategy_id_of(s):
    return dig(s, "strategyId", "id", "strategy_id")


def strategy_status(s):
    return dig(s, "status", "state")


def strategy_wallet(s):
    return dig(s, "strategyWalletAddress", "walletAddress", "wallet", "address")


def strategy_skill(s):
    return dig(s, "skillName", "skill_name", "skill")


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
