#!/usr/bin/env python3
"""Write STRATEGIES.md, the index of every strategy template, from strategies/catalog.json.

    python3 scripts/gen_strategies_md.py

Run it after gen_catalog.py. tests/test_strategies_index.py fails while STRATEGIES.md is stale.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSES = {"btc_eth": "BTC and ETH", "major_alts": "major alts", "universe_crypto": "any crypto perp",
           "xyz_equities": "stocks", "commodities": "commodities", "fx": "FX", "indices": "indices",
           "pre_ipo": "pre-IPO"}
DIRECTION = {"long_only": "Long", "short_only": "Short", "long_short": "Long and short"}


def _cell(text):
    text = " ".join((text or "").split()).replace(" — ", ", ").replace("—", ", ").replace("–", "-")
    return text.replace("|", "/")


def _idea(s):
    """The first sentence or two of the plain-words belief, enough to say what the strategy does."""
    sentences = re.findall(r".+?[.!?](?=\s|$)", _cell(s.get("belief_plain") or s.get("tagline")))
    idea = ""
    for sentence in sentences[:2]:
        idea = (idea + " " + sentence.strip()).strip()
        if len(idea) >= 90:
            break
    return idea or _cell(s.get("belief_plain") or s.get("tagline"))


def _name(s):
    return _cell(s["name"].split(" — ")[0])


def _markets(s):
    if s.get("assets") and s.get("asset_scope") in ("single", "basket"):
        shown = s["assets"][:4]
        more = len(s["assets"]) - len(shown)
        return ", ".join(shown) + (f" and {more} more" if more > 0 else "")
    if s.get("asset_scope") == "follows_traders":
        return "whatever the copied traders trade"
    return ", ".join(CLASSES.get(c, c.replace("_", " ")) for c in s.get("asset_classes", []) if c != "none") or "any"


def _anchor(label):
    return re.sub(r"[^a-z0-9 -]", "", label.lower()).replace(" ", "-")


def render(catalog):
    rows = catalog["skills"]
    groups = defaultdict(list)
    for s in rows:
        groups[s["archetype_label"]].append(s)
    order = sorted(groups, key=lambda g: (-len(groups[g]), g))
    out = [
        "# Senpi strategy templates for Hyperliquid",
        "",
        f"Every strategy template in senpi-skills, the open-source library every Senpi agent runs: {len(rows)} "
        "templates for trading Hyperliquid perps across crypto, stocks, commodities and indices. Each one is a "
        "package you can read here, and each has a page on [senpi.ai/strategies](https://senpi.ai/strategies) "
        "where your Senpi agent can run it 24/7 on its own strategy wallet, with a stop on every position.",
        "",
        f"Catalog as of {catalog['_updated']}. Generated from [`strategies/catalog.json`](strategies/catalog.json) "
        "by `scripts/gen_strategies_md.py`: don't edit this file by hand.",
        "",
        "| Style | Templates |",
        "|---|---|",
        *[f"| [{g}](#{_anchor(g)}) | {len(groups[g])} |" for g in order],
    ]
    for g in order:
        out += ["", f"## {g}", "", "| Template | What it does | Markets | Direction | Risk |", "|---|---|---|---|---|"]
        for s in sorted(groups[g], key=lambda s: _name(s).lower()):
            out.append(f"| [{_name(s)}](strategies/{s['id']}/) ([page](https://senpi.ai/strategies/{s['id']})) "
                       f"| {_idea(s)} | {_markets(s)} | {DIRECTION.get(s['direction'], s['direction'])} "
                       f"| {s['risk_level'].capitalize()} |")
    out += ["", "Trading perpetual futures carries risk, including the loss of your funds. Strategies can and do "
            "lose money. Nothing here is investment advice or a promise of returns.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    catalog = json.loads((ROOT / "strategies" / "catalog.json").read_text(encoding="utf-8"))
    (ROOT / "STRATEGIES.md").write_text(render(catalog), encoding="utf-8")
    print(f"STRATEGIES.md: {len(catalog['skills'])} templates")
