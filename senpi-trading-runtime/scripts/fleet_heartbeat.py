#!/usr/bin/env python3
# Senpi Fleet Heartbeat Monitor v1.0.0
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills
"""Fleet Heartbeat Monitor — proactive liveness + status digest.

Solves the "silence is ambiguous" problem: event-only notifications mean a
healthy-but-quiet agent (holding a position, or waiting for a setup) looks
identical to a dead one. An operator only discovers a dead producer — or a
quietly-winning one — by pulling the dashboard.

This monitor produces a PUSH digest on a schedule, and (with --alarm-only)
a real-time alarm when any producer goes silent. For each agent it checks:

  • LIVENESS  — producer-scan age via senpi `audit_query` on the agent's
    producer-signature MCP tool. Producers tick every 3-5 min, so a scan
    older than --silent-minutes (default 20) means the SCANNER is dead even
    if the runtime is still polling (the Dog/Owl/Lemon trap from 2026-05-21).
  • STATE     — open positions, uPnL, account value, last fill age via the
    Hyperliquid public Info API (no token needed).

Status per agent:
  🔴 SILENT   producer scan age > silent threshold  → DEAD-PRODUCER ALARM
  🟢 HOLDING  has open position(s)
  📈 TRADING  closed/opened a fill within --active-hours
  ⚪ WAITING  producer alive, flat, no recent fill (healthy idle)
  ⚫ DORMANT  no producer scans at all in the window AND flat

Delivery is pluggable: prints the digest to stdout by default; if
FLEET_WEBHOOK_URL is set, POSTs the digest there (wire it to a Telegram
bot sendMessage URL or any chat webhook).

Roster: a JSON file (see fleet-roster.example.json) mapping each agent to
its strategy wallet, senpi user_id, and producer-signature tool(s). The
signature varies per agent (e.g. leaderboard_get_markets, market_get_asset_data,
market_get_cross_asset_flows) — enumerate it with:
    grep -nE 'mcp_call\\(' <agent>/scripts/<agent>-producer.py

Usage:
    SENPI_AUTH_TOKEN=... python3 fleet_heartbeat.py --roster fleet-roster.json
    SENPI_AUTH_TOKEN=... python3 fleet_heartbeat.py --roster r.json --alarm-only
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HL_INFO = "https://api.hyperliquid.xyz/info"


# ─── senpi_runtime_helpers (for audit_query liveness) ───
def _load_client():
    here = Path(__file__).resolve().parent.parent  # senpi-trading-runtime/
    for cand in (here, Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"):
        if (cand / "senpi_runtime_helpers").is_dir():
            sys.path.insert(0, str(cand))
            break
    from senpi_runtime_helpers import SenpiClient  # type: ignore
    if not os.environ.get("SENPI_AUTH_TOKEN", "").strip():
        raise RuntimeError("SENPI_AUTH_TOKEN required for producer-liveness (audit_query).")
    return SenpiClient()


def _hl(body):
    req = urllib.request.Request(
        HL_INFO, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# ─── Per-agent checks ───────────────────────────────────────

def hl_state(wallet):
    """(account_value, [positions], total_upnl, last_fill_age_h)."""
    out = {"account_value": 0.0, "positions": [], "total_upnl": 0.0, "last_fill_age_h": None}
    try:
        cs = _hl({"type": "clearinghouseState", "user": wallet})
        out["account_value"] = float(cs.get("marginSummary", {}).get("accountValue", 0))
        for p in cs.get("assetPositions", []):
            pos = p["position"]
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            out["positions"].append({
                "coin": pos.get("coin", ""),
                "side": "LONG" if szi > 0 else "SHORT",
                "upnl": float(pos.get("unrealizedPnl", 0)),
                "roe": float(pos.get("returnOnEquity", 0)) * 100,
            })
        out["total_upnl"] = sum(p["upnl"] for p in out["positions"])
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:
        fills = _hl({"type": "userFills", "user": wallet})
        if fills:
            out["last_fill_age_h"] = (time.time() - fills[0]["time"] / 1000) / 3600.0
    except Exception:  # noqa: BLE001
        pass
    return out


def producer_scan_age_min(client, user_id, signature_tools, lookback_h=24):
    """Minutes since the agent's producer last made a signature MCP call.
    None if no scans found in the window (→ likely dead or never ran)."""
    start = datetime.fromtimestamp(time.time() - lookback_h * 3600, tz=timezone.utc).isoformat()
    newest = None
    for tool in signature_tools:
        try:
            r = client.mcp_call("audit_query", user_ids=[user_id], tool_name=tool,
                                start_time=start, limit=1)
            entries = (r or {}).get("data", {}).get("entries", [])
            if entries:
                ts = entries[0].get("timestamp")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if newest is None or dt > newest:
                        newest = dt
        except Exception:  # noqa: BLE001
            continue
    if newest is None:
        return None
    return (datetime.now(timezone.utc) - newest).total_seconds() / 60.0


def classify(scan_age_min, state, silent_min, active_h):
    if scan_age_min is not None and scan_age_min > silent_min:
        return "SILENT", "🔴"          # producer dead while (maybe) runtime alive — ALARM
    if state.get("positions"):
        return "HOLDING", "🟢"
    if scan_age_min is None:
        return "DORMANT", "⚫"          # no scans at all in window AND flat
    if state.get("last_fill_age_h") is not None and state["last_fill_age_h"] <= active_h:
        return "TRADING", "📈"
    return "WAITING", "⚪"             # alive producer, flat, no recent fill (healthy idle)


# ─── Digest ─────────────────────────────────────────────────

def build_digest(rows, silent_min):
    order = {"SILENT": 0, "DORMANT": 1, "HOLDING": 2, "TRADING": 3, "WAITING": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9), -r["state"].get("total_upnl", 0)))
    silent = [r for r in rows if r["status"] in ("SILENT", "DORMANT")]
    total_upnl = sum(r["state"].get("total_upnl", 0) for r in rows)
    lines = [
        f"🛰️ Senpi Fleet Heartbeat — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"{len(rows)} agents · open uPnL {total_upnl:+,.0f} · "
        f"{len(silent)} need attention ⚠️" if silent else
        f"{len(rows)} agents · open uPnL {total_upnl:+,.0f} · all alive ✅",
        "",
    ]
    if silent:
        lines.append("⚠️ ATTENTION:")
        for r in silent:
            age = r["scan_age_min"]
            age_s = "no scans 24h+" if age is None else f"silent {age/60:.1f}h"
            lines.append(f"  {r['emoji']} {r['name']} — producer {age_s} (scanner appears DEAD; restart detached)")
        lines.append("")
    for r in rows:
        if r["status"] in ("SILENT", "DORMANT"):
            continue
        st = r["state"]
        pos = ", ".join(f"{p['coin']} {p['side']} {p['upnl']:+.0f}" for p in st.get("positions", [])) or "flat"
        lines.append(f"  {r['emoji']} {r['name']:14s} {r['status']:8s} ${st.get('account_value',0):,.0f} | {pos}")
    return "\n".join(lines)


def deliver(text):
    print(text)
    url = os.environ.get("FLEET_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        # Telegram bot: set FLEET_WEBHOOK_URL to
        # https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHATID>
        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[fleet_heartbeat] delivery failed: {type(e).__name__}: {e}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True, help="path to fleet-roster.json")
    ap.add_argument("--silent-minutes", type=float, default=20.0)
    ap.add_argument("--active-hours", type=float, default=6.0)
    ap.add_argument("--alarm-only", action="store_true",
                    help="only emit if at least one agent is SILENT/DORMANT")
    args = ap.parse_args()

    roster = json.loads(Path(args.roster).read_text())
    client = _load_client()

    rows = []
    for a in roster:
        state = hl_state(a["strategy_wallet"])
        sigs = a.get("producer_signature") or ["leaderboard_get_markets", "market_get_asset_data"]
        if isinstance(sigs, str):
            sigs = [sigs]
        scan_age = producer_scan_age_min(client, a["user_id"], sigs)
        status, emoji = classify(scan_age, state, args.silent_minutes, args.active_hours)
        rows.append({"name": a["name"], "emoji": emoji, "status": status,
                     "scan_age_min": scan_age, "state": state})

    if args.alarm_only and not any(r["status"] in ("SILENT", "DORMANT") for r in rows):
        return
    deliver(build_digest(rows, args.silent_minutes))


if __name__ == "__main__":
    main()
