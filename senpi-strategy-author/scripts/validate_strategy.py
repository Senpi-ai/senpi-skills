#!/usr/bin/env python3
"""Validate a Senpi strategy PACKAGE (scanner.py + runtime.yaml(s) + strategy.yaml).

Usage:  python3 validate_strategy.py <package-dir> [<package-dir> ...]

Asserts the manifest ↔ runtime ↔ package consistency the deterministic install
relies on. Exit 0 = all packages valid; exit 1 = at least one error.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import ast
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")


def validate(pkg: Path) -> list:
    errs = []
    man_path = pkg / "strategy.yaml"
    if not man_path.is_file():
        return [f"{pkg}: missing strategy.yaml"]
    try:
        man = yaml.safe_load(man_path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        return [f"{man_path}: unparseable ({e})"]

    sid = man.get("id")
    if sid != pkg.name:
        errs.append(f"id {sid!r} != package dir {pkg.name!r}")
    if not man.get("version"):
        errs.append("missing version (single source for catalog + attribution)")
    if not man.get("instances"):
        errs.append("no instances[]")

    seen_wallet_envs = set()
    for inst in man.get("instances", []):
        name = inst.get("name", "?")
        rt_rel = inst.get("runtime")
        scn = (inst.get("scanner") or {})
        wenv = inst.get("wallet_env")

        rt = pkg / rt_rel if rt_rel else None
        if not rt or not rt.is_file():
            errs.append(f"instance {name}: runtime {rt_rel!r} not found")
            continue
        rt_text = rt.read_text()

        if scn.get("name") not in rt_text:
            errs.append(f"instance {name}: scanner.name {scn.get('name')!r} not an external_scanner in {rt_rel}")
        ep = pkg / "scripts" / scn.get("entrypoint", "scanner.py")
        if not (pkg / scn.get("entrypoint", "scanner.py")).is_file() and not ep.is_file():
            errs.append(f"instance {name}: scanner.entrypoint {scn.get('entrypoint')!r} not found")
        if not wenv or ("${%s}" % wenv) not in rt_text:
            errs.append(f"instance {name}: wallet_env {wenv!r} not bound as ${{{wenv}}} in {rt_rel}")
        seen_wallet_envs.add(wenv)
        if inst.get("params") is None:
            errs.append(f"instance {name}: missing params block")

    # multi-instance must use distinct wallets
    if len(man.get("instances", [])) > 1 and len(seen_wallet_envs) < len(man["instances"]):
        errs.append("multi-instance strategy must declare a distinct wallet_env per instance")

    # scanner present + parses; no '@senpi/runtime' without -ai anywhere
    for py in (pkg / "scripts").glob("*.py"):
        try:
            ast.parse(py.read_text())
        except SyntaxError as e:
            errs.append(f"{py.name}: syntax error ({e})")
    for f in pkg.rglob("*"):
        if f.is_file() and f.suffix in (".py", ".yaml", ".md"):
            t = f.read_text(errors="ignore")
            if "@senpi/runtime" in t and "@senpi-ai/runtime" not in t.replace("@senpi/runtime", ""):
                # crude: flag any bare @senpi/runtime occurrence
                if "@senpi/runtime" in t:
                    errs.append(f"{f.name}: contains '@senpi/runtime' (use '@senpi-ai/runtime')")
                    break
    return errs


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for d in argv[1:]:
        pkg = Path(d).resolve()
        errs = validate(pkg)
        if errs:
            bad += 1
            print(f"✗ {pkg.name}:")
            for e in errs:
                print(f"    - {e}")
        else:
            man = yaml.safe_load((pkg / "strategy.yaml").read_text())
            print(f"✓ {pkg.name} v{man.get('version')} ({len(man.get('instances', []))} instance(s))")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv)
