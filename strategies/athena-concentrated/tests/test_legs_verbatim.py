"""Athena's sleeves ARE Phalanx and Aegis. Each leg's scanners are byte-identical to its
source template and its runtime.yaml differs only in the identity lines (name, group, wallet,
the description's first line). A fix that lands in phalanx/ or aegis/ must land here too —
this test is what says so. The default weighting is pinned because it is the one thing this
package adds."""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
STRATEGIES = os.path.join(PKG, "..")
LEGS = {"phalanx": "phalanx", "aegis": "aegis"}          # athena leg -> source template
IDENTITY = ("name:", "group:", "  wallet:")


def _code_lines(path):
    """The lines that run: comments and blank lines stripped."""
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.strip() and not l.lstrip().startswith("#")]


def test_scanners_are_byte_identical_to_the_source_templates():
    for leg, src in LEGS.items():
        sdir = os.path.join(STRATEGIES, src, "main", "scanners")
        ddir = os.path.join(PKG, leg, "scanners")
        names = sorted(n for n in os.listdir(sdir) if n.endswith(".py"))
        assert names == sorted(n for n in os.listdir(ddir) if n.endswith(".py")), leg
        for n in names:
            with open(os.path.join(sdir, n), "rb") as a, open(os.path.join(ddir, n), "rb") as b:
                assert a.read() == b.read(), f"{leg}/scanners/{n} drifted from {src}"


# The concentration delta, declared line-for-line. phalanx carries the SAME 45% of its sleeve
# (2 x 22.5 = 3 x 15) through two larger positions instead of three smaller ones; aegis is
# untouched. Listing the exact lines means a fourth change to phalanx, or any change at all to
# aegis, fails here instead of quietly becoming part of "the concentration experiment".
CONCENTRATION_DELTA = {
    "phalanx": {"  slots: 2", "  margin_pct: 22.5", "      marginPctBase: 22.5"},
    # aegis sizes a FLAT 20% per position (2026-10-09). The 2026-10-07 attempt set
    # marginPctBase: 20 and did not deliver it — `margin_pct_for` scales the base by a
    # regime-intensity factor that floors at 0.25, so a mild regime sized 5% and four slots
    # deployed 20% of the sleeve, which is the under-deployment that was reported. base 80 x 0.25
    # lands exactly on 20 and marginPctMax 20 clamps everything above it; the flat outcome is
    # asserted over the whole conviction x regime grid in test_aegis_sizes_flat.py. Listed
    # line-by-line so a FOURTH change to aegis still fails here rather than joining "the experiment".
    # Standalone aegis and athena are deliberately untouched — only this package's leg moved.
    "aegis": {"  margin_pct: 20", "      marginPctBase: 80", "      marginPctMax: 20"},
}


def test_runtime_differs_only_in_identity_and_the_declared_concentration_delta():
    for leg, src in LEGS.items():
        s = _code_lines(os.path.join(STRATEGIES, src, "main", "runtime.yaml"))
        d = _code_lines(os.path.join(PKG, leg, "runtime.yaml"))
        assert len(s) == len(d), f"{leg}: runtime.yaml gained or lost lines vs {src}"
        diffs = [(a, b) for a, b in zip(s, d) if a != b]
        delta = CONCENTRATION_DELTA[leg]
        allowed = [b for a, b in diffs
                   if b.startswith(IDENTITY) or b.startswith("  ATHENA ") or b in delta]
        assert len(diffs) == 4 + len(delta) and len(allowed) == len(diffs), (
            f"{leg}: unexpected differences {[d for d in diffs if d[1] not in delta]}")
        assert {b for _a, b in diffs} >= delta, (
            f"{leg}: the declared concentration delta is missing — expected {sorted(delta)}")
        assert f"name: athena-concentrated-{leg}" in d and "group: athena-concentrated" in d
        assert f'  wallet: "${{ATHENA_{leg.upper()}_WALLET}}"' in d


def test_total_phalanx_exposure_is_unchanged():
    """The experiment is concentration, not size. If total exposure moved too, a difference in
    results could not be attributed to holding two names instead of three."""
    import yaml as _y
    src = _y.safe_load(open(os.path.join(STRATEGIES, LEGS["phalanx"], "main", "runtime.yaml"),
                            encoding="utf-8"))["strategy"]
    dst = _y.safe_load(open(os.path.join(PKG, "phalanx", "runtime.yaml"),
                            encoding="utf-8"))["strategy"]
    assert dst["slots"] * dst["margin_pct"] == src["slots"] * src["margin_pct"], (
        f'phalanx exposure moved: {dst["slots"]}x{dst["margin_pct"]} vs '
        f'{src["slots"]}x{src["margin_pct"]} — concentration and size are now confounded')
    assert dst["slots"] < src["slots"] and dst["margin_pct"] > src["margin_pct"], (
        "fewer, larger bets is the whole package")


def test_default_weighting_is_65_35():
    with open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8") as f:
        m = yaml.safe_load(f)
    shares = {i["name"]: i["funding_share"] for i in m["instances"]}
    assert shares == {"phalanx": 0.65, "aegis": 0.35}
    assert abs(sum(shares.values()) - 1.0) < 1e-9
