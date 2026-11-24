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


def test_runtime_differs_only_in_identity():
    for leg, src in LEGS.items():
        s = _code_lines(os.path.join(STRATEGIES, src, "main", "runtime.yaml"))
        d = _code_lines(os.path.join(PKG, leg, "runtime.yaml"))
        assert len(s) == len(d), f"{leg}: runtime.yaml gained or lost lines vs {src}"
        diffs = [(a, b) for a, b in zip(s, d) if a != b]
        allowed = [b for a, b in diffs if b.startswith(IDENTITY) or b.startswith("  ATHENA ")]
        assert len(diffs) == 4 and len(allowed) == 4, f"{leg}: unexpected differences {diffs}"
        assert f"name: athena-{leg}" in d and "group: athena" in d
        assert f'  wallet: "${{ATHENA_{leg.upper()}_WALLET}}"' in d


def test_default_weighting_is_65_35():
    with open(os.path.join(PKG, "strategy.yaml"), encoding="utf-8") as f:
        m = yaml.safe_load(f)
    shares = {i["name"]: i["funding_share"] for i in m["instances"]}
    assert shares == {"phalanx": 0.65, "aegis": 0.35}
    assert abs(sum(shares.values()) - 1.0) < 1e-9
