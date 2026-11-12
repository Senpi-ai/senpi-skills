#!/usr/bin/env python3
"""senpi-signals: Senpi Signals from the Senpi backend, rendered for the agent to present.

    python3 sweep.py --print-feed          # what an agent runs for a user: prints only the feed
    python3 sweep.py --brief 3             # the short version another skill closes with: top 3 reads, one line each
    python3 sweep.py --asset HYPE          # one asset: its reads, the proven cohort's split, the 4h board, market
    python3 sweep.py --json [--asset X]    # debugging: the backend's JSON as it came
    (SENPI_AUTH_TOKEN from env; the backend is https://agents.senpi.ai)

The skill computes nothing. signals-service (agent-bridge) scans the whole Hyperliquid universe every
few minutes and serves the reads, scores and wording; the browser extension and the Senpi app read the
same service, so the agent, the extension and the app always agree. This script only fetches and
formats:

    GET /v1/signals                         the two ranked feeds (--print-feed, --brief)
    GET /v1/signals/{asset}?insight=none    one asset's card, without the service's LLM read
                                            (the agent narrates; a skill lookup must not start one)

There is no local fallback. When the backend cannot answer, the output says Senpi Signals is
unavailable and the exit code is non-zero, so a caller can never present an outage as a quiet market.
Stdlib only; Python 3.9+.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "2.11.0"
BASE_URL = "https://agents.senpi.ai"
TIMEOUT_S = 20
ATTEMPTS = 2           # one retry, for a dropped connection or a 5xx blip; never for a 4xx
RETRY_SLEEP_S = 1.0
STALE_NOTE_MIN = 10    # the service marks a reading stale past 10 min (it refreshes every 3)

# exit codes: a caller that only checks the status must never read an outage as a quiet market
EXIT_OK, EXIT_UNAVAILABLE, EXIT_AUTH, EXIT_UNKNOWN_ASSET = 0, 3, 4, 5


class Unavailable(Exception):
    """The backend did not answer with signals. `code` picks the exit code, `line` is what a user sees."""

    def __init__(self, line, code=EXIT_UNAVAILABLE, detail=""):
        super().__init__(detail or line)
        self.line, self.code, self.detail = line, code, detail


OUTAGE_LINE = ("_Senpi Signals is unavailable right now — the Senpi backend did not answer, so nothing "
               "was measured. This is an outage, not a quiet market. Worth trying again in a minute._")
NOT_READY_LINE = ("_Senpi Signals is starting up — the first scan of the market has not landed yet. "
                  "Worth trying again in a few minutes._")
AUTH_LINE = ("_Senpi Signals could not verify this agent's Senpi sign-in (SENPI_AUTH_TOKEN), so nothing "
             "was read._")
NOT_ENABLED_LINE = "_Senpi Signals is not enabled for this account yet._"
SUBSCRIPTION_LINE = ("_Senpi Signals needs an active Senpi subscription or free trial. Nothing was read; "
                     "the user can subscribe in the Senpi app._")


def fetch(path, token=None, url=None, opener=urllib.request.urlopen, sleep=time.sleep):
    """GET `path` from the backend and return the decoded JSON body; raise Unavailable otherwise."""
    token = token if token is not None else os.environ.get("SENPI_AUTH_TOKEN", "").strip()
    if not token:
        raise Unavailable(AUTH_LINE, EXIT_AUTH, "SENPI_AUTH_TOKEN is not set")
    req = urllib.request.Request((url or BASE_URL) + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "User-Agent": f"senpi-signals-skill/{VERSION}"})
    last = ""
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(RETRY_SLEEP_S)
        try:
            with opener(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = _error_code(e)
            if e.code == 401:
                raise Unavailable(AUTH_LINE, EXIT_AUTH, f"401 {err}")
            if e.code == 402:
                raise Unavailable(SUBSCRIPTION_LINE, EXIT_AUTH, f"402 {err}")
            if e.code == 403:
                raise Unavailable(NOT_ENABLED_LINE, EXIT_AUTH, f"403 {err}")
            if e.code == 404 and err == "unknown_asset":
                raise Unavailable("", EXIT_UNKNOWN_ASSET, "404 unknown_asset")
            if e.code == 503 and err == "not_ready":
                raise Unavailable(NOT_READY_LINE, EXIT_UNAVAILABLE, "503 not_ready")
            last = f"{e.code} {err}"
            if e.code < 500:
                break
        except (urllib.error.URLError, OSError, ValueError) as e:  # network, timeout, a non-JSON body
            last = repr(e)
    raise Unavailable(OUTAGE_LINE, EXIT_UNAVAILABLE, last)


def _error_code(e):
    try:
        return (json.loads(e.read().decode("utf-8")) or {}).get("error", "")
    except Exception:  # noqa: BLE001 — an error body is a hint, never required
        return ""


# ─────────────────────────────────────────────────────────────────── rendering

# Printed at the TOP of every feed, including a quiet one. A reader should never have to ask what a
# number or a marker means.
HOW_TO_READ = [
    "**How to read this**",
    "",
    "🔥 **80+** strongest · 🟠 **65–79** worth a look · 🟡 **under 65** context",
    "⭐ top of feed · ⚑ a named wallet · **early** = positioning is in, the price move hasn't happened yet",
    "",
    "The score is a **0–100 weighted checklist, not a probability** — it ranks what to look at, "
    "it does not predict. *Trade* weighs directional edge · earliness · is it a change · does it cut "
    "against the crowd · size. *News* weighs how invisible it is on a chart · size · contradiction · "
    "change · a named wallet. Both are multiplied by credibility (book depth + how trustworthy the "
    "source is).",
    "",
    "Every claim is dated and priced where we have it — `[2h ago · from $41.20]`. No bracket means "
    "we could not date it, not that it is fresh.",
]
QUIET_LINE = "_Nothing notable stands out right now — a quiet read is a correct answer._"


def badge(sc):
    return "🔥" if sc >= 80 else ("🟠" if sc >= 65 else "🟡")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _score(s, key):
    return _num(s.get(key)) or 0.0


def _when(s):
    w = (s.get("when") or "").strip()
    return f" {w}" if w else ""


def _parse_ts(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_of_label(body):
    t = _parse_ts(body.get("as_of"))
    return t.strftime("%Y-%m-%d %H:%M") + " UTC" if t else "time unknown"


def stale_line(body, now=None):
    """One line when the reading is old: the backend is behind, and the reader must know the age."""
    if not body.get("stale"):
        return ""
    t = _parse_ts(body.get("as_of"))
    if t is None:
        return "_This reading is older than usual — the Senpi backend is behind._"
    now = now or datetime.datetime.now(datetime.timezone.utc)
    mins = max(STALE_NOTE_MIN, int((now - t).total_seconds() // 60))
    return f"_This reading is from {_as_of_label(body)}, about {mins} minutes old — the Senpi backend is behind._"


def not_measured_line(body):
    dark = [x for x in (body.get("not_measured") or []) if x]
    return "_Not measured this run: " + ", ".join(dark) + "._" if dark else ""


def _trade_lines(s, star):
    flag = "  ⚑" if s.get("wallet") else ""
    sc = _score(s, "trade_score")
    return [f"{star}{badge(sc)} **{sc:g}** · `{s.get('asset', '')}` — {s.get('detector', '')} · "
            f"{_score(s, 'credibility'):.2f} cred{flag}",
            f"  {s.get('read', '')}{_when(s)}",
            f"  _{'; '.join(s.get('numbers') or [])}_",
            ""]


def _news_lines(s, star):
    flag = "  ⚑" if s.get("wallet") else ""
    sc = _score(s, "social_score")
    return [f"{star}{badge(sc)} **{sc:g}** · `{s.get('asset', '')}` — {s.get('detector', '')}{flag}",
            f"  {s.get('news', '')}{_when(s)}",
            ""]


def render_feed(body, lens="both", top=None, now=None):
    """The feed an agent presents verbatim: the legend, the two badged sections, the coverage line."""
    trade = list(body.get("trade") or [])[:top] if lens in ("both", "trade") else []
    news = list(body.get("news") or [])[:top] if lens in ("both", "social") else []
    out = [f"# 🔭 Senpi Signals — {_as_of_label(body)}", "",
           "_Observation, not advice. Every number is from Senpi's latest scan of the market._", ""]
    out += HOW_TO_READ + ["", "---", ""]
    if trade:
        out += ["## Tradeable dislocations — for building ideas", ""]
        for i, s in enumerate(trade):
            out += _trade_lines(s, "⭐ " if i == 0 else "")
    if news:
        out += ["## Market news — for content", ""]
        for i, s in enumerate(news):
            out += _news_lines(s, "⭐ " if i == 0 else "")
    if not trade and not news:
        out += [QUIET_LINE, ""]
    tail = [x for x in (stale_line(body, now), not_measured_line(body)) if x]
    return "\n".join(out).rstrip("\n") + ("\n\n" + "\n".join(tail) if tail else "")


def render_brief(body, n, now=None):
    """The short version another skill closes with (senpi-market-pulse): the top n trade reads, one line
    each, with no legend and no engine words."""
    picks = list(body.get("trade") or [])[:max(0, n)]
    if not picks:
        lines = ["**🔭 Senpi Signals:** nothing notable stands out right now."]
    else:
        # a header that says "top reads" over two 🟡 lines recommends the best of a weak lot
        strongest = max(_score(s, "trade_score") for s in picks)
        lines = ["**🔭 Senpi Signals — top reads right now**" if strongest >= 65
                 else "**🔭 Senpi Signals — nothing strong right now, context only**"]
        for s in picks:
            sc = _score(s, "trade_score")
            lines.append(f"- {badge(sc)} **{sc:g}** · `{s.get('asset', '')}` — {s.get('read', '')}{_when(s)}")
    lines += [x for x in (stale_line(body, now), not_measured_line(body)) if x]
    return "\n".join(lines)


def _usd(v):
    v = _num(v)
    if v is None:
        return None
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return f"${v / div:.1f}{suf}"
    return f"${v:,.0f}"


def _price(v):
    v = _num(v)
    if v is None:
        return None
    return f"${v:,.2f}" if abs(v) >= 1 else f"${v:.6g}"


def _pct(v, signed=True):
    v = _num(v)
    if v is None:
        return None
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _side(v):
    return str(v).upper() if v else "no side"


def _market_line(m):
    parts = []
    if _price(m.get("price")):
        parts.append(f"price {_price(m.get('price'))}")
    if _pct(m.get("change_24h_pct")):
        parts.append(f"24h {_pct(m.get('change_24h_pct'))}")
    if _num(m.get("funding_apr_pct")) is not None:
        f = f"funding {_pct(m.get('funding_apr_pct'))}/yr of position size"
        if _num(m.get("funding_pctile")) is not None:
            f += f" ({_num(m.get('funding_pctile')):.0f}th percentile of the universe)"
        parts.append(f)
    if _usd(m.get("vol_24h")):
        parts.append(f"24h volume {_usd(m.get('vol_24h'))}")
    return " · ".join(parts)


def _positioning_lines(p):
    if not p:
        return []
    n_long, n_short, cohort = int(p.get("n_long") or 0), int(p.get("n_short") or 0), int(p.get("cohort_n") or 0)
    head = f"**Proven traders** (wallets with ≥ $1M lifetime realized PnL; {cohort} read): "
    share = _num(p.get("share_pct"))
    if p.get("side") and share is not None:
        head += f"{share:.0f}% of them are {_side(p.get('side'))}"
    else:
        head += "no side"
    head += f" — positioned split {n_short} short vs {n_long} long"
    if p.get("small_sample"):
        head += " (SMALL SAMPLE)"
    lines = [head]
    tiers = [t for t in (p.get("tiers") or []) if t.get("tier") and t.get("tier") != "proven"]
    for t in tiers:
        lines.append(f"  {t['tier']} tier (≥ {_usd(t.get('min_realized_usd'))} lifetime realized; "
                     f"{int(t.get('cohort_n') or 0)} read): {int(t.get('n_short') or 0)} short vs "
                     f"{int(t.get('n_long') or 0)} long")
    return lines


def _hot_line(h):
    share = _num((h or {}).get("share_pct"))
    if not h or share is None:
        return ""
    traders = _num(h.get("traders"))
    who = f" across {traders:.0f} traders" if traders is not None else ""
    return (f"**4h leaderboard** (what's winning right now — not smart money): {share:.0f}% of top "
            f"traders' 4h gains are {_side(h.get('side'))}{who}")


def render_asset(body, now=None):
    """One asset's card: its reads, then the facts they stand on."""
    asset = body.get("asset") or body.get("coin") or "?"
    out = [f"# 🔭 Senpi Signals — {asset} · {_as_of_label(body)}", "",
           "_Observation, not advice. Every number is from Senpi's latest scan of the market._", ""]
    # the card's takeaway, as the extension shows it: headline, story, why, what to watch
    summary = body.get("summary") or {}
    if summary.get("headline"):
        out.append(f"**In short: {summary['headline']}.** {summary.get('story') or ''}".rstrip())
        out += [f"- {w}" for w in (summary.get("why") or []) if w]
        if summary.get("watch"):
            out.append(f"_Watch for: {summary['watch']}_")
        out.append("")
    reads = list(body.get("signals") or [])
    if reads:
        out += ["## Reads", ""]
        for i, s in enumerate(reads):
            out += _trade_lines(dict(s, asset=asset), "⭐ " if i == 0 else "")
    elif not body.get("in_universe", True):
        out += [f"_{asset} is outside the markets Senpi Signals scores (the most-traded names by 24h "
                f"volume), so it carries no reads. The facts below are still current._", ""]
    else:
        out += [f"_Nothing notable on {asset} right now — a quiet read is a correct answer._", ""]
    facts = [x for x in [_market_line(body.get("market") or {})] if x]
    facts += _positioning_lines(body.get("positioning"))
    hot = _hot_line(body.get("hot_4h"))
    if hot:
        facts.append(hot)
    if facts:
        out += ["## Where it stands", ""] + [f"- {x}" if not x.startswith("  ") else f"  {x}" for x in facts]
    tail = [x for x in (stale_line(body, now), not_measured_line(body)) if x]
    return "\n".join(out).rstrip("\n") + ("\n\n" + "\n".join(tail) if tail else "")


