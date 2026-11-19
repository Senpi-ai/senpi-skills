"""Skill prose never names a strategy template that is not in strategies/catalog.json.

A delisted or deleted template that a skill still names is a dead end the agent keeps offering:
ops refuses it, or a stale copy left on a box deploys anyway. Delist a package and this fails
until every skill stops naming it.

Run: python3 -m pytest tests/test_no_dead_template_names.py -q
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Packages deleted from strategies/ that skill text used to name. A delisted package that still has
# a directory under strategies/ is found on its own.
DELETED = {"stingray"}

# (file relative to the repo root, id) — a dead template named on purpose, e.g. to explain a variant.
ALLOW = {
    ("senpi-strategy-discover/references/variant-families.md", "camel-spread"),
}


def _dead_ids():
    listed = {s["id"] for s in json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))["skills"]}
    packages = {p.parent.name for p in (ROOT / "strategies").glob("*/strategy.yaml")}
    return (packages | DELETED) - listed


def _prose():
    files = [*ROOT.glob("*/SKILL.md"), *ROOT.glob("*/references/**/*.md"), ROOT / "README.md"]
    return [p for p in files if p.relative_to(ROOT).parts[0] not in ("strategies", "docs", "tests")]


def test_skill_prose_names_no_template_outside_the_catalog():
    hits = []
    for dead in sorted(_dead_ids()):
        # the id as prose writes it — `shadow` or Shadow — but not "Shadow-testing" or "a shadow run"
        pat = re.compile(rf"`{re.escape(dead)}`|(?<![\w-]){re.escape(dead.title())}(?![\w-])")
        for p in _prose():
            rel = p.relative_to(ROOT).as_posix()
            if (rel, dead) in ALLOW:
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{rel}:{n}: {dead}")
    assert not hits, "skill text names templates that are not in the catalog:\n" + "\n".join(hits)


def test_the_guard_sees_a_dead_name():
    assert "stingray" in _dead_ids()
    assert re.search(r"(?<![\w-])Stingray(?![\w-])", "route it to **Stingray** instead")
    assert not re.search(r"(?<![\w-])Shadow(?![\w-])", "# Shadow-testing a strategy")
