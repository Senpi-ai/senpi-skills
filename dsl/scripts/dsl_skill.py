#!/usr/bin/env python3
"""
dsl_skill.py — Runtime peer skill discovery and require_skill() with semver.

Used by wolf-strategy and other skills that declare peerDependencies on dsl.
"""
from __future__ import annotations

import importlib
import json
import os
import sys


def _semver_gte(installed: str, required: str) -> bool:
    """True if installed version >= required (e.g. "5.1.0" >= "5.0.0")."""
    try:
        from packaging.version import Version
        return Version(installed) >= Version(required)
    except Exception:
        # Fallback: parse MAJOR.MINOR.PATCH
        def parse(v: str):
            parts = v.lstrip("v").split(".")
            return tuple(int(p) if p.isdigit() else 0 for p in (parts + ["0", "0"])[:3])
        return parse(installed) >= parse(required)


def require_skill(name: str, min_version: str | None = None):
    """
    Resolve a peer skill by name and optionally enforce minimum version.
    Adds the skill's scripts dir to sys.path and returns the dsl_engine module
    when name == "dsl".

    Raises ImportError with an actionable message if the skill is missing or outdated.
    """
    skills_root = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    )
    skill_dir = os.path.join(skills_root, name)
    scripts_dir = os.path.join(skill_dir, "scripts")

    if not os.path.isdir(scripts_dir):
        raise ImportError(
            f"Peer skill '{name}' not found at {skill_dir}.\n"
            f"Install with: senpi skills install {name}"
        )

    if min_version:
        manifest_path = os.path.join(skill_dir, "skill.json")
        if not os.path.isfile(manifest_path):
            raise ImportError(
                f"Peer skill '{name}' has no skill.json at {manifest_path}.\n"
                f"Install with: senpi skills install {name}"
            )
        with open(manifest_path) as f:
            version = json.load(f).get("version", "0.0.0")
        if not _semver_gte(version, min_version):
            raise ImportError(
                f"Skill '{name}' v{version} < required v{min_version}.\n"
                f"Upgrade with: senpi skills upgrade {name}"
            )

    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    if name == "dsl":
        return importlib.import_module("dsl_engine")
    return importlib.import_module(name.replace("-", "_"))
