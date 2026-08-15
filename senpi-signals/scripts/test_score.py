#!/usr/bin/env python3
"""Smoke test for score.py: diff detectors fire, illiquid noise is dropped, flip bonus applies,
events merge, dedup/rank hold, and the state snapshot advances. Run: python3 scripts/test_score.py
"""
import json, subprocess, sys, tempfile, pathlib

HERE = pathlib.Path(__file__).resolve().parent

PRIOR = {"ts": "2026-08-14T00:00:00+00:00", "asset_metrics": {
    "OIL":  {"oi": 1000, "price": 100.0},
    "SPCX": {"smart_dir": "long", "smart_share": 30},   # was LONG -> now SHORT => flip
    "MEME": {"oi": 100},
}}

CURRENT = {"asset_metrics": {
    "OIL":  {"oi": 1120, "price": 100.5, "notional_vol": 4_000_000},           # +12% OI, price flat
    "SPCX": {"smart_dir": "short", "crowd_dir": "long", "smart_share": 40,
             "notional_vol": 3_000_000},                                        # smart flips vs crowd
    "MEME": {"oi": 220, "notional_vol": 100_000},                              # +120% but illiquid -> drop
}, "events": [
    {"asset": "INTC", "detector": "whale_move", "usd": 10_000_000, "concrete_entity": "0x1234",
     "notional_vol": 5_000_000, "direction": "short", "numbers": ["grew INTC short by $10M to $50M"]},
]}


def main():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        state = d / "state.json"; state.write_text(json.dumps(PRIOR))
        cur = d / "cur.json"; cur.write_text(json.dumps(CURRENT))
        out = d / "o.md"
        r = subprocess.run([sys.executable, str(HERE / "score.py"), str(cur),
                            "--state", str(state), "--top", "6", "--out", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"score failed: {r.stderr}"
        res = json.loads(r.stdout)
        sigs = {s["asset"]: s for s in res["signals"]}

        # the three real signals surface; the illiquid one is dropped
        assert set(sigs) == {"OIL", "SPCX", "INTC"}, sigs.keys()
        assert "MEME" not in sigs, "illiquid micro-cap should be dropped"

        # detectors classified right
        assert sigs["OIL"]["detector"] == "oi_surge"
        assert sigs["OIL"]["conflict"] is True, "OI-up + price-flat should tag divergence/conflict"
        assert sigs["SPCX"]["detector"] == "sm_divergence"
        assert sigs["SPCX"]["flip"] is True, "smart money changed side vs prior => flip"
        assert sigs["INTC"]["detector"] == "whale_move"
        assert sigs["INTC"]["concrete_entity"] == "0x1234"

        # flip bonus makes SPCX outrank OIL; all above the floor; ranked descending
        scores = [s["score"] for s in res["signals"]]
        assert scores == sorted(scores, reverse=True), scores
        assert all(s >= 45 for s in scores)
        assert sigs["SPCX"]["score"] > sigs["OIL"]["score"]

        # markdown rendered + observation-not-advice disclaimer present
        md = out.read_text()
        assert "Observation, not advice" in md and "SPCX" in md

        # state snapshot advanced to the current reading
        new_state = json.loads(state.read_text())
        assert new_state["asset_metrics"]["OIL"]["oi"] == 1120, "state should advance to current"

    print("ok — detectors fire, illiquid dropped, flip bonus + rank hold, state advances")


if __name__ == "__main__":
    main()