# ─────────────────────────────────────────────────────────────────── entry point

def asset_path(asset):
    return "/v1/signals/" + urllib.parse.quote(asset.strip(), safe="") + "?insight=none"


def run(argv=None, fetcher=fetch, out=sys.stdout, err=sys.stderr):
    ap = argparse.ArgumentParser(description="senpi-signals: Senpi Signals from the Senpi backend")
    ap.add_argument("--print-feed", action="store_true", help="print the feed to present (the default)")
    ap.add_argument("--brief", type=int, default=None, metavar="N",
                    help="print only the top N trade reads, one line each (another skill's closing section)")
    ap.add_argument("--asset", default=None, help="one asset (any alias: BTC, BTC-PERP, xyz:TSLA, TSLA)")
    ap.add_argument("--lens", choices=["both", "trade", "social"], default="both")
    ap.add_argument("--top", type=int, default=None, help="cap each feed section at N reads")
    ap.add_argument("--json", action="store_true", help="print the backend's JSON as it came")
    a = ap.parse_args(argv)
    try:
        body = fetcher(asset_path(a.asset) if a.asset else "/v1/signals")
    except Unavailable as e:
        if e.code == EXIT_UNKNOWN_ASSET:
            print(f"_{a.asset} is not a Hyperliquid market Senpi Signals knows._", file=out)
        else:
            print(e.line, file=out)
        print(f"[senpi-signals] {BASE_URL}: {e.detail}", file=err)
        return e.code
    if a.json:
        print(json.dumps(body, indent=2), file=out)
    elif a.asset:
        print(render_asset(body), file=out)
    elif a.brief is not None:
        print(render_brief(body, a.brief), file=out)
    else:
        print(render_feed(body, lens=a.lens, top=a.top), file=out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
