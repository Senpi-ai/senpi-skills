#!/usr/bin/env python3
"""Generate strategies/catalog.json (the strategy registry index) from every strategies/*/strategy.yaml.

The catalog is GENERATED — never hand-edit it. Run from the repo root:
    python3 senpi-trading-runtime/scripts/gen_catalog.py [--updated YYYY-MM-DD] [--branch NAME] [--check]

`--check` writes nothing: it exits 1 when either committed catalog.json differs from a fresh run
(ignoring `_updated`), so CI can refuse a strategy.yaml change that ships without its catalog.

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
    "risk_level, tier, belief_plain, direction, descriptor, summary, risks, categories) are "
    "author-set in strategy.yaml `catalog:`; 'instances' lists each instance's name, funding_share "
    "and 'facts' (StrategyFacts v1 plus exit.lock_tiers, read from its runtime.yaml); 'shown_on' is the catalog id at the root of the "
    "'varies' chain (the page a version sits on), null when that root is not in the catalog; DERIVED "
    "fields (assets, leverage_max, funding_split, instance_count, cadence_seconds, time_horizon, "
    "max_slots) are computed from instances/params. Labels/glosses come from "
    "senpi-strategy-discover/references/glossary.yaml. 'min_budget' is the machine-COMPUTED minimum "
    "total budget at which the design functions (min_budget.py) — the smallest budget where every "
    "wallet funds and its smallest slot clears the $12 bumped notional; 'wallet_count' + "
    "'min_budget_binding_wallet' explain it. It is NOT authored — deleting "
    "an authored min_budget is a no-op; use min_budget_floor to RAISE it. Positions scale with budget "
    "above the min. A strategy is a deployable package; install via senpi-strategy-ops."
)

# Declared fields the author should set; missing ones warn.
DECLARED_REQUIRED = [
    "archetype", "sub_style", "asset_classes", "asset_scope",
    "risk_level", "tier", "belief_plain", "direction",
]

# Page-copy fields (author-set, optional for now): the line under the name, the hero's one-liner,
# and where the strategy goes wrong. Limits are what senpi-web's layout holds.
DESCRIPTOR_MAX = 45
SUMMARY_MAX = 140

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


# ---- per-instance facts (StrategyFacts v1) ----
# The shape senpi-trading-runtime records on launch (src/strategy/facts.ts), plus exit.lock_tiers
# (every phase-2 tier, which the page's profit-lock chart draws). Read straight from runtime.yaml,
# with the engine's defaults where the file is silent (runtime-yaml.md): phase2 is ON unless
# `enabled: false`; a time cut runs only with `enabled: true`. A field the file doesn't state is
# null. Raw values only — how a page words or hides them is the page's business.
FACTS_VERSION = 1


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _is_max_leverage_key(key):
    k = key.lower().replace("_", "")
    return "maxleverage" in k or "leveragemax" in k


def instance_facts(rt):
    strat = rt.get("strategy") or {}
    ext = [s for s in (rt.get("scanners") or [])
           if isinstance(s, dict) and s.get("type") == "external_scanner"]
    inputs = [s.get("inputs") or {} for s in ext]

    margin = next((m for i in inputs for m in [_num(i.get("marginPct", i.get("margin_pct")))]
                   if m), None) or _num(strat.get("margin_pct"))
    lev_default = _num(strat.get("default_leverage"))
    levs = [lev_default] if lev_default else []
    for i in inputs:
        for k, v in i.items():
            if _is_max_leverage_key(k) and _num(v):
                levs.append(v)
        tiers = i.get("leverageTiers")
        if isinstance(tiers, list):
            levs.extend(t["leverage"] for t in tiers if isinstance(t, dict) and _num(t.get("leverage")))
    intervals = [s["interval_seconds"] for s in ext if _num(s.get("interval_seconds"))]

    preset = ((rt.get("exit") or {}).get("dsl_preset")) or {}
    phase2 = preset.get("phase2") or {}
    tiers = []
    if phase2.get("enabled", True):
        tiers = [[t["trigger_pct"], t["lock_hw_pct"]] for t in (phase2.get("tiers") or [])
                 if isinstance(t, dict) and _num(t.get("trigger_pct"))
                 and isinstance(t.get("lock_hw_pct"), (int, float))]
    weak = preset.get("weak_peak_cut") or {}
    dead = preset.get("dead_weight_cut") or {}
    weak_cut = ({"after_min": weak["interval_in_minutes"], "under_pct": weak["min_value"]}
                if weak.get("enabled") is True and _num(weak.get("interval_in_minutes"))
                and _num(weak.get("min_value")) else None)
    dead_cut = ({"after_min": dead["interval_in_minutes"]}
                if dead.get("enabled") is True and _num(dead.get("interval_in_minutes")) else None)

    g = (rt.get("risk") or {}).get("guard_rails") or {}
    streak = _num(g.get("max_consecutive_losses"))
    pause = _num(g.get("cooldown_seconds"))

    return {
        "v": FACTS_VERSION,
        "leverage_default": lev_default,
        "leverage_max": max(levs) if levs else None,
        "margin_pct": margin,
        "slots": _num(strat.get("slots")),
        "scan_interval_s": min(intervals) if intervals else None,
        "exit": {
            "first_lock": tiers[0] if tiers else None,
            "lock_steps": len(tiers),
            "lock_tiers": tiers,
            "weak_peak_cut": weak_cut,
            "dead_weight_cut": dead_cut,
        },
        "limits": {
            "max_entries_per_day": _num(g.get("max_entries_per_day")),
            "daily_loss_pct": _num(g.get("daily_loss_limit_pct")),
            "loss_streak": {"count": streak, "pause_s": pause} if streak and pause else None,
            "drawdown_halt_pct": _num(g.get("drawdown_halt_pct")),
        },
    }


def build_instances(instances, runtimes, sid):
    out = []
    for inst in instances:
        name = inst.get("name")
        rt = runtimes.get(name)
        if rt is None:
            warn(f"{sid}: instance {name!r} has no readable runtime.yaml — its facts are null")
        out.append({
            "name": name,
            "funding_share": inst.get("funding_share"),
            "facts": instance_facts(rt) if rt is not None else None,
        })
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


def page_copy_problems(catalog):
    """Rule breaks in the page-copy fields that are present. A missing field is not a problem here:
    senpi-web falls back to what the page shows today. Shared with strategies/tests."""
    problems = []
    for field in ("descriptor", "summary", "risks"):
        v = catalog.get(field)
        if v is not None and (not isinstance(v, str) or not v.strip()):
            problems.append(f"`{field}` must be a non-empty string")
    d = catalog.get("descriptor")
    if isinstance(d, str) and d.strip():
        if len(d) > DESCRIPTOR_MAX:
            problems.append(f"`descriptor` is {len(d)} characters (max {DESCRIPTOR_MAX})")
        if d.rstrip().endswith("."):
            problems.append("`descriptor` ends with a full stop")
    sm = catalog.get("summary")
    if isinstance(sm, str) and len(sm) > SUMMARY_MAX:
        problems.append(f"`summary` is {len(sm)} characters (max {SUMMARY_MAX})")
    cats = catalog.get("categories")
    if cats is not None and not (isinstance(cats, list)
                                 and all(isinstance(x, str) and x for x in cats)):
        problems.append("`categories` must be a list of slugs")
    return problems


def stamp_shown_on(skills):
    """shown_on = the root of each record's `varies` chain, set only when that root is in the
    catalog (a root is its own). Needs every record, so it runs after they all exist."""
    ids = {s["id"] for s in skills}
    varies = {s["id"]: s.get("varies") for s in skills}
    for s in skills:
        node, seen = s["id"], {s["id"]}
        while varies.get(node):
            node = varies[node]
            if node in seen:  # a cycle is an authoring error; leave the record unplaced
                warn(f"{s['id']}: `varies` chain loops at {node}")
                node = None
                break
            seen.add(node)
        s["shown_on"] = node if node in ids else None


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
        for problem in page_copy_problems(c):
            warn(f"{sid}: {problem}")
        pkg_dir = os.path.dirname(man_path)
        cadence = derive_cadence_seconds(instances, pkg_dir)
        runtimes = load_runtimes(instances, pkg_dir)
        mb = min_budget.strategy_min_budget(m, runtimes)
        if isinstance(c.get("min_budget"), (int, float)):
            warn(f"{sid}: strategy.yaml still declares min_budget:{c['min_budget']} — it is now "
                 f"COMPUTED and the authored field is ignored; delete it (use min_budget_floor to raise)")
        if mb.get("unresolved_wallets"):
            warn(f"{sid}: min_budget could NOT resolve marginPct for sleeve(s) "
                 f"{mb['unresolved_wallets']} — they fell back to the $10 floor, so the minimum "
                 f"is likely understated. Add the sizing key to min_budget._MARGIN_KEYS or declare "
                 f"catalog.min_budget_floor.")

        skills.append({
            # identity
            "id": sid,
            "name": c.get("name"),
            "emoji": c.get("emoji"),
            # The template this one is a VARIANT of, when it is one. discover.py ranks a variant
            # below its parent on a tie and surfaces the name, so an agent can say "this is X with
            # one change" instead of offering two near-identical cards side by side.
            "varies": c.get("varies"),
            "tagline": c.get("tagline"),
            "belief_plain": c.get("belief_plain"),
            # page copy (declared; optional — senpi-web falls back when absent)
            "descriptor": c.get("descriptor"),
            "summary": c.get("summary"),
            "risks": c.get("risks"),
            "categories": c.get("categories") or [],
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
            "instance_count": instance_count,
            "funding_split": derive_funding_split(instances),
            "max_slots": derive_max_slots(instances, c),
            # each instance in manifest order, with what its runtime.yaml says it does
            "instances": build_instances(instances, runtimes, sid),
            # registry
            "branch": branch,
        })

    stamp_shown_on(skills)
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


def check(targets, branch):
    """0 when every committed copy equals a fresh build, else 1. `_updated` is the date of the
    last regeneration, not content, so the fresh build reuses each copy's own."""
    stale = []
    for t in targets:
        rel = os.path.relpath(t, REPO_ROOT)
        try:
            with open(t) as f:
                text = f.read()
            committed = json.loads(text)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            stale.append(f"{rel}: unreadable ({exc})")
            continue
        fresh = json.dumps(build(committed.get("_updated"), branch), ensure_ascii=False,
                           indent=2) + "\n"
        if fresh != text:
            stale.append(rel)
    if stale:
        print("catalog.json is stale — run senpi-trading-runtime/scripts/gen_catalog.py and "
              "commit both copies:", file=sys.stderr)
        for s in stale:
            print(f"  ✗ {s}", file=sys.stderr)
        return 1
    print(f"catalog.json is fresh ({len(targets)} copies)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--updated", default=datetime.date.today().isoformat(),
                    help="catalog _updated date (YYYY-MM-DD); defaults to today so it is a real "
                         "freshness signal, not a frozen string")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if a committed catalog.json differs from a fresh run "
                         "(ignoring _updated)")
    a = ap.parse_args()
    # Write BOTH: the repo copy (source of truth, next to the packages) AND the discover skill's local
    # copy, so the catalog travels with the skill when it's installed standalone (no ../../strategies/).
    targets = [
        os.path.join(REPO_ROOT, "strategies", "catalog.json"),
        os.path.join(REPO_ROOT, "senpi-strategy-discover", "catalog.json"),
    ]
    if a.check:
        sys.exit(check(targets, a.branch))
    catalog = build(a.updated, a.branch)
    blob = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    for t in targets:
        with open(t, "w") as f:
            f.write(blob)
    print(f"wrote catalog.json to {len(targets)} locations — {len(catalog['skills'])} strategies")
    if _WARNINGS:
        print(f"\n{len(_WARNINGS)} warning(s):", file=sys.stderr)
        for w in _WARNINGS:
            print(f"  ⚠ {w}", file=sys.stderr)
