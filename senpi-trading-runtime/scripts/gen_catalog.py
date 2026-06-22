#!/usr/bin/env python3
"""Generate catalog.json (the strategy registry index) from every */strategy.yaml.

The catalog is GENERATED — never hand-edit it. Run from the repo root:
    python3 senpi-trading-runtime/scripts/gen_catalog.py [--updated YYYY-MM-DD] [--branch NAME]

Each record carries DECLARED fields (author-set in strategy.yaml `catalog:`) + DERIVED fields
(computed from `instances[]/params`) + inlined glossary labels (from
senpi-strategy-discover/references/glossary.yaml). Authors own the declared values; this script
only assembles. Validation WARNS (never fails) so a pre-migration / partially-authored package
still generates.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GLOSSARY_PATH = os.path.join(
    REPO_ROOT, "senpi-strategy-discover", "references", "glossary.yaml"
)

INSTRUCTIONS = (
    "GENERATED from each strategy package's strategy.yaml — do not hand-edit; run "
    "senpi-trading-runtime/scripts/gen_catalog.py. Agents read this via senpi-strategy-discover "
    "(scripts/discover.py). DECLARED fields (sub_style, asset_classes, asset_scope, "
    "risk_level, tier, belief_plain, direction) are author-set in strategy.yaml `catalog:`; DERIVED "
    "fields (assets, leverage_max, funding_split, instance_count, cadence_seconds, time_horizon, "
    "max_slots) are computed from instances/params. Labels/glosses come from "
    "senpi-strategy-discover/references/glossary.yaml. 'min_budget' is a suggested comfortable "
    "starting size capped at 100 — NOT a hard gate. A strategy is a deployable package; install "
    "via senpi-strategy-ops."
)

# Declared fields the author should set; missing ones warn.
DECLARED_REQUIRED = [
    "sub_style", "asset_classes", "asset_scope",
    "risk_level", "tier", "belief_plain", "direction",
]

_WARNINGS = []


def warn(msg):
    _WARNINGS.append(msg)


def humanize(slug):
    return str(slug).replace("_", " ").replace("-", " ").title() if slug else slug


def load_glossary():
    try:
        with open(GLOSSARY_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        warn(f"glossary not found at {GLOSSARY_PATH}; labels will be humanized slugs")
        return {}


# ---- derivation helpers (opportunistic; tolerate inconsistent params) ----

def derive_assets(instances, catalog, sid):
    assets = []
    for inst in instances:
        p = (inst.get("params") or {})
        aa = p.get("allowedAssets")
        if isinstance(aa, list):
            assets.extend(aa)
        elif isinstance(p.get("asset"), str):
            assets.append(p["asset"])
    if not assets and isinstance(catalog.get("assets"), list):
        assets = list(catalog["assets"])
    seen, out = set(), []
    for a in assets:
        if a not in seen:
            seen.add(a)
            out.append(a)
    if not out:
        warn(f"{sid}: could not derive `assets` (no allowedAssets/asset param, no declared "
             f"catalog.assets) — named-asset matching will not work for this strategy")
    return out


def derive_leverage_max(instances, catalog, sid):
    vals = []
    for inst in instances:
        p = (inst.get("params") or {})
        for k, v in p.items():
            if k.lower().endswith("maxleverage") and isinstance(v, (int, float)):
                vals.append(v)
        tiers = p.get("leverageTiers")
        if isinstance(tiers, list):
            vals.extend(t["leverage"] for t in tiers
                        if isinstance(t, dict) and isinstance(t.get("leverage"), (int, float)))
        if isinstance(p.get("defaultLeverage"), (int, float)):
            vals.append(p["defaultLeverage"])
    if vals:
        return max(vals)
    if isinstance(catalog.get("leverage_max"), (int, float)):
        return catalog["leverage_max"]
    warn(f"{sid}: could not derive `leverage_max`")
    return None


def derive_max_slots(instances, catalog):
    vals = [(inst.get("params") or {}).get("maxSlots") for inst in instances]
    vals = [v for v in vals if isinstance(v, int)]
    if vals:
        return max(vals)
    return catalog.get("max_slots")


def derive_cadence_seconds(instances):
    ts = [inst.get("tick_seconds") for inst in instances
          if isinstance(inst.get("tick_seconds"), (int, float))]
    return min(ts) if ts else None


def derive_time_horizon(cadence, catalog):
    if catalog.get("time_horizon"):
        return catalog["time_horizon"]
    if cadence is None:
        return None
    if cadence <= 60:
        return "scalp"
    if cadence <= 600:
        return "swing"
    return "position"


def derive_funding_split(instances):
    fs = [inst.get("funding_share") for inst in instances]
    fs = [x for x in fs if isinstance(x, (int, float))]
    return fs if fs else [1.0]


def derive_min_budget(declared, instance_count, sid):
    declared = declared if isinstance(declared, (int, float)) else None
    if declared is not None and declared > 100:
        warn(f"{sid}: declared min_budget {declared} exceeds fleet floor of 100; capping to 100")
        return 100
    return declared if declared is not None else 100


# ---- glossary label inlining + enum validation ----

def label_for(glossary, kind, value, sid):
    if value is None:
        return None
    entry = (glossary.get(kind) or {}).get(value)
    if entry is None:
        warn(f"{sid}: {kind} '{value}' not documented in glossary.yaml")
        return humanize(value)
    return entry.get("label") or entry.get("gloss") or humanize(value)


def validate_declared(glossary, catalog, sid):
    for field in DECLARED_REQUIRED:
        if catalog.get(field) in (None, "", []):
            warn(f"{sid}: missing declared `{field}` (author must set it in strategy.yaml `catalog:`)")
    # enum membership (single-valued)
    for kind in ("risk_level", "tier", "asset_scope", "direction"):
        v = catalog.get(kind)
        if v is not None and v not in (glossary.get(kind) or {}):
            warn(f"{sid}: {kind} '{v}' not in glossary.yaml allowed values")
    # asset_classes (set)
    known_ac = glossary.get("asset_classes") or {}
    for c in (catalog.get("asset_classes") or []):
        if c not in known_ac:
            warn(f"{sid}: asset_class '{c}' not in glossary.yaml")


def build(updated, branch):
    glossary = load_glossary()
    skills = []
    for man_path in sorted(glob.glob("*/strategy.yaml")):
        m = yaml.safe_load(open(man_path)) or {}
        c = m.get("catalog", {}) or {}
        sid = m.get("id", os.path.dirname(man_path))
        instances = m.get("instances", []) or []
        instance_count = len(instances)

        validate_declared(glossary, c, sid)
        cadence = derive_cadence_seconds(instances)

        skills.append({
            # identity
            "id": sid,
            "name": c.get("name"),
            "emoji": c.get("emoji"),
            "tagline": c.get("tagline"),
            "belief_plain": c.get("belief_plain"),
            "version": m.get("version"),
            # worldview / theme surface (declared free text; no controlled vocab, no validation)
            "thesis": c.get("thesis"),
            "tags": c.get("tags") or [],
            # thesis facets (declared) + inlined labels
            "group": c.get("group"),
            "sub_style": c.get("sub_style"),
            "sub_style_label": label_for(glossary, "sub_style", c.get("sub_style"), sid),
            # market (declared + derived)
            "asset_classes": c.get("asset_classes") or [],
            "asset_scope": c.get("asset_scope"),
            "assets": derive_assets(instances, c, sid),
            "direction": c.get("direction"),
            # risk (declared + derived)
            "risk_level": c.get("risk_level"),
            "tier": c.get("tier"),
            "leverage_max": derive_leverage_max(instances, c, sid),
            "time_horizon": derive_time_horizon(cadence, c),
            "cadence_seconds": cadence,
            # capital
            "min_budget": derive_min_budget(c.get("min_budget"), instance_count, sid),
            "instance_count": instance_count,
            "funding_split": derive_funding_split(instances),
            "max_slots": derive_max_slots(instances, c),
        })

    groups = defaultdict(list)
    for s in skills:
        groups[s["group"]].append(s)
    for items in groups.values():
        for i, s in enumerate(sorted(items, key=lambda x: x["name"] or x["id"]), 1):
            s["sort_order"] = i
    skills.sort(key=lambda s: (s["group"] or "", s["sort_order"]))
    return {
        "_version": "2.1",
        "_updated": updated,
        "_generated": True,
        "_instructions": INSTRUCTIONS,
        "skills": skills,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--updated", default="2026-06-17")
    ap.add_argument("--branch", default="strategy-v2")
    a = ap.parse_args()
    catalog = build(a.updated, a.branch)
    with open("catalog.json", "w") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote catalog.json — {len(catalog['skills'])} strategies")
    if _WARNINGS:
        print(f"\n{len(_WARNINGS)} warning(s):", file=sys.stderr)
        for w in _WARNINGS:
            print(f"  ⚠ {w}", file=sys.stderr)
