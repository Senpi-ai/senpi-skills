#!/usr/bin/env python3
"""Strategy-package model: parse / validate / render — shared by deploy.py and close.py.

A strategy PACKAGE is `strategy.yaml` (our manifest) + one self-contained `runtime.yaml`
per instance + `<instance>/scanners/`. `strategy.yaml` is a thin deploy manifest; the
`runtime.yaml` owns everything the runtime needs (scanners, actions, exit, risk).

Linkage convention (validator-enforced, enables ledger-free reverse lookup):
  - every leg's runtime.yaml has  `group: <strategy id>`  and  `name: <id>-<instance>`
  - the manifest's `instances[].wallet_env` is bound as `${WALLET_ENV}` in that runtime.yaml

Render substitutes ONLY `${wallet_env}` (+ the decision-model env iff a runtime has an
`decision_mode: llm` action), then asserts zero `${...}` placeholders remain before deploy.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
from pathlib import Path

try:
    import yaml  # prefer PyYAML when present
except ImportError:
    import _yaml as yaml  # vendored stdlib-only fallback — agent hosts may lack PyYAML / pip

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class BadPackage(Exception):
    """Raised on a structurally unusable package (can't even build the model)."""


class Instance:
    """One deployable leg = one manifest entry + its runtime.yaml."""

    def __init__(self, manifest_entry, pkg_dir):
        e = manifest_entry if isinstance(manifest_entry, dict) else {}
        self.name = e.get("name")
        self.runtime_rel = e.get("runtime")
        self.wallet_env = e.get("wallet_env")
        self.funding_share = e.get("funding_share")
        self.runtime_path = (pkg_dir / self.runtime_rel) if self.runtime_rel else None
        self.runtime_text = None
        self.runtime_doc = None
        if self.runtime_path and self.runtime_path.is_file():
            self.runtime_text = self.runtime_path.read_text()
            try:
                self.runtime_doc = yaml.safe_load(self.runtime_text) or {}
            except yaml.YAMLError:
                self.runtime_doc = None

    # ---- fields read from the runtime.yaml ----
    @property
    def runtime_name(self):
        """The runtime instance id (`runtime delete --id`); == <strategy id>-<instance>."""
        return (self.runtime_doc or {}).get("name")

    @property
    def group(self):
        return (self.runtime_doc or {}).get("group")

    @property
    def external_scanner(self):
        for s in (self.runtime_doc or {}).get("scanners", []) or []:
            if isinstance(s, dict) and s.get("type") == "external_scanner":
                return s
        return {}

    @property
    def interval_seconds(self):
        return self.external_scanner.get("interval_seconds")

    @property
    def needs_model(self):
        """True iff any action runs decision_mode: llm (then a decision-model env must be injected)."""
        for a in (self.runtime_doc or {}).get("actions", []) or []:
            if isinstance(a, dict) and str(a.get("decision_mode", "")).lower() == "llm":
                return True
        return False

    def render(self, wallet, model_env=None, model=None):
        """Return the runtime.yaml text with ${wallet_env} (+ model env iff needed) resolved.

        Raises BadPackage if any `${...}` placeholder remains unresolved.
        """
        if self.runtime_text is None:
            raise BadPackage(f"instance {self.name}: runtime {self.runtime_rel!r} not readable")
        text = self.runtime_text.replace("${%s}" % self.wallet_env, wallet)
        if self.needs_model:
            if not (model_env and model):
                raise BadPackage(f"instance {self.name}: decision_mode: llm needs a decision model")
            text = text.replace("${%s}" % model_env, model)
        leftover = sorted(set(_VAR_RE.findall(text)))
        if leftover:
            raise BadPackage(
                f"instance {self.name}: unresolved ${{...}} after render: {', '.join(leftover)}")
        return text


class Package:
    def __init__(self, pkg_dir, manifest):
        self.dir = pkg_dir
        self.manifest = manifest
        self.id = manifest.get("id")
        self.version = manifest.get("version")
        self.defaults = manifest.get("defaults") or {}
        self.catalog = manifest.get("catalog") or {}
        self.instances = [Instance(e, pkg_dir) for e in (manifest.get("instances") or [])]

    @property
    def model_env(self):
        return self.defaults.get("decision_model_env")

    @property
    def any_needs_model(self):
        return any(i.needs_model for i in self.instances)


def resolve_pkg_dir(arg):
    """Accept a package PATH (strategies/spider) OR a bare strategy id (spider, as discover emits)
    and return the directory that holds strategy.yaml. Tries the arg as-is, then strategies/<arg>."""
    p = Path(arg)
    if (p / "strategy.yaml").is_file():
        return p
    nested = Path("strategies") / arg
    if (nested / "strategy.yaml").is_file():
        return nested
    return p  # let load() raise the BadPackage with the original arg


def load(pkg_dir) -> Package:
    """Parse strategy.yaml into a Package. Accepts a path or a bare id (resolved to strategies/<id>).
    Raises BadPackage if it can't be modelled at all."""
    pkg = resolve_pkg_dir(pkg_dir).resolve()
    man_path = pkg / "strategy.yaml"
    if not man_path.is_file():
        raise BadPackage(f"{pkg_dir!r}: no strategy.yaml found (looked at {pkg_dir} and "
                         f"strategies/{pkg_dir}) — pass a strategy id or package directory")
    try:
        man = yaml.safe_load(man_path.read_text())
    except yaml.YAMLError as e:
        raise BadPackage(f"strategy.yaml is not valid YAML: {e}")
    if not isinstance(man, dict):
        raise BadPackage("strategy.yaml did not parse to a mapping")
    if not man.get("id"):
        raise BadPackage("strategy.yaml: missing 'id'")
    if not isinstance(man.get("instances"), list) or not man["instances"]:
        raise BadPackage("strategy.yaml: 'instances' must be a non-empty list")
    return Package(pkg, man)


def validate(pkg: Package) -> list:
    """Return a list of consistency errors ([] == valid). Asserts the manifest ↔ runtime ↔
    package invariants deploy relies on, including the group/name linkage convention."""
    errs = []
    if pkg.id != pkg.dir.name:
        errs.append(f"id {pkg.id!r} != package dir {pkg.dir.name!r}")
    if not pkg.version:
        errs.append("missing version (single source for catalog + attribution)")

    seen_wallet_envs = set()
    for inst in pkg.instances:
        tag = f"instance {inst.name or '?'}"
        if not inst.name:
            errs.append(f"{tag}: missing name")
        if not inst.runtime_rel or inst.runtime_path is None or not inst.runtime_path.is_file():
            errs.append(f"{tag}: runtime {inst.runtime_rel!r} not found")
            continue
        if inst.runtime_doc is None:
            errs.append(f"{tag}: runtime {inst.runtime_rel!r} is not valid YAML")
            continue
        # linkage convention
        if inst.group != pkg.id:
            errs.append(f"{tag}: runtime group {inst.group!r} != strategy id {pkg.id!r}")
        expect_name = f"{pkg.id}-{inst.name}"
        if inst.runtime_name != expect_name:
            errs.append(f"{tag}: runtime name {inst.runtime_name!r} != {expect_name!r}")
        # wallet binding
        if not inst.wallet_env:
            errs.append(f"{tag}: missing wallet_env")
        elif ("${%s}" % inst.wallet_env) not in inst.runtime_text:
            errs.append(f"{tag}: wallet_env {inst.wallet_env!r} not bound as ${{{inst.wallet_env}}}")
        else:
            seen_wallet_envs.add(inst.wallet_env)
        # external scanner + entrypoint
        es = inst.external_scanner
        if not es:
            errs.append(f"{tag}: runtime has no external_scanner")
        else:
            sub = (es.get("path") or ".").lstrip("./")
            ep = inst.runtime_path.parent / sub / es.get("entrypoint", "scan.py")
            if not ep.is_file():
                errs.append(f"{tag}: scanner entrypoint {ep.name!r} not found at {ep.parent}")
        if inst.funding_share is None:
            errs.append(f"{tag}: missing funding_share")
        # llm actions need a model env declared
        if inst.needs_model and not pkg.model_env:
            errs.append(f"{tag}: decision_mode: llm but no defaults.decision_model_env declared")

    if len(pkg.instances) > 1 and len(seen_wallet_envs) < len(pkg.instances):
        errs.append("multi-instance strategy must declare a distinct wallet_env per instance")

    shares = [i.funding_share for i in pkg.instances if i.funding_share is not None]
    if shares and abs(sum(shares) - 1.0) > 1e-6:
        errs.append(f"funding_share must sum to 1.0 (got {sum(shares)})")

    # no bare @senpi/runtime anywhere in the package
    for f in pkg.dir.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".yaml", ".yml", ".md", ".json"):
            t = f.read_text(errors="ignore")
            if "@senpi/runtime" in t.replace("@senpi-ai/runtime", ""):
                errs.append(f"{f.relative_to(pkg.dir)}: contains '@senpi/runtime' (use '@senpi-ai/runtime')")
                break
    return errs
