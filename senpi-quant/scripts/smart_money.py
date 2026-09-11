#!/usr/bin/env python3
"""You vs the whale cohort. Two sources for the cohort, same math (ported from senpi-smart-money):

* senpi discovery — the ALL_TIME realized-PnL ranking (`discovery_get_top_traders`), members with ≥ $1M
  realized, their live books from `discovery_get_trader_state` with position ages (entry times →
  "WITH — BUT LATE"). Needs a Senpi token.
* public fallback — the largest profitable accounts on Hyperliquid's public leaderboard, their live books
  from `clearinghouseState` (no entry times → WITH / AGAINST only).

Per coin the cohort's bias is net/gross signed notional in [-1, +1]. The you-vs-whale-median table reads
`references/benchmark.json`, computed by `benchmark.py` over the public cohort with this skill's own engine."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import statistics

SMART_MIN_REALIZED = 1_000_000
PAGE_SIZE = 1000
MAX_PAGES = 6
SAMPLE_CAP = 150
STATE_BATCH = 50
MIN_MEMBERS = 3
LEAN = 0.2            # |bias| below this = the cohort is split
LATE_H = 4.0          # entering this much after the cohort's median entry = "late"


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
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("traders", "data", "results"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _realized(t):
    return _f(t, "realizedProfitAndLoss", "realized_profit_and_loss", "profit_and_loss_realized", "realizedPnl", "realized_pnl", default=0.0)


def _signed_notional(p):
    szi = _f(p, "szi", "size")
    val = _f(p, "positionValue", "notional", "position_value")
    if val <= 0:
        val = abs(szi) * _f(p, "entryPx", "markPx", "entry_price")
    return (1.0 if szi > 0 else (-1.0 if szi < 0 else 0.0)) * abs(val)


def _empty():
    return {"net": 0.0, "gross": 0.0, "n_long": 0, "n_short": 0, "entries_long": [], "entries_short": []}


def _add(per, coin, sn, entry_ms=None):
    d = per.setdefault(coin, _empty())
    d["net"] += sn; d["gross"] += abs(sn); d["n_long" if sn > 0 else "n_short"] += 1
    if entry_ms:
        d["entries_long" if sn > 0 else "entries_short"].append(entry_ms)


def _finish(per):
    for d in per.values():
        d["bias"] = round(d["net"] / d["gross"], 3) if d["gross"] > 0 else 0.0
        d["members"] = d["n_long"] + d["n_short"]
    return per


# ---------------------------------------------------------------- senpi source
def senpi_cohort(client, meta):
    smart, seen = [], set()
    for page in range(MAX_PAGES):
        try:
            resp = client.mcp_call("discovery_get_top_traders", time_frame="ALL_TIME", sort_by="PROFIT_AND_LOSS_REALIZED",
                                   open_position_filter=False, limit=PAGE_SIZE, offset=page * PAGE_SIZE, timeout=20)
        except Exception as e:  # noqa: BLE001
            meta.setdefault("warnings", []).append(f"top_traders page {page} failed: {e}")
            break
        rows = _traders_of(_ok(resp))
        if not rows:
            break
        page_top = None
        for t in rows:
            if not isinstance(t, dict):
                continue
            addr = str(_field(t, "address", "trader_address", "wallet", default="")).lower()
            if not addr or addr in seen:
                continue
            rp = _realized(t)
            page_top = rp if page_top is None else max(page_top, rp)
            if rp >= SMART_MIN_REALIZED and len(smart) < SAMPLE_CAP:
                smart.append(addr); seen.add(addr)
        if len(smart) >= SAMPLE_CAP or (page_top is not None and page_top < SMART_MIN_REALIZED):
            break
    return smart


def senpi_positions(client, addrs, meta):
    per = {}
    for i in range(0, len(addrs), STATE_BATCH):
        batch = addrs[i:i + STATE_BATCH]
        try:
            resp = client.mcp_call("discovery_get_trader_state", trader_addresses=batch, include_position_age=True, timeout=25)
        except Exception as e:  # noqa: BLE001
            meta.setdefault("warnings", []).append(f"trader_state batch failed: {e}")
            continue
        for t in _traders_of(_ok(resp)):
            for p in (t.get("openPositions") or t.get("open_positions") or []):
                if not isinstance(p, dict):
                    continue
                coin = p.get("coin") or p.get("asset")
                sn = _signed_notional(p) if coin else 0.0
                if not coin or sn == 0:
                    continue
                st = _f(p, "startTime", "start_time", default=0.0)
                entry_ms = (st * 1000.0 if st < 1e12 else st) if st else None   # documented as Unix seconds
                _add(per, coin, sn, entry_ms)
    return _finish(per)


# ---------------------------------------------------------------- public source
def public_positions(states):
    per = {}
    for cs in (states or {}).values():
        for ap in (cs or {}).get("assetPositions") or []:
            p = ap.get("position") or {}
            sn = _signed_notional(p)
            if p.get("coin") and sn:
                _add(per, p["coin"], sn)
    return _finish(per)


# ---------------------------------------------------------------- the reads
def compare(per, book, opened_episodes):
    """One row per open position: your side vs the cohort's bias on that coin, and the read."""
    opens = {e["coin"]: e["open_time"] for e in opened_episodes or []}
    rows, lags = [], []
    for p in book["positions"]:
        d = per.get(p["coin"])
        bias = d["bias"] if d else None
        members = d["members"] if d else 0
        lag = None
        if not d or members < MIN_MEMBERS:
            read = "NO COHORT VIEW"
        elif abs(bias) < LEAN:
            read = "COHORT SPLIT"
        elif (bias > 0) == (p["side"] == "LONG"):
            read = "WITH"
            ent = d["entries_long" if p["side"] == "LONG" else "entries_short"]
            if ent and p["coin"] in opens:
                lag = (opens[p["coin"]] - statistics.median(ent)) / 3.6e6
                lags.append(lag)
                read = f"WITH — BUT LATE (+{lag:.0f}h)" if lag > LATE_H else ("WITH — AHEAD" if lag < -LATE_H else "WITH")
        else:
            read = "AGAINST SMART MONEY"
        if not d:
            cohort = "no whale holds it"
        elif members < MIN_MEMBERS:
            cohort = f"{'LONG' if bias > 0 else 'SHORT'} · {members} wallet{'s' if members != 1 else ''} (thin)"
        elif abs(bias) < LEAN:
            cohort = f"SPLIT · {members} wallets"
        else:
            cohort = f"{'LONG' if bias > 0 else 'SHORT'} · {members} wallets ({bias:+.2f})"
        rows.append(dict(coin=p["coin"], you=f"{p['side']} {p['leverage']}x" if p.get("leverage") else p["side"], cohort=cohort,
                         bias=bias, members=members, read=read, lag_h=lag))
    return dict(rows=rows, against=[r["coin"] for r in rows if r["read"].startswith("AGAINST")],
                entry_lag_h=statistics.median(lags) if lags else None)


BENCH_ROWS = (("Median hold — winners", "hold_winners_h", "h"), ("Median hold — losers", "hold_losers_h", "h"), ("Losers held ÷ winners held", "hold_ratio", "x"),
              ("Fees + funding ÷ gross P&L", "cost_ratio", "%"), ("Taker share of volume", "taker_share", "%"), ("Win rate", "win_rate", "%"),
              ("Profit factor", "profit_factor", "x"), ("Avg winner ÷ avg loser", "payoff_ratio", "x"))


def benchmark_table(tr, bench):
    rows = []
    for label, key, unit in BENCH_ROWS:
        you, whale = tr.get(key), (bench or {}).get(key)
        rows.append(dict(metric=label, you=you, whale=whale, unit=unit,
                         better="lower" if key in ("hold_losers_h", "hold_ratio", "cost_ratio", "taker_share") else "higher"))
    return rows
