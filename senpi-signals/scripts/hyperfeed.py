#!/usr/bin/env python3
"""HYPERFEED MOVERS — "what's hot on the feed right now?", scored the way Penguin scores it.

Answers the on-demand questions the strategies cannot: *what's pumping right now*, *where are
traders making money on Hyperliquid this minute*, *what is the Hyperfeed telling us*. One
`leaderboard_get_markets` read, no crons, no background sampling.

THE HONESTY PROBLEM THIS SOLVES
-------------------------------
Penguin's detector (scripts/striker_scoring.py, byte-identical to the template's) is STATEFUL. Every
one of its six scoring reasons is a delta against a previous scan:

    FIRST_JUMP · IMMEDIATE_MOVER · CONTRIB_EXPLOSION · HIGH_VELOCITY · DEEP_CLIMBER · CLIMBING

The live scanner holds that history in ctx.state and re-reads the board every 90 SECONDS, so
"+42 ranks" means "+42 ranks in a minute and a half". A skill invoked when a human asks a question
has no such history — and the tempting fix, keeping a history file across user-initiated runs, is
the trap: the same "+42" measured against a baseline from eleven hours ago is a completely different
claim, and nothing in the output would say which one you got. That is the #778 failure mode (a
detector's lookback quietly decoupling from its sampling cadence) with the cadence now set by when
somebody felt like asking.

So this engine is built in two tiers and always says which one it is answering from.

    TIER A — THE SNAPSHOT (always available, one read, no history)
      Ranked by `contribution_pct_change_15m`, which is computed BY THE FEED, not by us: a real
      15-minute momentum number that needs no baseline of our own. Penguin's stateless gates are
      applied so the names shown are the ones it would even look at — rank outside the top 10
      (`rank <= 10` has no jump room left), the 4h price move agreeing with the
      leaders' direction, a live 15m contribution change, and the thin-side trader floor. This is the honest
      answer to "what's hot this minute" and it is complete on its own.

    TIER B — THE ROTATIONS (needs a second read, minutes apart)
      When the ring holds a baseline inside the freshness window, the VERBATIM Penguin scorer runs
      and the section reports its real score and its real reasons. Baseline age is printed every
      time, and classified:

        <= 300s   LIVE      comparable to the scanner's own 90s cadence; a Penguin-equivalent score
        <= 1800s  WIDE      scored, but over a longer window than Penguin uses — labelled, never
                            presented as the same signal
        >  1800s  STALE     no jump math at all. Tier A only, plus the note that running again in a
                            couple of minutes unlocks it.

      A STALE baseline is not scored quietly at a wider window. That is the whole point.

The ring is filled only by runs a human asked for. Two questions a few minutes apart is the normal
way Tier B lights up, and "ask me again in two minutes" is a better product than a cron nobody
wants. Nothing here schedules anything.

NAMING — NOT NEGOTIABLE. This reads `leaderboard_get_markets`: who is WINNING RIGHT NOW, over a 4h
rolling window, survivorship included. It is NOT senpi-signals' "smart money", which is the
>= $1M lifetime-realized cohort, and the two are regularly on opposite sides of the same name in the
same answer. Call this layer the Hyperfeed, the 4h leaders, or top traders. Never "smart money"
(senpi-market-pulse carries the same rule for the same reason).

Stdlib only. Read-only: one MCP read, one state file.

Run:
  python3 scripts/hyperfeed.py                 # the block, as the skill prints it
  python3 scripts/hyperfeed.py --json          # the same read, structured
  python3 scripts/hyperfeed.py --top 8
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import striker_scoring as scoring  # noqa: E402 — byte-identical to penguin's scoring.py

# ── freshness windows (seconds). The scanner itself re-reads every 90s; see the docstring. ──
MIN_BASELINE_S = 30        # below this there is nothing to diff: see classify()
LIVE_MAX_S = 300           # <= 5 min: Penguin-comparable
WIDE_MAX_S = 1800          # <= 30 min: scored, labelled as a wider window
RING_MAX = 12              # snapshots kept; a dozen covers a working session
RING_TTL_S = 6 * 3600      # drop anything older — it can only mislead a later run

LEADERBOARD_LIMIT = 100    # penguin's leaderboardLimit
TOP_N = 50                 # penguin's topN — the rows the detector ever scores
MIN_TRADERS = 10           # penguin's minTraderCount — a thin side never enters the rank order


def default_state_dir():
    """Shares senpi-signals' state root so the ring sits beside current.json / signals.md."""
    base = os.environ.get("SENPI_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".openclaw", "senpi-state")
    return os.path.join(base, "signals")


def _unwrap(raw):
    """leaderboard_get_markets nests rows at data.markets.markets; tolerate the flatter shapes too.
    Ported from penguin's _fetch_markets so the two read the same envelope."""
    rows = []
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, dict):
            rows = data.get("markets", [])
            if isinstance(rows, dict):
                rows = rows.get("markets", [])
        elif isinstance(data, list):
            rows = data
    elif isinstance(raw, list):
        rows = raw
    return rows if isinstance(rows, list) else []


