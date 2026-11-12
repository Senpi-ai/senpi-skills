"""The condor family's proven-cohort block must not drift between its three packages.

On 2026-10-12 condor, wild-condor and condor-concentrated all moved off the 4h gain
leaderboard for their DIRECTIONAL read. That board is circular: when a coin falls, every
wallet short it is mechanically at the top of the 4h gain board, so it "confirms" the very
move it is being asked to confirm — condor's own scorer conceded it was "a momentum read of
who is winning right now, not a proven cohort". Direction now comes from the 3TF price
action and is confirmed by HEADCOUNT among the wallets with >= $1M LIFETIME realized PnL.

Three copies of that block now exist, which is the repo's convention (camel's scorers,
aegis's scanners, shadow/squid's blacklist) — and the reason those all carry a drift test
is that a copy which silently diverges is indistinguishable from one that was fixed.

THE SPECIFIC FAILURE THIS GUARDS. The first build of condor-concentrated read lifetime PnL
with a field list that omitted `realizedProfitAndLoss` — the only name the API actually
sends. Every trader read 0.0, nothing cleared the $1M floor, and the cohort was empty on
every tick while logging "no traders with >= $1M realized", which reads like a market fact.
368 wallets qualified at the time. A live $600 deployment could not have fired once. One
package drifting back to a short field list would reproduce that exactly, and silently.

Deliberately text-based: three packages ship a module named `scoring` and another named
`scan`, so importing them in the shared pytest run would bind the wrong one.

Run: python3 -m pytest strategies/tests/test_condor_family_cohort.py -q
"""
import hashlib
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FAMILY = ("condor", "wild-condor", "condor-concentrated")
CANONICAL_FIELDS = ("realizedProfitAndLoss", "realized_profit_and_loss",
                    "profit_and_loss_realized", "realizedPnl", "realized_pnl")


def _scan(pkg):
    with open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scan.py"),
              encoding="utf-8") as fh:
        return fh.read()


def _cohort_block(pkg, normalise_prefix=True):
    """`_traders_of` through the end of `fetch_proven_cohort` — the shared part.

    Each package tags its own stderr (`[condor.scan]`, `[wild-condor.scan]`,
    `[condor-cc.scan]`), which is the one legitimate per-package difference, so it is
    normalised away before comparing. Everything else must match byte for byte."""
    src = _scan(pkg)
    start = src.index("def _traders_of(data):")
    end = src.index("def get_btc_macro(")
    block = src[start:end]
    if normalise_prefix:
        for tag in ("[wild-condor.scan]", "[condor-cc.scan]"):
            block = block.replace(tag, "[condor.scan]")
    return block


# ── the block is identical across all three ────────────────────────────────────────────

def test_the_cohort_block_is_byte_identical_across_the_family():
    digests = {p: hashlib.sha256(_cohort_block(p).encode()).hexdigest() for p in FAMILY}
    assert len(set(digests.values())) == 1, (
        "the proven-cohort block has drifted between condor family packages:\n  " +
        "\n  ".join(f"{p}: {d[:16]}" for p, d in digests.items()))


# ── the field read: the bug that shipped ───────────────────────────────────────────────

@pytest.mark.parametrize("pkg", FAMILY)
def test_the_realized_pnl_field_list_is_complete_and_correctly_ordered(pkg):
    block = _cohort_block(pkg)
    found = re.findall(r'"(realized[A-Za-z_]*|profit_and_loss_realized|pnl_realized)"', block)
    assert found[:5] == list(CANONICAL_FIELDS), (
        f"{pkg}: the realized-PnL field list is {found[:5]}, not the canonical tuple. "
        "`realizedProfitAndLoss` is the only name the API sends and it MUST come first — "
        "omitting it reads 0.0 for every trader and empties the cohort on every tick.")


@pytest.mark.parametrize("pkg", FAMILY)
def test_an_unreadable_response_is_not_reported_as_an_empty_market(pkg):
    """The log line must blame the field read, not claim the market has no winners."""
    assert "FIELD READ is broken" in _cohort_block(pkg), (
        f"{pkg}: an all-zero response must be reported as a broken read")


@pytest.mark.parametrize("pkg", FAMILY)
def test_a_failed_read_keeps_the_cached_cohort(pkg):
    """The cohort is a hard gate, so degrading to {} silently stops all trading."""
    block = _cohort_block(pkg)
    assert block.count("cohort_cache.get(\"data\", {}) if cohort_cache else {}") >= 3, (
        f"{pkg}: every failure path must fall back to the cached cohort")


# ── the board no longer supplies direction, anywhere in the family ─────────────────────

@pytest.mark.parametrize("pkg", FAMILY)
def test_the_board_is_price_data_only(pkg):
    src = _scan(pkg)
    assert "def fetch_price_changes" in src, f"{pkg}: still has the old fetch_sm_map"
    fn = src[src.index("def fetch_price_changes"):src.index("def _traders_of")]
    for banned in ("is_dominant", "pct_of_top_traders_gain", "trader_count"):
        assert banned not in fn, (
            f"{pkg}: fetch_price_changes still reads `{banned}` — the board must supply "
            "price data only, or the circular read is back")


@pytest.mark.parametrize("pkg", FAMILY)
def test_the_cohort_is_a_hard_gate_in_scan(pkg):
    src = _scan(pkg)
    assert "fetch_proven_cohort(" in src
    assert "no_cohort" in src, f"{pkg}: an unavailable cohort must short-circuit the tick"


# ── wild-condor is condor's scanner apart from its log prefix ──────────────────────────

def test_wild_condor_differs_from_condor_only_by_its_log_prefix():
    """It is a sizing/score-floor fork, so the detector must stay the parent's. Keeping
    this true is what lets a reader compare their results at all."""
    a, b = _scan("condor"), _scan("wild-condor")
    normalised = b.replace("[wild-condor.scan]", "[condor.scan]").replace("WILD CONDOR —",
                                                                          "CONDOR —")
    assert normalised == a, "wild-condor's scanner has drifted beyond its log prefix"

    def _scoring(pkg):
        with open(os.path.join(ROOT, "strategies", pkg, "main", "scanners", "scoring.py"),
                  encoding="utf-8") as fh:
            return fh.read()
    assert _scoring("condor") == _scoring("wild-condor"), (
        "condor and wild-condor must share one scorer byte-for-byte")
