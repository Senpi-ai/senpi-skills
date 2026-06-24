#!/usr/bin/env python3
"""STRICT pre-publish discovery-facet gate (curator only).

`gen_catalog.py` only WARNS on a missing/invalid `catalog:` facet, so a published
strategy with a weak discovery surface ships silently un-matchable. This validator
FAILS (exit 1) on any blocking problem, so a curator never publishes an
undiscoverable strategy. Run from the repo root BEFORE publishing:

    python3 senpi-strategy-author-curator/scripts/validate_catalog_facets.py strategies/<id>

Exit 0 = clean (may have non-blocking WARNs). Exit 1 = blocking errors. Exit 2 = usage/env.
"""
# Copyright 2026 Senpi (https://senpi.ai) - Apache-2.0
import os
import sys

try:
    import yaml
except ImportError:
    print("PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GLOSSARY = os.path.join(REPO_ROOT, "senpi-strategy-discover", "references", "glossary.yaml")

# The 8 author-set facets gen_catalog treats as required (DECLARED_REQUIRED).
REQUIRED = ["archetype", "sub_style", "asset_classes", "asset_scope",
            "risk_level", "tier", "belief_plain", "direction"]
# Glossary section per controlled-vocabulary facet. `sub_style` is extensible (warn, not fail).
SCALAR_VOCAB = {
    "archetype": ("archetype", False),
    "sub_style": ("sub_style", True),
    "asset_scope": ("asset_scope", False),
    "risk_level": ("risk_level", False),
    "tier": ("tier", False),
    "direction": ("direction", False),
}


def main():
    if len(sys.argv) < 2:
        print("usage: validate_catalog_facets.py strategies/<id>", file=sys.stderr)
        sys.exit(2)
    pkg = sys.argv[1].rstrip("/")
    man = os.path.join(pkg, "strategy.yaml")
    if not os.path.exists(man):
        print(f"FAIL  {pkg}: {man} not found", file=sys.stderr)
        sys.exit(1)

    cat = (yaml.safe_load(open(man)) or {}).get("catalog", {}) or {}
    try:
        gl = yaml.safe_load(open(GLOSSARY)) or {}
    except FileNotFoundError:
        print(f"FAIL  glossary not found at {GLOSSARY}", file=sys.stderr)
        sys.exit(1)

    def allowed(section):
        return set((gl.get(section) or {}).keys())

    errors, warns = [], []

    # 1) presence of the 8 required declared facets
    for f in REQUIRED:
        if f not in cat or cat[f] in (None, "", []):
            errors.append(f"missing/empty required facet: {f}")

    # 2) controlled-vocabulary (scalar) facets must match the glossary
    for field, (section, extensible) in SCALAR_VOCAB.items():
        v = cat.get(field)
        if v in (None, ""):
            continue
        if v not in allowed(section):
            msg = f"{field}={v!r} not in glossary[{section}] {sorted(allowed(section))}"
            (warns if extensible else errors).append(
                msg + (" (extensible: add it to glossary.yaml with a gloss)" if extensible else ""))

    # 3) asset_classes - the one field the engine HARD-FILTERS on
    ac = cat.get("asset_classes")
    if ac is not None:
        if not isinstance(ac, list) or not ac:
            errors.append("asset_classes must be a non-empty list (it's the engine's hard filter)")
        else:
            bad = [x for x in ac if x not in allowed("asset_classes")]
            if bad:
                errors.append(f"asset_classes has invalid value(s) {bad}; "
                              f"allowed {sorted(allowed('asset_classes'))}")

    # 4) discovery prose: belief_plain required+real; thesis strongly recommended
    if len((cat.get("belief_plain") or "").strip()) < 20:
        errors.append("belief_plain too short - write a real plain-language sentence (what it does)")
    if len((cat.get("thesis") or "").strip()) < 20:
        warns.append("no real `thesis` - it's the only worldview hook ('run a hedge fund', "
                     "'bet on a war'); add one unless this is a purely mechanical strategy")

    for w in warns:
        print(f"WARN  {pkg}: {w}")
    if errors:
        for e in errors:
            print(f"FAIL  {pkg}: {e}", file=sys.stderr)
        print(f"\n{len(errors)} blocking facet error(s) - fix before publishing to the catalog.",
              file=sys.stderr)
        sys.exit(1)
    print(f"OK  {pkg}: discovery facets clean ({len(warns)} warning(s)).")
    sys.exit(0)


if __name__ == "__main__":
    main()
