#!/usr/bin/env python3
"""Hyperliquid public Info API — every read the desk needs for ANY address, no auth.

`POST https://api.hyperliquid.xyz/info` with `{"type": ..., "user": "0x..."}`. Stdlib only. Every call
is cached on disk (default: <tempdir>/quant-desk/cache) so a re-run inside the TTL costs nothing, and
a `HLFixture` serves recorded responses for tests and `--fixture` runs. Paging: `userFillsByTime`
returns at most 2000 fills and `userFunding` at most 500 rows per call — both are paged by
`startTime = last.time + 1` until a short page.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

INFO_URL = os.environ.get("HL_INFO_URL", "https://api.hyperliquid.xyz/info")
LEADERBOARD_URL = os.environ.get("HL_LEADERBOARD_URL", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")
DAY_MS = 86_400_000
FILLS_PAGE = 2000
FUNDING_PAGE = 500
MAX_FILL_PAGES = 12           # 24k fills — past that the book is a market maker's, not a trader's
MAX_TWAP_PAGES = 25           # TWAP slices are tiny and numerous; 50k of them is a bot
MAX_FUNDING_PAGES = 40
RETRIES = 4                   # on HTTP 429 only
BACKOFF_S = 1.5
DEFAULT_CACHE = os.path.join(tempfile.gettempdir(), "quant-desk", "cache")
TTL = {"metaAndAssetCtxs::xyz": 120, "clearinghouseState": 120, "frontendOpenOrders": 120, "metaAndAssetCtxs": 120, "candleSnapshot": 900,
       "userFees": 3600, "portfolio": 600, "userNonFundingLedgerUpdates": 600, "userFillsByTime": 600,
       "userFunding": 600, "leaderboard": 6 * 3600}


class HLError(Exception):
    pass


def is_perp(coin):
    """Spot fills name the pair (`@107`, `PURR/USDC`); perps are bare names or `xyz:`-prefixed."""
    return not (coin.startswith("@") or "/" in coin)


class HL:
    """Live client with a disk cache. `cache_dir=None` disables caching."""

    def __init__(self, cache_dir=DEFAULT_CACHE, timeout=60, now_ms=None):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.now_ms = now_ms or int(time.time() * 1000)
        self.calls = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # ---- transport ----
    def info(self, body):
        key = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
        ttl = TTL.get(body.get("type"), 300)
        path = os.path.join(self.cache_dir, key + ".json") if self.cache_dir else None
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            with open(path) as fh:
                return json.load(fh)
        req = urllib.request.Request(INFO_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        data = None
        for attempt in range(RETRIES):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                # 429 = the public API's per-IP weight budget; back off and retry, never hammer it
                if e.code == 429 and attempt < RETRIES - 1:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise HLError(f"{body.get('type')}: HTTP {e.code}") from e
            except Exception as e:  # noqa: BLE001 — the caller decides whether this read was essential
                raise HLError(f"{body.get('type')}: {e}") from e
        if path:
            with open(path, "w") as fh:
                json.dump(data, fh)
        return data

    # ---- trader-level reads ----
    def fills(self, addr, start_ms, end_ms=None):
        end_ms = end_ms or self.now_ms
        out, t = [], start_ms
        for _ in range(MAX_FILL_PAGES):
            page = self.info({"type": "userFillsByTime", "user": addr, "startTime": t, "endTime": end_ms, "aggregateByTime": True})
            if not page:
                break
            out += page
            if len(page) < FILLS_PAGE:
                break
            t = page[-1]["time"] + 1
        return out

    def twap_slices(self, addr, start_ms, end_ms=None):
        """TWAP executions are NOT in `userFillsByTime` — Hyperliquid reports them separately, one record
        per slice, wrapped as {fill, twapId}. Without them an active trader's book shows phantom position
        jumps (about half the taker volume on the accounts checked). Same 2000-per-page paging."""
        end_ms = end_ms or self.now_ms
        out, t = [], start_ms
        for _ in range(MAX_TWAP_PAGES):
            try:
                page = self.info({"type": "userTwapSliceFillsByTime", "user": addr, "startTime": t, "endTime": end_ms})
            except HLError:
                break   # the endpoint is newer than the rest; a wallet with no TWAPs may 4xx on some deployments
            if not page:
                break
            for x in page:
                f = dict(x.get("fill") or x)
                f["twapId"] = x.get("twapId", f.get("twapId"))
                out.append(f)
            if len(page) < FILLS_PAGE:
                break
            t = max(f["time"] for f in out) + 1
        return out

    def funding(self, addr, start_ms, end_ms=None):
        end_ms = end_ms or self.now_ms
        out, t = [], start_ms
        for _ in range(MAX_FUNDING_PAGES):
            page = self.info({"type": "userFunding", "user": addr, "startTime": t, "endTime": end_ms})
            if not page:
                break
            out += page
            if len(page) < FUNDING_PAGE:
                break
            t = page[-1]["time"] + 1
        return out

    def trader(self, addr, days=90, lookback_days=60):
        """Everything about one address: fills back to `days + lookback_days` (so round trips that opened
        before the window are complete), funding and ledger over the window, live state and orders."""
        start = self.now_ms - (days + lookback_days) * DAY_MS
        win_start = self.now_ms - days * DAY_MS
        return {
            "address": addr, "now_ms": self.now_ms, "window_start_ms": win_start, "fetch_start_ms": start, "days": days,
            "clearinghouseState": self.info({"type": "clearinghouseState", "user": addr}),
            "frontendOpenOrders": self.info({"type": "frontendOpenOrders", "user": addr}),
            "fills": merge_fills(self.fills(addr, start), self.twap_slices(addr, start)),
            "userFunding": self.funding(addr, win_start),
            "userFees": self.info({"type": "userFees", "user": addr}),
            "portfolio": self.info({"type": "portfolio", "user": addr}),
            "ledger": self.info({"type": "userNonFundingLedgerUpdates", "user": addr, "startTime": win_start}),
        }

    # ---- market-level reads ----
    def meta(self, dex=""):
        return self.info({"type": "metaAndAssetCtxs", "dex": dex} if dex else {"type": "metaAndAssetCtxs"})

    def candles(self, coins, days=91, interval="1h", workers=6):
        """Hourly candles per coin over `days`, fetched in parallel; a coin that fails maps to None."""
        start = self.now_ms - days * DAY_MS
        def one(c):
            try:
                rows = self.info({"type": "candleSnapshot", "req": {"coin": c, "interval": interval, "startTime": start, "endTime": self.now_ms}})
                return c, [[r["t"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])] for r in rows]
            except Exception:  # noqa: BLE001
                return c, None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return dict(ex.map(one, sorted(set(coins))))

    def states(self, addrs, workers=6):
        def one(a):
            try:
                return a, self.info({"type": "clearinghouseState", "user": a})
            except Exception:  # noqa: BLE001
                return a, None
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return dict(ex.map(one, addrs))

    def leaderboard(self):
        """Hyperliquid's public leaderboard (every account with a window performance; ~40 MB), cached."""
        path = os.path.join(self.cache_dir, "leaderboard.json") if self.cache_dir else None
        if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL["leaderboard"]:
            with open(path) as fh:
                return json.load(fh)
        self.calls += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(LEADERBOARD_URL), timeout=max(self.timeout, 120)) as r:
                data = json.load(r)
        except Exception as e:  # noqa: BLE001
            raise HLError(f"leaderboard: {e}") from e
        if path:
            with open(path, "w") as fh:
                json.dump(data, fh)
        return data


