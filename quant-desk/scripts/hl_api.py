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
import random
import socket
import os
import tempfile
import time
import urllib.error
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor

INFO_URL = os.environ.get("HL_INFO_URL", "https://api.hyperliquid.xyz/info")
LEADERBOARD_URL = os.environ.get("HL_LEADERBOARD_URL", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")
DAY_MS = 86_400_000
FILLS_PAGE = 2000
FUNDING_PAGE = 500
MAX_FILL_PAGES = 12           # 24k fills — past that the book is a market maker's, not a trader's
MAX_TWAP_PAGES = 25           # TWAP slices are tiny and numerous; 50k of them is a bot
MAX_FUNDING_PAGES = 40
RETRIES = 4                   # 5xx and transport faults (timeout / reset) — see _request
# 429 gets its own, longer budget. It is not a fault, it is the venue's per-IP weight bucket, and the
# bucket refills on a ~minute. Four tries and 10.5s of backoff killed 3 of 7 desks run in parallel —
# and a 15-wallet round is exactly that shape. 6 tries capped at 20s gives ~63s, one refill window.
# (H3, @0xsarvesh #718.)
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_MAX_SLEEP_S = 20.0
# Retryable beyond 429: a 90-day desk makes 100-200 single-attempt requests, so one 503 or one reset
# socket aborted the whole run with exit 1. SKILL.md's "fails open" was only ever true of the
# OPTIONAL layers. (@0xsarvesh, #718.)
RETRY_CODES = (429, 500, 502, 503, 504)
BACKOFF_S = 1.5
DEFAULT_CACHE = os.path.join(tempfile.gettempdir(), "quant-desk", "cache")
TTL = {"metaAndAssetCtxs::xyz": 120, "clearinghouseState": 120, "frontendOpenOrders": 120, "metaAndAssetCtxs": 120, "candleSnapshot": 900,
       "userFees": 3600, "portfolio": 600, "userNonFundingLedgerUpdates": 600, "userFillsByTime": 600,
       "userFunding": 600, "leaderboard": 6 * 3600}


def _atomic_json(path, obj):
    """Write-then-rename. A half-written cache file reads as JSONDecodeError, which is not an HLError,
    so desk.py's handler misses it and the poisoned file stays hot for its whole TTL. Two desks run in
    parallel was enough to hit it. Same idiom as addresses.save(). (@0xsarvesh, #718.)"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".hl.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class HLError(Exception):
    pass


def is_perp(coin):
    """Spot fills name the pair (`@107`, `PURR/USDC`); perps are bare names or `xyz:`-prefixed."""
    return not (coin.startswith("@") or "/" in coin)


class HL:
    """Live client with a disk cache. `cache_dir=None` disables caching."""

    progress = None      # optional callable(str): the desk streams sub-steps of the long fetches through it

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
        for attempt in range(max(RETRIES, RATE_LIMIT_RETRIES)):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < RATE_LIMIT_RETRIES - 1:
                    # honour Retry-After when the venue sends it; it knows when the bucket refills
                    wait = float((e.headers or {}).get("Retry-After") or 0) or min(
                        RATE_LIMIT_MAX_SLEEP_S, BACKOFF_S * (2 ** attempt))
                    # Jitter, because the bucket is per-IP and shared: without it every desk in a
                    # parallel round backs off in lockstep and collides again on the same refill.
                    # Budget alone took a 7-way round from 4/7 to 6/7; jitter is what decorrelates
                    # the survivors.
                    time.sleep(min(wait, RATE_LIMIT_MAX_SLEEP_S) * (0.5 + random.random()))
                    continue
                if e.code in RETRY_CODES and e.code != 429 and attempt < RETRIES - 1:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise HLError(f"{body.get('type')}: HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout) as e:
                # a reset socket or a read timeout is not an answer, it is the absence of one
                if attempt < RETRIES - 1:
                    time.sleep(BACKOFF_S * (2 ** attempt))
                    continue
                raise HLError(f"{body.get('type')}: {e}") from e
            except Exception as e:  # noqa: BLE001 — the caller decides whether this read was essential
                raise HLError(f"{body.get('type')}: {e}") from e
        if path:
            _atomic_json(path, data)
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
            self._tick(f"[quant-desk]   · {len(out):,} fills scanned …")
            if len(page) < FILLS_PAGE:
                break
            t = page[-1]["time"] + 1
        return out

    def _tick(self, msg):
        if self.progress:
            self.progress(msg)

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
            self._tick(f"[quant-desk]   · {len(out):,} TWAP slices scanned …")
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
            "clearinghouseState_xyz": self._optional({"type": "clearinghouseState", "user": addr, "dex": "xyz"}),
            "frontendOpenOrders_xyz": self._optional({"type": "frontendOpenOrders", "user": addr, "dex": "xyz"}) or [],
            "spotClearinghouseState": self._optional({"type": "spotClearinghouseState", "user": addr}),
            "fills": merge_fills(self.fills(addr, start), self.twap_slices(addr, start)),
            "userFunding": self.funding(addr, win_start),
            "userFees": self.info({"type": "userFees", "user": addr}),
            # HIP-3 assets bill on their own schedule. Pricing xyz: volume with the main-dex rate
            # overstated one book's fees 4.01x (B2, #718).
            "userFees_xyz": self._optional({"type": "userFees", "user": addr, "dex": "xyz"}),
            "portfolio": self.info({"type": "portfolio", "user": addr}),
            "ledger": self.info({"type": "userNonFundingLedgerUpdates", "user": addr, "startTime": win_start}),
        }

    def _optional(self, body):
        try:
            return self.info(body)
        except HLError:
            return None

    # ---- market-level reads ----
    def meta(self, dex=""):
        return self.info({"type": "metaAndAssetCtxs", "dex": dex} if dex else {"type": "metaAndAssetCtxs"})

    def candles(self, coins, days=91, interval="1h", workers=6):
        """Hourly candles per coin over `days`, fetched in parallel; a coin that fails maps to None."""
        start = self.now_ms - days * DAY_MS
        todo = sorted(set(coins)); done = [0]; lock = threading.Lock()
        def one(c):
            try:
                rows = self.info({"type": "candleSnapshot", "req": {"coin": c, "interval": interval, "startTime": start, "endTime": self.now_ms}})
                out = c, [[r["t"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])] for r in rows]
            except Exception:  # noqa: BLE001
                out = c, None
            with lock:
                done[0] += 1; k = done[0]
            if k % 40 == 0 or k == len(todo):
                self._tick(f"[quant-desk]   · reading the tape: {k} of {len(todo)} coins{' · daily' if interval != '1h' else ''} …")
            return out
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return dict(ex.map(one, todo))

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
            _atomic_json(path, data)
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
        user = str(body.get("user", "")).lower(); dex = body.get("dex", "")
        if user and dex and f"hl::{t}::{user}::{dex}" not in self._r and t in ("clearinghouseState", "frontendOpenOrders"):
            raise HLError(f"fixture has no {t} for dex {dex}")          # a fixture without an xyz view = an account with no xyz collateral
        for key in (f"hl::{t}::{user}::{dex}" if dex else "", f"hl::{t}::{user}", f"hl::{t}::{req.get('coin', '')}::{req.get('interval', '')}", f"hl::{t}::{req.get('coin', '')}",
                    f"hl::{t}::{dex}", f"hl::{t}"):
            if not key:
                continue
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
    windows = {w: _window(me, w) for w in ("day", "week", "month", "allTime")}
    ranks = {"week": rank}
    for w in ("month", "allTime"):     # one bad week must not pass for the trader: the month and all-time ranks ride along
        mv = windows[w].get("pnl")
        if mv is not None:
            ranks[w] = 1 + sum(1 for r in rows if _window(r, w).get("pnl", 0.0) > mv)
    return {"rank": rank, "of": len(pnls), "top_pct": 100.0 * rank / len(pnls), "week_pnl": mine, "ranks": ranks,
            "windows": windows, "account_value": float(me.get("accountValue") or 0)}


# The bands a reader actually thinks in. "Find me traders to analyse" is a size question first —
# a $9k book and a $9M book teach different lessons — then a style question.
FIND_BANDS = {
    "5k-10k":   (5_000.0, 10_000.0),
    "10k-25k":  (10_000.0, 25_000.0),
    "25k-100k": (25_000.0, 100_000.0),
    "100k-1m":  (100_000.0, 1_000_000.0),
    "whales":   (1_000_000.0, float("inf")),
}


def find_traders(leaderboard, band=None, window="month", n=8, min_volume=250_000.0, losers=False):
    """Candidate wallets to run the desk on, from public leaderboard data alone.

    `band` is an account-size bracket (see FIND_BANDS) — the first thing a reader picks, because a
    $9k book and a $9M book teach different lessons. `window` is "week" for who is hot right now or
    "month"/"allTime" for who has held up. `min_volume` drops the vault and yield accounts that hold
    equity but never trade — they render as an empty desk.

    Returns dicts, not bare addresses: whoever is choosing needs to see why each one is on the list.
    """
    lo, hi = FIND_BANDS.get(band or "", (0.0, float("inf")))
    out = []
    for r in leaderboard.get("leaderboardRows") or []:
        av = float(r.get("accountValue") or 0)
        if not (lo <= av <= hi):
            continue
        w = _window(r, window)
        if w.get("vlm", 0) < min_volume:
            continue
        pnl = w.get("pnl", 0)
        if (pnl >= 0) if losers else (pnl <= 0):
            continue
        out.append(dict(address=r["ethAddress"], account_value=av, pnl=pnl,
                        roi=w.get("roi", 0), volume=w.get("vlm", 0), window=window,
                        turnover=(w.get("vlm", 0) / av) if av > 0 else 0.0))
    out.sort(key=lambda x: x["pnl"], reverse=not losers)
    return out[:n]


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
