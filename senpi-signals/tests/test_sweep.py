#!/usr/bin/env python3
"""Tests for sweep.py — Senpi Signals fetched from the Senpi backend and rendered, nothing computed.

The fixtures under fixtures/backend/ are real signals-service bodies (agent-bridge, served from its
recorded test data): skill-feed.json is GET /v1/signals under the skill profile, backend-feed.json the
same under the backend profile (what prod runs), backend-btc.json GET /v1/signals/BTC?insight=none.
No real wallet addresses. Run: python3 -m pytest senpi-signals/tests -q
"""
import copy
import io
import json
import pathlib
import sys
import urllib.error

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sweep  # noqa: E402

FIX = HERE / "fixtures" / "backend"


def _load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────── the skill computes nothing

def test_no_engine_is_left_in_the_skill():
    """The detectors, scores and cohort live in signals-service. A copy here is a second
    implementation that drifts from what the extension and the app show."""
    for gone in ("score.py", "smartmoney.py", "check_position_age.py"):
        assert not (SCRIPTS / gone).exists(), f"{gone} is back: the signals are computed by the backend"
    src = (SCRIPTS / "sweep.py").read_text(encoding="utf-8")
    for gone in ("import score", "import smartmoney", "mcp_client", "discovery_get_trader_state",
                 "leaderboard_get_markets", "market_list_instruments"):
        assert gone not in src, gone


def test_an_asset_lookup_never_starts_the_backends_llm_read():
    """The agent narrates the card itself; a skill lookup must not pay for the service's LLM read."""
    assert sweep.asset_path("HYPE") == "/v1/signals/HYPE?insight=none"
    assert sweep.asset_path("xyz:TSLA") == "/v1/signals/xyz%3ATSLA?insight=none"
    assert sweep.asset_path(" btc ") == "/v1/signals/btc?insight=none"


# ─────────────────────────────────────────────────────────────── rendering

def test_the_feed_renders_both_sections_with_the_legend_and_badges():
    out = sweep.render_feed(_load("skill-feed"))
    assert out.startswith("# 🔭 Senpi Signals — 2026-10-06 18:58 UTC")
    assert "**How to read this**" in out and "not a probability" in out
    assert "## Tradeable dislocations — for building ideas" in out
    assert "⭐ 🔥 **81.9** · `SOL` — whale_open · 1.00 cred  ⚑" in out
    # the backend's own wording, verbatim: the skill does not reword a read
    assert "opened $1.2M LONG 10 minutes ago on SOL — size, on a record, with a time on it." in out
    assert "SMALL SAMPLE" in out
    assert sweep.QUIET_LINE not in out


def test_the_feed_names_a_failed_source_and_never_reads_it_as_quiet():
    out = sweep.render_feed(_load("backend-feed"))
    assert out.endswith("_Not measured this run: momentum events._")
    assert "## Market news" not in out          # the news feed is empty in this reading: no empty heading


def test_a_quiet_feed_says_so():
    body = dict(_load("backend-feed"), trade=[], news=[], not_measured=[])
    out = sweep.render_feed(body)
    assert sweep.QUIET_LINE in out
    assert "Not measured" not in out


def test_the_lens_and_top_options_cut_the_feed():
    body = _load("skill-feed")
    assert "## Market news" not in sweep.render_feed(body, lens="trade")
    assert "## Tradeable dislocations" not in sweep.render_feed(body, lens="social")
    assert sweep.render_feed(body, lens="trade", top=1).count("· `") == 1


def test_the_brief_is_one_line_per_read_with_an_honest_header():
    body = _load("skill-feed")
    out = sweep.render_brief(body, 3)
    lines = out.splitlines()
    assert lines[0] == "**🔭 Senpi Signals — top reads right now**"
    assert len([x for x in lines if x.startswith("- ")]) == 3
    assert "How to read this" not in out
    weak = copy.deepcopy(body)
    for s in weak["trade"]:
        s["trade_score"] = 50
    assert sweep.render_brief(weak, 2).splitlines()[0] == "**🔭 Senpi Signals — nothing strong right now, context only**"
    assert sweep.render_brief(dict(body, trade=[]), 3).startswith("**🔭 Senpi Signals:** nothing notable")
    assert sweep.render_brief(_load("backend-feed"), 3).endswith("_Not measured this run: momentum events._")


