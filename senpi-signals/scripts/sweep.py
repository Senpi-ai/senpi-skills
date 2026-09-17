#!/usr/bin/env python3
"""senpi-signals sweep — the whole gather in ONE process, then score.py. No agent orchestration.

    python3 sweep.py --print-feed          # what an agent runs for a user: prints only the feed
    python3 sweep.py                       # debugging: run JSON, coverage lines, reads=<n>
    (SENPI_AUTH_TOKEN / SENPI_MCP_URL from env, like senpi-smart-money)

2.0 is ONE READING with no compare: it reads the market now, ranks what that reading shows, and keeps
no history, so back-to-back runs give the same feed. Compare over periods is v2.

What one sweep reads (the read budget, printed as `reads=<n>` at the end):
    1   market_list_instruments        universe + OI + price + 24h move + funding + volume, both dexes
    1   discovery_get_top_traders      the proven cohort (>= $1M lifetime realized), one page of 1000
    3   discovery_get_trader_state     the cohort's books, 50 wallets per read (SAMPLE_CAP 150)
    1   leaderboard_get_markets        the 4h board: crowd side, hot_4h_share, 4h price move
    1   leaderboard_get_momentum_events  the platform's own tiered whale/momentum events
    1   market_get_cross_asset_flows   BTC-led laggards
  = 8 reads (up to 13 if discovery pages twice / the board is thin). Every read fails soft: a failed
  source is recorded in `coverage`, the rest of the sweep still lands.

Outputs (in --out-dir; default $SENPI_STATE_DIR/signals or ~/.openclaw/senpi-state/signals), overwritten
each run: current.json and signals.md. Nothing else is written. Stdlib only; Python 3.9+.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import contextlib
import datetime
import io
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import score  # noqa: E402 — the ranker, called in-process

UNIVERSE_TOP_N = 120          # top-N by day notional volume (SKILL golden rule 6: floor + top-N)
# every MCP read here is an idempotent GET, so one transient failure is retried rather than written
# off as a dark source (see _read)
READ_ATTEMPTS = 2
RETRY_SLEEP_S = 0.75
UNIVERSE_MIN_VOL = score.CRED_FLOOR_VOL   # below this the ranker drops the name anyway
COHORT_PAGES = 1              # one page of 1000 (ALL_TIME, realized desc) fills the 150-wallet smart sample;
                              # the crowd band smartmoney pages for is not read here (crowd = 4h board)
BOARD_LIMIT = 500             # traders aggregated by leaderboard_get_markets (max 500)
MOMENTUM_LIMIT = 50
LAGGARD_MIN_FOLLOW = 0.8      # follow_rate below this is not a laggard, it is an uncorrelated name
HOURS_PER_YEAR = 24 * 365     # HL `funding` (metaAndAssetCtxs) is the HOURLY rate → ×8760 ×100 = %/yr


# ── sibling skill resolution ──────────────────────────────────────────────────
def skill_scripts(name, marker):
    """<skills root>/<name>/scripts holding `marker`. Roots: SENPI_SKILLS_DIR (the runtime's own
    comma-separated knob), every ancestor of this file (the repo checkout), then the claw defaults."""
    roots = [r for r in os.environ.get("SENPI_SKILLS_DIR", "").split(",") if r.strip()]
    d = HERE
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        roots.append(d)
    roots += ["/data/.openclaw/skills", os.path.expanduser("~/.openclaw/skills")]
    for r in roots:
        cand = os.path.join(r.strip(), name, "scripts")
        if os.path.isfile(os.path.join(cand, marker)):
            return cand
    return None


def _smartmoney():
    d = skill_scripts("senpi-smart-money", "smartmoney.py")
    if d is None:
        raise ImportError("senpi-smart-money/scripts/smartmoney.py not found (SENPI_SKILLS_DIR?)")
    if d not in sys.path:
        sys.path.insert(0, d)
    import smartmoney  # noqa: E402
    return smartmoney


# ── the client adapter: smartmoney wants .mcp_call(tool, **kw); the runtime gives call_tool(name, args)
class Client:
    def __init__(self, call_tool):
        self.call_tool = call_tool
        self.reads = 0
        self.failed = 0
        self.trader_states = []      # every discovery_get_trader_state row seen (for base-unit positions)
        self.top_traders = []        # every discovery_get_top_traders row seen (for lifetime realized PnL)
        self.wallet_pnl = {}         # proven wallet -> lifetime realized PnL, USD

    def mcp_call(self, tool, timeout=12, **kw):
        self.reads += 1
        try:
            resp = self.call_tool(tool, kw)
        except Exception:
            self.failed += 1
            raise
        if tool == "discovery_get_trader_state":
            self.trader_states.extend(_traders_of(_ok(resp)))
        elif tool == "discovery_get_top_traders":
            self.top_traders.extend(_traders_of(_ok(resp)))
        return resp


def _ok(resp):
    if isinstance(resp, dict):
        if resp.get("success") is False:
            return None
        return resp.get("data", resp)
    return resp


def _traders_of(data):
    """The row list of a read. The leaderboard tools wrap their client's payload under the same key
    it already uses — `leaderboard_get_markets` returns data.markets.markets[] and
    `leaderboard_get_momentum_events` data.events.events[] — so one same-key level is unwrapped."""
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        for k in ("traders", "markets", "events", "instruments", "data", "results"):
            v = data.get(k)
            if isinstance(v, dict) and isinstance(v.get(k), list):
                v = v[k]
            if isinstance(v, list):
                return [t for t in v if isinstance(t, dict)]
    return []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _read(c, tool, args, cov, key):
    """One guarded read: a failure degrades `key` in coverage, never the sweep.

    Every read here is an idempotent GET, so a transient 503 or a dropped socket is retried once
    before the source is written off. Without this a single blip silently removed a whole lens from
    the run — and a removed lens is indistinguishable from a lens that looked and found nothing.
    """
    err = None
    for attempt in range(READ_ATTEMPTS):
        try:
            data = _ok(c.mcp_call(tool, **args))
        except Exception as e:  # noqa: BLE001
            err = f"failed: {tool}: {str(e)[:160]}"
        else:
            if data is not None:
                return data
            err = f"failed: {tool}: tool reported success=false"
        if attempt + 1 < READ_ATTEMPTS:
            time.sleep(RETRY_SLEEP_S * (attempt + 1))
    cov[key] = err
    return None


def _dex(name):
    return "xyz" if str(name).lower().startswith("xyz:") else ""


# ── 1. universe + per-asset metrics (one read) ─────────────────────────────────
def universe(c, cov, top_n):
    rows = _traders_of(_read(c, "market_list_instruments", {}, cov, "universe"))
    keep = []
    for r in rows:
        name, ctx = r.get("name"), r.get("context") if isinstance(r.get("context"), dict) else {}
        vol = _num(ctx.get("dayNtlVlm"))
        if not name or r.get("is_delisted") or vol is None or vol < UNIVERSE_MIN_VOL:
            continue
        price, prev = _num(ctx.get("markPx")), _num(ctx.get("prevDayPx"))
        fund = _num(ctx.get("funding"))
        keep.append((vol, name, {
            "dex": _dex(name),
            "notional_vol": vol,
            "oi": _num(ctx.get("openInterest")),           # BASE units — immune to the price move
            "price": price,
            "price_change_pct_24h": (round((price - prev) / prev * 100, 3)
                                     if price is not None and prev else None),
            "funding_annualized_pct": (round(fund * HOURS_PER_YEAR * 100, 3) if fund is not None else None),
            # the funding-sign proxy (positive ⇒ longs pay ⇒ crowd LONG); the 4h board overrides it
            "crowd_dir": (None if fund in (None, 0) else ("long" if fund > 0 else "short")),
            "crowd_source": "funding_sign",
        }))
    keep.sort(key=lambda t: -t[0])
    metrics = {name: m for _, name, m in keep[:top_n]}
    # cross-sectional funding percentile: where |annualized| ranks inside THIS universe
    fa = sorted(abs(m["funding_annualized_pct"]) for m in metrics.values()
                if m["funding_annualized_pct"] is not None)
    for m in metrics.values():
        f = m["funding_annualized_pct"]
        if f is not None and len(fa) > 1:
            m["funding_pctile"] = round(100.0 * sum(1 for x in fa if x < abs(f)) / (len(fa) - 1), 1)
    if metrics:
        cov["universe"] = f"ok ({len(metrics)} of {len(rows)} instruments kept)"
    elif "universe" not in cov:
        cov["universe"] = "NO DATA: market_list_instruments returned no rows"
    return metrics


# ── 2. the proven cohort (senpi-smart-money's engine, imported as-is) ─────────
def cohort(c, cov, metrics):
    try:
        sm = _smartmoney()
    except ImportError as e:
        cov["cohort"] = f"unavailable: {e}"
        return
    sm.MAX_PAGES = COHORT_PAGES
    meta = {"warnings": []}
    smart, _crowd = sm.build_cohorts(c, meta)
    per = sm.cohort_bias(c, smart, meta, "smart") if smart else {}
    if not smart or not per:
        cov["cohort"] = "NO DATA: " + (meta.get("cohorts_unavailable")
                                       or "; ".join(meta["warnings"]) or "empty cohort read")
        return
    smart_set = set(smart)
    for t in c.top_traders:          # the ranking rows the cohort was cut from carry lifetime realized PnL
        w = str(t.get("address") or t.get("trader_address") or t.get("wallet") or "").lower()
        if w in smart_set and w not in c.wallet_pnl:
            c.wallet_pnl[w] = round(sm._realized(t), 2)
    positions = {}                    # coin → {wallet: signed BASE size}
    for t in c.trader_states:
        w = str(t.get("address") or t.get("traderAddress") or "").lower()
        if w not in smart_set:
            continue
        for p in t.get("openPositions") or t.get("open_positions") or []:
            szi = _num(p.get("szi")) if isinstance(p, dict) else None
            if p.get("coin") and szi:
                positions.setdefault(p["coin"], {})[w] = szi
    covered = 0
    for coin, d in per.items():
        m = metrics.get(coin)
        if m is None:
            continue
        covered += 1
        ln, sn = int(d["n_long"]), int(d["n_short"])
        m.update({
            "smart_source": "proven_cohort",
            "smart_share_kind": "cohort_pct",      # headcount share — price cannot move it
            "smart_dir": "long" if ln >= sn else "short",
            "smart_share": round(100.0 * max(ln, sn) / len(smart), 2),
            "smart_long_n": ln, "smart_short_n": sn, "cohort_n": len(smart),
            "smart_net_bias": d["bias"], "smart_net_usd": d["net"],   # the engine's notional lean, as colour
            "smart_positions": positions.get(coin, {}),
        })
    cov["cohort"] = (f"ok ({len(smart)} proven wallets, {covered} universe names positioned, "
                     f"{len(per) - covered} outside the universe)"
                     + ("; " + "; ".join(meta["warnings"]) if meta["warnings"] else ""))


# ── 3. the 4h board: crowd side + hot_4h_share + 4h price move ─────────────────
def board(c, cov, metrics):
    data = _read(c, "leaderboard_get_markets", {"limit": BOARD_LIMIT}, cov, "board_4h")
    rows = _traders_of(data)
    if not rows:
        cov.setdefault("board_4h", "NO DATA: leaderboard_get_markets returned no markets")
        return None
    hit = 0
    for r in rows:
        tok = str(r.get("token") or "")
        name = tok if tok in metrics else (f"xyz:{tok}" if f"xyz:{tok}" in metrics else None)
        if name is None:
            continue
        m = metrics[name]
        pc4 = _num(r.get("token_price_change_pct_4h"))
        if pc4 is not None:
            m["price_change_pct"] = pc4
        if r.get("is_dominant_direction") and str(r.get("direction")).lower() in ("long", "short"):
            hit += 1
            m["crowd_dir"] = str(r["direction"]).lower()
            m["crowd_source"] = "board_4h"
            m["hot_4h_dir"] = m["crowd_dir"]
            m["hot_4h_share"] = _num(r.get("pct_of_top_traders_gain"))
            m["hot_4h_trader_count"] = _num(r.get("trader_count"))
    inner = data.get("markets") if isinstance(data, dict) and isinstance(data.get("markets"), dict) else data
    src = _num(inner.get("source_trader_count")) if isinstance(inner, dict) else None
    cov["board_4h"] = f"ok ({hit} universe names on the board, {src or '?'} traders aggregated)"
    return src


# ── 4. events: momentum (the platform's own whale feed) + cross-asset laggards ─
def _age_min(ts, now):
    t = score._parse_ts(ts)
    return round((now - t).total_seconds() / 60.0, 1) if t else None


def momentum_events(c, cov, metrics, now):
    rows = _traders_of(_read(c, "leaderboard_get_momentum_events", {"limit": MOMENTUM_LIMIT}, cov, "momentum"))
    out = []
    for e in rows:
        if e.get("decision") != "sent":            # the platform already decided what is worth notifying
            continue
        tops = [p for p in (e.get("top_positions") or []) if isinstance(p, dict)]
        if not tops:
            continue
        p = max(tops, key=lambda x: abs(_num(x.get("delta_pnl")) or 0))
        mk = str(p.get("market") or "")
        asset = mk if mk in metrics else (f"xyz:{mk}" if f"xyz:{mk}" in metrics else None)
        if asset is None:
            continue
        m = metrics[asset]
        dp, wallet = _num(e.get("delta_pnl")) or 0.0, str(e.get("trader_id") or "")
        out.append({
            "asset": asset, "dex": m["dex"], "detector": "momentum_event",
            "direction": str(p.get("direction") or "").lower() or None,
            "numbers": [f"tier {e.get('tier')} {e.get('tier_label') or ''}".strip()
                        + f": a top trader's 4h PnL moved ${dp:,.0f}",
                        f"largest leg {mk} {str(p.get('direction') or '').upper()} "
                        f"(Δ ${_num(p.get('delta_pnl')) or 0:,.0f})"],
            "notional_vol": m.get("notional_vol"), "price_change_pct": m.get("price_change_pct"),
            "concrete_entity": (wallet[:6] + "…" + wallet[-4:]) if len(wallet) > 12 else (wallet or None),
            "magnitude": min(1.0, abs(dp) / 10_000_000), "age_minutes": _age_min(e.get("detected_at"), now),
            "as_of": e.get("detected_at"),
        })
    if "momentum" not in cov:
        cov["momentum"] = f"ok ({len(out)} sent events on universe names of {len(rows)} returned)"
    return out


def cross_asset(c, cov, metrics):
    data = _read(c, "market_get_cross_asset_flows", {}, cov, "cross_asset")
    if not isinstance(data, dict):
        cov.setdefault("cross_asset", "NO DATA: market_get_cross_asset_flows returned no payload")
        return []
    lead = data.get("leader") if isinstance(data.get("leader"), dict) else {}
    move, ldir = _num(lead.get("move_pct")), str(lead.get("direction") or "").lower()
    out = []
    for lg in data.get("laggards") or []:
        if not isinstance(lg, dict) or (_num(lg.get("follow_rate")) or 0) < LAGGARD_MIN_FOLLOW:
            continue
        asset = str(lg.get("asset") or "")
        m = metrics.get(asset)
        if m is None:
            continue
        gap = _num(lg.get("gap_pct")) or 0.0
        out.append({
            "asset": asset, "dex": m["dex"], "detector": "cross_asset_laggard",
            "direction": ldir if ldir in ("long", "short") else None,
            "numbers": [f"{lead.get('asset')} {move:+.1f}% in 4h" if move is not None else "leader moved",
                        f"{asset} {_num(lg.get('actual_move_pct')) or 0:+.1f}% vs "
                        f"{_num(lg.get('expected_move_pct')) or 0:+.1f}% expected (gap {gap:+.1f}pp)",
                        f"follow-rate {(_num(lg.get('follow_rate')) or 0) * 100:.0f}%"],
            "notional_vol": m.get("notional_vol"), "price_change_pct": m.get("price_change_pct"),
            "magnitude": min(1.0, abs(gap) / 5.0),
        })
    cov["cross_asset"] = (f"ok ({len(out)} laggards; leader {lead.get('asset')} {move:+.1f}%)"
                          if move is not None else "ok (no qualifying leader move)")
    return out


# ── the sweep ──────────────────────────────────────────────────────────────────
def gather(call_tool, top_n=UNIVERSE_TOP_N, now=None):
    """Everything score.py needs, from one client: {"asset_metrics", "events", "coverage", "reads"}."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    c, cov = Client(call_tool), {}
    metrics = universe(c, cov, top_n)
    if metrics:
        cohort(c, cov, metrics)
        src = board(c, cov, metrics)
        events = momentum_events(c, cov, metrics, now) + cross_asset(c, cov, metrics)
    else:
        src, events = None, []
    return {"generated": now.isoformat(), "asset_metrics": metrics, "events": events,
            "wallets": {w: {"realized_pnl_usd": v} for w, v in c.wallet_pnl.items()},
            "coverage": cov, "source_trader_count": src, "reads": c.reads, "reads_failed": c.failed}