class HLFixture(HL):
    """Serves recorded responses: `hl::<type>` or `hl::<type>::<key>` where key is the address (lower-case)
    for trader reads, the coin for candles, and `leaderboard` for the leaderboard. Never touches the network."""

    def __init__(self, recorded, now_ms=None):
        super().__init__(cache_dir=None, now_ms=now_ms or recorded.get("now_ms"))
        self._r = recorded

    def info(self, body):
        t = body.get("type")
        req = body.get("req") or {}
        for key in (f"hl::{t}::{str(body.get('user', '')).lower()}", f"hl::{t}::{req.get('coin', '')}::{req.get('interval', '')}", f"hl::{t}::{req.get('coin', '')}",
                    f"hl::{t}::{body.get('dex', '')}", f"hl::{t}"):
            if key in self._r:
                data = self._r[key]
                if t in ("userFillsByTime", "userTwapSliceFillsByTime", "userFunding") and isinstance(data, list):
                    # emulate the live endpoint: the time window, ascending, one page
                    lo, hi = body.get("startTime", 0), body.get("endTime", 1 << 62)
                    tm = (lambda x: (x.get("fill") or x)["time"])
                    rows = sorted((x for x in data if lo <= tm(x) <= hi), key=tm)
                    return rows[:FILLS_PAGE if t != "userFunding" else FUNDING_PAGE]
                return data
        raise HLError(f"fixture has no {t}")

    def leaderboard(self):
        if "hl::leaderboard" not in self._r:
            raise HLError("fixture has no leaderboard")
        return self._r["hl::leaderboard"]


def merge_fills(fills, slices):
    """One stream, de-duplicated on the trade id, time-ordered."""
    by = {f["tid"]: f for f in fills}
    for f in slices:
        by.setdefault(f["tid"], f)
    return sorted(by.values(), key=lambda f: (f["time"], f["tid"]))


# ---- leaderboard helpers ----
def _window(row, name):
    for k, v in row.get("windowPerformances") or []:
        if k == name:
            return {kk: float(vv) for kk, vv in v.items()}
    return {}


def weekly_rank(leaderboard, addr):
    """Rank by weekly PnL among every account on the public leaderboard, plus Hyperliquid's own ROI windows."""
    rows = leaderboard.get("leaderboardRows") or []
    addr = addr.lower()
    pnls = [(r["ethAddress"].lower(), _window(r, "week").get("pnl", 0.0)) for r in rows]
    mine = next((p for a, p in pnls if a == addr), None)
    if mine is None:
        return None
    rank = 1 + sum(1 for _, p in pnls if p > mine)
    me = next(r for r in rows if r["ethAddress"].lower() == addr)
    return {"rank": rank, "of": len(pnls), "top_pct": 100.0 * rank / len(pnls), "week_pnl": mine,
            "windows": {w: _window(me, w) for w in ("day", "week", "month", "allTime")}, "account_value": float(me.get("accountValue") or 0)}


def public_cohort(leaderboard, n=40, min_account_value=1_000_000.0, min_month_roi=0.05, min_month_volume=1_000_000.0):
    """The whale cohort from public data alone: large accounts that are up on the month and all-time AND
    actually trade (monthly volume ≥ $1M — the leaderboard also lists vault and yield accounts with equity
    but no fills), ranked by monthly PnL. The fallback when senpi discovery is unavailable."""
    rows = leaderboard.get("leaderboardRows") or []
    keep = []
    for r in rows:
        m, a = _window(r, "month"), _window(r, "allTime")
        if (float(r.get("accountValue") or 0) >= min_account_value and m.get("pnl", 0) > 0 and a.get("pnl", 0) > 0
                and m.get("roi", 0) >= min_month_roi and m.get("vlm", 0) >= min_month_volume):
            keep.append((m["pnl"], r["ethAddress"]))
    keep.sort(reverse=True)
    return [a for _, a in keep[:n]]