def test_a_stale_reading_carries_its_age():
    import datetime
    body = dict(_load("backend-feed"), stale=True)
    now = datetime.datetime(2026, 10, 6, 19, 40, tzinfo=datetime.timezone.utc)
    line = sweep.stale_line(body, now)
    assert line == ("_This reading is from 2026-10-06 18:58 UTC, about 41 minutes old — "
                    "the Senpi backend is behind._")
    assert line in sweep.render_feed(body, now=now)
    assert line in sweep.render_brief(body, 3, now=now)
    assert sweep.stale_line(_load("backend-feed")) == ""


def test_the_asset_card_carries_the_takeaway_the_split_and_the_board():
    out = sweep.render_asset(_load("backend-btc"))
    assert out.startswith("# 🔭 Senpi Signals — BTC · 2026-10-06 18:58 UTC")
    assert "**In short: Crowded long.** Everyone is already long." in out
    assert "_Watch for: Top wallets starting to sell._" in out
    assert "_Nothing notable on BTC right now — a quiet read is a correct answer._" in out
    # a headcount % never travels without the positioned split
    assert "62% of them are LONG — positioned split 2 short vs 5 long (SMALL SAMPLE)" in out
    # the 4h board is labelled for what it is
    assert "**4h leaderboard** (what's winning right now — not smart money)" in out
    assert "funding +10.95%/yr of position size" in out
    assert out.endswith("_Not measured this run: momentum events._")


def test_an_asset_with_reads_lists_them_and_one_outside_the_universe_says_why():
    card = _load("backend-btc")
    read = dict(_load("skill-feed")["trade"][1])
    read.pop("asset")
    out = sweep.render_asset(dict(card, signals=[read]))
    assert "## Reads" in out and "⭐ 🟠 **68.4** · `BTC` — sm_divergence" in out
    off = sweep.render_asset(dict(card, signals=[], in_universe=False, summary=None))
    assert "outside the markets Senpi Signals scores" in off
    assert "Nothing notable" not in off


# ─────────────────────────────────────────────────────────────── the backend call

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, error):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps({"error": error}).encode()))


class _Opener:
    """Answers with the queued results in order; records every request."""

    def __init__(self, *results):
        self.results, self.requests = list(results), []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Resp(json.dumps(r).encode())


def _fetch(opener, path="/v1/signals", token="tok"):
    return sweep.fetch(path, token=token, url="https://signals.test", opener=opener, sleep=lambda s: None)


def test_fetch_sends_the_agents_senpi_token():
    op = _Opener({"trade": []})
    assert _fetch(op) == {"trade": []}
    req = op.requests[0]
    assert req.full_url == "https://signals.test/v1/signals"
    assert req.get_header("Authorization") == "Bearer tok"
    assert req.get_header("User-agent").startswith("senpi-signals-skill/")


def test_no_token_is_an_auth_error_before_any_request(monkeypatch):
    monkeypatch.delenv("SENPI_AUTH_TOKEN", raising=False)
    op = _Opener()
    with pytest.raises(sweep.Unavailable) as e:
        sweep.fetch("/v1/signals", url="https://signals.test", opener=op)
    assert e.value.code == sweep.EXIT_AUTH and not op.requests