def rank(current_path, feed_path, now=None, top=None, lens="both"):
    """score.py in-process (its CLI is its API), with no --state: one reading, nothing written to history.
    Returns its stdout JSON."""
    argv = [current_path, "--out", feed_path, "--lens", lens]
    if now:
        argv += ["--now", now]
    if top:
        argv += ["--top", str(top)]
    saved, buf = sys.argv, io.StringIO()
    try:
        sys.argv = ["score.py"] + argv
        with contextlib.redirect_stdout(buf):
            score.main()
    finally:
        sys.argv = saved
    return json.loads(buf.getvalue())


def default_out_dir():
    """Where current.json + signals.md land, overwritten each run: $SENPI_STATE_DIR/signals when set, else
    ~/.openclaw/senpi-state/signals. They are this run's outputs, not history."""
    base = os.environ.get("SENPI_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".openclaw", "senpi-state")
    return os.path.join(base, "signals")


def run(call_tool, out_dir=None, now=None, top_n=UNIVERSE_TOP_N, top=None, lens="both"):
    """One sweep: one reading, no compare. Writes current.json + signals.md and returns
    {"summary", "reads", "coverage", "current", "result", "out_dir"}."""
    out_dir = out_dir or default_out_dir()
    os.makedirs(out_dir, exist_ok=True)
    cur = gather(call_tool, top_n=top_n, now=score._parse_ts(now) if now else None)
    # Hand the reading to score.py through files only THIS run can see, then publish them under the
    # stable names with os.replace (atomic). Two sweeps sharing an out dir — the chip and the sweep
    # inside a market pulse — used to write and re-read the same current.json, so an interleave let
    # one run rank the other's universe and present assets it never read.
    tag = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    run_current = os.path.join(out_dir, f".current.{tag}.json")
    run_feed = os.path.join(out_dir, f".signals.{tag}.md")
    try:
        with open(run_current, "w") as f:
            json.dump(cur, f)
        res = rank(run_current, run_feed, now=now, top=top, lens=lens)
        os.replace(run_current, os.path.join(out_dir, "current.json"))
        os.replace(run_feed, os.path.join(out_dir, "signals.md"))
    finally:
        for stale in (run_current, run_feed):
            if os.path.exists(stale):
                os.unlink(stale)
    cov = res.get("coverage") or {}
    summary = (f"assets {len(cur['asset_metrics'])} · events {len(cur['events'])} · "
               f"trade {len(res.get('trade') or [])} · social {len(res.get('social') or [])} · "
               f"smart-money lens {cov.get('smart_money_lens', 'n/a')} · "
               f"reads {cur['reads']} ({cur['reads_failed']} failed) · "
               f"out {out_dir}")
    return {"summary": summary, "reads": cur["reads"], "coverage": cur["coverage"], "current": cur,
            "result": res, "out_dir": out_dir}


# what a failed source is called in the feed's one "not measured" line — reader words, not tool names
PLAIN_SOURCE = {"universe": "market data", "cohort": "proven-trader positions", "board_4h": "the 4h leaderboard",
                "momentum": "momentum events", "cross_asset": "cross-asset flows"}


def universe_is_dark(coverage):
    """True when the ONE read everything else hangs off — market_list_instruments — gave us nothing.

    Every other lens is optional: it degrades and the feed says so. This one is not. With no
    universe there are no assets to score, so `score.py` renders its quiet-market line — and the
    skill teaches the agent that a quiet read is a correct answer to present as-is. A total outage
    would therefore be reported to the user as a calm market. It is an error, not a feed.
    """
    return str((coverage or {}).get("universe", "")).startswith(("failed", "NO DATA", "unavailable"))


def feed_text(rep):
    """The rendered feed an agent presents, verbatim, plus ONE plain line naming any source that failed —
    so a dark lens is never read as "nothing happened", without coverage lines, counts or tool names."""
    if universe_is_dark(rep.get("coverage")):
        return ("_Senpi Signals could not read the market this run — the universe read failed after "
                f"{READ_ATTEMPTS} attempts, so nothing was measured. This is an outage, not a quiet "
                "market. Worth trying again in a minute._")
    feed = (rep.get("result") or {}).get("feed_md")
    if feed is None:      # a caller that built `rep` by hand
        with open(os.path.join(rep["out_dir"], "signals.md")) as f:
            feed = f.read()
    feed = feed.rstrip("\n")
    dark = [PLAIN_SOURCE[k] for k, v in (rep.get("coverage") or {}).items()
            if k in PLAIN_SOURCE and str(v).startswith(("failed", "NO DATA", "unavailable"))]
    if dark:
        feed += "\n\n_Not measured this run: " + ", ".join(dark) + "._"
    return feed


def main(argv=None):
    ap = argparse.ArgumentParser(description="senpi-signals: gather the universe and rank it, in one process")
    ap.add_argument("--out-dir", default=None,
                    help="where current.json + signals.md land (default: $SENPI_STATE_DIR/signals or ~/.openclaw/senpi-state/signals)")
    ap.add_argument("--now", default=None, help="ISO timestamp (tests / backfills)")
    ap.add_argument("--top-n", type=int, default=UNIVERSE_TOP_N, help="universe size (top-N by volume)")
    ap.add_argument("--top", type=int, default=None, help="feed length (score.py --top)")
    ap.add_argument("--lens", choices=["both", "trade", "social"], default="both")
    ap.add_argument("--print-feed", action="store_true",
                    help="print only the feed to present (signals.md, plus one line naming any failed source); "
                         "diagnostics are shown only if the sweep itself fails")
    a = ap.parse_args(argv)
    d = skill_scripts("senpi-smart-money", "mcp_client.py")     # the vendored stdlib MCP transport
    if d is None:
        print("senpi-smart-money/scripts/mcp_client.py not found — set SENPI_SKILLS_DIR", file=sys.stderr)
        return 2
    if d not in sys.path:
        sys.path.insert(0, d)
    from mcp_client import MCPClient  # noqa: E402
    client = MCPClient()
    kwargs = dict(out_dir=a.out_dir, now=a.now, top_n=a.top_n, top=a.top, lens=a.lens)
    if a.print_feed:
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rep = run(lambda name, args: client.mcp_call(name, **args), **kwargs)
        except Exception:
            sys.stderr.write(err.getvalue())
            raise
        print(feed_text(rep))
        # a dark universe is an outage, not a quiet market: exit non-zero so a caller that only
        # checks the status code cannot present it as a successful, empty read
        return 3 if universe_is_dark(rep["coverage"]) else 0
    rep = run(lambda name, args: client.mcp_call(name, **args), **kwargs)
    print(json.dumps(rep["result"], indent=2))
    for k, v in rep["coverage"].items():
        print(f"[coverage] {k}: {v}", file=sys.stderr)
    print(f"[sweep] {rep['summary']}", file=sys.stderr)
    print(f"reads={rep['reads']}")
    if universe_is_dark(rep["coverage"]):
        print("[sweep] universe read failed — this run measured nothing; it is an outage, not a "
              "quiet market.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
