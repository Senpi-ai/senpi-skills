#!/usr/bin/env python3
"""Generate catalog.json (the strategy registry index) from every */strategy.yaml.

The catalog is GENERATED — never hand-edit it. Run from the repo root:
    python3 senpi-trading-runtime/scripts/gen_catalog.py [--updated YYYY-MM-DD] [--branch NAME]
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import glob
import json
from collections import defaultdict

import yaml

INSTRUCTIONS = (
    "GENERATED from each strategy package's strategy.yaml — do not hand-edit; "
    "run senpi-trading-runtime/scripts/gen_catalog.py. Agents read this to build "
    "the discovery list (senpi-strategy-discover). Group by 'group' (archetype "
    "slug), humanize the slug, sort within group by 'sort_order'. Do NOT invent "
    "quality tiers. 'min_budget' (~$100) is the platform floor and a suggested "
    "starting size, NOT a hard gate — position size scales with budget. A strategy "
    "is a deployable package; install via senpi-strategy-ops install_strategy."
)


def build(updated: str, branch: str) -> dict:
    skills = []
    for man_path in sorted(glob.glob("*/strategy.yaml")):
        m = yaml.safe_load(open(man_path)) or {}
        c = m.get("catalog", {})
        skills.append({
            "id": m["id"],
            "name": c.get("name"),
            "emoji": c.get("emoji"),
            "tagline": c.get("tagline"),
            "group": c.get("group"),
            "risk_level": c.get("risk_level"),
            "min_budget": c.get("min_budget", 100),
            "version": m["version"],
            "branch": branch,
        })
    groups = defaultdict(list)
    for s in skills:
        groups[s["group"]].append(s)
    for items in groups.values():
        for i, s in enumerate(sorted(items, key=lambda x: x["name"]), 1):
            s["sort_order"] = i
    skills.sort(key=lambda s: (s["group"], s["sort_order"]))
    return {
        "_version": "2.0",
        "_updated": updated,
        "_generated": True,
        "_instructions": INSTRUCTIONS,
        "skills": skills,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--updated", default="2026-06-08")
    ap.add_argument("--branch", default="strategy-v2")
    a = ap.parse_args()
    catalog = build(a.updated, a.branch)
    with open("catalog.json", "w") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote catalog.json — {len(catalog['skills'])} strategies")
