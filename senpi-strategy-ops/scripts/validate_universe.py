#!/usr/bin/env python3
"""Strict universe validator — every HARDCODED asset ticker in a strategy package MUST
exist as a live Hyperliquid instrument. Exits 1 on any unknown ticker.

WHY THIS EXISTS: a ticker that isn't a live instrument makes `market_get_asset_data`
throw `candleSnapshot status=500`; a well-behaved scan does `if not md: continue`, so it
skips that asset with NO error and NO trade — a pure silent no-emit. Hardcoding a ticker
you didn't verify against the live list is the exact "every guess fails silently" trap.
This gate makes shipping an unverified ticker impossible. (Caught on asia-ai 2026-06-25:
`xyz:NASDAQ` doesn't exist — HL's broad index is `xyz:XYZ100` — so the hedge sleeve 500'd
every tick and never traded.)

Auth-free: queries the public HL Info `meta` (main dex → bare names like SOL) and
`meta dex=xyz` (xyz dex → `xyz:`-prefixed names like xyz:TSM). Derived-universe
strategies (no hardcoded inputs.universe / inputs.asset / catalog.assets) pass trivially.

Usage:  python3 validate_universe.py strategies/<id>
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import urllib.request

import yaml

HL_INFO = "https://api.hyperliquid.xyz/info"


def _hl(body):
    req = urllib.request.Request(
        HL_INFO, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def live_tickers():
    """Set of every live instrument name: main dex bare (SOL), xyz dex prefixed (xyz:TSM)."""
    valid = set()
    main = _hl({"type": "meta"})
    for a in main.get("universe", []):
        if a.get("name") and not a.get("isDelisted"):
            valid.add(a["name"])
    xyz = _hl({"type": "meta", "dex": "xyz"})
    for a in (xyz.get("universe", []) if isinstance(xyz, dict) else []):
        n = a.get("name")
        if n and not a.get("isDelisted"):
            valid.add(n if n.startswith("xyz:") else "xyz:" + n)
    return valid


def collect_tickers(pkg_dir):
    """Every hardcoded ticker in the package: catalog.assets + each instance's
    runtime.yaml external_scanner inputs.universe / inputs.asset."""
    tickers = set()
    man = yaml.safe_load(open(os.path.join(pkg_dir, "strategy.yaml"))) or {}
    for a in ((man.get("catalog") or {}).get("assets") or []):
        if isinstance(a, str):
            tickers.add(a)
    for inst in (man.get("instances") or []):
        rt_rel = inst.get("runtime")
        if not rt_rel:
            continue
        rt = yaml.safe_load(open(os.path.join(pkg_dir, rt_rel))) or {}
        for s in (rt.get("scanners") or []):
            if not isinstance(s, dict) or s.get("type") != "external_scanner":
                continue
            inp = s.get("inputs", {}) or {}
            uni = inp.get("universe")
            if isinstance(uni, list):
                tickers.update(x for x in uni if isinstance(x, str))
            if isinstance(inp.get("asset"), str):
                tickers.add(inp["asset"])
    return {t for t in tickers if t}


class FetchError(Exception):
    """Raised when the live HL instrument universe can't be fetched."""


def unknown_tickers(pkg_dir):
    """Sorted list of hardcoded tickers in the package that are NOT live HL instruments.
    [] when all are live OR the strategy is derived (no hardcoded universe). Raises
    FetchError if the live universe can't be fetched (so callers can fail-safe). This is
    the importable entry point used by deploy.py's create preflight."""
    tickers = collect_tickers(pkg_dir)
    if not tickers:
        return []
    try:
        valid = live_tickers()
    except Exception as e:  # noqa: BLE001
        raise FetchError(str(e))
    return sorted(t for t in tickers if t not in valid)


def main(argv):
    if len(argv) != 2:
        print("usage: validate_universe.py strategies/<id>")
        return 2
    pkg = argv[1].rstrip("/")
    if not os.path.isfile(os.path.join(pkg, "strategy.yaml")):
        print(f"ERROR: {pkg}/strategy.yaml not found")
        return 2
    tickers = collect_tickers(pkg)
    if not tickers:
        print(f"OK  {pkg}: no hardcoded universe (derived) — nothing to validate.")
        return 0
    try:
        missing = unknown_tickers(pkg)
    except FetchError as e:
        print(f"ERROR {pkg}: could not fetch the live HL instrument universe: {e}")
        return 3
    if missing:
        print(f"FAIL {pkg}: {len(missing)} of {len(tickers)} ticker(s) are NOT live "
              f"Hyperliquid instruments:")
        for m in missing:
            print(f"    {m}")
        return 1
    print(f"OK  {pkg}: all {len(tickers)} hardcoded tickers are live Hyperliquid instruments.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
