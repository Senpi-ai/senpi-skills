#!/usr/bin/env python3
"""Does `startTime` stay put on a position nobody touched?

The whale-open detector's entire claim is "opened N minutes ago", read from `startTime` on a single
`discovery_get_trader_state`. Ignas found production evidence (PR #675) that on the `latest: true`
path `startTime` can jump FORWARD on a position whose `szi` never changed — 4592s of age becoming
17s in fourteen seconds. The error is one-directional: a reset always makes an old position look
newly opened, which is exactly the trigger. 19 of 170 (address, coin) pairs showed it.

Every one of those observations used `latest: true`. The sweep does NOT — it takes the cached path,
which has almost no production history. So the reset may be an artifact of the fresh-fetch
re-derivation and may never touch us. This settles it on the path we actually use.

Run it as TWO turns, minutes apart — an agent cannot hold a 20-minute sleep open in a chat session:

    SENPI_AUTH_TOKEN=… python3 check_position_age.py --snapshot   # turn 1: read and save
    …wait 20 minutes, do something else…
    SENPI_AUTH_TOKEN=… python3 check_position_age.py --compare    # turn 2: read again and diff

Read-only. Exits 0 if every unchanged position kept its startTime, 1 if any reset.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smartmoney as sm            # noqa: E402  the same engine the sweep uses
from mcp_client import MCPClient   # noqa: E402


def _positions(client, wallets):
    """{(wallet, coin): (szi, startTime)} — exactly the call the sweep makes, no `latest`."""
    out = {}
    for i in range(0, len(wallets), sm.STATE_BATCH):
        resp = client.mcp_call("discovery_get_trader_state",
                               trader_addresses=wallets[i:i + sm.STATE_BATCH],
                               include_position_age=True, timeout=30)
        for t in sm._traders_of(sm._ok(resp)) or []:
            w = str(t.get("address") or t.get("traderAddress") or "").lower()
            for p in t.get("openPositions") or t.get("open_positions") or []:
                if isinstance(p, dict) and p.get("coin"):
                    out[(w, p["coin"])] = (str(p.get("szi")), p.get("startTime"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="turn 1: read the cohort and save it")
    ap.add_argument("--compare", action="store_true", help="turn 2: read again and diff the snapshot")
    ap.add_argument("--state", default="/tmp/position-age-snapshot.json")
    ap.add_argument("--wallets", type=int, default=50, help="how many proven wallets to sample")
    a = ap.parse_args()

    client = MCPClient()
    meta = {"warnings": []}
    smart, _ = sm.build_cohorts(client, meta)
    if not smart:
        print(json.dumps({"error": "no cohort", "warnings": meta["warnings"]})); return 2
    wallets = list(smart)[: a.wallets]

    if a.snapshot or not a.compare:
        now = _positions(client, wallets)
        with open(a.state, "w") as fh:
            json.dump({"at": time.time(), "wallets": wallets,
                       "positions": {f"{k[0]}|{k[1]}": v for k, v in now.items()}}, fh)
        print(f"[check] snapshot: {len(now)} positions across {len(wallets)} wallets, saved to "
              f"{a.state}. Run again with --compare in ~20 minutes.")
        return 0

    with open(a.state) as fh:
        snap = json.load(fh)
    gap = time.time() - snap["at"]
    if gap < 120:
        print(f"[check] the snapshot is only {gap:.0f}s old — wait longer, or the reads land inside "
              "one cache window and nothing can move for the wrong reason.")
        return 2
    first = {tuple(k.split("|", 1)): tuple(v) for k, v in snap["positions"].items()}
    second = _positions(client, snap["wallets"])
    print(f"[check] snapshot {len(first)} positions, {gap / 60:.0f} min ago; now {len(second)}")

    same_size, moved = 0, []
    for k, (szi1, start1) in first.items():
        if k not in second:
            continue
        szi2, start2 = second[k]
        if szi1 != szi2:
            continue                       # the position changed: a new startTime is legitimate
        same_size += 1
        if start1 != start2:
            moved.append({"wallet": k[0], "coin": k[1], "szi": szi1,
                          "startTime_before": start1, "startTime_after": start2,
                          "jumped_forward_s": (start2 or 0) - (start1 or 0)})

    print(json.dumps({"gap_s": round(gap), "positions_unchanged_in_size": same_size,
                      "startTime_moved": len(moved), "detail": moved[:20]}, indent=2))
    if moved:
        print(f"\n[check] FAIL — {len(moved)} of {same_size} untouched positions had startTime move. "
              "The cached path resets too; whale opens cannot rest on this field.")
        return 1
    print(f"\n[check] PASS — all {same_size} untouched positions kept their startTime on the cached "
          "path. The reset is a `latest: true` artifact and the detector is sound.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
