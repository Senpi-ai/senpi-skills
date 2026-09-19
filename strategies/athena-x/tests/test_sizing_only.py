"""Athena-X is Athena at a different SIZE. Nothing else may differ — that is the whole claim.

Selection and sizing are separate decisions. Athena-X exists so a user can change the second
without hand-editing a fork, which is what happened on 2026-09-19 when the live package was edited
in place to get "bigger". The risk of a package like this is drift: a sizing fork quietly becomes a
second strategy, and then two packages with the same name disagree about what they trade.

So this pins it from both sides:
  - the scanners are byte-identical to the Phalanx and Aegis source templates, same as Athena's;
  - runtime.yaml differs from the source ONLY in identity lines and an ENUMERATED sizing set.
A logic change cannot enter this package without failing here.

Run: python3 -m pytest strategies/athena-x/tests -q
"""
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..")
STRATEGIES = os.path.join(PKG, "..")
LEGS = {"phalanx": "phalanx", "aegis": "aegis"}

# The ONLY keys Athena-X is allowed to move. Adding to this list is a deliberate act.
SIZING_KEYS = ("  slots:", "  margin_pct:", "  default_leverage:", "      marginPctBase:",
               "      marginPctMax:", "      leverageDefault:", "      maxSlots:",
               "    default_signal_validity_seconds:")
IDENTITY = ("name:", "group:", "  wallet:", "  ATHENA")


def _code_lines(path):
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f if l.strip() and not l.lstrip().startswith("#")]


def test_scanners_are_byte_identical_to_the_source_templates():
    for leg, src in LEGS.items():
        sdir = os.path.join(STRATEGIES, src, "main", "scanners")
        ddir = os.path.join(PKG, leg, "scanners")
        for fn in sorted(os.listdir(sdir)):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(sdir, fn), "rb") as a, open(os.path.join(ddir, fn), "rb") as b:
                assert a.read() == b.read(), f"{leg}/{fn} drifted from {src}"


def test_runtime_differs_only_in_identity_and_declared_sizing():
    for leg, src in LEGS.items():
        s = _code_lines(os.path.join(STRATEGIES, src, "main", "runtime.yaml"))
        d = _code_lines(os.path.join(PKG, leg, "runtime.yaml"))
        assert len(s) == len(d), f"{leg}: runtime.yaml gained or lost lines vs {src}"
        for a, b in zip(s, d):
            if a == b:
                continue
            ok = b.startswith(IDENTITY) or any(b.startswith(k) for k in SIZING_KEYS)
            assert ok, f"{leg}: {b!r} is neither identity nor a declared sizing key (was {a!r})"


def test_it_is_actually_bigger_than_athena():
    """A sizing package that does not change the size is a maintenance cost for nothing."""
    for leg in LEGS:
        base = yaml.safe_load(open(os.path.join(STRATEGIES, "athena", leg, "runtime.yaml"), encoding="utf-8"))
        x = yaml.safe_load(open(os.path.join(PKG, leg, "runtime.yaml"), encoding="utf-8"))
        assert x["strategy"]["default_leverage"] > base["strategy"]["default_leverage"], leg
        assert x["strategy"]["margin_pct"] > base["strategy"]["margin_pct"], leg
        assert x["strategy"]["slots"] > base["strategy"]["slots"], leg


def test_the_two_packages_never_share_a_wallet():
    """Both can be installed at once; binding the same env var would point them at one wallet."""
    seen = set()
    for pkg in ("athena", "athena-x"):
        for leg in LEGS:
            rt = yaml.safe_load(open(os.path.join(STRATEGIES, pkg, leg, "runtime.yaml"), encoding="utf-8"))
            w = rt["strategy"]["wallet"]
            assert w not in seen, f"{pkg}/{leg} reuses {w}"
            seen.add(w)


def test_the_daily_entry_cap_survives():
    """The live run this package came from removed it. At 5x, with phalanx now able to enter on a
    STANDING consensus, an uncapped book fills in one burst — keep the cap until evidence says otherwise."""
    for leg in LEGS:
        rt = yaml.safe_load(open(os.path.join(PKG, leg, "runtime.yaml"), encoding="utf-8"))
        assert rt["risk"]["guard_rails"]["max_entries_per_day"] > 0, leg
