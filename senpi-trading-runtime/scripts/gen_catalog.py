#!/usr/bin/env python3
"""Generate strategies/catalog.json (the strategy registry index) from every strategies/*/strategy.yaml.

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
import datetime
import glob
import json
import os
import sys
from collections import defaultdict

import yaml

import min_budget  # vendored (byte-identical with senpi-strategy-ops) — the canonical minimum calc

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GLOSSARY_PATH = os.path.join(
    REPO_ROOT, "senpi-strategy-discover", "references", "glossary.yaml"
)

INSTRUCTIONS = (
    "GENERATED from each strategy package's strategy.yaml — do not hand-edit; run "
    "senpi-trading-runtime/scripts/gen_catalog.py. Agents read this via senpi-strategy-discover "
    "(scripts/discover.py). DECLARED fields (archetype, sub_style, asset_classes, asset_scope, "
    "risk_level, tier, belief_plain, direction) are author-set in strategy.yaml `catalog:`; DERIVED "
    "fields (assets, leverage_max, funding_split, instance_count, cadence_seconds, time_horizon, "
    "max_slots) are computed from instances/params. Labels/glosses come from "
    "senpi-strategy-discover/references/glossary.yaml. 'min_budget' is the machine-COMPUTED minimum "
    "total budget at which the design functions (min_budget.py) — the smallest budget where every "
    "wallet funds and its smallest slot clears the $12 bumped notional; 'wallet_count' + "
    "'min_budget_binding_wallet' + 'min_budget_breakdown' explain it. It is NOT authored — deleting "
    "an authored min_budget is a no-op; use min_budget_floor to RAISE it. Positions scale with budget "
    "above the min. A strategy is a deployable package; install via senpi-strategy-ops."
)

# Declared fields the author should set; missing ones warn.
DECLARED_REQUIRED = [
    "archetype", "sub_style", "asset_classes", "asset_scope",
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
    # Universe scanners + cohort/copy-traders legitimately have no fixed asset list
    # (they scan the live board or follow traders), so named-asset matching not applying
    # is by-design, not a defect — don't warn for those scopes.
    if not out and catalog.get("asset_scope") not in ("universe", "follows_traders"):
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


def derive_cadence_seconds(instances, pkg_dir):
    """Min scan cadence. Prefer the legacy manifest `tick_seconds`; otherwise read each leg's
    runtime.yaml external_scanner `interval_seconds` (the thin v2 manifest dropped tick_seconds)."""
    vals = []
    for inst in instances:
        ts = inst.get("tick_seconds")
        if isinstance(ts, (int, float)):
            vals.append(ts)
            continue
        rt_rel = inst.get("runtime")
        if not rt_rel:
            continue
        try:
            rt = yaml.safe_load(open(os.path.join(pkg_dir, rt_rel))) or {}
        except (FileNotFoundError, yaml.YAMLError):
            continue
        for s in rt.get("scanners", []) or []:
            if (isinstance(s, dict) and s.get("type") == "external_scanner"
                    and isinstance(s.get("interval_seconds"), (int, float))):
                vals.append(s["interval_seconds"])
    return min(vals) if vals else None


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


def load_runtimes(instances, pkg_dir):
    """Parse each instance's runtime.yaml -> {instance_name: dict}, for min_budget.strategy_min_budget."""
    out = {}
    for inst in instances:
        rel, name = inst.get("runtime"), inst.get("name")
        if not rel or not name:
            continue
        try:
            out[name] = yaml.safe_load(open(os.path.join(pkg_dir, rel))) or {}
        except Exception as exc:  # noqa: BLE001
            warn(f"{name}: runtime.yaml unreadable for min_budget ({exc})")
    return out


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
    for kind in ("archetype", "risk_level", "tier", "asset_scope", "direction"):
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
    for man_path in sorted(glob.glob("strategies/*/strategy.yaml")):
        m = yaml.safe_load(open(man_path)) or {}
        c = m.get("catalog", {}) or {}
        sid = m.get("id", os.path.dirname(man_path))
        # The catalog is the DEPLOYABLE-package registry. A package flagged non-deployable
        # (e.g. blocked on a missing runtime capability) is tracked + tested in the repo but
        # must NOT surface to users as installable — skip it (loudly).
        if c.get("status") == "blocked" or c.get("deployable") is False:
            print(f"  · skipping non-deployable package: {sid} "
                  f"(catalog.status={c.get('status')!r})", file=sys.stderr)
            continue
        instances = m.get("instances", []) or []
        instance_count = len(instances)

        validate_declared(glossary, c, sid)
        pkg_dir = os.path.dirname(man_path)
        cadence = derive_cadence_seconds(instances, pkg_dir)
        mb = min_budget.strategy_min_budget(m, load_runtimes(instances, pkg_dir))
        if isinstance(c.get("min_budget"), (int, float)):
            warn(f"{sid}: strategy.yaml still declares min_budget:{c['min_budget']} — it is now "
                 f"COMPUTED and the authored field is ignored; delete it (use min_budget_floor to raise)")

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
            "archetype": c.get("archetype"),
            "archetype_label": label_for(glossary, "archetype", c.get("archetype"), sid),
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
            # capital — min_budget is COMPUTED (min_budget.py), never authored
            "min_budget": mb["min_budget"],
            "wallet_count": mb["wallet_count"],
            "min_budget_binding_wallet": mb["binding_wallet"],
            "min_budget_breakdown": mb["breakdown"],
            "instance_count": instance_count,
            "funding_split": derive_funding_split(instances),
            "max_slots": derive_max_slots(instances, c),
            # registry
            "branch": branch,
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
    ap.add_argument("--updated", default=datetime.date.today().isoformat(),
                    help="catalog _updated date (YYYY-MM-DD); defaults to today so it is a real "
                         "freshness signal, not a frozen string")
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    catalog = build(a.updated, a.branch)
    blob = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    # Write BOTH: the repo copy (source of truth, next to the packages) AND the discover skill's local
    # copy, so the catalog travels with the skill when it's installed standalone (no ../../strategies/).
    targets = [
        os.path.join(REPO_ROOT, "strategies", "catalog.json"),
        os.path.join(REPO_ROOT, "senpi-strategy-discover", "catalog.json"),
    ]
    for t in targets:
        with open(t, "w") as f:
            f.write(blob)
    print(f"wrote catalog.json to {len(targets)} locations — {len(catalog['skills'])} strategies")
    if _WARNINGS:
        print(f"\n{len(_WARNINGS)} warning(s):", file=sys.stderr)
        for w in _WARNINGS:
            print(f"  ⚠ {w}", file=sys.stderr)
