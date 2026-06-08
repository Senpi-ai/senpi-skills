"""Strategy package manifest loader — `strategy.yaml` is the single source of truth.

A strategy is a *package*: `scanner.py` + `runtime.yaml`(s) + a `strategy.yaml`
deploy declaration. `strategy.yaml` is canonical for every tunable; the scanner
reads its parameters from here (via `load_params`) instead of a `config/*.json`.

Resolution:
  - `find_manifest(start)` walks up from a file/dir to locate `strategy.yaml`.
  - `resolve_instance(...)` picks the instance for THIS process: the sole
    instance for single-instance strategies, otherwise the one whose declared
    `env` block (e.g. `{SPIDER_LEG: swing}`) matches the current environment.
  - `load_params(...)` returns that instance's `params` dict.

The instance-selecting env var (e.g. `SPIDER_LEG`) is set by the installer from
`strategy.yaml.instances[].env`; the scanner never hardcodes which instance it is.
"""
# Copyright 2026 Senpi (https://senpi.ai)
# Licensed under MIT
# Source: https://github.com/Senpi-ai/senpi-skills

import os
import sys
from pathlib import Path

MANIFEST_NAME = "strategy.yaml"


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # PyYAML — present on standard producer hosts
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "PyYAML is required to read strategy.yaml. Install it on the "
            "producer host (`pip install pyyaml`)."
        ) from e
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return data


def find_manifest(start=None) -> Path:
    """Walk up from `start` (a file or dir; defaults to the running script) to
    the nearest `strategy.yaml`. Raises FileNotFoundError if none is found."""
    anchor = Path(start or sys.argv[0] or ".").resolve()
    if anchor.is_file():
        anchor = anchor.parent
    for d in (anchor, *anchor.parents):
        candidate = d / MANIFEST_NAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"{MANIFEST_NAME} not found walking up from {anchor}"
    )


def load_manifest(start=None) -> dict:
    """Parse and return the whole strategy.yaml as a dict."""
    return _load_yaml(find_manifest(start))


def resolve_instance(start=None, instance_name=None) -> dict:
    """Return the instance dict for THIS process.

    - `instance_name` given -> that named instance.
    - single-instance strategy -> the sole instance.
    - multi-instance -> the instance whose declared `env` all matches the
      current environment (e.g. SPIDER_LEG=swing). Raises if it can't decide.
    """
    manifest = load_manifest(start)
    instances = manifest.get("instances") or []
    if not instances:
        raise ValueError(f"{MANIFEST_NAME} has no instances")

    if instance_name is not None:
        for inst in instances:
            if inst.get("name") == instance_name:
                return inst
        raise ValueError(f"instance {instance_name!r} not declared")

    if len(instances) == 1:
        return instances[0]

    matches = [
        inst for inst in instances
        if (inst.get("env"))
        and all(os.environ.get(k) == str(v) for k, v in inst["env"].items())
    ]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(i.get("name", "?") for i in instances)
    raise RuntimeError(
        f"Cannot resolve which instance to run among [{names}]. Set the "
        f"instance-selecting env var declared in {MANIFEST_NAME}.instances[].env "
        f"(matched {len(matches)})."
    )


def load_params(start=None, instance_name=None) -> dict:
    """Return the `params` dict for this process's instance — the single source
    of scanner tunables. Drop-in replacement for the old `load_config()`."""
    inst = resolve_instance(start, instance_name)
    return dict(inst.get("params") or {})
