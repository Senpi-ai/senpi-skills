#!/usr/bin/env python3
"""Strategy-package model: parse / validate / render — shared by deploy.py and close.py.

A strategy PACKAGE is `strategy.yaml` (our manifest) + one self-contained `runtime.yaml`
per instance + `<instance>/scanners/`. `strategy.yaml` is a thin deploy manifest; the
`runtime.yaml` owns everything the runtime needs (scanners, actions, exit, risk).

Linkage convention (validator-enforced, enables ledger-free reverse lookup):
  - every instance's runtime.yaml has  `group: <strategy id>`  and  `name: <id>-<instance>`
  - the manifest's `instances[].wallet_env` is bound as `${WALLET_ENV}` in that runtime.yaml

Render substitutes ONLY `${wallet_env}` (+ the decision-model env iff a runtime has an
`decision_mode: llm` action), then asserts zero `${...}` placeholders remain before deploy.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re
from pathlib import Path

try:
    import yaml  # prefer PyYAML when present
except ImportError:
    import _yaml as yaml  # vendored stdlib-only fallback — agent hosts may lack PyYAML / pip

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MARGIN_PCT_RE = re.compile(r"margin_?pct", re.I)


def margin_fraction_offenders(doc, path=""):
    """Margin-percent keys whose value is a fraction (0,1] where a PERCENT (0,100] is required
    (`marginPct: 0.10` meant 10 — 100× too small). Walks the whole runtime doc (scanners[].inputs,
    strategy.margin_pct, or a top-level emit; any key: marginPct / marginPctBase / margin_pct). Mirrors
    senpi-strategy-author validate_strategy so author-green == deploy-green. [(key, value), ...]."""
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            kp = f"{path}.{k}" if path else str(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and _MARGIN_PCT_RE.search(str(k)) and 0 < v <= 1:
                out.append((kp, v))
            else:
                out.extend(margin_fraction_offenders(v, kp))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.extend(margin_fraction_offenders(v, f"{path}[{i}]"))
    return out


class BadPackage(Exception):
    """Raised on a structurally unusable package (can't even build the model)."""


class Instance:
    """One deployable instance = one manifest entry + its runtime.yaml."""

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
    def exit_block(self):
        ex = (self.runtime_doc or {}).get("exit")
        return ex if isinstance(ex, dict) else {}

    @property
    def has_dsl(self):
        """True iff the runtime.yaml ships a DSL exit block (`exit:` with a preset or `engine: dsl`) —
        the built-in trailing stop. A deployed strategy WITHOUT one runs its positions naked (the
        funded-but-no-DSL hole). Mirrors the author-side validate_strategy exit-block requirement."""
        ex = self.exit_block
        return bool(ex) and (bool(ex.get("dsl_preset")) or str(ex.get("engine", "")).lower() == "dsl")

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


def strategies_root():
    """Absolute, CWD-independent root where fetched strategy packages live.

    MUST stay outside any managed skill dir: the runtime's skills-manager swap-replaces those
    dirs on every SKILL.md version bump, destroying anything inside (the 2026-07-30 incident —
    a CWD-relative dest meant deploys run from a skill dir were wiped on the next bump).

    Precedence: SENPI_STRATEGIES_DIR env > /data/workspace/strategies (agent hosts) >
    CWD-relative strategies/ (dev hosts without /data/workspace — unchanged legacy behavior)."""
    env = os.environ.get("SENPI_STRATEGIES_DIR", "").strip()
    if env:
        return Path(env)
    workspace = Path("/data/workspace")
    if workspace.is_dir():
        return workspace / "strategies"
    return Path("strategies")


def resolve_pkg_dir(arg):
    """Accept a package PATH (strategies/spider) OR a bare strategy id (spider, as discover emits)
    and return the directory that holds strategy.yaml. Tries the arg as-is, then strategies/<arg>
    (CWD-relative, legacy), then <strategies_root()>/<arg> (where remote fetches land)."""
    p = Path(arg)
    if (p / "strategy.yaml").is_file():
        return p
    nested = Path("strategies") / arg
    if (nested / "strategy.yaml").is_file():
        return nested
    durable = strategies_root() / arg
    if (durable / "strategy.yaml").is_file():
        return durable
    return p  # let load() raise the BadPackage with the original arg


_DEFAULT_INSTANCE = "main"


def _flat_instance(pkg_dir, pkg_id):
    """Synthesize the manifest entry for a FLAT single-instance package (one `runtime.yaml` at the
    package root + `scanners/`) — the layout agents naturally scaffold. Binds `wallet_env` to the
    `${...}` the flat runtime already uses for its wallet, so render substitutes correctly; falls back
    to `<ID>_WALLET` when the runtime declares none (validate then flags the missing binding, rather
    than the deploy failing opaquely). The synthesized instance flows through validate/render/deploy
    identically to a declared one."""
    wallet_env = None
    try:
        doc = yaml.safe_load((pkg_dir / "runtime.yaml").read_text()) or {}
        wallet_ref = (doc.get("strategy") or {}).get("wallet") if isinstance(doc, dict) else None
        m = _VAR_RE.search(str(wallet_ref or ""))
        if m:
            wallet_env = m.group(1)
    except Exception:  # noqa — best-effort detection; validate catches a bad/absent binding
        pass
    if not wallet_env:
        wallet_env = re.sub(r"[^A-Za-z0-9]", "_", str(pkg_id or "")).upper().strip("_") + "_WALLET"
    return {"name": _DEFAULT_INSTANCE, "runtime": "runtime.yaml",
            "wallet_env": wallet_env, "funding_share": 1.0}


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
        # Accept a FLAT single-instance package — synthesize the canonical `main` instance when there
        # IS a root runtime.yaml to point it at. A package with neither instances nor a root
        # runtime.yaml is genuinely unusable.
        if (pkg / "runtime.yaml").is_file():
            man = dict(man)
            man["instances"] = [_flat_instance(pkg, man.get("id"))]
        else:
            raise BadPackage("strategy.yaml: 'instances' must be a non-empty list "
                             "(or ship a single flat runtime.yaml at the package root)")
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
        # linkage convention — messages are PRESCRIPTIVE (the exact value to set), since every model
        # in the bake-off tripped on `name: <id>` vs the required `<id>-<instance>`.
        if inst.group != pkg.id:
            errs.append(f"{tag}: set runtime `group: {pkg.id}` (found {inst.group!r})")
        expect_name = f"{pkg.id}-{inst.name}"
        if inst.runtime_name != expect_name:
            errs.append(f"{tag}: set runtime `name: {expect_name}` (found {inst.runtime_name!r})")
        # marginPct is a PERCENT in (0,100]; a value <= 1 is the fraction slip (0.10 meant 10, 100x too
        # small). Refuse to fund it (author's validate_strategy flags it too; ops re-checks fetched packages).
        for kp, val in margin_fraction_offenders(inst.runtime_doc):
            errs.append(f"{tag}: `{kp}` must be a PERCENT in (0,100] — set {val * 100:g} (not {val})")
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
            # the runtime engine requires a non-empty signal_data_schema MAP on the external_scanner,
            # as a sibling of `inputs` (not nested inside it) — the other bake-off tripwire. Surface it
            # here, pre-funding, instead of at runtime-create after the wallet is already funded.
            sds = es.get("signal_data_schema")
            if not isinstance(sds, dict) or not sds:
                errs.append(f"{tag}: external_scanner needs a non-empty `signal_data_schema` map "
                            f"(a sibling of `inputs`, not nested inside it)")
        # Protection is not optional — a deployed strategy with no DSL exit runs every position naked
        # (the funded-but-no-DSL hole). Refuse to deploy it. (author's validate_strategy checks this too;
        # ops re-checks so a hand-edited or fetched package can't slip a naked strategy past deploy.)
        if not inst.has_dsl:
            errs.append(f"{tag}: runtime {inst.runtime_rel!r} has no DSL exit block "
                        f"(exit.dsl_preset / engine: dsl) — every deployed strategy must ship "
                        f"built-in protection")
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