@pytest.mark.parametrize("code,error,line,exit_code", [
    (401, "invalid_token", sweep.AUTH_LINE, sweep.EXIT_AUTH),
    (402, "subscription_required", sweep.SUBSCRIPTION_LINE, sweep.EXIT_AUTH),
    (403, "not_enabled", sweep.NOT_ENABLED_LINE, sweep.EXIT_AUTH),
    (503, "not_ready", sweep.NOT_READY_LINE, sweep.EXIT_UNAVAILABLE),
])
def test_each_refusal_is_named_and_never_retried(code, error, line, exit_code):
    op = _Opener(_http_error(code, error), {"never": "reached"})
    with pytest.raises(sweep.Unavailable) as e:
        _fetch(op)
    assert (e.value.line, e.value.code) == (line, exit_code)
    assert len(op.requests) == 1


def test_a_blip_is_retried_once_then_it_is_an_outage():
    op = _Opener(_http_error(503, "store_unavailable"), {"trade": []})
    assert _fetch(op) == {"trade": []}
    op = _Opener(urllib.error.URLError("refused"), _http_error(500, ""))
    with pytest.raises(sweep.Unavailable) as e:
        _fetch(op)
    assert (e.value.line, e.value.code) == (sweep.OUTAGE_LINE, sweep.EXIT_UNAVAILABLE)
    assert len(op.requests) == sweep.ATTEMPTS


def test_the_backend_is_agents_senpi_ai():
    assert sweep.BASE_URL == "https://agents.senpi.ai"
    op = _Opener({"trade": []})
    sweep.fetch("/v1/signals", token="tok", opener=op)
    assert op.requests[0].full_url == "https://agents.senpi.ai/v1/signals"
    src = (SCRIPTS / "sweep.py").read_text(encoding="utf-8")
    for gone in ("dev.senpi.ai", "SENPI_SIGNALS_URL", "SENPI_MCP_URL"):
        assert gone not in src, gone


# ─────────────────────────────────────────────────────────────── the command line

def _run(argv, fetcher):
    out, err = io.StringIO(), io.StringIO()
    code = sweep.run(argv, fetcher=fetcher, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_print_feed_and_brief_read_the_feed_route():
    paths = []

    def fetcher(path):
        paths.append(path)
        return _load("skill-feed")

    code, out, _ = _run(["--print-feed"], fetcher)
    assert code == 0 and "## Tradeable dislocations" in out
    code, out, _ = _run(["--brief", "3"], fetcher)
    assert code == 0 and out.startswith("**🔭 Senpi Signals — top reads right now**")
    assert paths == ["/v1/signals", "/v1/signals"]


def test_asset_reads_the_card_route_without_the_llm_read():
    paths = []

    def fetcher(path):
        paths.append(path)
        return _load("backend-btc")

    code, out, _ = _run(["--asset", "BTC"], fetcher)
    assert code == 0 and "Crowded long" in out
    assert paths == ["/v1/signals/BTC?insight=none"]


def test_an_outage_is_never_presented_as_a_quiet_market():
    def down(path):
        raise sweep.Unavailable(sweep.OUTAGE_LINE, sweep.EXIT_UNAVAILABLE, "503 store_unavailable")

    for argv in (["--print-feed"], ["--brief", "3"], ["--asset", "BTC"]):
        code, out, err = _run(argv, down)
        assert code == sweep.EXIT_UNAVAILABLE
        assert out.strip() == sweep.OUTAGE_LINE
        assert "nothing notable" not in out.lower() and sweep.QUIET_LINE not in out
        assert "503 store_unavailable" in err          # the detail goes to stderr, never to the user


def test_an_unknown_asset_says_so():
    def unknown(path):
        raise sweep.Unavailable("", sweep.EXIT_UNKNOWN_ASSET, "404 unknown_asset")

    code, out, _ = _run(["--asset", "NOTACOIN"], unknown)
    assert code == sweep.EXIT_UNKNOWN_ASSET
    assert out.strip() == "_NOTACOIN is not a Hyperliquid market Senpi Signals knows._"


def test_json_prints_the_body_as_it_came():
    code, out, _ = _run(["--json"], lambda path: _load("backend-feed"))
    assert code == 0 and json.loads(out) == _load("backend-feed")