def normalize(raw, xyz_banned=False, min_traders=MIN_TRADERS, top_n=TOP_N):
    """Feed rows -> the dict shape striker_scoring.score_market expects.

    Field names, the rank convention (pre-filter list index + 1) and the thin-side filter are
    penguin's _fetch_markets verbatim. Keeping the rank convention matters: a rank computed after
    filtering is a different number, and every jump threshold is expressed in it.
    """
    out = []
    for i, m in enumerate(_unwrap(raw)):
        if not isinstance(m, dict):
            continue
        token = str(m.get("token", m.get("asset", ""))).upper()
        dex = m.get("dex", "")
        if xyz_banned and (dex == "xyz" or token.lower().startswith("xyz:")):
            continue
        if not token:
            continue
        if int(m.get("trader_count", 0) or 0) < min_traders:
            continue
        out.append({
            "token": token,
            "dex": dex,
            "rank": i + 1,
            "direction": str(m.get("direction", "")).upper(),
            "contribution": scoring.safe_float(m.get("pct_of_top_traders_gain", 0)),
            "traders": int(m.get("trader_count", 0)),
            "price_chg_4h": scoring.safe_float(m.get("token_price_change_pct_4h", 0)),
            "price_chg_1h": scoring.safe_float(m.get("token_price_change_pct_1h",
                                               m.get("price_change_1h", 0))),
            "cc_15m": scoring.safe_float(m.get("contribution_pct_change_15m", 0)),
        })
    return out[:top_n]


# ── the ring ────────────────────────────────────────────────────────────────────────────────────
def ring_path(state_dir):
    return os.path.join(state_dir, "hyperfeed_ring.json")


def load_ring(state_dir, now_s):
    """Snapshots newest-last, TTL-pruned. A corrupt or unreadable ring degrades to empty: the read
    still answers Tier A, which needs no history at all."""
    try:
        with open(ring_path(state_dir)) as fh:
            ring = json.load(fh)
        if not isinstance(ring, list):
            return []
    except (OSError, ValueError):
        return []
    fresh = [s for s in ring
             if isinstance(s, dict) and isinstance(s.get("ts"), (int, float))
             and now_s - s["ts"] <= RING_TTL_S]
    fresh.sort(key=lambda s: s["ts"])
    return fresh[-RING_MAX:]


def save_ring(state_dir, ring):
    """Atomic: two questions asked at once must not leave a half-written ring behind."""
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, f".hyperfeed_ring.{os.getpid()}.{uuid.uuid4().hex[:8]}.json")
    try:
        with open(tmp, "w") as fh:
            json.dump(ring[-RING_MAX:], fh)
        os.replace(tmp, ring_path(state_dir))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def snapshot_of(markets, ts):
    """Only what the scorer needs from a prior scan — mirrors penguin's _snapshot_of."""
    return {"ts": ts,
            "markets": [{"token": m["token"], "dex": m["dex"], "rank": m["rank"],
                         "contribution": m["contribution"]} for m in markets]}


def classify(age_s):
    """Bands by baseline age. Note BOTH ends are refused, not just the stale one.

    TOOFRESH exists because the first live run produced a 3-SECOND baseline (two reads back to
    back) and the block called it "comparable to the scanner's own 90-second cadence". Nothing
    rank-jumps in 3 seconds; the board is usually byte-identical. Claiming a real comparison off a
    3s diff is the same error as scoring an 11-hour one — an honest window has a floor as well as
    a ceiling.
    """
    if age_s is None:
        return "NONE"
    if age_s < MIN_BASELINE_S:
        return "TOOFRESH"
    if age_s <= LIVE_MAX_S:
        return "LIVE"
    if age_s <= WIDE_MAX_S:
        return "WIDE"
    return "STALE"


