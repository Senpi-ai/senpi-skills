"""Regression guard: the discovery engine must DEFAULT to a REAL catalog, never a fixture.

A 'TEMPORARY, revert before merge' line once pointed the default catalog at
tests/fixtures/catalog_fullfleet.json and shipped to strategy-v2, so live discovery recommended
from synthetic data. These tests fail loudly if the default ever drifts back to a fixture.

The default now resolves to the skill-local `senpi-strategy-discover/catalog.json` (bundled with the
skill) or the repo `strategies/catalog.json` (dev checkout) — both are real; a test fixture is not.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import discover  # noqa: E402

_REAL = ("strategies/catalog.json", "senpi-strategy-discover/catalog.json")


def test_default_catalog_is_production_not_a_fixture():
    p = os.path.abspath(discover.default_catalog()).replace(os.sep, "/")
    assert p.endswith(_REAL), f"default catalog is {p}, not a real catalog"
    assert "tests/fixtures" not in p, f"default catalog points at a test fixture: {p}"


def test_default_catalog_loads_the_real_fleet():
    recs = discover.load_catalog(discover.default_catalog())
    ids = {r.get("id") for r in recs}
    assert len(recs) > 50, f"expected the full fleet, got {len(recs)} records"
    for known in ("kodiak", "rhino", "thesis-fund"):
        assert known in ids, f"real strategy {known!r} missing from the default catalog"


def _run_default(*extra):
    script = os.path.join(HERE, "..", "scripts", "discover.py")
    return subprocess.run(
        [sys.executable, script, "--catalog", discover.default_catalog(), "--no-market", *extra],
        capture_output=True, text=True, timeout=30,
    )


def test_default_cli_output_is_a_bounded_shortlist():
    proc = _run_default()
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["meta"]["eligible_count"] > result["meta"]["returned_n"] == 8
    assert len(proc.stdout) <= 12_000


def test_themed_default_cli_output_is_bounded():
    proc = _run_default("--theme", "k-shape two-speed long-short divergence dispersion winners laggards")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["meta"]["returned_n"] == 8
    assert len(proc.stdout) <= 12_000
    # theme ranking must run before the cap — capped first, this is the alphabetical 8 and cougar is gone
    assert "cougar" in [c["id"] for c in result["candidates"]]


def _risk(cid):
    return next(r for r in discover.load_catalog(discover.default_catalog()) if r["id"] == cid)["risk_level"]


def test_a_risk_ask_returns_that_risk_level_not_incidental_text_matches():
    """SKILL.md's own few-shot ("something safe for BTC, ~$300"): 9 conservative records are eligible, and
    without risk_level scoring the 8 returned held two aggressive/moderate text matches (wolf, hyena)."""
    proc = _run_default("--assets", "btc_eth", "--budget", "300", "--theme", "conservative defensive low-risk hedged")
    assert proc.returncode == 0, proc.stderr
    risks = [_risk(c["id"]) for c in json.loads(proc.stdout)["candidates"]]
    assert len(risks) == 8 and risks.count("conservative") >= 7 and "aggressive" not in risks, risks


def test_variants_fold_into_their_family_instead_of_filling_the_shortlist():
    """Before folding, 4 of 8 were penguin/pelican siblings (references/variant-families.md: offer a
    variant only through its parent). Now one card per family, the siblings named on the parent's card."""
    proc = _run_default("--theme", "aggressive high-leverage momentum breakout")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    cands = result["candidates"]
    assert len(cands) == 8 and not [c["id"] for c in cands if c.get("varies")]
    penguin = next(c for c in cands if c["id"] == "penguin")
    assert {"penguin-chase-200bp", "penguin-chase-300bp", "penguins-duo"} <= set(penguin["variants"])
    assert result["meta"]["eligible_count"] > result["meta"]["families_count"] >= result["meta"]["returned_n"]
    assert len(proc.stdout) <= 12_000


class _LiveShapedClient:
    """Stub MCP client with live-shaped responses so market_facts + user_context are populated."""

    def mcp_call(self, tool, **kw):
        if tool == "market_get_funding_regime":
            return {"data": {"regime": "neutral"}}
        if tool == "account_get_portfolio":
            return {"data": {"portfolio": {"total_in_hyperliquid": 120.5,
                                           "positions": [{"coin": c} for c in ("BTC", "ETH", "SOL", "HYPE")]}}}
        if tool == "market_get_asset_data":
            return {"data": {"asset_context": {"markPx": "101.2", "prevDayPx": "99.0", "funding": "0.0000125"},
                             "oi_velocity": {"oi_trend": "rising"},
                             "candles": {"4h": [{"c": str(100 + i)} for i in range(8)]}}}
        return {"data": {}}


