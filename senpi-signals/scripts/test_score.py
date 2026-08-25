#!/usr/bin/env python3
"""Tests for score.py — the dual-lens (trade + social) ranker.

Pure-function units for the three multipliers (credibility, confirmation, freshness) and the
family-capped rank(), plus an end-to-end subprocess run that exercises: two feeds, illiquid drop,
the trade-only liquidity floor, change>state (funding_flip beats/keeps a static funding_extreme out
of the trade feed), the ~1h baseline-ring pick (a 3-min-old snapshot must NOT be the diff baseline),
and the state advancing. Run: python3 scripts/test_score.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402

NOW = "2026-08-24T02:00:00+00:00"
BASELINE_TS = "2026-08-24T00:55:00+00:00"   # ~65 min old → the correct diff baseline
NOISE_TS = "2026-08-24T01:57:00+00:00"      # 3 min old → must NOT be chosen as the baseline


# ── pure units: the three multipliers + rank() ──

def test_credibility_multiplier_bands():
    assert score.credibility(50_000_000) == 1.0            # fat book → full credibility
    assert score.credibility(1_000_000) == 0.45            # at the drop floor → min mult
    assert score.credibility(500_000) == 0.45              # below floor clamps (dropped upstream anyway)
    assert score.credibility(None) == 0.8                  # unknown liquidity ≠ proof of thinness
    mid = score.credibility(13_000_000)
    assert 0.45 < mid < 1.0                                # ramps monotonically between floor and full


def test_confirmation_rewards_price_agreeing_with_the_side():
    assert score.confirmation({"direction": "short", "price_change_pct": -3.0}) == 1.0   # short + down = working
    assert score.confirmation({"direction": "long", "price_change_pct": 3.0}) == 1.0     # long + up = working
    assert score.confirmation({"direction": "short", "price_change_pct": 3.0}) == 0.0    # short + up = contra
    assert score.confirmation({"direction": None, "price_change_pct": -3.0}) == 0.5      # no side = neutral
    assert score.confirmation({"direction": "short", "price_change_pct": None}) == 0.5   # no price = neutral


def test_freshness_penalizes_recent_repeats_and_recovers():
    now = score._parse_ts(NOW)
    assert score.freshness("BTC", "oi_surge", {}, now) == 1.0                                   # never shown
    just = {"BTC|oi_surge": "2026-08-24T01:58:00+00:00"}                                        # 2 min ago
    assert score.freshness("BTC", "oi_surge", just, now) < 0.6                                  # heavy penalty
    old = {"BTC|oi_surge": "2026-08-24T00:55:00+00:00"}                                         # 65 min ago
    assert score.freshness("BTC", "oi_surge", old, now) == 1.0                                  # recovered


def test_rank_caps_per_family_and_dedupes_asset():
    sigs = [
        {"asset": "A", "detector": "funding_flip", "social_score": 90},
        {"asset": "B", "detector": "funding_extreme", "social_score": 85},
        {"asset": "C", "detector": "funding_extreme", "social_score": 80},   # 3rd funding → capped out
        {"asset": "D", "detector": "oi_surge", "social_score": 70},
    ]
    kept = score.rank(sigs, "social_score", 40, 6, 2)
    fams = [score.FAMILY[s["detector"]] for s in kept]
    assert fams.count("funding") == 2, fams          # family cap holds — no funding flood
    assert "oi" in fams                               # a different family still gets a slot


def test_default_state_path_is_durable_and_env_overridable():
    # the diff engine is worthless if state doesn't survive across chats — the default must be durable,
    # never /tmp (which the agent wipes per-chat). Env overrides let ops point at a persistent volume.
    saved = {k: os.environ.get(k) for k in ("SENPI_SIGNALS_STATE", "SENPI_STATE_DIR")}
    try:
        os.environ.pop("SENPI_STATE_DIR", None)
        os.environ["SENPI_SIGNALS_STATE"] = "/data/sig.json"
        assert score._default_state_path() == "/data/sig.json"                       # explicit file wins
        os.environ.pop("SENPI_SIGNALS_STATE")
        os.environ["SENPI_STATE_DIR"] = "/data/.openclaw/senpi-state"                # the runtime's base state dir…
        assert score._default_state_path() == "/data/.openclaw/senpi-state/signals/state.json"  # …+ signals/ subdir
        os.environ.pop("SENPI_STATE_DIR")
        p = score._default_state_path()                                              # else the runtime's home default…
        assert p.endswith("/.openclaw/senpi-state/signals/state.json") and "/tmp" not in p, p    # …never /tmp
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_one_sidedness_uses_the_positioned_split_not_the_whole_cohort():
    # "43% of the cohort is short" is a ROUT at 429-vs-40 and NOISE at 429-vs-380. The un-positioned
    # remainder is not the other side, so it must never be counted as one.
    rout = score.one_sidedness({"smart_short_n": 429, "smart_long_n": 40}, "short")
    noise = score.one_sidedness({"smart_short_n": 429, "smart_long_n": 380}, "short")
    assert rout > 0.9 and 0.5 < noise < 0.55, (rout, noise)
    # reads the side it's asked about
    assert score.one_sidedness({"smart_short_n": 429, "smart_long_n": 40}, "long") < 0.1
    # unknown split → None (caller must say "unknown", never imply a side)
    assert score.one_sidedness({"smart_short_n": 429}, "short") is None
    assert score.one_sidedness({}, "short") is None
    assert score.one_sidedness({"smart_short_n": 0, "smart_long_n": 0}, "short") is None


def test_cohort_positioning_trend_fires_on_a_12h_build():
    # the signal Jason asked for: "43% now vs 38% of the same cohort ~12h ago".
    now = score._parse_ts("2026-08-25T12:00:00+00:00")
    cur = {"HYPE": {"smart_dir": "short", "crowd_dir": "long", "smart_share": 43,
                    "smart_short_n": 429, "smart_long_n": 40, "notional_vol": 9e8}}
    slow = {"HYPE": {"smart_dir": "short", "smart_share": 38}}          # ~12h ago
    sigs = score.detect_from_metrics(cur, {}, slow)
    trend = [s for s in sigs if s["detector"] == "sm_positioning_build"]
    assert len(trend) == 1, [s["detector"] for s in sigs]
    t = trend[0]
    assert t["is_change"] is True and t["direction"] == "short"
    joined = " ".join(t["numbers"])
    assert "43% of the proven cohort" in joined and "38%" in joined and "up from" in joined, joined
    assert "one-sided" in joined, joined                                 # carries the positioned split
    # a build outranks a merely-standing divergence on the same name (change + edge both higher)
    div = [s for s in sigs if s["detector"] == "sm_divergence"][0]
    for s in (t, div):
        s["trade_score"] = score.trade_score(s, 1.0)
    assert t["trade_score"] > div["trade_score"], (t["trade_score"], div["trade_score"])
    # a move below the threshold, or a side rotation, does NOT fire
    assert not [s for s in score.detect_from_metrics(cur, {}, {"HYPE": {"smart_dir": "short", "smart_share": 42}})
                if s["detector"] == "sm_positioning_build"]              # +1pp < TREND_MIN_PP
    assert not [s for s in score.detect_from_metrics(cur, {}, {"HYPE": {"smart_dir": "long", "smart_share": 38}})
                if s["detector"] == "sm_positioning_build"]              # different side = not a build


def test_whale_move_requires_a_move_not_a_holding():
    # round-3 rule: a big HOLDING is not a signal — only a recent move (open/add/flip or PnL swing) is.
    assert score.normalize_event({"asset": "HYPE", "detector": "whale_move", "notional_vol": 5e8,
                                  "numbers": ["holds $78M HYPE from an old entry"]}) is None
    ev = score.normalize_event({"asset": "INTC", "detector": "whale_move", "change_usd": 10_000_000,
                                "concrete_entity": "0x1234", "notional_vol": 8e6})
    assert ev is not None and ev["magnitude"] > 0 and ev["concrete_entity"] == "0x1234"   # a real add fires
    assert score.normalize_event({"asset": "SOL", "detector": "whale_move", "opened": True}) is not None
    assert score.normalize_event({"asset": "ETH", "detector": "whale_move", "pnl_swing_usd": 4e6}) is not None


# ── end-to-end ──

PRIOR_STATE = {"ts": NOW, "snapshots": [
    # the ~65-min baseline the change-detectors SHOULD diff against
    {"ts": BASELINE_TS, "asset_metrics": {
        "OIL":  {"oi": 1000, "price": 100.0},
        "SPCX": {"smart_dir": "long", "smart_share": 30},        # was LONG → now SHORT ⇒ flip
        "FUND": {"funding_annualized_pct": 40.0},                # was +40%/yr → now negative ⇒ funding_flip
        "MICRO": {"oi": 100},
    }},
    # a 3-min-old near-copy of CURRENT: if the baseline picker wrongly used THIS, the deltas vanish
    {"ts": NOISE_TS, "asset_metrics": {
        "OIL":  {"oi": 1149, "price": 100.29},
        "SPCX": {"smart_dir": "short", "smart_share": 42},
        "FUND": {"funding_annualized_pct": -30.0},
        "MICRO": {"oi": 249},
    }},
], "surfaced": {}}

CURRENT = {"asset_metrics": {
    "OIL":  {"oi": 1150, "price": 100.3, "notional_vol": 40_000_000},                    # +15% OI, price flat
    "SPCX": {"smart_dir": "short", "crowd_dir": "long", "smart_share": 42,
             "price_change_pct": -3.5, "notional_vol": 20_000_000},                       # smart flips short, price down
    "FUND": {"funding_annualized_pct": -30.0, "funding_pctile": 80, "notional_vol": 30_000_000},  # sign FLIP
    "FEXT": {"funding_pctile": 98, "funding_annualized_pct": 120, "notional_vol": 30_000_000},    # static extreme
    "FEX2": {"funding_pctile": 97, "funding_annualized_pct": 90, "notional_vol": 25_000_000},     # static extreme
    "THIN": {"smart_dir": "short", "crowd_dir": "long", "smart_share": 35,
             "notional_vol": 4_000_000},                                                  # mid-liquid: social-only band
    "MICRO": {"oi": 250, "notional_vol": 200_000},                                        # +150% OI but illiquid → drop
}, "events": [
    {"asset": "INTC", "detector": "whale_move", "change_usd": 10_000_000, "concrete_entity": "0x1234",
     "notional_vol": 8_000_000, "direction": "short", "price_change_pct": -2.0,
     "numbers": ["grew INTC short by $10M to $50M"]},   # a real MOVE (change_usd), not a bare holding
]}


def _run(current, state_path, now, extra=None):
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        cur = d / "cur.json"; cur.write_text(json.dumps(current))
        out = d / "o.md"
        r = subprocess.run([sys.executable, str(HERE / "score.py"), str(cur), "--state", str(state_path),
                            "--now", now, "--out", str(out), *(extra or [])],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"score failed: {r.stderr}"
        return json.loads(r.stdout), out.read_text()


def test_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        state = pathlib.Path(d) / "state.json"
        state.write_text(json.dumps(PRIOR_STATE))
        res, md = _run(CURRENT, state, NOW)

        trade = {s["asset"]: s for s in res["trade"]}
        social = {s["asset"]: s for s in res["social"]}

        # two feeds exist; the ~1h snapshot (not the 3-min one) was the diff baseline
        assert res["diff_baseline_ts"] == BASELINE_TS, res["diff_baseline_ts"]

        # illiquid micro-cap dropped from BOTH feeds
        assert "MICRO" not in trade and "MICRO" not in social

        # the thin ($3M) market is content-worthy but excluded from the TRADE feed (liquidity floor)
        assert "THIN" in social and "THIN" not in trade

        # detectors classified right; the funding sign-flip is a CHANGE, the static levels are not
        assert social["OIL"]["detector"] == "oi_surge" and social["OIL"]["conflict"] is True
        assert social["SPCX"]["detector"] == "sm_divergence" and social["SPCX"]["flip"] is True
        assert trade["FUND"]["detector"] == "funding_flip" and trade["FUND"]["is_change"] is True

        # change>state: a static funding_extreme is carry, not a trade — it never clears the trade floor
        assert all(s["detector"] != "funding_extreme" for s in res["trade"]), res["trade"]
        # …but funding_flip (the regime change) does earn a trade slot
        assert "FUND" in trade

        # confirmation pays: smart-short with price falling outranks the OI coil on the trade lens
        assert trade["SPCX"]["trade_score"] > trade["OIL"]["trade_score"]

        # family cap: at most 2 funding-family items in each feed (no more 4-funding floods)
        for feed in (res["trade"], res["social"]):
            fams = [score.FAMILY[s["detector"]] for s in feed]
            assert fams.count("funding") <= 2, fams

        # credibility is a multiplier: same detector (sm_divergence), the fatter book scores higher
        assert social["SPCX"]["social_score"] > social["THIN"]["social_score"]
        assert social["SPCX"]["credibility"] > social["THIN"]["credibility"]
        assert social["THIN"]["credibility"] < 1.0   # thin book is discounted, not full-credibility

        # both lenses ranked descending, each within its floor
        assert [s["trade_score"] for s in res["trade"]] == sorted((s["trade_score"] for s in res["trade"]), reverse=True)
        assert all(s["trade_score"] >= score.MIN_TRADE for s in res["trade"])
        assert all(s["social_score"] >= score.MIN_SOCIAL for s in res["social"])

        # markdown: disclaimer + both sections rendered
        assert "Observation, not advice" in md
        assert "for building ideas" in md and "for content" in md

        # state advanced: the ring grew with the current reading, surfaced marks the social picks
        st = json.loads(state.read_text())
        newest = st["snapshots"][-1]
        assert newest["asset_metrics"]["OIL"]["oi"] == 1150, "ring should append the current reading"
        adhoc = st["surfaced_by"]["adhoc"]
        assert adhoc, "surfaced map should record what was shown, for anti-repeat"
        assert any(k.startswith("SPCX|") for k in adhoc)

        # anti-repeat: an identical re-run 5 min later shrinks the SOCIAL feed (just-surfaced items are
        # penalized below the floor — a cron won't re-post the same six), while the TRADE feed is
        # unchanged (a standing edge is still an edge — users still get it).
        res2, _ = _run(CURRENT, state, "2026-08-24T02:05:00+00:00")
        assert len(res2["social"]) < len(res["social"]), "social should rotate, not repeat"
        assert {s["asset"] for s in res2["trade"]} == {s["asset"] for s in res["trade"]}, "trade is not freshness-gated"


def test_consumer_namespacing_shares_ring_isolates_freshness():
    # the content cron and a user's on-demand run share ONE market baseline (the ring) but keep
    # SEPARATE anti-repeat memory — the cron's "already posted" must not blank a user's browse.
    with tempfile.TemporaryDirectory() as d:
        state = pathlib.Path(d) / "state.json"
        state.write_text(json.dumps(PRIOR_STATE))

        r_soc, _ = _run(CURRENT, state, NOW, extra=["--consumer", "social"])
        assert r_soc["social"], "social consumer should produce a feed"
        sb = json.loads(state.read_text())["surfaced_by"]
        assert sb.get("social"), sb                         # only the social consumer's memory was written
        assert "adhoc" not in sb, sb

        # a user run 5 min later (default 'adhoc') shares the ~1h ring baseline but is NOT penalized
        # by what the cron surfaced
        r_adhoc, _ = _run(CURRENT, state, "2026-08-24T02:05:00+00:00")
        assert r_adhoc["diff_baseline_ts"] == BASELINE_TS, "adhoc run shares the same ~1h baseline"
        assert {s["asset"] for s in r_soc["social"]} & {s["asset"] for s in r_adhoc["social"]}, \
            "adhoc feed should not be suppressed by the cron's anti-repeat memory"
        sb2 = json.loads(state.read_text())["surfaced_by"]
        assert sb2.get("social") and sb2.get("adhoc"), sb2  # both memories now exist, independently


def test_quiet_market_is_a_clean_empty():
    # no diffs, no events, no prior → nothing fires, and that's a correct answer (not a crash)
    with tempfile.TemporaryDirectory() as d:
        state = pathlib.Path(d) / "s.json"
        res, md = _run({"asset_metrics": {"BTC": {"oi": 1000, "price": 50000, "notional_vol": 9e8}}},
                       state, NOW)
        assert res["trade"] == [] and res["social"] == []
        assert "Nothing notable" in md


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL {len(fns)} SIGNALS TESTS PASS")