# ── the two tiers ───────────────────────────────────────────────────────────────────────────────
def tier_a(markets, top):
    """The stateless read: Penguin's stateless gates, ranked by the feed's own 15m delta.

    Gate order and thresholds are the scanner's: rank > 10 (a top-10 name has no jump room left),
    4h price agreeing with the leaders' direction, and cc_15m > 0 (penguin's freshness gate,
    `if cc_15m <= 0: continue`). What this CANNOT tell you is whether a name just jumped — that is
    Tier B, and it needs a second read.
    """
    rows = []
    for m in markets:
        if m["rank"] <= 10:
            continue                 # penguin: `if rank <= 10: continue` — no jump room left
        if not scoring.check_4h_alignment(m["direction"], m["price_chg_4h"]):
            continue
        if m["cc_15m"] <= 0:
            continue
        rows.append(m)
    # cc_15m comes off the feed QUANTIZED to 0.1%, so on a quiet board the whole top slice ties at
    # the same value (first live run: six rows, five of them +0.5%). Sorting on it alone then hands
    # back an order that looks ranked and is not. Tiebreak on share of top-trader gains, which is
    # the magnitude behind the change, then on rank.
    rows.sort(key=lambda m: (-m["cc_15m"], -m["contribution"], m["rank"]))
    return rows[:top]


def delta_is_flat(rows):
    """True when the 15m change cannot separate the slice — the caller must say so."""
    return len(rows) > 1 and len({round(r["cc_15m"], 1) for r in rows}) == 1


def score_against(markets, ring, now_s, hour_utc, top):
    """Run the verbatim scorer against the freshest baseline; returns (rows, age_s, band)."""
    if not ring:
        return [], None, "NONE"
    baseline = ring[-1]
    age_s = max(0.0, now_s - baseline["ts"])
    band = classify(age_s)
    if band in ("STALE", "TOOFRESH"):
        return [], age_s, band
    prev_by_key = {(m["token"], m.get("dex", "")): m for m in baseline.get("markets", [])}
    prev_tokens = set(prev_by_key)
    oldest = ring[0]
    old_by_key = {(m["token"], m.get("dex", "")): m for m in oldest.get("markets", [])}
    contrib_hist = {}
    for snap in ring:
        for m in snap.get("markets", []):
            contrib_hist.setdefault((m["token"], m.get("dex", "")), []).append(m["contribution"])

    scored = []
    for m in markets:
        key = (m["token"], m.get("dex", ""))
        recent = contrib_hist.get(key, []) + [m["contribution"]]
        res = scoring.score_market(m, prev_by_key.get(key), old_by_key.get(key),
                                   prev_tokens, recent, hour_utc)
        if not res:
            continue
        score, reasons, meta = res
        scored.append({"token": m["token"], "dex": m.get("dex", ""),
                       "direction": m["direction"], "score": score, "reasons": reasons,
                       "rank": m["rank"], "traders": m["traders"],
                       "contribution": m["contribution"], "cc_15m": m["cc_15m"],
                       "price_chg_1h": m["price_chg_1h"], "price_chg_4h": m["price_chg_4h"],
                       "meta": meta})
    scored.sort(key=lambda r: (-r["score"], r["rank"]))
    return scored[:top], age_s, band


def read(call_tool, state_dir=None, now=None, top=6, xyz_banned=False, persist=True):
    """One Hyperfeed read. Returns the structured block; writes this run into the ring."""
    state_dir = state_dir or default_state_dir()
    now_s = now if now is not None else time.time()
    hour_utc = datetime.fromtimestamp(now_s, timezone.utc).hour

    # GUARDED READ, the same shape penguin's scanner uses (scan.py `_read`): the transport RAISES
    # on a failed tool — mcp_client._unwrap turns a `success: false` envelope into MCPError — so a
    # bare call leaks a traceback where this section promises to say "unavailable" and invent
    # nothing. The first live run did exactly that against a down leaderboard API. The envelope
    # branch is kept too: a caller that injects its own call_tool (the runtime, the tests) may hand
    # back the envelope instead of raising, and both paths must degrade identically.
    def _degraded(why, error=None):
        return {"ok": False, "degraded": why, "error": error, "movers": [], "rotations": [],
                "baseline": {"band": "NONE", "age_s": None}}

    try:
        raw = call_tool("leaderboard_get_markets", {"limit": LEADERBOARD_LIMIT})
    except Exception as exc:  # noqa: BLE001 — transport; a feed outage is a read, not a crash
        return _degraded(f"leaderboard_get_markets failed: {type(exc).__name__}: {exc}")
    if isinstance(raw, dict) and raw.get("success") is False:
        return _degraded("leaderboard_get_markets failed", raw.get("error"))
    markets = normalize(raw, xyz_banned=xyz_banned)
    if not markets:
        return _degraded("leaderboard returned no usable rows")

    ring = load_ring(state_dir, now_s)
    rotations, age_s, band = score_against(markets, ring, now_s, hour_utc, top)
    movers = tier_a(markets, top)
    if persist:
        save_ring(state_dir, ring + [snapshot_of(markets, now_s)])

    return {"ok": True, "generated_at": datetime.fromtimestamp(now_s, timezone.utc).isoformat(),
            "universe": len(markets), "hour_utc": hour_utc,
            "baseline": {"band": band, "age_s": None if age_s is None else round(age_s, 1),
                         "snapshots": len(ring)},
            "movers": movers, "rotations": rotations,
            "thresholds": {"min_score": scoring.STRIKER_MIN_SCORE,
                           "min_reasons": scoring.STRIKER_MIN_REASONS,
                           "min_rank_jump": scoring.STRIKER_MIN_RANK_JUMP,
                           "live_max_s": LIVE_MAX_S, "wide_max_s": WIDE_MAX_S}}


