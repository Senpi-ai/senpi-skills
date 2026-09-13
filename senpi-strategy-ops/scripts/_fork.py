#!/usr/bin/env python3
"""A template deploys under the user's name — `fork()` is how.

It copies a catalog package to `<strategies root>/<owner>-<template>/` (or `<their-words>/`) and
rewrites the four things that carry identity: the manifest `id`, its `catalog.name`, each runtime's
`name`/`group` linkage and the first line of its `description` (the mandate senpi-portfolio reads
back). `forked_from` records the lineage. Everything else stays byte-identical — the edits are
textual, never a YAML re-dump, so comments and layout survive. Levers are moved afterwards with the
normal edit path, never here.

Two names, on purpose. The backend strategy name is derived by the deploy verb from the package id
and allows only `[A-Za-z0-9_-]` (and the backend lowercases it), so the id is `ignas-phalanx`. The
display name — `Ignas's Phalanx` — lives in `catalog.name` and the runtime description, and is what
the agent says. Both are printed by every verb that makes or deploys a fork.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import shutil
from pathlib import Path

import _pkg

MAX_ID = 40        # the deploy verb's sanitizeStrategyName cap (runtime src/deploy/package.ts)
MAX_OWNER = 20     # leaves room for `-<template>` and `-<instance>` under the cap
MIN_ID = 3         # the backend's floor for a strategy name
SKIP_NAMES = {".deploy-state.json", ".senpi-proof.json", "__pycache__", ".DS_Store"}


class ForkError(ValueError):
    """A fork that must not be made — the message is the user-facing reason."""


def slug(text, cap=MAX_ID):
    """Lowercase `[a-z0-9-]`, words joined by '-', capped — the shape the deploy verb accepts unchanged."""
    s = re.sub(r"[^a-z0-9-]", "", re.sub(r"[\s_]+", "-", str(text or "").strip().lower()))
    return re.sub(r"-{2,}", "-", s).strip("-")[:cap].strip("-")


def short_title(catalog_name, pkg_id):
    """`Phalanx — Proven-Cohort Rotation` → `Phalanx`: the name before the tagline separator."""
    t = re.split(r"\s[—–-]\s|:\s", str(catalog_name or pkg_id or "").strip(), 1)[0].strip()
    return t or str(pkg_id)


def names_for(pkg, owner=None, name=None):
    """`(id, display)` for a fork of `pkg`: `--name` (their words) wins; else `<owner>-<template>`."""
    if name and str(name).strip():
        fid, display = slug(name), str(name).strip()
    elif owner and str(owner).strip():
        if re.fullmatch(r"[Mm]\d+", str(owner).strip()):
            raise ForkError(f"{owner!r} is the user's Senpi ID, not a name: leave --owner off and their username is "
                            f"read for you, or ask what to call it and pass --name <their words>")
        o = slug(owner, MAX_OWNER)
        if not o:
            raise ForkError(f"owner {owner!r} leaves nothing usable in a strategy id — pass --name <their words> instead")
        fid, display = f"{o}-{pkg.id}"[:MAX_ID].strip("-"), f"{str(owner).strip()}'s {short_title((pkg.catalog or {}).get('name'), pkg.id)}"
    else:
        raise ForkError("a template deploys under the user's name, and their Senpi username could not be read: "
                        "ask what to call it and pass --name <their words>")
    if len(fid) < MIN_ID:
        raise ForkError(f"{fid!r} is shorter than the {MIN_ID} characters a strategy name needs — pass --name <their words>")
    return fid, display


def _set_top_key(text, key, value):
    """Replace the first column-0 `key:` line, or insert one before the first non-comment line."""
    pat = re.compile(rf"(?m)^{re.escape(key)}:[ \t]*.*$")
    if pat.search(text):
        return pat.sub(f"{key}: {value}", text, count=1)
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")), 0)
    lines.insert(at, f"{key}: {value}")
    return "\n".join(lines)


def _set_catalog_name(text, display):
    """`catalog.name` — the first indented `name:` inside the column-0 `catalog:` block."""
    lines = text.split("\n")
    quoted = '"' + display.replace('"', '\\"') + '"'
    start = next((i for i, l in enumerate(lines) if re.match(r"^catalog:\s*(#.*)?$", l)), None)
    if start is None:
        raise ForkError("strategy.yaml has no `catalog:` block to name the fork in")
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.strip() and not l.startswith((" ", "\t")):
            break                                   # the block ended
        m = re.match(r"^(\s+)name:\s*.*$", l)
        if m:
            lines[i] = f"{m.group(1)}name: {quoted}"
            return "\n".join(lines)
    lines.insert(start + 1, f"  name: {quoted}")
    return "\n".join(lines)


def _add_forked_from(text, template_id, version):
    block = f'forked_from:\n  id: {template_id}\n  version: "{version}"'
    lines = text.split("\n")
    at = next(i for i, l in enumerate(lines) if re.match(r"^version:", l))     # validate requires `version`
    lines.insert(at + 1, block)
    return "\n".join(lines)


def _prefix_description(text, prefix):
    """Put `prefix` first in the runtime `description` — block scalar, quoted line or absent."""
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if re.match(r"^description:", l)), None)
    if at is None:
        anchor = next((i for i, l in enumerate(lines) if re.match(r"^group:", l)), 0)
        lines[anchor + 1:anchor + 1] = ["description: >", f"  {prefix}"]
        return "\n".join(lines)
    head = lines[at]
    m = re.match(r"^description:\s*([>|][-+]?)\s*(#.*)?$", head)
    if m:                                            # block scalar: a new first line at the block's indent
        indent = "  "
        for l in lines[at + 1:]:
            if l.strip():
                indent = re.match(r"^(\s*)", l).group(1) or "  "
                break
        lines.insert(at + 1, f"{indent}{prefix}")
        return "\n".join(lines)
    raw = head.split(":", 1)[1].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].replace('\\"', '"')
    lines[at:at + 1] = ["description: >", f"  {prefix}"] + ([f"  {raw}"] if raw else [])
    return "\n".join(lines)


def _ignore(_dir, names):
    return [n for n in names if n in SKIP_NAMES or n.startswith(".senpi-fetch-") or ".bak-" in n]


def fork(pkg, root, owner=None, name=None, log=lambda m: None):
    """Make (or reuse) the user's fork of `pkg` under `root`. Returns `(dir, info)`.

    Reuse is by lineage: an existing directory is returned untouched when it is a fork of this same
    template (a live fork keeps its deploy state; a fork is a copy, so a newer template is not grafted
    onto it). Any other occupant of the name is refused rather than overwritten."""
    if (pkg.manifest or {}).get("forked_from"):
        ff = pkg.manifest["forked_from"] or {}
        raise ForkError(f"{pkg.id} is already a fork (of {ff.get('id')} {ff.get('version')}) — deploy it by "
                        f"directory, or fork the template it came from")
    fid, display = names_for(pkg, owner, name)
    root = Path(root)
    dest = root / fid
    info = dict(id=fid, display=display, forked_from=dict(id=pkg.id, version=str(pkg.version)),
                runtimes=[f"{fid}-{i.name}" for i in pkg.instances], dir=str(dest), reused=False)
    if dest.exists():
        try:
            have = _pkg.load(dest)
        except _pkg.BadPackage as e:
            raise ForkError(f"{dest} exists but is not a package ({e}) — move it aside or pass --name <another name>")
        ff = (have.manifest or {}).get("forked_from") or {}
        if ff.get("id") != pkg.id:
            raise ForkError(f"{dest} exists and is not a fork of {pkg.id} — pass --name <another name>")
        info["reused"] = True
        info["display"] = (have.catalog or {}).get("name") or display
        log(f"  fork {fid} already exists ({info['display']}, from {ff.get('id')} {ff.get('version')}) — using it")
        return dest, info
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg.dir, dest, ignore=_ignore)
    try:
        man = dest / "strategy.yaml"
        text = man.read_text()
        text = _set_top_key(text, "id", fid)
        text = _set_catalog_name(text, display)
        text = _add_forked_from(text, pkg.id, pkg.version)
        man.write_text(text)
        title = short_title((pkg.catalog or {}).get("name"), pkg.id)
        for inst in pkg.instances:
            rp = dest / inst.runtime_rel
            t = rp.read_text()
            t = _set_top_key(t, "name", f"{fid}-{inst.name}")
            t = _set_top_key(t, "group", fid)
            t = _prefix_description(t, f"{display} — forked from {title} {pkg.version}.")
            rp.write_text(t)
        forked = _pkg.load(dest)
        errs = _pkg.validate(forked)
        if errs:
            raise ForkError("the fork did not validate — nothing kept: " + "; ".join(errs))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    log(f"  forked {pkg.id} {pkg.version} → {dest} ({display})")
    return dest, info