def test_live_enriched_themed_output_degrades_instead_of_failing_closed(monkeypatch, capsys):
    """Production adds market_facts + user_context on top of the --no-market size; an over-budget default
    result must trim (facts, then bottom candidates), never print zero candidates with exit 1."""
    monkeypatch.setattr(discover, "_get_client", lambda: _LiveShapedClient())
    seen = {}
    real_fit = discover.fit_budget

    def spy(result, budget):
        seen["untrimmed"] = len(json.dumps(result, ensure_ascii=False))
        return real_fit(result, budget)

    monkeypatch.setattr(discover, "fit_budget", spy)
    eligible = discover.match(discover.normalize_intent(SimpleNamespace()),
                              discover.load_catalog(discover.default_catalog()))["meta"]["eligible_count"]
    rc = discover.main(["--catalog", discover.default_catalog(), "--theme", "risk-off defensive hedge tail-risk"])
    out = capsys.readouterr().out.strip()
    result = json.loads(out)
    assert rc == 0
    assert len(out) <= discover.OUTPUT_BUDGET
    assert len(result["candidates"]) >= 1
    assert result["meta"]["returned_n"] == len(result["candidates"])
    assert result["meta"]["eligible_count"] == eligible
    trimmed = any(w.startswith("output trimmed to fit") for w in result["meta"]["warnings"])
    assert trimmed == (seen["untrimmed"] > discover.OUTPUT_BUDGET)
    assert {m["id"] for m in result["meta"]["theme_matches"]} <= {c["id"] for c in result["candidates"]}


def _fat_result(n, facts_chars, other_chars):
    fact = {"asset": "BTC", "note": "x" * facts_chars}
    return {"candidates": [{"id": f"s{i}", "blurb": "y" * other_chars, "market_facts": [fact]} for i in range(n)],
            "build_custom": {}, "meta": {"eligible_count": 50, "returned_n": n, "warnings": [],
                                         "theme_matches": [{"id": f"s{i}"} for i in range(n)]}}


def _size(result):
    return len(json.dumps(result, ensure_ascii=False))


def test_fit_budget_leaves_an_in_budget_result_untouched():
    res = _fat_result(4, 100, 100)
    before = json.dumps(res)
    assert json.dumps(discover.fit_budget(res, 100_000)) == before


def test_fit_budget_drops_lowest_ranked_market_facts_first():
    res = _fat_result(4, 1_000, 50)
    budget = _size(res) - 1_500           # two facts' worth of trimming is enough
    discover.fit_budget(res, budget)
    assert _size(res) <= budget
    assert [bool(c["market_facts"]) for c in res["candidates"]] == [True, True, False, False]
    assert res["meta"]["returned_n"] == 4 and res["meta"]["eligible_count"] == 50
    assert res["meta"]["warnings"] == [discover.TRIM_WARNING]


def test_fit_budget_then_drops_bottom_candidates_keeping_theme_matches_consistent():
    res = _fat_result(6, 200, 1_000)
    budget = _size(res) - 2_000           # facts alone can't cover it; candidates must go
    discover.fit_budget(res, budget)
    assert _size(res) <= budget
    ids = [c["id"] for c in res["candidates"]]
    assert ids == [f"s{i}" for i in range(len(ids))] and 1 <= len(ids) < 6   # dropped from the bottom
    assert all(c["market_facts"] == [] for c in res["candidates"])
    assert [m["id"] for m in res["meta"]["theme_matches"]] == ids
    assert res["meta"]["returned_n"] == len(ids) and res["meta"]["eligible_count"] == 50
    assert res["meta"]["warnings"] == [discover.TRIM_WARNING]


def test_fit_budget_never_returns_fewer_than_one_candidate():
    res = _fat_result(3, 100, 5_000)
    discover.fit_budget(res, 10)          # unreachable budget
    assert [c["id"] for c in res["candidates"]] == ["s0"]
    assert res["meta"]["returned_n"] == 1
    assert res["meta"]["warnings"] == [discover.TRIM_WARNING]
