"""STRATEGIES.md is generated from strategies/catalog.json and must match it.

Fix a failure with: python3 scripts/gen_strategies_md.py
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_strategies_md_matches_the_catalog():
    spec = importlib.util.spec_from_file_location("gen_strategies_md", ROOT / "scripts" / "gen_strategies_md.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    want = gen.render(json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8")))
    have = (ROOT / "STRATEGIES.md").read_text(encoding="utf-8")
    assert have == want, "STRATEGIES.md is stale: run python3 scripts/gen_strategies_md.py"