# ── rendering ───────────────────────────────────────────────────────────────────────────────────
def render(rep):
    L = []
    if not rep.get("ok"):
        L.append("**Hyperfeed Movers** — unavailable")
        L.append(f"> The feed read failed ({rep.get('degraded')}). Nothing below is a market read; "
                 f"say so rather than filling the gap from memory.")
        return "\n".join(L)

    b = rep["baseline"]
    L.append(f"**Hyperfeed Movers** — {rep['universe']} Hyperfeed markets, "
             f"{rep['generated_at'][11:16]} UTC")
    L.append("")
    if rep["movers"]:
        L.append("*These trades are earning top traders the most right now.*")
        L.append("")
        L.append("| # | market | side | share of top traders' gains | change, last 15 min "
                 "| price, last 4h | traders on it |")
        L.append("|---|---|---|---|---|---|---|")
        for i, m in enumerate(rep["movers"], 1):
            nm = f"{m['token']}" + (f" ({m['dex']})" if m["dex"] else "")
            L.append(f"| {i} | {nm} | {'long' if m['direction'] == 'LONG' else 'short'} "
                     f"| {m['contribution']:.1f}% | {m['cc_15m']:+.1f}% "
                     f"| {m['price_chg_4h']:+.2f}% | {m['traders']} |")
        L.append("")
        L.append("> **Share of top traders' gains** is how much of everything the winning traders made "
                 "is sitting in that one position — so 4% means a twenty-fifth of all their profit is "
                 "in that name, on that side. **Change, last 15 min** is whether that share is growing "
                 "right now, which is what makes it *hot* rather than merely large. **Price, last 4h** "
                 "is the token itself, for context — a big share with a flat price means they are "
                 "positioned and the move has not happened yet.")
        if delta_is_flat(rep["movers"]):
            L.append("")
            L.append("> The 15-minute change is **the same for every row here** (the feed reports it in "
                     "0.1% steps), so this is not a ranking — it is the list of names that qualify, in "
                     "order of share. Do not call the top row the hottest.")
    else:
        L.append("*No name currently clears the gates* — nothing outside the top 10 has a rising "
                 "15-minute contribution with the 4h move agreeing. That is a real read: the feed "
                 "is quiet, not broken.")
    L.append("")

    # The window is stated in EVERY branch, scored or not. "Baseline age is a printed fact" is the
    # design premise; printing it only when something scored is how a wide-window read gets mistaken
    # for a 90-second one on the quiet days, which are most days.
    if b["band"] in ("LIVE", "WIDE"):
        window = f"{b['age_s']:.0f}s" if b["age_s"] is not None else "?"
        note = ("a tight window, the same kind the live strategies watch"
                if b["band"] == "LIVE" else
                f"a WIDE window — these built up over {window}, not in the last minute or two")
        L.append(f"*Suddenly climbing* — compared against a reading from {window} ago ({note}):")
        L.append("")
        if rep["rotations"]:
            for r in rep["rotations"]:
                nm = f"{r['token']}" + (f" ({r['dex']})" if r["dex"] else "")
                side = 'long' if r['direction'] == 'LONG' else 'short'
                L.append(f"- **{nm} {side}** — jumped {r['meta']['rankJump']} places, now #{r['rank']} "
                         f"with {r['traders']} traders on it _(strength {r['score']}: "
                         f"{' · '.join(r['reasons'])})_")
        else:
            L.append("- Nothing. No name jumped far enough or fast enough to count. That is the normal "
                     "answer most of the time — it is what the strategies spend their day waiting "
                     "through, and it is a real read, not a missing one.")
    elif b["band"] == "SWEEP":
        # Inside a sweep there is never a baseline and never will be — a sweep is one reading and
        # keeps no history. So do NOT print "ask again in ~2 minutes": a second sweep would say
        # exactly this again. Point at the command that does own a ring.
        L.append("*Anything just breaking out?* — can't tell from one look. Spotting a name that is "
                 "**suddenly** climbing needs two readings minutes apart, and this is one. Ask again in "
                 "a couple of minutes and I can tell you which of these the top traders are piling "
                 "into right now, versus which were already there.")
    elif b["band"] == "TOOFRESH":
        L.append(f"*Suddenly climbing* — can't say yet. The last reading was only {b['age_s']:.0f} "
                 f"seconds ago, and nothing moves that fast, so anything I reported would be noise. "
                 f"**Ask again in a couple of minutes.**")
    elif b["band"] == "STALE":
        L.append(f"*Suddenly climbing* — can't say. My last reading was {b['age_s'] / 60:.0f} minutes "
                 f"ago, which is too long to call anything *sudden* — a name can climb that far in half "
                 f"an hour without it meaning much. **Ask again in a couple of minutes** and this "
                 f"reading becomes the comparison point.")
    else:
        L.append("*Suddenly climbing* — can't say yet: this is my first reading, so there is nothing to "
                 "compare it against. **Ask again in a couple of minutes** and I can tell you which "
                 "names are climbing right now rather than just sitting high.")
    return "\n".join(L)


