#!/usr/bin/env python3
"""validate_universe — every hardcoded ticker in a strategy package must be a live HL instrument.

A ticker that isn't live doesn't error at deploy: `market_get_asset_data` 500s, the scan skips the
name, and the strategy silently trades nothing (the `xyz:NASDAQ` incident — the real index is
`xyz:XYZ100`). This gate makes that failure loud, before money moves.

  python3 validate_universe.py strategies/<id> [strategies/<id2> …]   # exit 1 on any unknown ticker
  python3 validate_universe.py --all                                  # every package under strategies/
  python3 validate_universe.py strategies/<id> --json                 # machine-readable report

What counts as "hardcoded": ticker-shaped strings (BTC, xyz:AAPL) in `strategy.yaml` `catalog.assets`
and under any `scanners[].inputs` key whose name suggests an asset list (asset/universe/basket/
whitelist/sleeve/class*/volume*/…). Derived-universe strategies (no hardcoded names) pass trivially.
Requires SENPI_AUTH_TOKEN (reads the live instrument list once via market_list_instruments).
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import yaml  # prefer PyYAML when present
except ImportError:  # agent hosts may lack PyYAML / pip
    import _yaml as yaml  # vendored stdlib-only fallback
from mcp_client import MCPClient, MCPError  # noqa: E402


def load_yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh.read())

TICKER = re.compile(r"^(xyz:)?[A-Z][A-Z0-9]{0,11}$")
KEY_HINT = re.compile(r"(asset|universe|basket|coin|symbol|market|whitelist|sleeve|defensiv|equit|"
                      r"probe|allowed|watch|class|volume|metal|energ|indice|long|short)", re.I)
# uppercase enum-ish values that are never tickers — never flag these
NOT_TICKERS = {"WEEK", "MONTH", "DAY", "ALL", "ALL_TIME", "LONG", "SHORT", "BUY", "SELL",
               "ASC", "DESC", "AND", "OR", "TRUE", "FALSE", "NONE", "AUTO"}


def _collect(node, key_path, out):
    """Collect ticker-shaped strings from values under asset-suggesting keys."""
    if isinstance(node, dict):
        for k, v in node.items():
            _collect(v, f"{key_path}.{k}", out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, key_path, out)
    elif isinstance(node, str):
        leaf = key_path.rsplit(".", 1)[-1]
        if TICKER.match(node) and node not in NOT_TICKERS and KEY_HINT.search(leaf):
            out.add(node)


def package_tickers(pkg_dir):
    """All hardcoded ticker-shaped strings in strategy.yaml catalog + every runtime*.yaml inputs."""
    found = set()
    sy = os.path.join(pkg_dir, "strategy.yaml")
    if os.path.isfile(sy):
        doc = load_yaml(sy) or {}
        _collect((doc.get("catalog") or {}).get("assets"), "catalog.assets", found)
    for ry in sorted(glob.glob(os.path.join(pkg_dir, "**", "runtime*.yaml"), recursive=True)):
        doc = load_yaml(ry) or {}
        for sc in (doc.get("scanners") or []):
            if isinstance(sc, dict):
                _collect(sc.get("inputs"), "inputs", found)
    return found


def unknown_tickers(tickers, live):
    """Tickers with no live instrument in either form. Scanners on the xyz DEX prefix bare names
    in code (`f"xyz:{token}"`), so a bare ticker is valid if `T` or `xyz:T` is live."""
    return sorted(t for t in tickers
                  if t not in live and (":" in t or f"xyz:{t}" not in live))


def live_instruments():
    resp = MCPClient().mcp_call("market_list_instruments", timeout=25)
    data = resp.get("data", resp) if isinstance(resp, dict) else {}
    names = set()
    for inst in (data.get("instruments") or []):
        if isinstance(inst, dict) and inst.get("name") and not inst.get("is_delisted"):
            names.add(inst["name"])
    if not names:
        raise MCPError("market_list_instruments returned no instruments — cannot validate")
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify hardcoded tickers against live HL instruments")
    ap.add_argument("packages", nargs="*", help="strategy package dirs (e.g. strategies/spider)")
    ap.add_argument("--all", action="store_true", help="validate every package under strategies/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    pkgs = a.packages
    if a.all:
        pkgs = sorted(d for d in glob.glob("strategies/*") if os.path.isfile(os.path.join(d, "strategy.yaml")))
    if not pkgs:
        ap.error("give package dir(s) or --all")

    try:
        live = live_instruments()
    except Exception as e:  # noqa — no token / network down must be loud, not a silent pass
        print(json.dumps({"error": f"live instrument list unavailable: {e}"}) if a.json
              else f"ERROR: live instrument list unavailable: {e}", file=sys.stderr)
        return 2

    report, bad_total = [], 0
    for pkg in pkgs:
        tickers = package_tickers(pkg)
        unknown = unknown_tickers(tickers, live)
        bad_total += len(unknown)
        report.append({"package": pkg, "hardcoded": sorted(tickers), "unknown": unknown,
                       "ok": not unknown})
        if not a.json:
            if unknown:
                print(f"✗ {pkg}: NOT LIVE: {', '.join(unknown)}   (checked {len(tickers)} hardcoded)")
            else:
                print(f"✓ {pkg}: {len(tickers)} hardcoded ticker(s), all live" if tickers
                      else f"✓ {pkg}: derived universe (no hardcoded tickers)")
    if a.json:
        print(json.dumps({"live_count": len(live), "packages": report,
                          "ok": bad_total == 0}, ensure_ascii=False))
    return 0 if bad_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