READ_TIMEOUT_S = 20          # one leaderboard read; generous, and the only read this script makes


def _adapter(client):
    """`call_tool(name, args)` over an MCPClient — same contract as sweep.py's `_adapter`.

    MCPClient exposes `mcp_call(tool, timeout=..., **arguments)`, NOT `call_tool`. The first cut of
    this script called `client.call_tool(name, args)` and died with AttributeError on the very first
    live run: the engine and its tests inject `call_tool` themselves, so every test passed while the
    only real entry point was broken. `test_cli_adapter_matches_mcpclient` now pins it.
    """
    def call_tool(name, args, timeout=READ_TIMEOUT_S):
        return client.mcp_call(name, timeout=timeout, **args)
    return call_tool


def main(argv=None, _call_tool=None):
    """`_call_tool` is a TEST SEAM, not an option — it is how the CLI path gets covered at all.

    The two bugs that reached a live box both lived between main() and the transport (a wrong method
    name, then an unguarded raise), and both survived a green suite because every test called `read`
    with its own injected call_tool and never went through here. Passing one in lets a test drive
    the real argv parsing, rendering and exit code without an MCP session.
    """
    ap = argparse.ArgumentParser(description="Hyperfeed Movers — the live top-trader feed read")
    ap.add_argument("--json", action="store_true", help="structured output instead of the block")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--xyz-banned", action="store_true", help="crypto only (Penguin's universe)")
    ap.add_argument("--no-persist", action="store_true", help="do not write this run into the ring")
    a = ap.parse_args(argv)

    if _call_tool is None:
        from mcp_client import MCPClient  # noqa: E402 — byte-identical to senpi-smart-money's
        _call_tool = _adapter(MCPClient())
    call_tool = _call_tool

    rep = read(call_tool, state_dir=a.state_dir, top=a.top,
               xyz_banned=a.xyz_banned, persist=not a.no_persist)
    print(json.dumps(rep, indent=2) if a.json else render(rep))
    # EXIT 0 EVEN WHEN THE FEED IS DOWN. A handled outage is a READ, not a crash: the block already
    # says "unavailable" and names the reason, and sweep.py sets the house precedent — it exits 0
    # with "Not measured this run: the 4h leaderboard, …" rather than failing. Live proof this
    # matters: run side by side on a box that could not reach the leaderboard, sweep.py exited 0 and
    # this exited 1, so the agent reported a crashed script instead of presenting the honest block.
    # `rep["ok"]` is in the JSON for a caller that wants to branch on it.
    return 0


if __name__ == "__main__":
    sys.exit(main())
